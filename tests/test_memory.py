import subprocess
import sys
import time

import pytest

from video_audio_cleaner import memory


def test_budget_defaults_to_memory_less_eight_gb(monkeypatch):
    monkeypatch.delenv("VAC_MEMORY_BUDGET_GB", raising=False)
    monkeypatch.setattr(memory, "total_bytes", lambda: 48 * memory.GB)
    assert memory.budget_bytes() == 40 * memory.GB
    monkeypatch.setattr(memory, "total_bytes", lambda: 8 * memory.GB)
    assert memory.budget_bytes() == 4 * memory.GB          # at least half the machine
    monkeypatch.setenv("VAC_MEMORY_BUDGET_GB", "12.5")
    assert memory.budget_bytes() == 12.5 * memory.GB


def test_room_is_judged_against_our_own_usage(monkeypatch):
    monkeypatch.setenv("VAC_MEMORY_BUDGET_GB", "10")
    monkeypatch.setattr(memory, "our_usage", lambda max_age=1.0: 7 * memory.GB)
    assert memory.room_for(2.5)
    assert not memory.room_for(3.5)
    assert memory.never_fits(11)
    assert not memory.never_fits(9)
    monkeypatch.setattr(memory, "our_usage", lambda max_age=1.0: None)
    assert memory.room_for(100)                            # unknown: never blocks


def test_clean_estimate_grows_with_the_stretch():
    assert memory.clean_estimate_gb(0) == pytest.approx(memory.CLEAN_BASE_GB)
    # the 10-minute measurement (6.3 GB) stays inside the estimate
    assert memory.clean_estimate_gb(600) >= 6.3


def test_usage_counts_our_children():
    memory.forget_usage()
    if memory.our_usage() is None:
        pytest.skip("ps cannot list processes here")
    before = memory.usage_of(memory.tree())
    child = subprocess.Popen([sys.executable, "-c",
                              "b = bytearray(400 * 2**20)\nimport time; time.sleep(5)"])
    try:
        time.sleep(1.5)
        memory.forget_usage()
        assert child.pid in memory.tree()
        assert memory.our_usage() - before > 300 * 2**20
    finally:
        child.kill()
        child.wait()


def test_usage_is_unknown_when_ps_fails(monkeypatch):
    def fail(*a, **k):
        return subprocess.CompletedProcess(a, 1, "", "operation not permitted")
    monkeypatch.setattr(memory.subprocess, "run", fail)
    memory.forget_usage()
    assert memory.our_usage() is None
    memory.forget_usage()
