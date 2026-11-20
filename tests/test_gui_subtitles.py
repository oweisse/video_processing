"""Subtitles through the GUI server (docs/subtitles-plan.md §6)."""
import pytest

pytest.importorskip("fastapi")
from tests.test_gui_server import H, client, isolated_settings, project  # noqa: E402,F401  (fixtures)
from video_audio_cleaner import subs  # noqa: E402


@pytest.fixture
def sid(client):
    return client.get("/api/library", headers=H).json()["sources"][0]["id"]


def _seed(project, sid, lines):
    p = subs.doc_path(project, sid)
    subs.update(p, lambda d: (d.__setitem__("cues", [subs.make_cue(a, b, t) for a, b, t in lines]),
                             d.__setitem__("coverage", [[0, 8]])))
    return p


def test_document_and_atomic_edits(client, project, sid):
    assert client.get(f"/api/subtitles/{sid}", headers=H).json()["cues"] == []
    _seed(project, sid, [(1, 2.5, "שלום לכולם"), (3, 4.5, "hello there")])
    doc = client.get(f"/api/subtitles/{sid}", headers=H).json()
    a, b = (c["id"] for c in doc["cues"])
    r = client.post(f"/api/subtitles/{sid}/edit", headers=H, json={"ops": [
        {"op": "text", "id": a, "text": "שלום לכולם, ברוכים הבאים"},
        {"op": "text", "id": a, "lang": "en", "text": "Hello everyone, welcome"},
        {"op": "time", "id": b, "start": 3.1, "end": 5}]})
    assert r.status_code == 200 and {c["id"] for c in r.json()["cues"]} == {a, b}
    # an unknown id: nothing of that request is applied
    r = client.post(f"/api/subtitles/{sid}/edit", headers=H, json={"ops": [
        {"op": "delete", "id": b}, {"op": "delete", "id": "nope"}]})
    assert r.status_code == 404
    doc = client.get(f"/api/subtitles/{sid}", headers=H).json()
    assert len(doc["cues"]) == 2 and doc["cues"][0]["tr"]["en"]["text"] == "Hello everyone, welcome"
    r = client.post(f"/api/subtitles/{sid}/edit", headers=H, json={"ops": [{"op": "text", "id": a, "text": " "}]})
    assert r.status_code == 400
    lib = client.get("/api/library", headers=H).json()["sources"][0]
    assert lib["subtitles"]["cues"] == 2 and lib["subtitles"]["langs"]["en"]["done"] == 1


def test_project_cues_and_srt(client, project, sid):
    _seed(project, sid, [(1, 2.5, "שלום לכולם"), (3, 4.5, "hello there")])
    client.post("/api/projects", headers=H, json={"name": "Show"})
    p = client.get("/api/projects/Show", headers=H).json()
    p["sources"][sid] = {"path": client.get("/api/library", headers=H).json()["sources"][0]["path"]}
    p["tracks"]["video"] = [{"id": "c1", "source": sid, "in_sec": 2.8, "out_sec": 6, "audio": None}]
    assert client.put("/api/projects/Show", headers=H, json=p).status_code == 200
    r = client.get("/api/projects/Show/subtitles?lang=mixed", headers=H).json()
    assert [(i["start"], i["text"]) for i in r["items"]] == [(0.2, "hello there")]
    he = client.get("/api/projects/Show/subtitles?lang=he", headers=H).json()
    assert he["items"][0]["missing"] is True and he["completeness"]["missing"] == 1
    srt = client.get("/api/projects/Show/subtitles.srt?lang=mixed", headers=H).text
    assert "00:00:00,200 --> 00:00:01,700" in srt and "hello there" in srt
    allc = client.get("/api/projects/Show/subtitles?lang=all", headers=H).json()["completeness"]
    assert set(allc) == {"mixed", "en", "he"}


def test_job_commands(client, sid):
    def preview(body):
        r = client.post("/api/jobs/preview", headers=H, json=body)
        assert r.status_code == 200, r.text
        return r.json()["argv"]
    argv = preview({"type": "subtitles", "source_id": sid, "start_sec": 1, "duration_sec": 3, "language": "he"})
    assert argv[1:3] == ["-m", "video_audio_cleaner.subtitles_cli"] and "transcribe" in argv
    assert argv[argv.index("--start") + 1] == "1" and argv[argv.index("--end") + 1] == "4"
    assert argv[argv.index("--language") + 1] == "he"
    argv = preview({"type": "subtitles_translate", "source_id": sid, "to": "en", "mode": "stale"})
    assert "translate" in argv and argv[argv.index("--mode") + 1] == "stale" and "--start" not in argv
    r = client.post("/api/jobs/preview", headers=H, json={"type": "subtitles_translate", "source_id": sid, "to": "fr"})
    assert r.status_code == 400


def test_glossary(client):
    r = client.put("/api/subtitles/glossary", headers=H, json=[{"he": "אופיר", "en": "Ofir"}, {"he": "", "en": ""}])
    assert r.json() == [{"he": "אופיר", "en": "Ofir"}]
    assert client.get("/api/subtitles/glossary", headers=H).json() == [{"he": "אופיר", "en": "Ofir"}]
