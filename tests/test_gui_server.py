"""The GUI server (docs/gui-design.md §5): library, jobs, media and the API guard."""
import json
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from video_audio_cleaner import tune  # noqa: E402
from video_audio_cleaner.gui import commands, library  # noqa: E402
from video_audio_cleaner.gui.app import create_app  # noqa: E402
from video_audio_cleaner.tune import Candidate  # noqa: E402

FAKE = str(Path(__file__).with_name("fake_tool.py"))
H = {"x-vac": "1"}


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("VAC_GUI_SETTINGS", str(tmp_path / "settings.json"))


@pytest.fixture
def project(fx, tmp_path):
    """An output folder like a real one: two outputs of the fixture, a stray file, a plan."""
    out = tmp_path / "project"
    out.mkdir()
    for name, tag, start in (("fixture_00m02s-00m06s.mp4", None, 2.0),
                             ("fixture_00m02s-00m06s_v2.mp4", "v2", 2.0)):
        shutil.copy(fx.video, out / name)
        (out / name).with_suffix(".params.json").write_text(json.dumps({
            "schema": 1, "output": name, "created": "2026-11-07T10:00:00+00:00",
            "reproduce": "clean-audio x",
            "parameters": {"input_video": str(fx.video), "source": "tape", "tag": None},
            "window": {"requested_start_sec": start, "requested_duration_sec": 4.0,
                       "actual_start_sec": start, "actual_duration_sec": 4.0,
                       "keyframe_snap_sec": 0.0},
            "results": {"final_lufs": -17.0, "lufs_before_normalize": -25.0}}))
    (out / "stray.mp4").write_bytes(b"x")
    tune_dir = out / "_tuning" / "fixture"
    tune_dir.mkdir(parents=True)
    cands = [asdict(Candidate(10.0, "off", False, fidelity=0.97, speech_to_hiss=14,
                              warble=5, clicks=10)),
             asdict(Candidate(20.0, "off", False, fidelity=0.95, speech_to_hiss=16,
                              warble=5, clicks=10))]
    (tune_dir / "plan.json").write_text(json.dumps({
        "schema": 1, "method": tune.METHOD, "source": str(fx.video),
        "grid": {"source": "tape", "separate_music": False},
        "criteria": {"fidelity_tolerance": 0.03, "max_warble_increase": 0.5,
                     "click_weight": 0.5, "min_fidelity": None},
        "output_flags": {"normalize": True, "target_lufs": "auto"},
        "windows": [{"label": "00m02s-00m06s", "start_sec": 2.0, "end_sec": 6.0,
                     "candidates": cands, "picked": cands[1], "runner_up": cands[0],
                     "source": {"speech_to_hiss": 13, "warble": 5, "clicks": 10},
                     "music": None}]}))
    return out


@pytest.fixture
def client(project):
    with TestClient(create_app(project)) as c:
        yield c


def wait_for(client, job_id, states=("done", "failed", "cancelled"), timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}", headers=H).json()
        if job["state"] in states:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job stuck in {job['state']}")


# ------------------------------------------------------------------ library
def test_library_groups_outputs_under_their_source(client, fx):
    lib = client.get("/api/library", headers=H).json()
    [source] = lib["sources"]
    assert source["path"] == str(fx.video) and source["exists"]
    assert source["source_kind"] == "tape"
    assert [(o["file"], o["label"], o["tag"]) for o in source["outputs"]] == [
        ("fixture_00m02s-00m06s.mp4", "00m02s-00m06s", None),
        ("fixture_00m02s-00m06s_v2.mp4", "00m02s-00m06s", "v2")]
    assert [p["windows"][0]["label"] for p in source["plans"]] == ["00m02s-00m06s"]
    assert [o["file"] for o in lib["unknown"]] == ["stray.mp4"]


def test_name_parts():
    assert library.name_parts("02-חתונה_24m45s-29m45s_v2.mp4") == ("24m45s-29m45s", "v2")
    assert library.name_parts("a_1h02m00s-end.mp4") == ("1h02m00s-end", None)
    assert library.name_parts("plain.mp4") == (None, None)


def test_probe_is_cached(client, project):
    sid = client.get("/api/library", headers=H).json()["sources"][0]["id"]
    probe = client.get(f"/api/sources/{sid}/probe", headers=H).json()
    assert probe["video"]["width"] == 640 and probe["audio"]["channels"] == 2
    assert (project / "_gui" / "cache" / "probe" / f"{sid}.json").exists()
    assert client.get("/api/library", headers=H).json()["sources"][0]["probe"]["duration_sec"]


def test_no_project_open(tmp_path):
    with TestClient(create_app(None)) as c:
        assert c.get("/api/library", headers=H).json()["output_dir"] is None
        assert c.get("/api/jobs", headers=H).json() == []
        r = c.post("/api/library/open", headers=H, json={"output_dir": str(tmp_path)})
        assert r.json()["output_dir"] == str(tmp_path)


# ------------------------------------------------------------------ guard
def test_api_needs_the_header_and_a_local_host(client):
    assert client.get("/api/library").status_code == 403
    assert client.get("/api/library", headers={**H, "host": "evil.example"}).status_code == 403
    assert client.get("/api/library", headers={**H, "host": "127.0.0.1:8787"}).status_code == 200


def test_media_is_limited_to_the_project(client, tmp_path):
    secret = tmp_path / "secret.mp4"
    secret.write_bytes(b"x")
    assert client.get("/media/file", params={"path": str(secret)}).status_code == 403


def test_media_supports_ranges(client, project):
    path = str(project / "fixture_00m02s-00m06s.mp4")
    r = client.get("/media/file", params={"path": path}, headers={"range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100


def test_original_and_output_audio_are_prepared(client, project):
    name = "fixture_00m02s-00m06s.mp4"
    for kind in ("output", "original_for"):
        r = client.get("/media/audio", params={"kind": kind, "file": name})
        assert r.status_code == 200 and r.content[4:8] == b"ftyp", kind
    assert len(list((project / "_gui" / "cache" / "audio").glob("*.m4a"))) == 2


# ------------------------------------------------------------------ commands
def test_clean_request_becomes_a_valid_command(fx, project):
    argv = commands.clean_argv(fx.video, project, 1485.5, 300, True, "tape", True, "v3",
                               overrides={"--declick_nf": -30, "--precise_cut": True})
    assert argv[1:3] == ["-m", "video_audio_cleaner.cli"]
    assert "--skip_first_n_sec" in argv and argv[argv.index("--skip_first_n_sec") + 1] == "1485.5"
    assert ["--autotune", "--source", "tape", "--keep_candidates"] == \
        argv[argv.index("--autotune"):argv.index("--autotune") + 4]
    assert "--declick_nf=-30" in argv and "--precise_cut" in argv
    assert commands.display(argv).startswith("clean-audio ")


@pytest.mark.parametrize("kwargs, message", [
    (dict(autotune=True, source_kind=None), "source kind"),
    (dict(overrides={"--atten_lim_db": 10}), "chosen by autotune"),
    (dict(overrides={"--output_dir": "/tmp"}), "set by the form"),
    (dict(overrides={"--nope": 1}), "Unknown option"),
    (dict(autotune=False, overrides={"--atten_lim_db": "loud"}), "would be refused"),
])
def test_bad_requests_are_refused(fx, project, kwargs, message):
    kwargs = {"autotune": True, "source_kind": "tape", **kwargs}
    with pytest.raises(ValueError, match=message):
        commands.clean_argv(fx.video, project, **kwargs)


def test_tune_command(fx, project):
    argv = commands.tune_argv(fx.video, project, [{"start_sec": 2, "end_sec": 6}], "digital",
                              True)
    assert argv[3:5] == [str(fx.video), "00m02s-00m06s"]
    assert argv[argv.index("--out_dir") + 1] == str(project / "_tuning" / "fixture-with-music")


def test_schema_lists_options_but_not_reserved_ones():
    flags = {o["flag"]: o for o in commands.cli_schema()}
    assert flags["--atten_lim_db"]["autotuned"] and flags["--atten_lim_db"]["default"] == 20.0
    assert flags["--precise_cut"]["kind"] == "flag"
    assert "--output_dir" not in flags and "--progress" not in flags


def test_preview_name(client):
    sid = client.get("/api/library", headers=H).json()["sources"][0]["id"]
    r = client.get("/api/outputs/preview-name", headers=H,
                   params={"source_id": sid, "start_sec": 2, "duration_sec": 4}).json()
    assert r == {"name": "fixture_00m02s-00m06s.mp4", "exists": True}


# ------------------------------------------------------------------ jobs
def submit_fake(client, mode):
    app_queue = client.app.state.queue
    lib = client.app.state.project["library"]
    return app_queue.submit("clean", f"fake {mode}", [sys.executable, FAKE, mode],
                            lib.output_dir)["id"]


def test_job_runs_and_reports(client, project):
    job = wait_for(client, submit_fake(client, "ok"))
    assert job["state"] == "done" and job["returncode"] == 0
    assert job["step"]["title"] == "Working" and job["progress"]["total_sec"] == 2.0
    assert job["messages"] == [{"level": "warning", "text": "careful"}]
    assert job["result"]["results"] == {"final_lufs": -17.0}
    log = client.get(f"/api/jobs/{job['id']}/log", headers=H).text
    assert "stderr chatter" in log and "not json" in log
    saved = json.loads((project / "_gui" / "jobs" / f"{job['id']}.json").read_text())
    assert saved["state"] == "done"


def test_failed_job_keeps_the_error(client):
    job = wait_for(client, submit_fake(client, "fail"))
    assert job["state"] == "failed" and job["error"] == "it broke"
    retry = client.post(f"/api/jobs/{job['id']}/retry", headers=H).json()
    assert retry["id"] != job["id"] and retry["argv"] == job["argv"]


def test_cancel_interrupts_the_process(client):
    job_id = submit_fake(client, "slow")
    wait_for(client, job_id, states=("running",))
    time.sleep(0.3)
    client.delete(f"/api/jobs/{job_id}", headers=H)
    job = wait_for(client, job_id)
    assert job["state"] == "cancelled" and job["returncode"] == 130


def test_ml_jobs_run_one_at_a_time(client):
    first = submit_fake(client, "slow")
    second = submit_fake(client, "ok")
    wait_for(client, first, states=("running",))
    time.sleep(0.3)
    assert client.get(f"/api/jobs/{second}", headers=H).json()["state"] == "queued"
    client.delete(f"/api/jobs/{first}", headers=H)
    assert wait_for(client, second)["state"] == "done"


def test_unfinished_jobs_are_marked_lost_after_a_restart(project):
    folder = project / "_gui" / "jobs"
    folder.mkdir(parents=True)
    (folder / "20260101T000000-abcd.json").write_text(json.dumps({
        "id": "20260101T000000-abcd", "type": "clean", "state": "running",
        "output_dir": str(project), "started": "x", "finished": None}))
    with TestClient(create_app(project)) as c:
        [job] = c.get("/api/jobs", headers=H).json()
        assert job["state"] == "lost" and "stopped while" in job["error"]


def test_create_job_validates(client):
    sid = client.get("/api/library", headers=H).json()["sources"][0]["id"]
    r = client.post("/api/jobs", headers=H, json={"type": "clean", "source_id": sid,
                                                  "autotune": True})
    assert r.status_code == 400 and "source kind" in r.json()["detail"]
    r = client.post("/api/jobs", headers=H, json={"type": "nope"})
    assert r.status_code == 400


# ------------------------------------------------------------------ plans
def test_pick_a_candidate_and_delete_its_audio(client, project):
    plan = str(project / "_tuning" / "fixture" / "plan.json")
    entry = client.post("/api/plans/pick", headers=H,
                        json={"plan_path": plan, "window": "00m02s-00m06s", "index": 0}).json()
    assert entry["user_pick"] == 0 and entry["picked"]["atten"] == 10.0
    lib = client.get("/api/library", headers=H).json()
    assert lib["sources"][0]["plans"][0]["windows"][0]["user_pick"] == 0
    r = client.post("/api/plans/pick", headers=H,
                    json={"plan_path": "/etc/passwd", "window": "x", "index": 0})
    assert r.status_code == 403
    r = client.post("/api/plans/candidates/delete", headers=H,
                    json={"plan_path": plan, "window": "00m02s-00m06s"})
    assert r.json() == {"ok": True}


def test_delete_moves_to_trash(client, project):
    r = client.post("/api/outputs/fixture_00m02s-00m06s_v2.mp4/delete", headers=H).json()
    assert len(r["moved"]) == 2
    assert (project / "_trash" / "fixture_00m02s-00m06s_v2.mp4").exists()
    files = [o["file"] for o in client.get("/api/library", headers=H).json()["sources"][0]["outputs"]]
    assert files == ["fixture_00m02s-00m06s.mp4"]


# ------------------------------------------------------------------ native window
class FakeDialogs:
    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def pick(self, kind, start=None):
        self.asked.append((kind, start))
        return self.answer


def test_native_dialogs_only_in_the_window(project):
    with TestClient(create_app(project)) as c:
        assert c.get("/api/app", headers=H).json()["native"] is False
        assert c.get("/api/fs/pick", headers=H).status_code == 501
    dialogs = FakeDialogs("/x/y.mp4")
    with TestClient(create_app(project, dialogs=dialogs)) as c:
        assert c.get("/api/app", headers=H).json()["native"] is True
        r = c.get("/api/fs/pick", headers=H, params={"kind": "dir", "start": "/tmp"})
        assert r.json() == {"path": "/x/y.mp4"} and dialogs.asked == [("dir", "/tmp")]


def test_gui_parser_defaults_to_the_window():
    from video_audio_cleaner.gui.main import build_parser
    assert build_parser().parse_args([]).mode == "window"
    assert build_parser().parse_args(["--browser"]).mode == "browser"
    assert build_parser().parse_args(["--no-open"]).mode == "none"


def test_shown_commands_leave_out_the_progress_flag(fx, project):
    argv = commands.clean_argv(fx.video, project, 0, 10, True, "tape")
    assert "--progress" in argv
    assert "--progress" not in commands.display(argv)


# ------------------------------------------------------------------ editor
def test_projects_round_trip(client, project, fx):
    r = client.post("/api/projects", headers=H, json={"name": "Wedding cut"})
    assert r.status_code == 200 and r.json()["tracks"]["video"] == []
    assert client.post("/api/projects", headers=H, json={"name": "Wedding cut"}).status_code == 409
    assert client.post("/api/projects", headers=H, json={"name": "a/b"}).status_code == 400
    p = r.json()
    sid = client.get("/api/library", headers=H).json()["sources"][0]["id"]
    p["sources"][sid] = {"path": str(fx.video)}
    p["tracks"]["video"].append({"id": "c1", "source": sid, "in_sec": 2, "out_sec": 6})
    assert client.put("/api/projects/Wedding cut", headers=H, json=p).status_code == 200
    bad = dict(p, tracks={"video": [{"id": "x", "source": sid, "in_sec": 6, "out_sec": 2}]})
    assert client.put("/api/projects/Wedding cut", headers=H, json=bad).status_code == 400
    [summary] = client.get("/api/library", headers=H).json()["projects"]
    assert summary["name"] == "Wedding cut" and summary["duration_sec"] == 4.0
    assert summary["export_path"] == str(project / "Wedding cut.mp4")
    song = project / "song.wav"
    shutil.copy(fx.music, song)
    stored = client.post("/api/projects/Wedding cut/music", headers=H,
                         json={"path": str(song)}).json()["path"]
    assert stored == str(project / "_projects" / "Wedding cut" / "music" / "song.wav")


def test_export_plan_and_jobs(client, project, fx):
    client.post("/api/projects", headers=H, json={"name": "cut"})
    sid = client.get("/api/library", headers=H).json()["sources"][0]["id"]
    p = client.get("/api/projects/cut", headers=H).json()
    from video_audio_cleaner import probe
    p["sources"][sid] = {"path": str(fx.video), "probe": probe.probe(str(fx.video))}
    p["tracks"]["video"] = [{"id": "c1", "source": sid, "in_sec": 2, "out_sec": 6}]
    client.put("/api/projects/cut", headers=H, json=p)
    plan = client.get("/api/projects/cut/plan", headers=H).json()
    assert plan["mode"] == "copy" and plan["output"].endswith("cut.mp4")
    job = client.post("/api/jobs", headers=H, json={"type": "export", "project": "cut"}).json()
    assert job["argv"][2] == "video_audio_cleaner.export"
    assert wait_for(client, job["id"], timeout=60)["state"] == "done"
    assert (project / "cut.mp4").exists()


def test_prepare_a_source_for_editing(client, project):
    sid = client.get("/api/library", headers=H).json()["sources"][0]["id"]
    assert client.get("/media/proxy", params={"source": sid}).status_code == 404
    job = client.post("/api/jobs", headers=H, json={"type": "prep", "source_id": sid}).json()
    assert wait_for(client, job["id"], timeout=60)["state"] == "done"
    src = client.get("/api/library", headers=H).json()["sources"][0]
    assert src["prep"] == {"proxy": True, "thumbs": True, "peaks": True}
    assert client.get("/media/proxy", params={"source": sid}).content[4:8] == b"ftyp"
    thumbs = client.get("/media/thumbs", params={"source": sid}).json()
    assert thumbs == {"interval": 2.0, "count": 30}
    assert client.get("/media/thumb", params={"source": sid, "i": 3}).content[:2] == b"\xff\xd8"
    assert client.get("/media/peaks", params={"source": sid}).content[:4] == b"VACP"


def test_exports_are_not_listed_as_stray_files(client, project):
    (project / "Cut.mp4").write_bytes(b"x")
    (project / "Cut.params.json").write_text(json.dumps({"kind": "export", "output": "Cut.mp4"}))
    lib = client.post("/api/library/rescan", headers=H).json()
    assert [o["file"] for o in lib["unknown"]] == ["stray.mp4"]


def test_source_posters(client, project):
    sid = client.get("/api/library", headers=H).json()["sources"][0]["id"]
    r = client.get("/media/poster", params={"source": sid, "i": 2})
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    assert (project / "_gui" / "cache" / "posters" / f"{sid}_2.jpg").exists()
    assert client.get("/media/poster", params={"source": sid, "i": 4}).status_code == 422
