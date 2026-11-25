from video_audio_cleaner.gui import projects


def test_music_with_the_same_name_gets_its_own_copy(tmp_path):
    out = tmp_path / "lib"
    out.mkdir()
    projects.create(str(out), "P")
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "song.mp3").write_bytes(b"first song")
    (b / "song.mp3").write_bytes(b"second song")
    first = projects.add_music(str(out), "P", str(a / "song.mp3"))
    again = projects.add_music(str(out), "P", str(a / "song.mp3"))
    second = projects.add_music(str(out), "P", str(b / "song.mp3"))
    assert first == again and first.endswith("song.mp3")
    assert second.endswith("song_2.mp3")
    assert open(second, "rb").read() == b"second song"
    assert open(first, "rb").read() == b"first song"


def test_subtitle_jobs_show_their_command_name():
    from video_audio_cleaner.gui import commands
    argv = [commands.PYTHON, "-m", "video_audio_cleaner.subtitles_cli", "transcribe", "--progress", "json"]
    assert commands.display(argv) == "clean-audio-subtitles transcribe"


def test_export_checks_the_music_file_too(tmp_path):
    import pytest
    from video_audio_cleaner import export as E
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    p = {"schema": E.SCHEMA, "sources": {"s": {"path": str(video)}},
         "tracks": {"video": [{"id": "a", "source": "s", "in_sec": 0, "out_sec": 1}],
                    "music": [{"file": str(tmp_path / "gone.mp3")}]}}
    with pytest.raises(E.ProjectError, match="gone.mp3"):
        E.check_files(p)


def test_only_videos_become_sources(tmp_path):
    import subprocess
    from video_audio_cleaner.gui import library
    doc = tmp_path / "notes.txt"
    doc.write_text("private")
    pic = tmp_path / "x.jpg"
    tape = tmp_path / "tape.mpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:duration=1",
                    "-frames:v", "1", str(pic)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:duration=2",
                    str(tape)], check=True)
    assert not library.is_video(doc) and not library.is_video(pic)
    assert library.is_video(tape) and library.is_video(tmp_path / "any.mp4")


def test_requests_from_other_sites_are_refused(tmp_path, monkeypatch):
    import pytest
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from video_audio_cleaner.gui.app import create_app
    monkeypatch.setenv("VAC_GUI_SETTINGS", str(tmp_path / "settings.json"))
    with TestClient(create_app(None)) as c:
        evil = {"sec-fetch-site": "cross-site", "sec-fetch-mode": "no-cors", "sec-fetch-dest": "video"}
        assert c.get("/media/file?path=/x.mp4", headers=evil).status_code == 403
        assert c.get("/api/events", headers={"sec-fetch-site": "same-site", "sec-fetch-mode": "cors"}).status_code == 403
        # a link to the app still opens it; our own pages and tools without a browser work
        r = c.get("/", headers={"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate"})
        assert r.status_code == 200 and r.headers["cross-origin-resource-policy"] == "same-origin"
        own = c.get("/api/library", headers={"x-vac": "1", "sec-fetch-site": "same-origin"})
        assert own.status_code != 403
