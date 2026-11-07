"""B2: the temp dir must not be copied or deleted while a child still writes to it."""
import os
import time

from video_audio_cleaner import procs
from video_audio_cleaner.cli import _cleanup_temp


def test_cleanup_stops_children_before_copying(tmp_path):
    temp_dir = tmp_path / "work"
    keep = tmp_path / "keep"
    temp_dir.mkdir()
    keep.mkdir()
    target = temp_dir / "growing.txt"
    writer = procs.popen(["sh", "-c", f"while true; do echo x >> '{target}'; sleep 0.01; done"])
    procs.register_path(str(temp_dir))
    time.sleep(0.2)

    _cleanup_temp(str(temp_dir), str(keep))

    assert writer.poll() is not None, "child still running after cleanup"
    assert not temp_dir.exists()
    copied = (keep / "growing.txt").read_text()
    time.sleep(0.1)
    assert (keep / "growing.txt").read_text() == copied, "copy kept growing"
    assert os.path.getsize(keep / "growing.txt") > 0


def test_demucs_stays_offline_only_when_the_model_is_cached(monkeypatch, tmp_path):
    import huggingface_hub
    from huggingface_hub import constants
    from video_audio_cleaner import separation
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setattr(constants, "HF_HUB_OFFLINE", False)
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a: None)
    assert separation.use_cached_model_offline() is False      # first run: may download
    assert constants.HF_HUB_OFFLINE is False

    bag = tmp_path / "htdemucs.yaml"
    bag.write_text("models: [abc]\n")
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache",
                        lambda repo, name: str(bag) if name.endswith(".yaml") else str(tmp_path))
    assert separation.use_cached_model_offline() is True
    assert constants.HF_HUB_OFFLINE is True
