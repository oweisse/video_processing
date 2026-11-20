<script lang="ts">
  // The Subtitles tab (docs/subtitles-plan.md §7.4): every line the timeline shows, with
  // its spoken (mixed) text and its English and Hebrew versions, editable in place. Times
  // shown are the resulting video's; edits are saved per line, in the video's own time.
  import { tick } from "svelte";
  import { api, type SubtitleOp } from "../lib/api";
  import type { Clip } from "../lib/edit";
  import { place } from "../lib/edit";
  import { reportError, toast } from "../lib/state.svelte";
  import { FLAG_TEXT, isStale, timelineCues, type Cue, type TimelineCue } from "../lib/subtitles";
  import { formatClock } from "../lib/time";
  import { canUndo, edit, subs, undo } from "./subtitles.svelte";

  let { clips, time, selected, sourceName, onseek, onplay }: {
    clips: Clip[];
    time: number;
    selected: string | null; // the selected clip's id
    sourceName: (id: string) => string;
    onseek: (t: number) => void;
    onplay: (from: number, to: number) => void;
  } = $props();

  let scope = $state<"clip" | "all">("all");
  let needsLook = $state(false);

  const items = $derived(timelineCues(clips, subs.docs, "mixed"));
  const cueOf = (it: TimelineCue): Cue | undefined => subs.docs[it.source]?.cues.find((c) => c.id === it.cue);
  const placed = $derived(place(clips));
  const clipIndex = (id: string) => placed.findIndex((p) => p.clip.id === id);
  const lookFlags = (c: Cue) => !!c.flags?.length || (["en", "he"] as const).some((l) => isStale(c, l) || c.tr?.[l]?.flags?.length);
  const rows = $derived(items.filter((it) => {
    if (scope === "clip" && selected && it.clip !== selected) return false;
    const c = cueOf(it);
    return c && (!needsLook || lookFlags(c));
  }));
  const playing = $derived(items.find((it) => time >= it.start && time < it.end)?.cue ?? null);

  // Follow the playhead inside the list's own scroll area only: the page never moves
  // (scrollIntoView would scroll the page down to this tab while you watch), and not
  // while you are typing in the list.
  let list = $state<HTMLDivElement | null>(null);
  $effect(() => {
    const id = playing;
    if (!id || !list) return;
    if (list.contains(document.activeElement) && document.activeElement?.tagName === "TEXTAREA") return;
    const row = list.querySelector<HTMLElement>(`[data-cue="${id}"]`);
    if (!row) return;
    const top = row.offsetTop - list.offsetTop;
    const head = 28; // the sticky column header
    if (top < list.scrollTop + head) list.scrollTop = Math.max(0, top - head);
    else if (top + row.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = top + row.offsetHeight - list.clientHeight;
  });

  // ---------------------------------------------------------------- saving text
  const timers = new Map<string, ReturnType<typeof setTimeout>>();
  function queueText(it: TimelineCue, lang: "mixed" | "en" | "he", text: string, now = false) {
    const key = `${it.cue}:${lang}`;
    clearTimeout(timers.get(key));
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
    else timers.set(key, setTimeout(save, 800));
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

  function keys(ev: KeyboardEvent, it: TimelineCue, lang: "mixed" | "en" | "he", i: number) {
    const ta = ev.currentTarget as HTMLTextAreaElement;
    const mod = ev.metaKey || ev.ctrlKey;
    if (mod && ev.key === "Enter" && lang === "mixed") { ev.preventDefault(); void split(it, ta); }
    else if (mod && ev.key.toLowerCase() === "j") { ev.preventDefault(); queueText(it, lang, ta.value, true); void merge(it); }
    else if (mod && ev.key.toLowerCase() === "p") { ev.preventDefault(); onplay(it.start, it.end); }
    else if (ev.key === "Enter" && !ev.shiftKey && !mod) {
      ev.preventDefault();
      queueText(it, lang, ta.value, true);
      const nextRow = list?.querySelectorAll<HTMLTextAreaElement>(`textarea[data-lang="${lang}"]`)[i + 1];
      nextRow?.focus();
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

<div class="panel col" role="presentation" onkeydown={panelKeys}>
  <div class="row head">
    <span class="row seg">
      <button class:on={scope === "all"} onclick={() => (scope = "all")}>Whole video</button>
      <button class:on={scope === "clip"} onclick={() => (scope = "clip")} disabled={!selected}
              title={selected ? "Only the selected clip's lines" : "Select a clip on the timeline first"}>This clip</button>
    </span>
    <label class="row small"><input type="checkbox" bind:checked={needsLook} /> Needs a look</label>
    <span class="faint small">{rows.length} line{rows.length === 1 ? "" : "s"}</span>
    <span class="spacer"></span>
    <span class="faint tiny">⏎ next line · ⇧⏎ line break · ⌘⏎ split at the cursor · ⌘J merge with the next · ⌘P play it</span>
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
          <div class="grid row-cue" class:now={playing === it.cue} data-cue={it.cue}>
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
              <textarea dir="auto" data-lang="mixed" rows="2" value={c.text}
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
                  <div class="same faint small" title="This line is already in {lang === 'en' ? 'English' : 'Hebrew'}: the {lang === 'en' ? 'English' : 'Hebrew'} track shows it as it is">same as spoken</div>
                {:else}
                  <textarea dir="auto" data-lang={lang} rows="2" value={tr?.text ?? ""} placeholder="not translated"
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
              <button class="tiny-btn" title="Split at the cursor (⌘⏎ in the text)" onclick={() => split(it, list?.querySelector(`[data-cue="${it.cue}"] textarea[data-lang="mixed"]`) ?? null)}>✂</button>
              <button class="tiny-btn" title="Merge with the next line (⌘J)" onclick={() => merge(it)}>⤓</button>
              <button class="tiny-btn" title="Add a line after this one" onclick={() => addAfter(it)}>＋</button>
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
  .same { padding: 6px; font-style: italic; }
  .flags { display: flex; gap: 4px; flex-wrap: wrap; align-items: center; }
  .flag { font-size: 10.5px; padding: 0 5px; border-radius: 3px; background: var(--bg-sunken); color: var(--warn); cursor: help; }
  .stale-tag { color: var(--warn); }
  .acts { display: flex; gap: 2px; flex-wrap: wrap; justify-content: flex-end; }
  .tiny-btn { padding: 1px 5px; font-size: 11.5px; line-height: 1.4; }
  .tiny-btn.danger { color: var(--danger, #e5534b); }
  .small { font-size: 12px; }
  .tiny { font-size: 11px; }
  .pad { padding: 10px 2px; }
</style>
