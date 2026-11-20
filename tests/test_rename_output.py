import json

import pytest

from video_audio_cleaner.gui import projects, rename


def test_new_name_replaces_only_the_tag():
    assert rename.new_name("a b_04m12s-06m47s_v1.mp4", "music kept") == "a b_04m12s-06m47s_music kept.mp4"
    assert rename.new_name("a b_04m12s-06m47s.mp4", "x") == "a b_04m12s-06m47s_x.mp4"
    assert rename.new_name("a b_04m12s-06m47s_x.mp4", "") == "a b_04m12s-06m47s.mp4"
    with pytest.raises(ValueError):
        rename.new_name("a_04m12s-06m47s.mp4", "a/b")


def test_rename_moves_files_and_updates_projects(tmp_path):
    out = tmp_path / "vid_00m00s-00m10s_v1.mp4"
    out.write_bytes(b"x")
    (tmp_path / "vid_00m00s-00m10s_v1.params.json").write_text(json.dumps(
        {"output": out.name, "parameters": {"tag": "v1"}, "reproduce": f"clean-audio --output={out.name}"}))
    p = projects.new("Show")
    p["sources"]["s1"] = {"path": "/v/vid.mp4"}
    p["tracks"]["video"] = [{"id": "c1", "source": "s1", "in_sec": 1, "out_sec": 5,
                             "audio": {"file": str(out), "output": out.name, "start_sec": 0}}]
    projects.save(tmp_path, "Show", p)

    target, updated = rename.rename_output(tmp_path, out, "music kept")
    assert target.name == "vid_00m00s-00m10s_music kept.mp4" and target.exists() and not out.exists()
    side = json.loads((tmp_path / "vid_00m00s-00m10s_music kept.params.json").read_text())
    assert side["output"] == target.name and side["parameters"]["tag"] == "music kept"
    assert target.name in side["reproduce"]
    assert updated == ["Show"]
    clip = projects.load(tmp_path, "Show")["tracks"]["video"][0]
    assert clip["audio"]["output"] == target.name and clip["audio"]["file"] == str(target)

    other = tmp_path / "vid_00m00s-00m10s_v2.mp4"
    other.write_bytes(b"y")
    with pytest.raises(FileExistsError):
        rename.rename_output(tmp_path, other, "music kept")
