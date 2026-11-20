"""The size budget through the GUI server (docs/stitch-and-size-budget-plan.md §11)."""
import json

import pytest

pytest.importorskip("fastapi")
from tests.test_gui_server import H, client, isolated_settings, project, wait_for  # noqa: E402,F401
from video_audio_cleaner import probe  # noqa: E402


def budgeted(client, fx, total_gb=0.0006):
    client.post("/api/projects", headers=H, json={"name": "cut"})
    sid = client.get("/api/library", headers=H).json()["sources"][0]["id"]
    p = client.get("/api/projects/cut", headers=H).json()
    p["sources"][sid] = {"path": str(fx.video), "probe": probe.probe(str(fx.video))}
    # Grain, so the budget bites even on the small fixture frame.
    p["tracks"]["video"] = [{"id": "c1", "source": sid, "in_sec": 0, "out_sec": 12,
                             "video_filters": ["noise=alls=20:allf=t"]}]
    p["export"].update(budget={"total_gb": total_gb}, codec="hevc", preset="veryfast",
                       audio_bitrate="64k")
    assert client.put("/api/projects/cut", headers=H, json=p).status_code == 200
    return p


def test_estimate_then_export(client, project, fx):
    p = budgeted(client, fx)
    assert client.get("/api/projects/cut/estimate", headers=H).json() == {"estimate": None,
                                                                          "stale": False}
    job = client.post("/api/jobs", headers=H, json={"type": "export_estimate", "project": "cut"}).json()
    assert job["title"] == "Estimate cut" and "--estimate" in job["argv"]
    done = wait_for(client, job["id"], timeout=180)
    assert done["state"] == "done", done.get("error")
    assert done["result"]["output"] is None and done["result"]["results"]["estimate"]["mode"] == "size"
    got = client.get("/api/projects/cut/estimate", headers=H).json()
    est = got["estimate"]
    assert got["stale"] is False and est["mode"] == "size" and est["words"]
    assert est["budget_bytes"] == 600_000 and est["predicted_bytes"] <= 600_000
    assert not (project / "cut.mp4").exists()

    job = client.post("/api/jobs", headers=H, json={"type": "export", "project": "cut"}).json()
    assert job["title"] == "Export cut" and "--estimate" not in job["argv"]
    done = wait_for(client, job["id"], timeout=180)
    assert done["state"] == "done", done.get("error")
    results = done["result"]["results"]
    assert results["budget_mode"] == "size" and results["estimate"]["key"] == est["key"]
    assert abs((project / "cut.mp4").stat().st_size / 600_000 - 1) < 0.10

    # Another budget: the saved estimate is stale.
    p = client.get("/api/projects/cut", headers=H).json()
    p["export"]["budget"] = {"per_hour_gb": 2}
    client.put("/api/projects/cut", headers=H, json=p)
    assert client.get("/api/projects/cut/estimate", headers=H).json()["stale"] is True
    p["export"]["budget"] = None
    client.put("/api/projects/cut", headers=H, json=p)
    assert client.get("/api/projects/cut/estimate", headers=H).json()["stale"] is True


def test_plan_lists_missing_files(client, project, fx, tmp_path):
    p = budgeted(client, fx)
    gone = str(tmp_path / "gone.wav")
    p["tracks"]["video"][0]["audio"] = {"file": gone, "start_sec": 0}
    client.put("/api/projects/cut", headers=H, json=p)
    plan = client.get("/api/projects/cut/plan", headers=H).json()
    assert plan["missing"] == [gone]


def test_estimate_job_needs_a_known_project(client, project):
    r = client.post("/api/jobs", headers=H, json={"type": "export_estimate", "project": "nope"})
    assert r.status_code == 404
