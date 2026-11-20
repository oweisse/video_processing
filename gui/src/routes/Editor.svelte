<script lang="ts">
  // The editor (docs/gui-design.md §8-§10): pick ranges from prepared sources, arrange
  // them on the timeline, lay music under them, export one MP4.
  import { onDestroy, untrack } from "svelte";
  import { ltr } from "../lib/bidi";
  import { api, media, type Output, type Source } from "../lib/api";
  import ExportDialog from "../edit/ExportDialog.svelte";
  import { audioContext } from "../lib/audio";
  import { STREAM_AFTER } from "../lib/voices";
  import { versionName, versionSettings, type Piece } from "../media/pieces";
  import AddVideos from "../edit/AddVideos.svelte";
  import Modal from "../components/Modal.svelte";
  import StorageView from "../components/StorageView.svelte";
  import SubtitlesPanel from "../edit/SubtitlesPanel.svelte";
  import GenerateSubtitles, { type Stretch as SubStretch } from "../edit/GenerateSubtitles.svelte";
  import SubtitlesSetup from "../edit/SubtitlesSetup.svelte";
  import NamesDialog from "../edit/NamesDialog.svelte";
  import { edit as editSubs, loadDoc, loadSetup, setTrack, subs as subtitles } from "../edit/subtitles.svelte";
  import { clipStatus, cueAt, timelineCues, type Track } from "../lib/subtitles";
  import { deleteVersion, renameVersion as renameShared } from "../media/versions";
  import NewRun from "./NewRun.svelte";
  import Candidates from "./Candidates.svelte";
  import VideoStrips from "../edit/VideoStrips.svelte";
  import FrameDialog from "../edit/FrameDialog.svelte";
  import { cleanStretches, clipStretch, type BatchRow } from "../edit/cleanclips";
  import { awaiting, await_, forget } from "../edit/awaiting.svelte";
  import {
    audioFor, coveringOutput, coveringOutputs, glideFrame, matchFrame, sameFrame, zoomGlide, zoomJoin, ZOOM_GLIDE_CHOICES, ZOOM_GLIDE_SEC, displayWidth, suggestCanvas, zoomOf, duration as editDuration, joinsOf, newId, place, clipAt, split,
    type Clip, type Join, type JoinInfo, type MusicClip, type Project,
  } from "../lib/edit";
  import { Program } from "../lib/program";
  import { href } from "../lib/router.svelte";
  import { loadPref, savePref } from "../lib/prefs";
  import { findOutput, loadLibrary, nativePick, reportError, store, toast, queued } from "../lib/state.svelte";
  import { formatClock, formatDuration } from "../lib/time";
  import FolderBrowser from "../components/FolderBrowser.svelte";
  import Timeline, { type Selection, type ThumbInfo } from "../components/Timeline.svelte";

  let { name }: { name: string } = $props();

  let project = $state<Project | null>(null);
  let error = $state<string | null>(null);
  let selected = $state<Selection>(null);
  let pps = $state(20);
  let time = $state(0);
  let playing = $state(false);
  let saving = $state<"saved" | "saving" | "error">("saved");
  const undo: string[] = [];
  const redo: string[] = [];
  let historyTick = $state(0);

  // ---------------------------------------------------------------- loading and saving
  // svelte-ignore state_referenced_locally
  api.getProject(name).then((p) => (project = p)).catch((e) => (error = e.message));

  let saveTimer: ReturnType<typeof setTimeout> | undefined;
  function scheduleSave() {
    saving = "saving";
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, 600);
  }
  async function flush() {
    clearTimeout(saveTimer);
    if (!project) return;
    try {
      await api.saveProject(name, $state.snapshot(project));
      saving = "saved";
    } catch (e) {
      saving = "error";
      reportError(e);
    }
  }

  function commit(mutate: (p: Project) => void) {
    if (!project) return;
    const before = JSON.stringify($state.snapshot(project));
    const next = JSON.parse(before) as Project;
    mutate(next);
    if (JSON.stringify(next) === before) return;
    undo.push(before);
    if (undo.length > 200) undo.shift();
    redo.length = 0;
    historyTick++;
    project = next;
    scheduleSave();
  }
  function undoStep() {
    const prev = undo.pop();
    if (!prev || !project) return;
    redo.push(JSON.stringify($state.snapshot(project)));
    project = JSON.parse(prev);
    historyTick++;
    scheduleSave();
  }
  function redoStep() {
    const next = redo.pop();
    if (!next || !project) return;
    undo.push(JSON.stringify($state.snapshot(project)));
    project = JSON.parse(next);
    historyTick++;
    scheduleSave();
  }

  // ---------------------------------------------------------------- sources
  const sources = $derived((store.library?.sources ?? []).filter((s) => s.exists));
  const sourceById = (id: string) => store.library?.sources.find((s) => s.id === id) ?? null;
  const sourceLength = (id: string) =>
    sourceById(id)?.probe?.duration_sec ?? project?.sources[id]?.probe?.duration_sec ?? Infinity;
  const joinInfo: JoinInfo = {
    sourceSeconds: (id) => sourceById(id)?.probe?.duration_sec ?? project?.sources[id]?.probe?.duration_sec,
    audioSeconds: (c) => (c.audio?.output ? findOutput(c.audio.output)?.output.window?.actual_duration_sec : null),
  };
  const sourceName = (id: string) => sourceById(id)?.name ?? project?.sources[id]?.path.split("/").pop() ?? id;

  let thumbs = $state<Record<string, ThumbInfo | undefined>>({});
  $effect(() => {
    for (const s of sources) {
      if (s.prep?.thumbs && !thumbs[s.id]) {
        const v = s.probe?.video;
        const aspect = v ? displayWidth(v) / v.height : 16 / 9;
        thumbs[s.id] = { interval: 2, count: 0, aspect };
        api.thumbs(s.id).then((t) => (thumbs[s.id] = { ...t, aspect })).catch(() => (thumbs[s.id] = undefined));
      }
    }
  });

  // ---------------------------------------------------------------- program player
  let videoA = $state<HTMLVideoElement>();
  let videoB = $state<HTMLVideoElement>();
  let program = $state<Program | null>(null);
  let raf = 0;
  // The videos only exist once the project has loaded.
  $effect(() => {
    if (!videoA || !videoB || program) return;
    const pr = new Program(audioContext(), [videoA, videoB], {
      proxy: (id) => `/media/proxy?source=${encodeURIComponent(id)}`,
      // A cleaned clip plays from its whole output (sound to spare is in it); original
      // sound is cut for the clip, with what a soft cut borrows on either side.
      // Long original sound streams from the editing copy, which carries it.
      clipAudio: (c, pre, post) => {
        if (c.audio?.output && c.audio.start_sec !== undefined) {
          return { url: media.outputAudio(c.audio.output), offset: c.in_sec - c.audio.start_sec,
                   seconds: c.audio.duration_sec ?? joinInfo.audioSeconds(c) };
        }
        const span = c.out_sec - c.in_sec + pre + post;
        if (span > STREAM_AFTER && sourceById(c.source)?.prep?.proxy) {
          return { url: `/media/proxy?source=${encodeURIComponent(c.source)}`, offset: c.in_sec,
                   seconds: joinInfo.sourceSeconds(c.source) };
        }
        return { url: media.original(c.source, c.in_sec - pre, span), offset: pre, seconds: span };
      },
      music: (m) => media.file(m.file),
      joinInfo,
      // a zoom glide moves the framing over the first second of a clip that continues a shot
      frameAt: (i, t) => {
        const cs = project?.tracks.video ?? [];
        const c = cs[i];
        if (!c) return null;
        const g = zoomGlide(cs[i - 1], c);
        return g && t < g.sec ? glideFrame(g, t, outShape, pictureOf(c.source)) : c.frame;
      },
    });
    pr.onchange(() => (playing = pr.playing));
    const loop = () => {
      pr.frame();
      syncSourceSound();
      time = pr.time;
      if (playUntil !== null && (!pr.playing || time >= playUntil)) {
        if (pr.playing) pr.pause();
        playUntil = null;
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    program = pr;
  });
  onDestroy(() => {
    cancelAnimationFrame(raf);
    program?.dispose();
    void flush();
  });
  $effect(() => {
    if (!project || !program) return;
    const snap = $state.snapshot(project.tracks) as Project["tracks"];
    void program.setEdit(snap.video, snap.music);
  });

  const total = $derived(project ? editDuration(project.tracks.video) : 0);
  // ---------------------------------------------------------------- subtitles (docs/subtitles-plan.md §7)
  let playUntil: number | null = null;
  function playRange(from: number, to: number) {
    srcVideo?.pause();
    program?.seek(from);
    playUntil = to;
    void program?.play();
  }
  let lowerTab = $state<"videos" | "subtitles">(loadPref("edit.lowerTab", "videos"));
  $effect(() => savePref("edit.lowerTab", lowerTab));
  let generating = $state<{ stretches: SubStretch[]; again: boolean } | null>(null);
  let showSubSetup = $state(false);
  let showSubMenu = $state(false);
  let showNames = $state(false);
  // every video the project uses: its subtitles, loaded once
  $effect(() => {
    for (const sid of Object.keys(project?.sources ?? {})) void loadDoc(sid);
  });
  // while a subtitle job writes to a video of this project, show its work as it comes
  const subJobKey = $derived(store.jobs
    .filter((j) => j.type.startsWith("subtitles") && j.type !== "subtitles_setup" && project?.sources[j.meta?.source_id])
    .map((j) => `${j.meta.source_id}:${j.state}:${j.progress?.processed_sec ?? ""}`).join("|"));
  let lastSubKey = "";
  $effect(() => {
    const key = subJobKey;
    if (key === lastSubKey) return;
    const prev = new Set(lastSubKey.split("|"));
    lastSubKey = key;
    const changed = new Set(key.split("|").filter((k) => k && !prev.has(k)).map((k) => k.split(":")[0]));
    for (const sid of changed) void loadDoc(sid, true);
    // once a job has ended, fetch again a moment later, in case its last write raced a fetch
    const ended = key.split("|").filter((k) => k && !prev.has(k) && /:(done|failed|cancelled):/.test(k)).map((k) => k.split(":")[0]);
    for (const sid of new Set(ended)) setTimeout(() => void loadDoc(sid, true), 1500);
    if (key.includes(":done:")) void loadSetup();
  });
  const subTrack = $derived<Track>(subtitles.track);
  const subItems = $derived(project && subTrack !== "off" ? timelineCues(project.tracks.video, subtitles.docs, subTrack) : []);
  const laneItems = $derived(project ? timelineCues(project.tracks.video, subtitles.docs, subTrack === "off" ? "mixed" : subTrack) : []);
  const shownSub = $derived(cueAt(subItems, time));
  const subStatus = (c: Clip) => clipStatus(c, subtitles.docs[c.source]);
  const withoutSubs = $derived(project ? project.tracks.video.filter((c) => !c.audio?.mute && !subStatus(c).covered).length : 0);
  const subJobFor = (c: Clip) => store.jobs.find((j) => j.type.startsWith("subtitles") && j.type !== "subtitles_setup"
    && (j.state === "queued" || j.state === "running") && j.meta?.source_id === c.source
    && (j.meta?.start_sec == null || (j.meta.start_sec < c.out_sec && j.meta.start_sec + (j.meta.duration_sec ?? 1e9) > c.in_sec)));
  async function needSetup(what: "transcribe" | "translate") {
    const st = subtitles.setup ?? (await loadSetup());
    if (st && !st.ready[what]) {
      showSubSetup = true;
      return true;
    }
    return false;
  }
  function stretchOf(c: Clip): SubStretch {
    const o = c.audio?.output ? findOutput(c.audio.output)?.output ?? null : null;
    return { source: c.source, sourceName: sourceName(c.source), start: c.in_sec, end: c.out_sec, cleaned: o, clips: [c.id] };
  }
  async function deleteSubtitles(c: Clip) {
    const st = subStatus(c);
    const i = project ? project.tracks.video.indexOf(c) + 1 : 0;
    if (!confirm(`Delete the subtitles of clip ${i} (${st.cues} line${st.cues === 1 ? "" : "s"}, with their English and Hebrew versions)?\n\n` +
        "Only the subtitles go: the clip stays on the timeline. You can generate them again.")) return;
    if (await editSubs(c.source, [{ op: "delete_range", start: c.in_sec, end: c.out_sec }], "deleting subtitles"))
      toast(`Deleted the subtitles of clip ${i}. Undo is in the Subtitles tab.`);
  }
  async function generateFor(c: Clip, again: boolean) {
    if (await needSetup("transcribe")) return;
    generating = { stretches: [stretchOf(c)], again };
  }
  async function generateMissing() {
    showSubMenu = false;
    if (!project || (await needSetup("transcribe"))) return;
    const todo = project.tracks.video.filter((c) => !c.audio?.mute && !subStatus(c).covered);
    if (!todo.length) return toast("Every clip already has subtitles");
    const st = cleanStretches(todo, (sid) => sourceById(sid)?.probe?.duration_sec ?? Infinity);
    generating = {
      again: false,
      stretches: st.map((x) => ({ source: x.source, sourceName: sourceName(x.source), start: x.start, end: x.end, clips: x.clips })),
    };
  }
  async function translateClips(cs: Clip[], to: "en" | "he", mode: "missing" | "stale" | "all") {
    showSubMenu = false;
    if (await needSetup("translate")) return;
    const st = cleanStretches(cs, (sid) => sourceById(sid)?.probe?.duration_sec ?? Infinity, 0);
    try {
      for (const x of st) {
        await api.submit({ type: "subtitles_translate", source_id: x.source, to, mode, start_sec: x.start, duration_sec: x.end - x.start });
      }
      toast(`Translating into ${to === "en" ? "English" : "Hebrew"}. Lines appear in the Subtitles tab as they are done.`);
    } catch (e) {
      reportError(e);
    }
  }
  const TRACKS: [Track, string][] = [["off", "Off"], ["mixed", "Mixed"], ["en", "English"], ["he", "Hebrew"]];
  const TRACK_TIPS: Record<Track, string> = {
    off: "No subtitles in the preview",
    mixed: "What is said, in the language it is said in (Hebrew and English mixed)",
    en: "English only: Hebrew lines show their English translation",
    he: "Hebrew only: English lines show their Hebrew translation",
  };

  // The clip under the playhead, for the Resulting video's "Clip N · time in clip" readout.
  const placedClips = $derived(project ? place(project.tracks.video) : []);
  const playingClip = $derived(clipAt(placedClips, Math.min(time, Math.max(0, total - 1e-3))));
  // Timeline zoom, like the video strips': −/+ halve and double it, "fit" shows it all.
  let timelineWidth = $state(0);
  const MAX_PPS = 400;
  const fitPps = $derived(Math.max(0.5, (timelineWidth - 70) / Math.max(1, total)));
  const ppsLabel = $derived.by(() => {
    const z = pps / fitPps;
    return z < 1.05 ? "whole" : `${z < 10 ? z.toFixed(1) : Math.round(z)}×`;
  });
  function setPps(v: number) {
    pps = Math.max(fitPps, Math.min(MAX_PPS, v));
  }
  // The timeline opens showing the whole edit.
  // …or as it was left: the zoom (and, in Timeline, the scroll) is remembered per project.
  let fitted = false;
  $effect(() => {
    if (fitted || !timelineWidth || !project || !total) return;
    fitted = true;
    const saved = loadPref<number | null>(`edit.timelineZoom.${name}`, null);
    pps = saved ? Math.max(fitPps, Math.min(MAX_PPS, saved)) : fitPps;
  });
  $effect(() => {
    const v = pps; // read first, so every zoom change saves
    if (fitted) savePref(`edit.timelineZoom.${name}`, v);
  });
  // The resulting video's picture: the project's, else what the export would choose.
  const outCanvas = $derived.by(() => {
    if (!project) return null;
    if (project.canvas) return project.canvas;
    const seconds = new Map<string, number>();
    for (const c of project.tracks.video) seconds.set(c.source, (seconds.get(c.source) ?? 0) + c.out_sec - c.in_sec);
    const ids = seconds.size ? [...seconds.keys()] : Object.keys(project.sources);
    return suggestCanvas(ids.map((id) => {
      const v = sourceById(id)?.probe?.video ?? project!.sources[id]?.probe?.video;
      return { display_width: v ? displayWidth(v) : 0, height: v?.height ?? 0, fps: v?.fps ?? 30, seconds: seconds.get(id) ?? 1 };
    }));
  });
  const outShape = $derived(outCanvas ? outCanvas.width / outCanvas.height : 16 / 9);
  // Zooming a clip (its frame on the original picture).
  let framing = $state<string | null>(null);
  const framingClip = $derived(framing ? project?.tracks.video.find((c) => c.id === framing) ?? null : null);
  const pictureOf = (id: string) => {
    const v = sourceById(id)?.probe?.video;
    return v ? displayWidth(v) / v.height : 16 / 9;
  };
  const fps = $derived(project?.canvas?.fps ?? 30);

  // ---------------------------------------------------------------- source viewer
  let srcId = $state("");
  $effect(() => {
    if (!srcId && sources.length) srcId = (project && Object.keys(project.sources)[0]) || sources[0].id;
  });
  const src = $derived(sourceById(srcId));
  let srcVideo = $state<HTMLVideoElement | null>(null);
  let srcTime = $state(0);
  let srcPlaying = $state(false);
  let markIn = $state<number | null>(null);
  // The viewer the transport keys (Space, J/K/L, arrows) drive: the last one clicked, or the
  // Source viewer after a click on a video's strip.
  let active = $state<"source" | "program">("program");
  let markOut = $state<number | null>(null);
  /** Show another video in the Source viewer (its marks belong to the old one). */
  function selectSource(id: string) {
    if (id === srcId) return;
    srcId = id;
    markIn = null;
    markOut = null;
    hearing = null;
  }

  // ---------------------------------------------------------------- hearing a cleaned version
  // The Source viewer plays the proxy (original sound). With a version chosen (a click on
  // its green bar, or the Sound choice), its sound plays instead inside its stretch, kept
  // on the picture's clock; outside the stretch the original is heard.
  let hearing = $state<string | null>(null);
  // The clean form, and a window's candidates, open over the editor.
  let cleaning = $state<Record<string, string> | null>(null);
  let cleaningClip = $state<string | null>(null); // the timeline clip the open clean form is for
  let cleaningBatch = $state<BatchRow[] | null>(null);
  let candidatesFor = $state<{ plan: string; win: string; clip?: string; jobs?: string[] } | null>(null);
  // Both players stop while one of these is open (it has its own player).
  $effect(() => {
    if (cleaning || cleaningBatch || candidatesFor || framing) {
      program?.pause();
      srcVideo?.pause();
    }
  });
  function cleanStretch() {
    if (!src || !markRange) return;
    const start = Math.floor(markRange.a);
    cleaning = { source: src.id, start: String(start), duration: String(Math.ceil(markRange.b - start)), kind: src.source_kind ?? "" };
  }
  let srcAudio = $state<HTMLAudioElement | null>(null);
  const hearingWindow = $derived.by(() => {
    const w = hearing ? findOutput(hearing)?.output.window : null;
    return w && w.actual_duration_sec != null ? { start: w.actual_start_sec, end: w.actual_start_sec + w.actual_duration_sec } : null;
  });
  function syncSourceSound() {
    const v = srcVideo, a = srcAudio, w = hearingWindow;
    if (!v) return;
    const inside = !!(a && w && v.currentTime >= w.start && v.currentTime < w.end);
    if (v.muted !== inside) v.muted = inside;
    if (!a || !w) return;
    const expected = v.currentTime - w.start;
    if (!inside || v.paused) {
      if (!a.paused) a.pause();
      if (inside && !a.seeking && Math.abs(a.currentTime - expected) > 0.05) a.currentTime = expected;
      return;
    }
    if (a.paused) {
      a.currentTime = expected;
      void a.play().catch(() => {});
      return;
    }
    if (a.seeking || a.readyState < 2) return;
    // The picture's clock moves in frame steps, so small differences are noise: leave
    // anything under 60 ms alone and correct the rest gently.
    const drift = a.currentTime - expected;
    if (Math.abs(drift) > 0.25) a.currentTime = expected;
    const rate = Math.abs(drift) < 0.06 ? 1 : Math.max(0.97, Math.min(1.03, 1 - drift / 2));
    if (a.playbackRate !== rate) a.playbackRate = rate;
  }
  /**
   * A click on a version's bar: hear that version, without moving the picture (playback
   * goes on where it is; outside the version's stretch the original is heard until the
   * playhead reaches it). A bar of another video shows that video, at the click.
   */
  function pickVersion(id: string, p: Piece, t: number) {
    if (p.kind === "tuned") {
      candidatesFor = { plan: p.plan.path, win: p.window.label };
      return;
    }
    if (id !== srcId) seekSource(id, t);
    active = "source";
    hearing = p.output.file;
  }
  // A seek asked for before the newly chosen video has loaded.
  let pendingSeek: number | null = null;
  function seekSource(id: string, t: number) {
    active = "source";
    const switching = id !== srcId;
    selectSource(id);
    srcTime = t;
    if (!switching && srcVideo && srcVideo.readyState >= 1) srcVideo.currentTime = t;
    else pendingSeek = t;
  }
  function sourceLoaded() {
    if (srcVideo && pendingSeek !== null) srcVideo.currentTime = pendingSeek;
    pendingSeek = null;
  }

  // ---------------------------------------------------------------- videos to pick clips from
  // In the order the user set (project.video_order), then any others.
  const videos = $derived.by(() => {
    const ids = Object.keys(project?.sources ?? {});
    const order = (project?.video_order ?? []).filter((id) => ids.includes(id));
    return [...order, ...ids.filter((id) => !order.includes(id))].map((id) => sourceById(id)).filter((s) => !!s);
  });
  function reorderVideos(ids: string[]) {
    commit((p) => {
      p.video_order = ids;
    });
  }
  const usedVideos = $derived(new Set(project?.tracks.video.map((c) => c.source) ?? []));
  let addingVideos = $state(false);
  function addVideos(ids: string[]) {
    addingVideos = false;
    commit((p) => {
      for (const id of ids) {
        const s = sourceById(id);
        if (s) p.sources[id] = { path: s.path, probe: s.probe };
      }
    });
    for (const id of ids) {
      const s = sourceById(id);
      if (s && !s.prep?.proxy) void api.submit({ type: "prep", source_id: id }).catch(() => {});
    }
    if (ids[0]) selectSource(ids[0]);
  }
  function removeVideo(id: string) {
    if (usedVideos.has(id)) return;
    commit((p) => {
      delete p.sources[id];
    });
  }

  // The marked stretch, and the sound it would be heard through: any cleaned output that
  // covers all of it (newest first, or the one chosen), else the original.
  const markRange = $derived.by(() => {
    if (!src) return null;
    const length = src.probe?.duration_sec ?? Infinity;
    const a = Math.max(0, markIn ?? srcTime);
    return { a, b: Math.min(length, markOut !== null && markOut > a + 0.1 ? markOut : a + 10) };
  });
  const markCovering = $derived(markRange ? coveringOutputs(src, markRange.a, markRange.b) : []);
  let soundChoice = $state<string | null | undefined>(undefined); // undefined: the newest cleaned one
  $effect(() => {
    void markIn;
    void markOut;
    void srcId;
    soundChoice = undefined;
  });
  // The version being heard is the default when it covers the clip; else the newest.
  const markSound = $derived(
    soundChoice === null
      ? null
      : (markCovering.find((o) => o.file === soundChoice) ?? markCovering.find((o) => o.file === hearing) ?? markCovering[0] ?? null),
  );
  // Tuned windows that hold the marked stretch and kept their candidates' sound.
  const markTuned = $derived(
    markRange && src
      ? src.plans.flatMap((pl) => pl.windows.filter((w) => w.candidate_files && w.start_sec <= markRange.a + 0.01 && w.end_sec >= markRange.b - 0.01).map((w) => ({ plan: pl, w })))
      : [],
  );

  const prepJob = $derived(
    store.jobs.find((j) => j.type === "prep" && j.meta?.source_id === srcId && (j.state === "running" || j.state === "queued")),
  );
  async function prepare(s: Source) {
    try {
      await api.submit({ type: "prep", source_id: s.id });
      toast(`Preparing ${s.name}: proxy, thumbnails and waveform`);
    } catch (e) {
      reportError(e);
    }
  }

  function srcToggle() {
    if (!srcVideo) return;
    if (srcVideo.paused) {
      program?.pause();
      void srcVideo.play();
    } else srcVideo.pause();
  }

  function addClip(atPlayhead = false) {
    const s = src;
    if (!s || !project || !markRange) return;
    const { a: inSec, b: outSec } = markRange;
    const out = markSound;
    const clip: Clip = { id: newId(), source: s.id, in_sec: inSec, out_sec: outSec, audio: audioFor(out) };
    // Soft sound into the new clip (the join rules keep a plain cut where nothing changes).
    if (project.tracks.video.length && !(atPlayhead && time <= 0.01)) clip.transition_in = { type: "soft_cut", sec: 1 };
    commit((p) => {
      p.sources[s.id] = { path: s.path, probe: s.probe };
      let clips = p.tracks.video;
      let at = clips.length;
      if (atPlayhead && clips.length) {
        clips = split(clips, time);
        const placed = place(clips);
        at = placed.filter((q) => q.start + q.length / 2 <= time).length;
      }
      p.tracks.video = [...clips.slice(0, at), clip, ...clips.slice(at)];
    });
    selected = { kind: "clip", id: clip.id };
    if (!out && !markCovering.length) toast("No cleaned output covers this range: the clip plays its original sound. Use “Clean this range” in the inspector.");
    markIn = null;
    markOut = null;
  }

  // ---------------------------------------------------------------- selection and inspector
  const selClip = $derived(selected?.kind === "clip" ? project?.tracks.video.find((c) => c.id === selected!.id) ?? null : null);
  const selIndex = $derived(selClip && project ? project.tracks.video.findIndex((c) => c.id === selClip.id) : -1);
  const joins = $derived(project ? joinsOf(project.tracks.video, joinInfo) : []);
  function joinText(j: Join): string {
    if (j.kind === "dip") return "Neither side has sound to spare past the cut: the sound fades out, then in.";
    if (j.kind === "cut") return "Same video and sound on both sides: nothing to soften.";
    if (j.kind !== "blend") return "";
    if (j.lead === undefined || Math.abs(j.lead - j.sec / 2) < 0.01) return "The two sounds overlap around the cut.";
    return j.lead > j.sec / 2
      ? "This clip's sound starts just before the cut and overlaps the previous one."
      : "The previous sound runs on just past the cut, under this one.";
  }
  const transitionValue = (c: Clip) => (c.transition_in?.sec ? `${c.transition_in.type}:${c.transition_in.sec}` : "cut");
  const selMusic = $derived(selected?.kind === "music" ? project?.tracks.music.find((m) => m.id === selected!.id) ?? null : null);
  const covering = $derived.by(() => {
    if (!selClip) return [];
    const s = sourceById(selClip.source);
    return (s?.outputs ?? []).filter((o) => {
      const w = o.window;
      return o.exists && o.kind === "video" && w && w.actual_duration_sec != null &&
        w.actual_start_sec <= selClip.in_sec + 0.01 && w.actual_start_sec + w.actual_duration_sec >= selClip.out_sec - 0.01;
    });
  });

  // ---------------------------------------------------------------- cleaning the timeline's clips
  // A clip still on its original sound, with no cleaned version that covers it.
  const uncleaned = (c: Clip) => !c.audio?.output && !c.audio?.mute && !coveringOutputs(sourceById(c.source), c.in_sec, c.out_sec).length;
  /** The clean jobs queued or running whose stretch holds the clip. */
  function cleaningJob(c: Clip) {
    return store.jobs.find((j) => {
      if (j.type !== "clean" || (j.state !== "queued" && j.state !== "running") || j.meta?.source_id !== c.source) return false;
      const start = Number(j.meta?.start_sec ?? 0);
      const length = Number(j.meta?.duration_sec ?? 0) || sourceLength(c.source) - start;
      return start <= c.in_sec + 0.01 && start + length >= c.out_sec - 0.01;
    }) ?? null;
  }
  const cleanState = $derived.by(() => {
    const clips = project?.tracks.video ?? [];
    const busy = clips.filter((c) => cleaningJob(c));
    const waiting = clips.filter((c) => uncleaned(c) && !busy.includes(c));
    return { total: clips.length, cleaned: clips.filter((c) => c.audio?.output).length, busy, waiting };
  });
  function cleanClips() {
    const rows = cleanStretches(cleanState.waiting, sourceLength);
    cleaningBatch = rows.map((r) => ({
      source: r.source, start: r.start, duration: r.end - r.start, clips: r.clips,
      note: `${r.clips.length} clip${r.clips.length > 1 ? "s" : ""}`,
    }));
  }
  /** The full clean form for one clip (padded for soft sound), switching it to the result. */
  function cleanClip(c: Clip) {
    const { start, end } = clipStretch(c, sourceLength(c.source));
    cleaningClip = c.id;
    cleaning = { source: c.source, start: String(start), duration: String(end - start), kind: sourceById(c.source)?.source_kind ?? "" };
  }
  // Tuned windows of the clip's video that overlap it and kept their candidates' sound.
  const clipTuned = $derived.by(() => {
    const c = selClip;
    const s = c ? sourceById(c.source) : null;
    if (!c || !s) return [];
    return s.plans.flatMap((pl) =>
      pl.windows.filter((w) => w.candidate_files && w.start_sec < c.out_sec && w.end_sec > c.in_sec).map((w) => ({ plan: pl, w })),
    );
  });
  function clipCandidates(c: Clip, plan: string, win: string) {
    await_(name, c.id, coveringOutputs(sourceById(c.source), c.in_sec, c.out_sec).map((o) => o.file));
    candidatesFor = { plan, win, clip: c.id, jobs: store.jobs.map((j) => j.id) };
  }
  function closeCandidates() {
    // Nothing rendered from them: the clip waits for nothing.
    const f = candidatesFor;
    if (f?.clip && !store.jobs.some((j) => !f.jobs!.includes(j.id))) forget(name, f.clip);
    candidatesFor = null;
  }
  // A clip waiting for a clean takes the new cleaned version as soon as it appears.
  $effect(() => {
    const wait = awaiting[name];
    if (!project || !wait || !store.library) return;
    const ready: [string, Output][] = [];
    for (const [id, known] of Object.entries(wait)) {
      const c = project.tracks.video.find((c) => c.id === id);
      if (!c) {
        forget(name, id);
        continue;
      }
      const fresh = coveringOutputs(sourceById(c.source), c.in_sec, c.out_sec).find((o) => !known.includes(o.file));
      if (fresh) ready.push([id, fresh]);
    }
    if (!ready.length) return;
    for (const [id] of ready) forget(name, id);
    untrack(() =>
      commit((p) => {
        for (const [id, o] of ready) {
          const c = p.tracks.video.find((c) => c.id === id);
          if (c && !c.audio?.mute) c.audio = audioFor(o, c.audio?.gain_db ?? 0);
        }
      }),
    );
    toast(ready.length === 1 ? `A clip now plays its cleaned sound (${versionName(ready[0][1])})` : `${ready.length} clips now play their cleaned sound`);
  });

  function updateClip(id: string, change: (c: Clip) => void) {
    commit((p) => {
      const c = p.tracks.video.find((c) => c.id === id);
      if (!c) return;
      change(c);
      // A trim can leave the cleaned audio short: fall back to another covering output, or the original.
      if (c.audio?.output) {
        const s = sourceById(c.source);
        const o = coveringOutput(s, c.in_sec, c.out_sec, c.audio.output);
        if (!o) {
          c.audio = c.audio.gain_db ? { gain_db: c.audio.gain_db } : null;
          toast("The cleaned audio does not cover the new range: this clip now plays its original sound.");
        } else if (o.file !== c.audio.output) {
          c.audio = audioFor(o, c.audio.gain_db ?? 0);
        }
      }
    });
  }
  function setAudio(id: string, choice: string) {
    updateClip(id, (c) => {
      const gain = c.audio?.gain_db ?? 0;
      if (choice === "__original") c.audio = gain ? { gain_db: gain } : null;
      else if (choice === "__mute") c.audio = { mute: true };
      else c.audio = audioFor(coveringOutputs(sourceById(c.source), c.in_sec, c.out_sec).find((o) => o.file === choice) ?? null, gain);
    });
    // a choice made by hand ends any wait for a clean to switch to
    forget(name, id);
  }

  // The sound menu a clip's badge on the timeline opens: every cleaned version covering it,
  // the original, muted, and the tuned candidates to render more from.
  let showStorage = $state(false);
  let soundMenu = $state<{ clip: string; x: number; y: number } | null>(null);
  const soundClip = $derived(soundMenu ? project?.tracks.video.find((c) => c.id === soundMenu!.clip) ?? null : null);
  const tunedFor = (c: Clip) => {
    const s = sourceById(c.source);
    return (s?.plans ?? []).flatMap((pl) =>
      pl.windows.filter((w) => w.candidate_files && w.start_sec < c.out_sec && w.end_sec > c.in_sec).map((w) => ({ plan: pl, w })));
  };
  // This editor's copy of the project follows a deleted version too (else saving would
  // point the clips back at the deleted file): those clips play their original sound.
  function followRename(old: string, now: { file: string; path: string }) {
    commit((p) => {
      for (const c of p.tracks.video) if (c.audio?.output === old) { c.audio.output = now.file; c.audio.file = now.path; }
    });
    if (hearing === old) hearing = now.file;
  }
  function dropVersion(file: string) {
    if (hearing === file) hearing = null;
    commit((p) => {
      for (const c of p.tracks.video) {
        if (c.audio?.output === file) {
          const gain = c.audio.gain_db ?? 0;
          c.audio = gain ? { gain_db: gain } : null;
        }
      }
    });
  }
  async function renameVersion(o: Output) {
    const old = o.file;
    const r = await renameShared(o);
    if (r) followRename(old, r);
  }

  function openSoundMenu(id: string, r: DOMRect) {
    soundMenu = { clip: id, x: Math.min(r.left, window.innerWidth - 380), y: r.bottom + 4 };
  }
  function updateMusic(id: string, change: (m: MusicClip) => void) {
    commit((p) => {
      const m = p.tracks.music.find((m) => m.id === id);
      if (m) change(m);
    });
  }

  function removeSelected() {
    if (!selected || !project) return;
    const sel = selected;
    // ask first: a click or ⌫ in the wrong place must not lose work
    if (sel.kind === "clip") {
      const i = project.tracks.video.findIndex((c) => c.id === sel.id);
      const c = project.tracks.video[i];
      if (!c) return;
      if (!confirm(`Delete clip ${i + 1} (${sourceName(c.source)}, ${formatClock(c.in_sec, false)}–${formatClock(c.out_sec, false)}) from the timeline?\n\n` +
          "The video itself, its cleaned versions and its subtitles stay. Undo (⌘Z) brings the clip back while this page is open.")) return;
    } else {
      const m = project.tracks.music.find((x) => x.id === sel.id);
      if (!m || !confirm(`Remove the music "${m.name ?? m.file.split("/").pop()}" from the timeline?\n\nUndo (⌘Z) brings it back while this page is open.`)) return;
    }
    commit((p) => {
      if (sel.kind === "clip") {
        p.tracks.video = p.tracks.video.filter((c) => c.id !== sel.id);
        if (p.tracks.video[0]?.transition_in) p.tracks.video[0].transition_in = null;
      } else p.tracks.music = p.tracks.music.filter((m) => m.id !== sel.id);
    });
    selected = null;
  }

  // ---------------------------------------------------------------- music
  let browsingMusic = $state(false);
  async function chooseMusic() {
    const picked = await nativePick("audio");
    if (picked === undefined) browsingMusic = true;
    else if (picked) await addMusic(picked);
  }
  async function addMusic(path: string) {
    browsingMusic = false;
    try {
      const stored = await api.addMusic(name, path);
      const length = stored.duration_sec ?? Math.max(10, total);
      const m: MusicClip = {
        id: newId("m"), file: stored.path, name: path.split("/").pop(), at_sec: 0, in_sec: 0,
        out_sec: Math.min(length, Math.max(total, 10)), file_duration_sec: stored.duration_sec ?? undefined,
        gain_db: -18, fade_in_sec: 2, fade_out_sec: 3,
      };
      commit((p) => p.tracks.music.push(m));
      selected = { kind: "music", id: m.id };
    } catch (e) {
      reportError(e);
    }
  }

  // ---------------------------------------------------------------- export
  // The settings, estimate and export live in edit/ExportDialog.svelte (also on Deliver).
  let exporting = $state(false);
  async function openExport() {
    await flush();
    exporting = true;
  }

  // ---------------------------------------------------------------- keyboard
  // Only fields you type into keep the keys. A slider, checkbox or button that still has
  // the focus after a click does not: otherwise ←/→ would move that slider by its own step
  // (the scrub bar's is about a frame) instead of the chosen one.
  const TEXT_INPUTS = new Set(["text", "number", "search", "email", "url", "tel", "password"]);
  function typing(ev: KeyboardEvent) {
    const el = ev.target as HTMLElement | null;
    if (!el) return false;
    if (el.isContentEditable || el.tagName === "TEXTAREA" || el.tagName === "SELECT") return true;
    return el.tagName === "INPUT" && TEXT_INPUTS.has((el as HTMLInputElement).type);
  }
  function clearMarks() {
    markIn = null;
    markOut = null;
  }
  function keydown(ev: KeyboardEvent) {
    if (typing(ev) || exporting || !program || cleaning || cleaningBatch || candidatesFor || framing) return;
    const key = ev.key.toLowerCase();
    const mod = ev.metaKey || ev.ctrlKey;
    let handled = true;
    if (mod && key === "z") ev.shiftKey ? redoStep() : undoStep();
    else if (mod) handled = false;
    else if (key === "escape" && soundMenu) soundMenu = null;
    // Esc or ⌥X: clear the marked In–Out stretch (⌥X is "clear in and out" in most editors)
    else if ((key === "escape" || (ev.altKey && ev.code === "KeyX")) && (markIn !== null || markOut !== null)) clearMarks();
    else if (key === "escape") handled = false;
    else if (active === "source" && ["arrowleft", "arrowright", "j", "k", "l", " "].includes(key)) sourceKey(key, ev.shiftKey);
    else if (key === " ") {
      srcVideo?.pause();
      program.toggle();
    } else if (key === "s") commit((p) => (p.tracks.video = split(p.tracks.video, time)));
    else if (key === "delete" || key === "backspace") removeSelected();
    else if (key === "arrowleft") program.seek(time - stepSize(ev.shiftKey, playing, fps));
    else if (key === "arrowright") program.seek(time + stepSize(ev.shiftKey, playing, fps));
    else if (key === "j") program.seek(time - 5);
    else if (key === "k") program.pause();
    else if (key === "l") void program.play();
    else if (key === "home") program.seek(0);
    else if (key === "end") program.seek(total);
    else if (key === "i") markIn = srcTime;
    else if (key === "o") markOut = srcTime;
    else if (key === ",") addClip(false);
    else if (key === "=" || key === "+") setPps(pps * 2);
    else if (key === "-") setPps(pps / 2);
    else handled = false;
    if (handled) ev.preventDefault();
  }

  // How far ←/→ move: "auto" is a second while playing and a frame while paused, "frame"
  // one frame, else seconds. ⇧ swaps to the other end: a frame for steps in seconds, a
  // second for frames.
  const STEPS = ["auto", "frame", "0.1", "0.25", "0.5", "1", "2", "5", "10"] as const;
  // The two viewers stay pinned at the top while the timeline and strips scroll under them;
  // the handle below them sets their height (remembered).
  const VIEWERS_MIN = 200;
  let viewersH = $state<number>(loadPref("edit.viewersHeight", Math.round(window.innerHeight * 0.45)));
  $effect(() => savePref("edit.viewersHeight", viewersH));
  const clampViewers = (h: number) => Math.round(Math.max(VIEWERS_MIN, Math.min(window.innerHeight - 160, h)));
  let resizing: { y: number; h: number } | null = null;
  function resizeDown(ev: PointerEvent) {
    ev.preventDefault();
    (ev.currentTarget as Element).setPointerCapture(ev.pointerId);
    resizing = { y: ev.clientY, h: viewersH };
  }
  function resizeMove(ev: PointerEvent) {
    if (resizing) viewersH = clampViewers(resizing.h + ev.clientY - resizing.y);
  }

  let arrowStep = $state<string>(loadPref("edit.arrowStep", "auto"));
  $effect(() => savePref("edit.arrowStep", arrowStep));
  function stepSize(shift: boolean, isPlaying: boolean, frameRate: number) {
    const frame = 1 / (frameRate || 30);
    const base = arrowStep === "auto" ? (isPlaying ? 1 : frame) : arrowStep === "frame" ? frame : Number(arrowStep) || 1;
    if (!shift) return base;
    return base === frame ? 1 : frame;
  }

  function sourceKey(key: string, shift: boolean) {
    const v = srcVideo;
    if (!v) {
      if (key === " ") toast(`${src?.name ?? "This video"} is not prepared yet, so it cannot play here.`);
      return;
    }
    const step = (d: number) => (v.currentTime = Math.max(0, Math.min(v.duration || Infinity, v.currentTime + d)));
    if (key === " ") srcToggle();
    else if (key === "k") v.pause();
    else if (key === "l") {
      program?.pause();
      void v.play();
    } else if (key === "j") step(-5);
    else step((key === "arrowleft" ? -1 : 1) * stepSize(shift, !v.paused, src?.probe?.video?.fps ?? 30));
    srcTime = v.currentTime;
  }

  function onTimelineChange(clips: Clip[], music: MusicClip[]) {
    commit((p) => {
      // re-check cleaned-audio coverage for trimmed clips
      p.tracks.video = clips.map((c) => {
        if (!c.audio?.output) return c;
        const o = coveringOutput(sourceById(c.source), c.in_sec, c.out_sec, c.audio.output);
        if (o) return o.file === c.audio.output ? c : { ...c, audio: audioFor(o, c.audio.gain_db ?? 0) };
        toast("The cleaned audio does not cover the new range: that clip now plays its original sound.");
        return { ...c, audio: c.audio.gain_db ? { gain_db: c.audio.gain_db } : null };
      });
      p.tracks.music = music;
    });
  }

  const num = (v: string) => (Number.isFinite(Number(v)) ? Number(v) : 0);
</script>

<svelte:window onkeydown={keydown} />

<div class="page col editor" style:--pinned-h="{viewersH + 10}px">
  {#if error}
    <div class="error-box">{error}</div>
  {:else if !project}
    <p class="dim">Loading…</p>
  {:else}
    <div class="row">
      <a href={href(["edit"])}>Projects</a><span class="faint">/</span>
      <h1>{ltr(project.name)}</h1>
      <span class="dim">{project.tracks.video.length} clips · {formatDuration(total)}</span>
      <span class="faint small">{saving === "saving" ? "saving…" : saving === "error" ? "not saved" : "saved"}</span>
      <span class="spacer"></span>
      {#key historyTick}
        <button class="small" disabled={!undo.length} onclick={undoStep} title="⌘Z">Undo</button>
        <button class="small" disabled={!redo.length} onclick={redoStep} title="⇧⌘Z">Redo</button>
      {/key}
      {#if cleanState.total}
        <span class="faint small" title="Clips playing a cleaned version of their sound">
          {cleanState.cleaned} of {cleanState.total} clips cleaned{cleanState.busy.length ? ` · ${cleanState.busy.length} cleaning` : ""}</span>
      {/if}
      <button disabled={!cleanState.waiting.length} onclick={cleanClips}
              title="Clean the sound of every clip still on its original sound, with the same settings; each clip switches to its cleaned sound when it is ready. Select a clip to clean it with its own settings.">
        Clean clips{cleanState.waiting.length ? ` (${cleanState.waiting.length})` : ""}…</button>
      <span class="menu-anchor">
        <button onclick={() => (showSubMenu = !showSubMenu)} title="Subtitles for the whole project">Subtitles…</button>
        {#if showSubMenu}
          <div class="menu-backdrop" role="presentation" onpointerdown={() => (showSubMenu = false)}></div>
          <div class="sub-menu card col" role="menu">
            <button class="item" role="menuitem" disabled={!withoutSubs} onclick={generateMissing}>Generate for clips without subtitles{withoutSubs ? ` (${withoutSubs})` : ""}…</button>
            <button class="item" role="menuitem" onclick={() => translateClips(project!.tracks.video, "en", "missing")}>Create English only for all</button>
            <button class="item" role="menuitem" onclick={() => translateClips(project!.tracks.video, "he", "missing")}>Create Hebrew only for all</button>
            <button class="item" role="menuitem" onclick={() => translateClips(project!.tracks.video, "en", "stale")}>Update out-of-date English</button>
            <button class="item" role="menuitem" onclick={() => translateClips(project!.tracks.video, "he", "stale")}>Update out-of-date Hebrew</button>
            <div class="sep"></div>
            <button class="item" role="menuitem" onclick={() => { showSubMenu = false; showNames = true; }}>Names…</button>
            <button class="item" role="menuitem" onclick={() => { showSubMenu = false; showSubSetup = true; }}>Set up…</button>
            <button class="item" role="menuitem" onclick={() => { showSubMenu = false; lowerTab = "subtitles"; }}>Edit subtitles</button>
          </div>
        {/if}
      </span>
      <button onclick={() => (showStorage = true)} title="Disk used by this project's videos: originals, editing copies, tuning audio, rendered versions">Storage…</button>
      <button onclick={chooseMusic}>♪ Add music…</button>
      <button class="primary" disabled={!project.tracks.video.length} onclick={openExport}>Export…</button>
    </div>

    <div class="pinned">
    <div class="viewers" style:height="{viewersH}px">
      <!-- the marked stretch: its sound, cleaning it, adding it (prepared or not: the
           export reads the original; only the preview here needs the editing copy) -->
      {#snippet markControls()}
      {#if markIn !== null || markOut !== null}
        <label class="row small sound" title="The sound this clip plays">Sound
          <select value={markSound?.file ?? "original"}
                  onchange={(e) => { const v = (e.target as HTMLSelectElement).value; soundChoice = v === "original" ? null : v; hearing = soundChoice ?? null; }}>
            {#each markCovering as o (o.file)}
              <option value={o.file}>cleaned · {versionName(o)} · {o.created?.slice(0, 10) ?? ""}</option>
            {/each}
            <option value="original">original sound</option>
          </select>
        </label>
        {#each markTuned as t (t.plan.path + t.w.label)}
          <button class="link small" onclick={() => (candidatesFor = { plan: t.plan.path, win: t.w.label })}
                  title="Hear the tuning candidates of {t.w.label}, and render another">candidates…</button>
        {/each}
        <button class="small" onclick={cleanStretch} disabled={!src?.exists}
                title="Clean the sound of the marked stretch (autotune with candidates, as in Enhance)">Clean this stretch…</button>
      {/if}
      <button class="small" onclick={() => addClip(true)} title="Insert at the playhead">Insert at playhead</button>
      <button class="small primary" onclick={() => addClip(false)} title=",">＋ Add to end</button>
      {/snippet}
      <!-- source viewer -->
      <div class="card col viewer" class:active={active === "source"} onpointerdowncapture={() => (active = "source")}>
        <div class="row">
          <h3>Source</h3>
          <select value={srcId} onchange={(e) => { selectSource((e.target as HTMLSelectElement).value); (e.target as HTMLSelectElement).blur(); }} class="grow">
            {#each sources as s (s.id)}<option value={s.id}>{ltr(s.name)}{s.prep?.proxy ? "" : " (not prepared)"}</option>{/each}
          </select>
        </div>
        {#if src && src.prep?.proxy}
          <div class="stage"><div class="screen" style:--ar={pictureOf(src.id)}>
            <!-- svelte-ignore a11y_media_has_caption -->
            <video bind:this={srcVideo} src="/media/proxy?source={src.id}" preload="auto" onloadedmetadata={sourceLoaded}
                   ontimeupdate={() => (srcTime = srcVideo?.currentTime ?? 0)}
                   onplay={() => (srcPlaying = true)} onpause={() => (srcPlaying = false)}></video>
            {#if hearing}<audio bind:this={srcAudio} src={media.outputAudio(hearing)} preload="auto"></audio>{/if}
          </div></div>
          <div class="scrub">
            <input type="range" min="0" max={src.probe?.duration_sec ?? 0} step="0.04" value={srcTime}
                   oninput={(e) => { if (srcVideo) srcVideo.currentTime = Number((e.target as HTMLInputElement).value); srcTime = srcVideo?.currentTime ?? 0; }} />
            <div class="cover">
              {#each src.outputs.filter((o) => o.exists && o.window?.actual_duration_sec) as o (o.file)}
                <div class="covered" title="cleaned: {o.file}"
                     style="left:{(o.window!.actual_start_sec / (src.probe?.duration_sec || 1)) * 100}%;width:{((o.window!.actual_duration_sec ?? 0) / (src.probe?.duration_sec || 1)) * 100}%"></div>
              {/each}
              {#if markIn !== null}
                <div class="mark" style="left:{(markIn / (src.probe?.duration_sec || 1)) * 100}%;width:{(((markOut ?? markIn) - markIn) / (src.probe?.duration_sec || 1)) * 100}%"></div>
              {/if}
            </div>
          </div>
          <div class="row wrap">
            <button class="small" onclick={srcToggle}>{srcPlaying ? "❚❚" : "▶"}</button>
            {#if hearing}
              {@const o = findOutput(hearing)?.output}
              <span class="hearing small" title={hearing}>hearing: cleaned · {o ? versionName(o) : "?"}
                <button class="link small" onclick={() => (hearing = null)} title="Hear the original sound">original</button></span>
            {/if}
            <span class="mono small">{formatClock(srcTime)}</span>
            <button class="small" onclick={() => (markIn = srcTime)} title="I">In {markIn !== null ? formatClock(markIn) : ""}</button>
            <button class="small" onclick={() => (markOut = srcTime)} title="O">Out {markOut !== null ? formatClock(markOut) : ""}</button>
            {#if markIn !== null || markOut !== null}
              <button class="small" onclick={clearMarks} title="Clear the marked stretch (Esc or ⌥X)">✕ Clear</button>
            {/if}
            <span class="spacer"></span>
            {@render markControls()}
          </div>
          <div class="faint tiny">Green under the slider = cleaned outputs. Clips there play their cleaned sound.</div>
        {:else if src}
          <div class="prep col">
            <p class="dim">{ltr(src.name)} is not prepared yet, so it cannot play here. You can already pick clips
              from it: drag along its strip in the Videos panel below, then add them. Preparing makes a light
              editing copy (made once, kept in <code>_gui/cache</code>) so it plays and scrubs instantly.</p>
            <div class="row wrap">
              <span class="mono small">{formatClock(srcTime)}</span>
              {#if markIn !== null}<span class="small">clip {formatClock(markIn)}–{formatClock(markOut ?? markIn)}</span>
                <button class="small" onclick={clearMarks} title="Clear the marked stretch (Esc or ⌥X)">✕ Clear</button>{/if}
              <span class="spacer"></span>
              {@render markControls()}
            </div>
            {#if prepJob}
              <div class="row">
                <span class="dim small">{prepJob.step?.title ?? "Waiting…"}
                  {#if prepJob.progress?.total_sec} · {Math.round((100 * prepJob.progress.processed_sec) / prepJob.progress.total_sec)}%{/if}</span>
                <button class="small danger" disabled={prepJob.cancel_requested}
                        onclick={() => api.cancel(prepJob.id).catch(reportError)}>{prepJob.cancel_requested ? "Cancelling…" : "Cancel"}</button>
              </div>
            {:else}
              <div><button class="primary" onclick={() => prepare(src)}>Prepare for editing</button></div>
            {/if}
          </div>
        {:else}
          <p class="dim">No videos in this library folder yet.</p>
        {/if}
      </div>

      <!-- program viewer -->
      <div class="card col viewer" class:active={active === "program"} onpointerdowncapture={() => (active = "program")}>
        <div class="row"><h3>Resulting video</h3><span class="spacer"></span>
          {#if playingClip}
            {@const pc = playingClip}
            <span class="small clip-time" title="The clip playing now: time into it / its length, and where that is in its video">
              <span class="dim">Clip {pc.index + 1}</span>
              <span class="mono">{formatClock(Math.max(0, time - pc.start))} / {formatClock(pc.length)}</span>
              <span class="faint">· {sourceName(pc.clip.source)} @ <span class="mono">{formatClock(pc.clip.in_sec + Math.max(0, time - pc.start))}</span></span>
            </span>
            <span class="faint">·</span>
          {/if}
          <span class="mono small" title="Time in the whole resulting video">{formatClock(time)} / {formatClock(total)}</span></div>
        <div class="row track-row">
          <span class="small dim">Subtitles shown</span>
          <span class="row seg" role="group" aria-label="Subtitles shown in the video">
            {#each TRACKS as [t, label] (t)}
              <button class:on={subTrack === t} aria-pressed={subTrack === t} title={TRACK_TIPS[t]} onclick={() => setTrack(t)}>{label}</button>
            {/each}
          </span>
        </div>
        <div class="stage"><div class="screen program" style:--ar={outShape}>
          <!-- svelte-ignore a11y_media_has_caption -->
          <video bind:this={videoA} preload="auto"></video>
          <!-- svelte-ignore a11y_media_has_caption -->
          <video bind:this={videoB} preload="auto"></video>
          {#if !project.tracks.video.length}<div class="overlay dim">Nothing on the timeline yet: drag along a video's strip below, then press ＋ Add to end.</div>{/if}
          {#if shownSub}
            <div class="subs" class:missing={shownSub.missing}>
              {#each shownSub.text.split("\n") as line, k (k)}<div dir="auto">{line}</div>{/each}
              {#if shownSub.missing}<span class="sub-tag">not translated</span>{:else if shownSub.stale}<span class="sub-tag">out of date</span>{/if}
            </div>
          {/if}
        </div></div>
        <div class="row">
          <button class="small" onclick={() => program?.seek(0)} title="Home">⏮</button>
          <button class="small" onclick={() => { srcVideo?.pause(); program?.toggle(); }} title="Space">{playing ? "❚❚ Pause" : "▶ Play"}</button>
          <button class="small" onclick={() => commit((p) => (p.tracks.video = split(p.tracks.video, time)))} title="S">✂ Split</button>
          <span class="spacer"></span>
          {#if project.tracks.music.length}
            {@const duck = project.tracks.music_duck ?? { enabled: true, threshold_db: -35, ratio: 6, attack_ms: 20, release_ms: 600 }}
            <label class="row small duck" title="Turns the background music down automatically whenever someone speaks, so voices stay clear">
              <input type="checkbox" checked={duck.enabled}
                     onchange={(e) => commit((p) => (p.tracks.music_duck = { ...duck, enabled: (e.target as HTMLInputElement).checked }))} />
              Lower the music while people talk</label>
            <span class="faint tiny">{duck.enabled
              ? "This happens in the exported video; this preview plays the music at one steady volume."
              : "The music stays at the same volume throughout, also in the exported video."}</span>
          {/if}
        </div>
      </div>
    </div>
    <div class="resize" role="separator" aria-orientation="horizontal" title="Drag to make the viewers bigger or smaller (double-click: default size)"
         onpointerdown={resizeDown} onpointermove={resizeMove} onpointerup={() => (resizing = null)}
         ondblclick={() => (viewersH = clampViewers(window.innerHeight * 0.45))}><span></span></div>
    </div>

    <div class="row">
      <span class="row small tzoom" title="Zoom the timeline; fit shows all of it">
        <span class="dim">Zoom</span>
        <button class="icon-btn" onclick={() => setPps(pps / 2)} disabled={pps <= fitPps * 1.001} title="Zoom out">−</button>
        <span class="mono faint">{ppsLabel}</span>
        <button class="icon-btn" onclick={() => setPps(pps * 2)} disabled={pps >= MAX_PPS} title="Zoom in">+</button>
        {#if pps > fitPps * 1.05}<button class="link small" onclick={() => setPps(fitPps)} title="Show the whole timeline">fit</button>{/if}
      </span>
      <label class="row small arrows" title="How far the left and right arrow keys move the outlined viewer. ⇧ with an arrow moves a frame instead (or a second, when the step is a frame).">
        <kbd>←</kbd><kbd>→</kbd> move
        <select bind:value={arrowStep} onchange={(e) => (e.target as HTMLSelectElement).blur()}>
          {#each STEPS as st (st)}
            <option value={st}>{st === "auto" ? "1 s playing, 1 frame paused" : st === "frame" ? "1 frame" : `${st} s`}</option>
          {/each}
        </select>
      </label>
      <span class="faint tiny">drag clips to reorder, edges to trim · <kbd>S</kbd> split ·
        <kbd>⌫</kbd> delete · <kbd>Space</kbd> play (the outlined viewer) · <kbd>J</kbd><kbd>K</kbd><kbd>L</kbd> · <kbd>I</kbd>/<kbd>O</kbd> (<kbd>Esc</kbd> clears)
        marks · <kbd>,</kbd> add · <kbd>⌘Z</kbd> undo</span>
    </div>
    <div class="timeline-wrap" onpointerdowncapture={() => (active = "program")}>
    <Timeline clips={project.tracks.video} music={project.tracks.music} {time} bind:pps bind:viewWidth={timelineWidth} memory="edit.timelineScroll.{name}" {selected} {thumbs}
              {sourceLength} {sourceName} onseek={(t) => program?.seek(t)} onselect={(s) => (selected = s)}
              onchange={onTimelineChange} onsound={openSoundMenu}
              subtitles={laneItems} ccOf={(c) => subStatus(c)}
              onsubtitle={(t) => { program?.seek(t); lowerTab = "subtitles"; selected = null; }} />
    </div>

    <!-- the selected clip (or music, or the project's settings): right under the timeline -->
    <div class="card inspector">
      {#if selClip}
        {@const c = selClip}
        <div class="row wrap">
          <h3>Clip {selIndex + 1}</h3><span class="dim small">{sourceName(c.source)}</span>
          <label class="field">In <input type="number" step="0.1" value={c.in_sec.toFixed(2)}
            onchange={(e) => updateClip(c.id, (x) => (x.in_sec = Math.max(0, Math.min(x.out_sec - 0.1, num((e.target as HTMLInputElement).value)))))} /></label>
          <label class="field">Out <input type="number" step="0.1" value={c.out_sec.toFixed(2)}
            onchange={(e) => updateClip(c.id, (x) => (x.out_sec = Math.min(sourceLength(x.source), Math.max(x.in_sec + 0.1, num((e.target as HTMLInputElement).value)))))} /></label>
          <span class="dim small">{formatClock(c.out_sec - c.in_sec)}</span>
          <label class="field">Sound
            <select value={c.audio?.mute ? "__mute" : c.audio?.output ?? "__original"} onchange={(e) => setAudio(c.id, (e.target as HTMLSelectElement).value)}>
              {#each covering as o (o.file)}<option value={o.file}>cleaned: {o.tag ?? o.label}</option>{/each}
              <option value="__original">original (not cleaned)</option>
              <option value="__mute">muted</option>
            </select></label>
          <label class="field">Gain <input type="number" step="0.5" value={c.audio?.gain_db ?? 0} disabled={c.audio?.mute}
            onchange={(e) => updateClip(c.id, (x) => (x.audio = { ...(x.audio ?? {}), gain_db: num((e.target as HTMLInputElement).value) }))} /> dB</label>
          <button class="small" onclick={() => (framing = c.id)} disabled={!sourceById(c.source)?.probe?.video}
                  title="Zoom in: choose the part of the picture this clip shows">
            {c.frame ? `Zoom ${Math.round(zoomOf(c.frame, outShape, pictureOf(c.source)) * 100)}%…` : "Zoom…"}</button>
          {#if selIndex > 0}
            {@const prev = project.tracks.video[selIndex - 1]}
            {@const matched = matchFrame(prev.frame, outShape, pictureOf(prev.source), pictureOf(c.source))}
            {#if !sameFrame(matched, c.frame)}
              <button class="small" onclick={() => updateClip(c.id, (x) => (x.frame = matched))}
                      title={prev.frame
                        ? `Give this clip the framing of clip ${selIndex}: zoom ${Math.round(zoomOf(prev.frame, outShape, pictureOf(prev.source)) * 100)}% on the same part of the picture, so the zoom carries on across the join`
                        : `Clip ${selIndex} shows the whole picture: show the whole picture here too`}>
                Match clip {selIndex}'s zoom</button>
            {/if}
          {/if}
          {#if zoomJoin(project.tracks.video[selIndex - 1], c)}
            <label class="field" title="This clip continues the previous one's shot with another framing. Glide: the picture moves smoothly from that framing to this one (eased), starting at the cut. Cut: it jumps.">Zoom change
              <select value={c.zoom_glide === false ? "cut" : String(c.zoom_glide?.sec ?? ZOOM_GLIDE_SEC)}
                onchange={(e) => { const v = (e.target as HTMLSelectElement).value;
                  updateClip(c.id, (x) => { if (v === "cut") x.zoom_glide = false; else if (Number(v) === ZOOM_GLIDE_SEC) delete x.zoom_glide; else x.zoom_glide = { sec: Number(v) }; }); }}>
                {#each ZOOM_GLIDE_CHOICES as sec (sec)}<option value={String(sec)}>glide {sec} s</option>{/each}
                <option value="cut">cut (no glide)</option>
              </select></label>
          {/if}
          <label class="field" title="Soft sound: the picture cuts, the sounds pass softly from one clip to the next. Crossfade: picture and sound dissolve, and the clips overlap.">Into this clip
            <select disabled={selIndex === 0} value={transitionValue(c)}
              onchange={(e) => updateClip(c.id, (x) => {
                const [type, sec] = (e.target as HTMLSelectElement).value.split(":");
                x.transition_in = sec ? { type: type as "crossfade" | "soft_cut", sec: num(sec) } : null;
              })}>
              <option value="cut">cut</option>
              {#each [0.5, 1, 2] as sec}<option value={`soft_cut:${sec}`}>soft sound {sec} s</option>{/each}
              {#each [0.5, 1, 2] as sec}<option value={`crossfade:${sec}`}>crossfade {sec} s</option>{/each}
            </select></label>
          {#if c.transition_in?.type === "soft_cut" && joins[selIndex]}
            <span class="faint small join-note">{joinText(joins[selIndex])}</span>
          {/if}
          <span class="spacer"></span>
          {#if cleaningJob(c)}
            <span class="faint small">cleaning…</span>
          {:else if awaiting[name]?.[c.id]}
            <span class="faint small">switches to the new version when it is ready</span>
          {/if}
          {#each clipTuned as t (t.plan.path + t.w.label)}
            <button class="link small" onclick={() => clipCandidates(c, t.plan.path, t.w.label)}
                    title="Hear the tuning candidates of {t.w.label} and render another; the clip switches to it">candidates {t.w.label}…</button>
          {/each}
          <button class="small" onclick={() => cleanClip(c)} disabled={!sourceById(c.source)?.exists}
                  title="Clean this clip's sound with all the settings (autotune with candidates); the clip switches to the result">
            {covering.length ? "Clean again…" : "Clean this clip…"}</button>
          <button class="small danger" onclick={removeSelected}>Delete</button>
        </div>
        {@const st = subStatus(c)}
        {@const job = subJobFor(c)}
        <div class="row wrap subs-row">
          <span class="dim small">Subtitles</span>
          <span class="small">{#if job}<span class="faint">{job.state === "running" ? (job.step?.title ?? "working") + "…" : "waiting…"}</span>
            {:else if !st.cues && !st.covered}<span class="faint">none yet</span>
            {:else if !st.cues}<span class="faint">no speech found</span>
            {:else}{st.cues} line{st.cues > 1 ? "s" : ""}{st.flagged ? ` · ${st.flagged} to check` : ""}
              {#each [["en", "English"], ["he", "Hebrew"]] as const as [l, name] (l)}
                {@const s = st[l]}
                · {name} {s.of === 0 || (s.done === s.of && !s.stale) ? "✓" : s.done === 0 ? "–" : `${s.done}/${s.of}`}{s.stale ? ` (${s.stale} out of date)` : ""}
              {/each}
            {/if}</span>
          {#if !st.covered}
            <button class="small" disabled={!!job || !sourceById(c.source)?.exists} onclick={() => generateFor(c, false)}>Generate subtitles…</button>
          {/if}
          {#if st.cues}
            {#each [["en", "English"], ["he", "Hebrew"]] as const as [l, name] (l)}
              {@const s = st[l]}
              {#if s.stale}
                <button class="small" disabled={!!job} onclick={() => translateClips([c], l, "stale")}>Update {name} ({s.stale})</button>
              {:else if s.done < s.of}
                <button class="small" disabled={!!job} onclick={() => translateClips([c], l, "missing")}>Create {name} only</button>
              {/if}
            {/each}
            <button class="link small" onclick={() => (lowerTab = "subtitles")}>Edit…</button>
            <button class="link small" disabled={!!job} onclick={() => generateFor(c, true)} title="Transcribe this clip again, e.g. with another model">Generate again…</button>
            <button class="link small danger" disabled={!!job} onclick={() => deleteSubtitles(c)} title="Delete this clip's subtitle lines and their translations (the clip stays)">Delete subtitles…</button>
          {/if}
        </div>
      {:else if selMusic}
        {@const m = selMusic}
        <div class="row wrap">
          <h3>♪ {m.name ?? "Music"}</h3>
          <label class="field">Starts at <input type="number" step="0.5" value={m.at_sec}
            onchange={(e) => updateMusic(m.id, (x) => (x.at_sec = Math.max(0, num((e.target as HTMLInputElement).value))))} /> s</label>
          <label class="field">From <input type="number" step="0.5" value={m.in_sec}
            onchange={(e) => updateMusic(m.id, (x) => (x.in_sec = Math.max(0, Math.min(x.out_sec - 0.5, num((e.target as HTMLInputElement).value)))))} /></label>
          <label class="field">To <input type="number" step="0.5" value={m.out_sec}
            onchange={(e) => updateMusic(m.id, (x) => (x.out_sec = Math.min(x.file_duration_sec ?? Infinity, Math.max(x.in_sec + 0.5, num((e.target as HTMLInputElement).value)))))} /></label>
          <label class="field">Level <input type="number" step="1" value={m.gain_db}
            onchange={(e) => updateMusic(m.id, (x) => (x.gain_db = num((e.target as HTMLInputElement).value)))} /> dB</label>
          <label class="field">Fade in <input type="number" step="0.5" min="0" value={m.fade_in_sec}
            onchange={(e) => updateMusic(m.id, (x) => (x.fade_in_sec = Math.max(0, num((e.target as HTMLInputElement).value))))} /></label>
          <label class="field">Fade out <input type="number" step="0.5" min="0" value={m.fade_out_sec}
            onchange={(e) => updateMusic(m.id, (x) => (x.fade_out_sec = Math.max(0, num((e.target as HTMLInputElement).value))))} /></label>
          <span class="spacer"></span>
          <button class="small danger" onclick={removeSelected}>Delete</button>
        </div>
      {:else}
        {@const duck = project.tracks.music_duck ?? { enabled: true, threshold_db: -35, ratio: 6, attack_ms: 20, release_ms: 600 }}
        <div class="row wrap">
          <h3>Project</h3>
          <span class="dim small">Select a clip or music to edit it.</span>
          <span class="spacer"></span>
          <label class="field"><input type="checkbox" checked={duck.enabled}
            onchange={(e) => commit((p) => (p.tracks.music_duck = { ...duck, enabled: (e.target as HTMLInputElement).checked }))} />
            Lower the music while people talk</label>
          <label class="field" title="How strongly the music is turned down (higher = quieter under speech)">by ratio <input type="number" step="1" min="1" value={duck.ratio} disabled={!duck.enabled}
            onchange={(e) => commit((p) => (p.tracks.music_duck = { ...duck, ratio: Math.max(1, num((e.target as HTMLInputElement).value)) }))} /></label>
          <label class="field">when speech is above <input type="number" step="1" value={duck.threshold_db} disabled={!duck.enabled}
            onchange={(e) => commit((p) => (p.tracks.music_duck = { ...duck, threshold_db: num((e.target as HTMLInputElement).value) }))} /> dB</label>
        </div>
      {/if}
    </div>

    <div class="row lower-tabs">
      <button class:on={lowerTab === "videos"} onclick={() => (lowerTab = "videos")}>Videos</button>
      <button class:on={lowerTab === "subtitles"} onclick={() => (lowerTab = "subtitles")}>Subtitles</button>
    </div>
    {#if lowerTab === "subtitles"}
      <div class="card">
        <SubtitlesPanel clips={project.tracks.video} {time} selected={selClip?.id ?? null} {sourceName}
                        onseek={(t) => program?.seek(t)} onplay={playRange} />
      </div>
    {:else}
    <!-- the videos to pick clips from, under the timeline they feed -->
    <VideoStrips {videos} current={srcId} time={srcTime}
                 marks={markIn !== null ? { start: markIn, end: markOut ?? markIn } : null} used={usedVideos}
                 onseek={(id, t) => seekSource(id, t)}
                 onrange={(id, a, b) => { seekSource(id, a); markIn = a; markOut = b; }}
                 {hearing} onpiece={pickVersion}
                 onadd={() => (addingVideos = true)} onremove={removeVideo} onreorder={reorderVideos}
                 onversion={(old, now) => (now ? followRename(old, now) : dropVersion(old))} />
    {/if}
    {#if addingVideos}
      <AddVideos available={sources.filter((s) => !project?.sources[s.id])} onadd={addVideos} onclose={() => (addingVideos = false)} />
    {/if}

    {#if soundMenu && soundClip}
      {@const c = soundClip}
      {@const versions = coveringOutputs(sourceById(c.source), c.in_sec, c.out_sec)}
      <div class="menu-backdrop" role="presentation" onpointerdown={() => (soundMenu = null)}></div>
      <div class="sound-menu card col" style="left:{soundMenu.x}px;top:{soundMenu.y}px" role="menu">
        <div class="faint small">Sound of clip {project.tracks.video.indexOf(c) + 1} · {sourceName(c.source)} {formatClock(c.in_sec, false)}–{formatClock(c.out_sec, false)}</div>
        {#each versions as o (o.file)}
          <button class="item" class:on={c.audio?.output === o.file} role="menuitem"
                  onclick={() => { setAudio(c.id, o.file); soundMenu = null; }}>
            <span class="check">{c.audio?.output === o.file ? "●" : ""}</span>
            <span class="col grow"><span>{versionName(o)}</span><span class="faint tiny">{versionSettings(o)}{o.created ? ` · made ${new Date(o.created).toLocaleString()}` : ""}</span></span>
            <span class="link tiny" role="button" tabindex="-1" title="Change this version's label (its file and every clip using it follow)"
                  onclick={(e) => { e.stopPropagation(); const v = o; soundMenu = null; void renameVersion(v); }} onkeydown={() => {}}>rename</span>
            <span class="link tiny danger" role="button" tabindex="-1" title="Delete this rendered version (moves to _trash); clips using it go back to their original sound"
                  onclick={(e) => { e.stopPropagation(); const v = o; soundMenu = null; void deleteVersion(v).then((gone) => { if (gone) dropVersion(v.file); }); }} onkeydown={() => {}}>delete</span>
          </button>
        {:else}
          <div class="faint small">No cleaned version covers this clip yet.</div>
        {/each}
        <button class="item" class:on={!c.audio?.file && !c.audio?.mute} role="menuitem" onclick={() => { setAudio(c.id, "__original"); soundMenu = null; }}>
          <span class="check">{!c.audio?.file && !c.audio?.mute ? "●" : ""}</span><span>Original sound (not cleaned)</span></button>
        <button class="item" class:on={!!c.audio?.mute} role="menuitem" onclick={() => { setAudio(c.id, "__mute"); soundMenu = null; }}>
          <span class="check">{c.audio?.mute ? "●" : ""}</span><span>Muted</span></button>
        <div class="sep"></div>
        {#each tunedFor(c) as t (t.plan.path + t.w.label)}
          <button class="item" role="menuitem" onclick={() => { const clip = c; soundMenu = null; clipCandidates(clip, t.plan.path, t.w.label); }}>
            <span class="check"></span><span>Candidates {formatClock(t.w.start_sec, false)}–{formatClock(t.w.end_sec, false)} ({t.w.candidates.length})… <span class="faint tiny">listen, render another, it switches here</span></span></button>
        {/each}
        <button class="item" role="menuitem" onclick={() => { const clip = c; soundMenu = null; cleanClip(clip); }}>
          <span class="check"></span><span>{versions.length ? "Clean again…" : "Clean this clip…"}</span></button>
      </div>
    {/if}

  {/if}
</div>

{#if cleaning}
  <Modal title={cleaningClip ? "Clean a clip" : "Clean a stretch"} onclose={() => { cleaning = null; cleaningClip = null; }}>
    <NewRun params={cleaning} onqueued={() => {
      const c = cleaningClip ? project?.tracks.video.find((c) => c.id === cleaningClip) : null;
      if (c) await_(name, c.id, coveringOutputs(sourceById(c.source), c.in_sec, c.out_sec).map((o) => o.file));
      cleaning = null;
      cleaningClip = null;
      toast(c ? "Cleaning started. The clip switches to the cleaned sound when it is ready."
              : "Cleaning started. It shows as a striped bar on the video's strip; when it is done the new version appears there, and its candidates under it.");
    }} />
  </Modal>
{/if}
{#if framingClip && sourceById(framingClip.source)}
  <Modal title="Zoom into clip {(project?.tracks.video.indexOf(framingClip) ?? 0) + 1}" onclose={() => (framing = null)}>
    <FrameDialog source={sourceById(framingClip.source)!} clip={framingClip} shape={outShape} height={outCanvas?.height ?? 720}
                 onclose={() => (framing = null)}
                 onapply={(f) => { const id = framingClip.id; framing = null; updateClip(id, (x) => { if (f) x.frame = f; else delete x.frame; }); }} />
  </Modal>
{/if}
{#if cleaningBatch}
  <Modal title="Clean the timeline's clips" onclose={() => (cleaningBatch = null)}>
    <NewRun params={{}} batch={cleaningBatch} onqueued={(rows) => {
      for (const r of rows) for (const id of r.clips ?? []) await_(name, id, []);
      cleaningBatch = null;
    }} />
  </Modal>
{/if}
{#if generating}
  <Modal title={generating.again ? "Generate subtitles again" : "Generate subtitles"} width="min(640px, 94vw)" onclose={() => (generating = null)}>
    <GenerateSubtitles stretches={generating.stretches} docs={subtitles.docs} again={generating.again}
                       onqueued={() => (generating = null)} onclose={() => (generating = null)} />
  </Modal>
{/if}
{#if showSubSetup}
  <Modal title="Set up subtitles" width="min(720px, 94vw)" onclose={() => (showSubSetup = false)}>
    <SubtitlesSetup onclose={() => (showSubSetup = false)} />
  </Modal>
{/if}
{#if showNames}
  <Modal title="Names" width="min(560px, 94vw)" onclose={() => (showNames = false)}>
    <NamesDialog onclose={() => (showNames = false)} />
  </Modal>
{/if}

{#if showStorage && project}
  <Modal title="Storage · {project.name}" onclose={() => (showStorage = false)}>
    <StorageView project={project.name} />
  </Modal>
{/if}

{#if candidatesFor}
  <Modal title="Candidates" onclose={closeCandidates}>
    <!-- a clip opened them: anything cleaned or rendered from here switches the clip (closeCandidates keeps the wait) -->
    <Candidates planPath={candidatesFor.plan} label={candidatesFor.win} onqueued={candidatesFor.clip ? () => {} : undefined}
                ondeleted={() => { if (candidatesFor?.clip) forget(name, candidatesFor.clip); candidatesFor = null; }} />
  </Modal>
{/if}

{#if browsingMusic}
  <FolderBrowser mode="file" title="Add music" onpick={addMusic} onclose={() => (browsingMusic = false)} />
{/if}

{#if exporting && project}
  <ExportDialog {name} {project} {commit} {flush} onclose={() => (exporting = false)} />
{/if}

<style>
  .editor { max-width: none; gap: 10px; }
  /* the viewers stay at the top of the scrolling page; the timeline and strips pass under them */
  .pinned { position: sticky; top: 0; z-index: 20; background: var(--bg); }
  .viewers { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .viewer { gap: 6px; padding: 10px 12px; min-height: 0; overflow: hidden; }
  .tzoom { gap: 4px; align-items: center; }
  .menu-anchor { position: relative; }
  .sub-menu { position: absolute; right: 0; top: calc(100% + 4px); z-index: 41; width: 300px; gap: 2px; padding: 6px; box-shadow: 0 8px 24px rgb(0 0 0 / 0.5); }
  .sub-menu .item { display: block; text-align: left; background: none; border: none; padding: 5px 6px; border-radius: 4px; cursor: pointer; color: var(--text); font-size: 13px; }
  .sub-menu .item:hover:not(:disabled) { background: var(--bg-hover); }
  .sub-menu .item:disabled { color: var(--text-faint); cursor: default; }
  .track-row { gap: 8px; align-items: center; }
  .seg button { border-radius: 0; padding: 3px 12px; font-size: 12.5px; }
  .seg button:first-child { border-radius: var(--radius) 0 0 var(--radius); }
  .seg button:last-child { border-radius: 0 var(--radius) var(--radius) 0; }
  .seg button.on { background: var(--accent); color: #fff; border-color: var(--accent); }
  .subs {
    position: absolute; left: 6%; right: 6%; bottom: 5%; z-index: 3; text-align: center; pointer-events: none;
    display: flex; flex-direction: column; align-items: center; gap: 0.1em; font-size: max(13px, 4.6cqh); line-height: 1.25;
  }
  .subs > div { background: rgb(0 0 0 / 0.62); color: #fff; padding: 0.05em 0.4em; border-radius: 3px; unicode-bidi: plaintext; text-wrap: balance; }
  .subs.missing > div { font-style: italic; color: #ddd; }
  .sub-tag { font-size: 0.5em; color: var(--warn); background: rgb(0 0 0 / 0.6); padding: 0 0.4em; border-radius: 3px; }
  .subs-row { gap: 8px; align-items: center; margin-top: 6px; padding-top: 6px; border-top: 1px dashed var(--border); }
  .lower-tabs { gap: 2px; margin-bottom: -4px; }
  .lower-tabs button { border-radius: var(--radius) var(--radius) 0 0; padding: 4px 14px; font-size: 13px; background: var(--bg-sunken); border-bottom: none; }
  .lower-tabs button.on { background: var(--bg-raised); color: var(--text); font-weight: 600; }
  .clip-time { display: inline-flex; gap: 6px; align-items: baseline; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .tzoom .mono { min-width: 40px; text-align: center; font-size: 11px; }
  .icon-btn { width: 22px; height: 20px; padding: 0; line-height: 1; font-size: 13px; }
  .menu-backdrop { position: fixed; inset: 0; z-index: 40; }
  .sound-menu { position: fixed; z-index: 41; width: 370px; gap: 2px; padding: 8px; box-shadow: 0 8px 24px rgb(0 0 0 / 0.5); }
  .sound-menu .item { display: flex; gap: 8px; align-items: flex-start; text-align: left; background: none; border: none; padding: 5px 6px; border-radius: 4px; cursor: pointer; color: var(--text); }
  .sound-menu .item:hover { background: var(--bg-hover); }
  .sound-menu .item.on { color: var(--b-color); }
  .sound-menu .check { width: 12px; flex: none; }
  .sound-menu .sep { height: 1px; background: var(--border); margin: 4px 0; }
  .tiny { font-size: 11px; }
  .viewer.active { box-shadow: inset 0 0 0 1px var(--accent); }
  /* the screen fills what height is left, keeping its shape */
  .stage { flex: 1; min-height: 60px; container-type: size; display: grid; place-items: center; }
  .screen { position: relative; background: #000; border-radius: var(--radius); overflow: hidden;
    --ar: 1.7778; aspect-ratio: var(--ar); width: min(100cqw, 100cqh * var(--ar)); }
  .resize { height: 10px; cursor: row-resize; display: grid; place-items: center; touch-action: none; }
  .resize span { width: 48px; height: 4px; border-radius: 2px; background: var(--border); }
  .resize:hover span { background: var(--accent); }
  .prep { overflow: auto; }
  .screen video { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
  .overlay { position: absolute; inset: 0; display: grid; place-items: center; }
  .scrub { position: relative; }
  .scrub input { width: 100%; }
  .cover { position: relative; height: 5px; background: var(--bg-sunken); border-radius: 2px; }
  .covered { position: absolute; top: 0; height: 100%; background: var(--b-color); opacity: 0.8; }
  .mark { position: absolute; top: -2px; height: 9px; min-width: 2px; background: var(--accent); opacity: 0.7; }
  .prep { flex: 1; min-height: 0; justify-content: center; }
  .grow { flex: 1; min-width: 0; }
  .wrap { flex-wrap: wrap; }
  .small { font-size: 12.5px; }
  .duck { gap: 5px; white-space: nowrap; }
  .hearing { color: var(--b-color); display: inline-flex; gap: 6px; align-items: center; }
  .tiny { font-size: 11.5px; }
  .inspector { padding: 10px 14px; }
  .field { display: inline-flex; align-items: center; gap: 5px; font-size: 12.5px; color: var(--text-dim); }
  .field input[type="number"] { width: 78px; }
</style>
