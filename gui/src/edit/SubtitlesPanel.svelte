<script lang="ts">
  // The Subtitles tab (docs/subtitles-plan.md §7.4): every line the timeline shows, with
  // its spoken (mixed) text and its English and Hebrew versions, editable in place. Times
  // shown are the resulting video's; edits are saved per line, in the video's own time.
  import { onDestroy, tick, untrack } from "svelte";
  import { loadPref, savePref } from "../lib/prefs";
  import { api, type SubtitleOp } from "../lib/api";
  import type { Clip } from "../lib/edit";
  import { place } from "../lib/edit";
  import { reportError, toast } from "../lib/state.svelte";
  import { FLAG_TEXT, isStale, timelineCues, type Cue, type TimelineCue } from "../lib/subtitles";
  import { formatClock } from "../lib/time";
  import { canUndo, edit, lookedOps, needsLook as needsLookAt, subs, undo } from "./subtitles.svelte";

  let { clips, time, selected, sourceName, onseek, onplay }: {
    clips: Clip[];
    time: number;
    selected: string | null; // the selected clip's id
    sourceName: (id: string) => string;
    onseek: (t: number) => void;
    onplay: (from: number, to: number) => void;
  } = $props();

  let scope = $state<"clip" | "all">(loadPref("edit.subtitlesScope", "all"));
  let needsLook = $state(loadPref("edit.subtitlesNeedsLook", false));
  $effect(() => savePref("edit.subtitlesScope", scope));
  $effect(() => savePref("edit.subtitlesNeedsLook", needsLook));

  const items = $derived(timelineCues(clips, subs.docs, "mixed"));
  const cueOf = (it: TimelineCue): Cue | undefined => subs.docs[it.source]?.cues.find((c) => c.id === it.cue);
  const placed = $derived(place(clips));
  const clipIndex = (id: string) => placed.findIndex((p) => p.clip.id === id);
  // "This clip" is the clip you selected, or, once the playhead moves into another clip
  // (playing on, or seeking), that one: whichever changed last.
  const playheadClip = $derived.by(() => {
    let id: string | null = null;
    for (const p of placed) if (time >= p.start + p.fadeIn / 2) id = p.clip.id; else break;
    return id;
  });
  let shownClip = $state<string | null>(untrack(() => selected ?? playheadClip));
  $effect(() => { if (selected) shownClip = selected; });
  let lastPlayhead: string | null = untrack(() => playheadClip);
  $effect(() => {
    const p = playheadClip;
    if (p && p !== lastPlayhead) shownClip = p;
    lastPlayhead = p;
  });
  const rows = $derived(items.filter((it) => {
    if (scope === "clip" && shownClip && it.clip !== shownClip) return false;
    const c = cueOf(it);
    return c && (!needsLook || needsLookAt(c));
  }));
  const playing = $derived(items.find((it) => time >= it.start && time < it.end)?.cue ?? null);

  // The row for what is playing: the line under the playhead, or, when the list hides it
  // (Needs a look, This clip), the last listed line before it.
  const rowKey = (it: TimelineCue) => it.cue + it.clip;
  const nowRow = $derived.by(() => {
    let best: TimelineCue | null = null;
    for (const it of rows) if (it.start <= time) best = it; else break;
    return best;
  });

  // Follow the playhead inside the list's own scroll area only: the page never moves
  // (scrollIntoView would scroll the page down to this tab while you watch), and not
  // for a few seconds after you type, so a box you are writing in stays put.
  let list = $state<HTMLDivElement | null>(null);
  let follow = $state(loadPref("edit.subtitlesFollow", true));
  let typedAt = 0;
  function scrollTo(it: TimelineCue, center: boolean) {
    const row = list?.querySelector<HTMLElement>(`[data-key="${CSS.escape(rowKey(it))}"]`);
    if (!list || !row) return;
    const top = row.offsetTop; // the list is the rows' offset parent
    const head = 28; // the sticky column header
    let to: number | null = null;
    if (center) to = top - (list.clientHeight - row.offsetHeight) / 2;
    else if (top < list.scrollTop + head) to = top - head;
    else if (top + row.offsetHeight > list.scrollTop + list.clientHeight) to = top - head;
    if (to !== null) list.scrollTo({ top: Math.max(0, to), behavior: "smooth" });
  }
  $effect(() => {
    const it = nowRow;
    if (!follow || !it || !list) return;
    if (performance.now() - typedAt < 3000) return;
    untrack(() => scrollTo(it, false));
  });
  function jumpToNow() {
    if (nowRow) scrollTo(nowRow, true);
    else toast("Nothing listed at or before the playhead");
  }
  function setFollow(v: boolean) {
    follow = v;
    savePref("edit.subtitlesFollow", v);
    if (v) jumpToNow();
  }

  // ---------------------------------------------------------------- saving text
  // Typing is saved 0.8 s after the last key; what is still waiting is saved when the
  // box loses focus, when the tab closes (switching to Videos, leaving the editor) and
  // when the page is left.
  const timers = new Map<string, { timer: ReturnType<typeof setTimeout>; save: () => void }>();
  function flush() {
    for (const { timer, save } of [...timers.values()]) { clearTimeout(timer); save(); }
  }
  onDestroy(flush);
  function queueText(it: TimelineCue, lang: "mixed" | "en" | "he", text: string, now = false) {
    const key = `${it.cue}:${lang}`;
    clearTimeout(timers.get(key)?.timer);
    timers.delete(key);
    const save = () => {
      timers.delete(key);
      const c = cueOf(it);
      if (!c) return;
      const cur = lang === "mixed" ? c.text : c.tr?.[lang]?.text ?? "";
      if (text.trim() === cur.trim()) return;
      if (lang === "mixed" && !text.trim()) {
        toast("A subtitle cannot be empty: use Delete to remove it", "error");
        return;
      }
      void edit(it.source, [lang === "mixed" ? { op: "text", id: it.cue, text } :
        text.trim() ? { op: "text", id: it.cue, lang, text } : { op: "clear_tr", id: it.cue, lang }], "a text edit");
    };
    if (now) save();
    else timers.set(key, { timer: setTimeout(save, 800), save });
  }

  // ---------------------------------------------------------------- line actions
  async function split(it: TimelineCue, ta: HTMLTextAreaElement | null) {
    const at = ta ? ta.selectionStart : undefined;
    if (ta) queueText(it, "mixed", ta.value, true);
    await tick();
    await edit(it.source, [{ op: "split", id: it.cue, ...(at ? { at_char: at } : {}) }], "a split");
  }
  const merge = (it: TimelineCue) => edit(it.source, [{ op: "merge", id: it.cue }], "a merge");
  const remove = (it: TimelineCue) => edit(it.source, [{ op: "delete", id: it.cue }], "a delete");
  function lookedAt(it: TimelineCue) {
    const c = cueOf(it);
    if (c) void edit(it.source, lookedOps(c), "looked at");
  }
  // every line the list shows that still needs a look, per video (one undo step each)
  const toLook = $derived(rows.filter((it) => { const c = cueOf(it); return c && needsLookAt(c); }));
  async function lookedAtAll() {
    flush();
    const bySource = new Map<string, SubtitleOp[]>();
    for (const it of toLook) {
      const c = cueOf(it);
      if (c) bySource.set(it.source, [...(bySource.get(it.source) ?? []), ...lookedOps(c)]);
    }
    for (const [source, ops] of bySource) await edit(source, ops, `looked at ${ops.length} line${ops.length === 1 ? "" : "s"}`);
  }
  function addAfter(it: TimelineCue) {
    const c = cueOf(it);
    const doc = subs.docs[it.source];
    if (!c || !doc) return;
    const next = doc.cues.find((x) => x.start > c.start && x.id !== c.id);
    const start = c.end + 0.1;
    const end = Math.min(start + 2, next ? next.start - 0.05 : start + 2);
    if (end - start < 0.3) return toast("No room after this line; shorten it or the next one first", "error");
    void edit(it.source, [{ op: "add", start, end, text: "…" }], "an added line");
  }
  function nudge(it: TimelineCue, which: "start" | "end", by: number) {
    const c = cueOf(it);
    if (!c) return;
    const ops: SubtitleOp[] = [{ op: "time", id: c.id, start: which === "start" ? c.start + by : c.start, end: which === "end" ? c.end + by : c.end }];
    void edit(it.source, ops, "a timing change");
  }
  async function update(it: TimelineCue, lang: "en" | "he") {
    const c = cueOf(it);
    if (!c) return;
    try {
      await api.submit({ type: "subtitles_translate", source_id: it.source, to: lang, start_sec: c.start, duration_sec: Math.max(0.1, c.end - c.start), mode: "stale" });
      toast(`Updating the ${lang === "en" ? "English" : "Hebrew"} line`);
    } catch (e) {
      reportError(e);
    }
  }

  // col: the column the box is in (for ⏎ to the next box); saveAs: the text it edits (a
  // line already in English shows its spoken text in the English column, and edits that)
  function keys(ev: KeyboardEvent, it: TimelineCue, col: "mixed" | "en" | "he", i: number, saveAs = col) {
    const ta = ev.currentTarget as HTMLTextAreaElement;
    const mod = ev.metaKey || ev.ctrlKey;
    if (mod && !ev.shiftKey && ev.key === "Enter" && saveAs === "mixed") { ev.preventDefault(); void split(it, ta); }
    else if (mod && ev.key.toLowerCase() === "j") { ev.preventDefault(); queueText(it, saveAs, ta.value, true); void merge(it); }
    else if (mod && ev.key.toLowerCase() === "p") { ev.preventDefault(); onplay(it.start, it.end); }
    else if (mod && ev.key.toLowerCase() === "k") { ev.preventDefault(); queueText(it, saveAs, ta.value, true); lookedAt(it); }
    else if (mod && ev.shiftKey && ev.key === "Enter") { ev.preventDefault(); queueText(it, saveAs, ta.value, true); addAfter(it); }
    else if (ev.key === "Enter" && !ev.shiftKey && !mod) {
      ev.preventDefault();
      queueText(it, saveAs, ta.value, true);
      // the next line's box in this column
      const boxes = list?.querySelectorAll<HTMLTextAreaElement>(`textarea[data-lang="${col}"]`) ?? [];
      [...boxes].find((b) => Number(b.dataset.row) > i)?.focus();
    } else if (ev.key === "Escape") ta.blur();
  }
  function panelKeys(ev: KeyboardEvent) {
    const t = ev.target as HTMLElement;
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "z" && t.tagName !== "TEXTAREA") {
      ev.preventDefault();
      ev.stopPropagation();
      void undo();
    }
  }
  const flagText = (f: string) => FLAG_TEXT[f] ?? f;
</script>

<svelte:window onpagehide={flush} />

<div class="panel col" role="presentation" onkeydown={panelKeys} oninput={() => (typedAt = performance.now())}>
  <div class="row head">
    <span class="row seg">
      <button class:on={scope === "all"} onclick={() => (scope = "all")}>Whole video</button>
      <button class:on={scope === "clip"} onclick={() => (scope = "clip")} disabled={!shownClip}
              title="Only one clip's lines: the selected clip, or the one playing (it moves on with the playhead)">{scope === "clip" && shownClip ? `Clip ${clipIndex(shownClip) + 1}` : "This clip"}</button>
    </span>
    <label class="row small"><input type="checkbox" bind:checked={needsLook} /> Needs a look</label>
    <span class="faint small">{rows.length} line{rows.length === 1 ? "" : "s"}</span>
    {#if needsLook && toLook.length}
      <button class="small" onclick={lookedAtAll}
              title="Clear the marks on every line listed here and accept their translations as they are (Undo puts them back)">Mark all {toLook.length} as looked at</button>
    {/if}
    <button class="small" onclick={jumpToNow} disabled={!nowRow} title="Scroll to the line playing now">Jump to now playing</button>
    <label class="row small" title="Keep the line playing now in view while the video plays"><input type="checkbox" checked={follow} onchange={(e) => setFollow((e.target as HTMLInputElement).checked)} /> Follow playback</label>
    <span class="spacer"></span>
    <span class="faint tiny">⏎ next line · ⇧⏎ line break · ⌘⏎ split at the cursor · ⌘⇧⏎ add a line after · ⌘J merge with the next · ⌘P play it · ⌘K looked at</span>
    <button class="small" onclick={() => undo()} disabled={!canUndo()} title="Undo the last subtitle edit (⌘Z outside a text box)">Undo</button>
  </div>
  {#if !items.length}
    <p class="faint pad">No subtitles on the timeline yet. Select a clip and choose <b>Generate subtitles…</b> under the
      timeline, or <b>Subtitles… → Generate for clips without subtitles</b> at the top.</p>
  {:else}
    <div class="list" bind:this={list}>
      <div class="grid hdr"><span>Time</span><span>Mixed</span><span>English</span><span>Hebrew</span><span></span></div>
      {#each rows as it, i (it.cue + it.clip)}
        {@const c = cueOf(it)}
        {#if c}
          {#if i === 0 || rows[i - 1].clip !== it.clip}
            <div class="clipsep">Clip {clipIndex(it.clip) + 1} · {sourceName(it.source)}</div>
          {/if}
          <div class="grid row-cue" class:now={playing === it.cue} data-cue={it.cue} data-key={rowKey(it)}>
            <span class="time mono">
              <button class="link small" onclick={() => onseek(it.start)} title="Go there">{formatClock(it.start)}</button>
              <span class="faint">{(it.end - it.start).toFixed(1)} s</span>
              <span class="nudges">
                <button class="tiny-btn" title="Start 0.1 s earlier" onclick={() => nudge(it, "start", -0.1)}>⇤</button>
                <button class="tiny-btn" title="Start 0.1 s later" onclick={() => nudge(it, "start", 0.1)}>⇥</button>
                <button class="tiny-btn" title="End 0.1 s earlier" onclick={() => nudge(it, "end", -0.1)}>⇤</button>
                <button class="tiny-btn" title="End 0.1 s later" onclick={() => nudge(it, "end", 0.1)}>⇥</button>
              </span>
            </span>
            <span class="cell">
              <textarea dir="auto" data-lang="mixed" data-row={i} rows="2" value={c.text}
                        oninput={(e) => queueText(it, "mixed", (e.target as HTMLTextAreaElement).value)}
                        onblur={(e) => queueText(it, "mixed", (e.target as HTMLTextAreaElement).value, true)}
                        onkeydown={(e) => keys(e, it, "mixed", i)}></textarea>
              {#if c.flags?.length}<span class="flags">{#each c.flags as f (f)}<span class="flag" title={flagText(f)}>{f.replace("_", " ")}</span>{/each}</span>{/if}
            </span>
            {#each ["en", "he"] as const as lang (lang)}
              {@const tr = c.tr?.[lang]}
              {@const same = c.lang === lang}
              <span class="cell" class:stale={isStale(c, lang)}>
                {#if same}
                  <textarea dir="auto" data-lang={lang} data-row={i} rows="2" value={c.text} class="same-box"
                            title="This line is already in {lang === 'en' ? 'English' : 'Hebrew'}: the {lang === 'en' ? 'English' : 'Hebrew'} track shows the spoken line, so editing it here edits the spoken line"
                            oninput={(e) => queueText(it, "mixed", (e.target as HTMLTextAreaElement).value)}
                            onblur={(e) => queueText(it, "mixed", (e.target as HTMLTextAreaElement).value, true)}
                            onkeydown={(e) => keys(e, it, lang, i, "mixed")}></textarea>
                  <span class="faint tiny">same as spoken</span>
                {:else}
                  <textarea dir="auto" data-lang={lang} data-row={i} rows="2" value={tr?.text ?? ""} placeholder="not translated"
                            oninput={(e) => queueText(it, lang, (e.target as HTMLTextAreaElement).value)}
                            onblur={(e) => queueText(it, lang, (e.target as HTMLTextAreaElement).value, true)}
                            onkeydown={(e) => keys(e, it, lang, i)}></textarea>
                  {#if isStale(c, lang)}
                    <span class="flags"><span class="flag stale-tag" title="The spoken line changed after this was translated">out of date</span>
                      <button class="link tiny" onclick={() => update(it, lang)}>Update</button></span>
                  {:else if tr?.flags?.length}
                    <span class="flags">{#each tr.flags as f (f)}<span class="flag" title={flagText(f)}>{f.replace("_", " ")}</span>{/each}</span>
                  {/if}
                {/if}
              </span>
            {/each}
            <span class="acts">
              <button class="tiny-btn" title="Play this line (⌘P)" onclick={() => onplay(it.start, it.end)}>▶</button>
              {#if needsLookAt(c)}
                <button class="tiny-btn ok" title="Looked at: clear this line's marks and accept its translations as they are (⌘K)" onclick={() => lookedAt(it)}>✓</button>
              {/if}
              <button class="tiny-btn" title="Split at the cursor (⌘⏎ in the text)" onclick={() => split(it, list?.querySelector(`[data-cue="${it.cue}"] textarea[data-lang="mixed"]`) ?? null)}>✂</button>
              <button class="tiny-btn" title="Merge with the next line (⌘J)" onclick={() => merge(it)}>⤓</button>
              <button class="tiny-btn" title="Add a line after this one (⌘⇧⏎)" onclick={() => addAfter(it)}>＋</button>
              <button class="tiny-btn danger" title="Delete this line" onclick={() => remove(it)}>✕</button>
            </span>
          </div>
        {/if}
      {/each}
    </div>
  {/if}
</div>

<style>
  .panel { gap: 6px; padding: 8px 10px; }
  .head { gap: 10px; align-items: center; flex-wrap: wrap; }
  .seg button { border-radius: 0; padding: 3px 10px; font-size: 12px; }
  .seg button:first-child { border-radius: var(--radius) 0 0 var(--radius); }
  .seg button:last-child { border-radius: 0 var(--radius) var(--radius) 0; }
  .seg button.on { background: var(--accent); color: #fff; border-color: var(--accent); }
  .list { display: flex; flex-direction: column; max-height: 55vh; overflow-y: auto; position: relative; }
  .grid { display: grid; grid-template-columns: 118px minmax(0, 1.3fr) minmax(0, 1fr) minmax(0, 1fr) 118px; gap: 8px; align-items: start; }
  .hdr { font-size: 11.5px; color: var(--text-dim); padding: 4px 0; border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--bg-raised); z-index: 2; }
  .clipsep { font-size: 11.5px; color: var(--text-faint); padding: 8px 0 2px; border-bottom: 1px dashed var(--border); }
  .row-cue { padding: 5px 0; border-bottom: 1px solid var(--gap, var(--border)); }
  .row-cue.now { background: color-mix(in srgb, var(--accent) 10%, transparent); box-shadow: inset 3px 0 0 var(--accent); }
  .time { display: flex; flex-direction: column; gap: 2px; font-size: 12px; padding-left: 6px; }
  .nudges { display: flex; gap: 2px; }
  .cell { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
  .cell.stale textarea { border-color: var(--warn); }
  textarea { width: 100%; resize: vertical; font-size: 13.5px; line-height: 1.35; padding: 4px 6px; unicode-bidi: plaintext; min-height: 2.9em; }
  .same-box { color: var(--text-dim); }
  .flags { display: flex; gap: 4px; flex-wrap: wrap; align-items: center; }
  .flag { font-size: 10.5px; padding: 0 5px; border-radius: 3px; background: var(--bg-sunken); color: var(--warn); cursor: help; }
  .stale-tag { color: var(--warn); }
  .acts { display: flex; gap: 2px; flex-wrap: wrap; justify-content: flex-end; }
  .tiny-btn { padding: 1px 5px; font-size: 11.5px; line-height: 1.4; }
  .tiny-btn.danger { color: var(--danger, #e5534b); }
  .tiny-btn.ok { color: var(--ok, #57ab5a); }
  .small { font-size: 12px; }
  .tiny { font-size: 11px; }
  .pad { padding: 10px 2px; }
</style>
