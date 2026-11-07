"""clean-audio-batch: render windows of a video with tuned settings.

Two ways to run it:

  clean-audio-batch PLAN.json --output_dir OUT
      Tuning already ran: render every window with the settings in PLAN.json
      (written by clean-audio-tune, or by hand).

  clean-audio-batch --video V --windows_file F --output_dir OUT [--tune_dir T]
      Reuses the tuning in T (default OUT/_tuning/<video name>, the same cache
      clean-audio --autotune uses) if it already covers this video and these
      windows; otherwise tunes the missing ones first. --retune redoes them all.

Before rendering it prints which plan and settings it is using, and every command
line it will run; --dry-run stops there. Existing outputs are skipped unless
--force. Ctrl+C stops the whole batch; other failures are reported and the batch
carries on. Each output's .params.json records that its settings came from
tuning, with the plan and the scores that won.

Minimal hand-written plan:

  {"source": "/path/video.mp4", "stem": "my_video",
   "windows": [{"label": "07m00s-08m00s", "start_sec": 420, "end_sec": 480,
                "flags": ["--normalize", "--atten_lim_db", "10", "--reduce_hiss"]}]}
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

CLEAN_AUDIO = str(Path(sys.executable).with_name("clean-audio"))


def load_plan(path):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("source", "windows"):
        if key not in plan:
            raise SystemExit(f"{path}: plan has no '{key}'")
    for w in plan["windows"]:
        for key in ("label", "start_sec", "end_sec", "flags"):
            if key not in w:
                raise SystemExit(f"{path}: a window is missing '{key}': {w}")
    return plan


def commands(plan, output_dir, stem=None, only=None):
    stem = stem or plan.get("stem") or Path(plan["source"]).stem
    out = []
    for w in plan["windows"]:
        if only and w["label"] not in only:
            continue
        name = f"{stem}_{w['label']}.mp4"
        cmd = [CLEAN_AUDIO, plan["source"], "--output_dir", str(output_dir),
               "--output", name,
               "--skip_first_n_sec", f"{w['start_sec']:g}",
               "--process_n_seconds", f"{w['end_sec'] - w['start_sec']:g}",
               *w["flags"]]
        out.append((w, name, cmd))
    return out


def plan_covers(plan_path, video, labels):
    """True when an existing plan was tuned on this video and has every window."""
    if not Path(plan_path).exists():
        return False, "no tuning found"
    plan = load_plan(plan_path)
    if os.path.abspath(plan["source"]) != os.path.abspath(video):
        return False, f"it was tuned on a different video ({plan['source']})"
    missing = sorted(set(labels) - {w["label"] for w in plan["windows"]})
    if missing:
        return False, f"it lacks window(s) {', '.join(missing)}"
    return True, ""


def ensure_plan(args):
    """Mode 2: reuse the tuning if it covers these windows, else tune. Returns plan path."""
    from .tune import parse_window

    texts = list(args.window or [])
    if args.windows_file:
        texts += [l for l in Path(args.windows_file).read_text().splitlines() if l.strip()]
    if not texts:
        raise SystemExit("--video needs windows: --window or --windows_file")
    labels = [parse_window(t).label for t in texts]
    from .tune import default_tune_dir
    tune_dir = Path(args.tune_dir) if args.tune_dir else \
        default_tune_dir(args.output_dir, args.video)
    plan_path = tune_dir / "plan.json"

    covered, why = plan_covers(plan_path, args.video, labels)
    if covered and not args.retune:
        print(f"Tuning already done for these {len(labels)} window(s): reusing {plan_path}\n")
    else:
        reason = "--retune given" if covered else why
        print(f"Tuning first ({reason}). Results go to {tune_dir}; windows already "
              f"tuned there are kept.\n")
        from . import tune
        # tune keeps windows it already has and tunes only the missing ones.
        tune_args = [args.video, *texts, "--out_dir", str(tune_dir)]
        if args.stem:
            tune_args += ["--stem", args.stem]
        if args.retune:
            tune_args.append("--retune")
        if args.source:
            tune_args += ["--source", args.source]
        tune.main(tune_args)
        print()
    args.only = args.only or labels
    return plan_path


def describe_plan(plan_path, plan, jobs):
    print(f"Using tuned parameters from {plan_path}")
    if "created" in plan:
        print(f"  tuned {plan['created']} on {plan['source']}")
    if "criteria" in plan:
        c = plan["criteria"]
        if "fidelity_tolerance" in c:
            print(f"  criteria: per window, within {c['fidelity_tolerance']} fidelity of the "
                  f"gentlest setting and {c['max_warble_increase']} warble of the calmest, "
                  f"click weight {c['click_weight']}")
        else:
            print(f"  criteria: {c}")
    print()
    for w, name, _ in jobs:
        flags = " ".join(w["flags"])
        notes = []
        if w.get("met_criteria") is False:
            notes.append("NO candidate met the criteria — gentlest setting used")
        if w.get("noise_sample_suspect"):
            notes.append("no clear pause for the hiss profile")
        print(f"  {w['label']:<16} {flags}")
        for note in notes:
            print(f"  {'':<16} ⚠ {note}")
    print()


def record_tuning(output_dir, name, plan_path, plan, window):
    """Add a 'tuning' section to the output's .params.json."""
    from .provenance import sidecar_path

    path = sidecar_path(Path(output_dir) / name)
    if not path.exists():
        return
    record = json.loads(path.read_text(encoding="utf-8"))
    picked = window.get("picked", {})
    record["tuning"] = {
        "plan": str(Path(plan_path).resolve()),
        "tuned": plan.get("created"),
        "criteria": plan.get("criteria"),
        "met_criteria": window.get("met_criteria"),
        "picked": {k: picked[k] for k in ("fidelity", "hiss_gain_db", "warble", "clicks",
                                         "click_gain_db", "score") if k in picked},
        "source_window": window.get("source"),
    }
    path.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")


def build_parser():
    p = argparse.ArgumentParser(prog="clean-audio-batch", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("plan", nargs="?", help="plan.json from clean-audio-tune (tuning done).")
    p.add_argument("--output_dir", required=True, help="Where the outputs go.")
    p.add_argument("--video", help="Source video, to tune-or-reuse instead of giving a plan.")
    p.add_argument("--window", action="append", metavar="START-END",
                   help="A window (repeatable), with --video.")
    p.add_argument("--windows_file", help="One window or old filename per line, with --video.")
    p.add_argument("--tune_dir", help="Where tuning lives "
                                      "(default: OUTPUT_DIR/_tuning/<video name>).")
    p.add_argument("--retune", action="store_true", help="Tune again even if tuning exists.")
    p.add_argument("--source", choices=("tape", "digital"), default=None,
                   help="When tuning: what the recording came from (asked if not "
                        "given; see clean-audio-tune).")
    p.add_argument("--stem", default=None, help="Output name prefix (default: the plan's).")
    p.add_argument("--only", nargs="+", metavar="LABEL", help="Render only these windows.")
    p.add_argument("--dry-run", action="store_true", help="Print the commands, run nothing.")
    p.add_argument("--force", action="store_true", help="Re-render existing outputs.")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if bool(args.plan) == bool(args.video):
        raise SystemExit("Give either a plan.json, or --video with windows.")
    plan_path = args.plan if args.plan else ensure_plan(args)

    plan = load_plan(plan_path)
    jobs = commands(plan, args.output_dir, args.stem, args.only)
    if not jobs:
        raise SystemExit("Nothing to do: no windows matched.")
    output_dir = Path(args.output_dir)

    describe_plan(plan_path, plan, jobs)
    print(f"{len(jobs)} run(s):\n")
    for i, (_, name, cmd) in enumerate(jobs, 1):
        exists = (output_dir / name).exists()
        note = "   # exists, will skip" if exists and not args.force else ""
        print(f"[{i}/{len(jobs)}] {shlex.join(cmd)}{note}\n")
    if args.dry_run:
        return 0

    done, skipped, failed = [], [], []
    for i, (window, name, cmd) in enumerate(jobs, 1):
        print(f"\n{'=' * 70}\n[{i}/{len(jobs)}] {window['label']} -> {name}\n{'=' * 70}")
        if (output_dir / name).exists() and not args.force:
            print("already rendered, skipping (use --force to redo)")
            skipped.append(name)
            continue
        began = time.time()
        code = subprocess.call(cmd)
        if code == 130:
            print("\nInterrupted — stopping the batch.")
            break
        if code == 0:
            record_tuning(output_dir, name, plan_path, plan, window)
            done.append(name)
        else:
            failed.append(name)
        print(f"{'done' if code == 0 else f'FAILED (exit {code})'} in {time.time() - began:.0f}s")

    print(f"\nRendered {len(done)}, skipped {len(skipped)}, failed {len(failed)}.")
    for name in failed:
        print(f"  failed: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
