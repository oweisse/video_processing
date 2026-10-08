"""Ctrl+C at any point must leave nothing behind."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

CLEAN = Path(sys.executable).with_name("clean-audio")


def run_and_interrupt(args, tmp_path, wait_for):
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    env = dict(os.environ, TMPDIR=str(tmpdir))
    proc = subprocess.Popen([str(CLEAN), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env=env)
    log = []
    deadline = time.time() + 60
    for line in proc.stdout:
        log.append(line)
        if wait_for in line:
            break
        if time.time() > deadline:
            proc.kill()
            pytest.fail("stage never reached:\n" + "".join(log))
    time.sleep(0.5)
    proc.send_signal(signal.SIGINT)
    rest, _ = proc.communicate(timeout=30)
    log.append(rest)
    return proc.returncode, "".join(log), tmpdir


def test_interrupt_during_denoise_cleans_up(fx, tmp_path):
    out_dir = tmp_path / "out"
    code, log, tmpdir = run_and_interrupt(
        [str(fx.video), "--output_dir", str(out_dir), "--normalize"], tmp_path,
        wait_for="Filtering noise")
    assert code == 130, log
    assert "Interrupted" in log
    assert "Traceback" not in log
    assert not list(tmpdir.glob("video_audio_cleaner_*")), "temp dir left behind"
    assert not list(out_dir.glob("*.partial.*")), "partial output left behind"
    leftover = subprocess.run(["pgrep", "-f", str(tmpdir)], capture_output=True, text=True)
    assert leftover.stdout.strip() == "", "child process still running"
