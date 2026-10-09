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
