"""Every output gets a .params.json that fully describes how it was made."""
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from video_audio_cleaner.cli import build_parser
from video_audio_cleaner.provenance import full_command

CLEAN = Path(sys.executable).with_name("clean-audio")


def parse(argv):
    return vars(build_parser().parse_args(argv))


@pytest.mark.parametrize("argv", [
    ["in.mp4", "--output_dir", "out"],
    ["in.mp4", "--output_dir", "out", "--normalize", "--reduce_hiss", "--declick",
     "--atten_lim_db", "none", "--target_lufs", "-18", "--skip_first_n_sec", "1h2m",
     "--process_n_seconds", "30", "--hiss_method", "patch", "--audio_gain", "2dB"],
    ["in.mp4", "--output_dir", "out", "--compare", "--separate_music",
     "--music_level_db", "off", "--precise_cut", "-o", "x.wav"],
])
def test_reproduce_command_round_trips(argv):
    args = build_parser().parse_args(argv)
    rebuilt = full_command(build_parser(), args)
    assert rebuilt[0] == "clean-audio"
    assert parse(rebuilt[1:]) == vars(args)


def test_every_output_gets_a_complete_sidecar(fx, tmp_path):
    out = tmp_path / "out"
    subprocess.run([str(CLEAN), str(fx.video), "--output_dir", str(out),
                    "--skip_first_n_sec", "9s", "--process_n_seconds", "4s",
                    "--normalize"], check=True, capture_output=True, text=True)
    video = next(out.glob("*.mp4"))
    record = json.loads(video.with_suffix(".params.json").read_text())

    # Every parser option is recorded, defaults included.
    assert set(record["parameters"]) == set(parse(["x", "--output_dir", "y"]))
    assert record["parameters"]["atten_lim_db"] == 20.0
    assert record["parameters"]["output"] == video.name

    # The keyframe snap is recorded: 9 s was requested, the cut is at the 8 s keyframe.
    assert record["window"]["requested_start_sec"] == 9.0
    assert record["window"]["actual_start_sec"] == 8.0

    assert -18 < record["results"]["final_lufs"] < -14
    assert record["environment"]["video_audio_cleaner"]
    assert record["environment"]["ffmpeg"].startswith("ffmpeg version")

    # The recorded command reproduces the same parameters.
    argv = shlex.split(record["reproduce"])
    assert parse(argv[1:]) == record["parameters"]


def test_autotune_refuses_knobs_it_chooses(fx, tmp_path):
    out = subprocess.run([str(CLEAN), str(fx.video), "--output_dir", str(tmp_path),
                          "--process_n_seconds", "5", "--autotune", "--declick",
                          "--atten_lim_db=10"], capture_output=True, text=True)
    assert out.returncode == 1
    assert "--autotune chooses --atten_lim_db, --declick itself" in out.stderr


def test_tag_names_the_output_and_rerun_says_it_replaces(fx, tmp_path):
    out = tmp_path / "out"
    cmd = [str(CLEAN), str(fx.video), "--output_dir", str(out),
           "--skip_first_n_sec", "8s", "--process_n_seconds", "4s", "--tag", "take2"]
    first = subprocess.run(cmd, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    video = out / f"{fx.video.stem}_00m08s-00m12s_take2.mp4"
    assert video.exists()
    assert "will be replaced" not in first.stdout

    # The reproduce command names the file outright; --tag is already in the name.
    record = json.loads(video.with_suffix(".params.json").read_text())
    params = parse(shlex.split(record["reproduce"])[1:])
    assert (params["output"], params["tag"]) == (video.name, None)

    again = subprocess.run(cmd, capture_output=True, text=True)
    assert again.returncode == 0, again.stderr
    assert "will be replaced" in again.stdout


def test_output_name_with_a_stale_window_is_refused(fx, tmp_path):
    out = subprocess.run([str(CLEAN), str(fx.video), "--output_dir", str(tmp_path),
                          "--skip_first_n_sec", "8s", "--process_n_seconds", "4s",
                          "-o", "clip_00m08s-00m20s.mp4"], capture_output=True, text=True)
    assert out.returncode == 1
    assert "00m08s-00m12s" in out.stderr and "--tag" in out.stderr
    assert not list(tmp_path.glob("*.mp4"))
