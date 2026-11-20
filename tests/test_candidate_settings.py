import json

from video_audio_cleaner.candidate_settings import candidate_settings


def _plan(tmp_path, separate_music=False):
    cand = {"atten": 20.0, "hiss": "profile:12", "declick": False, "separate_music": separate_music,
            "music_level_db": -20.0 if separate_music else None, "fidelity": 0.9, "speech_to_hiss": 10,
            "warble": 8, "clicks": 16, "hiss_gain_db": 10, "click_gain_db": 0, "score": 10,
            "qualifies": True, "noise_sample_suspect": False}
    plan = {"schema": 1, "method": 4, "source": "/videos/a b.mp4", "stem": "a b",
            "grid": {"source": "digital", "separate_music": separate_music},
            "output_flags": {"normalize": True, "target_lufs": "auto"},
            "criteria": {}, "windows": [{"label": "04m12s-06m47s", "start_sec": 252.0, "end_sec": 407.0,
                                          "picked": cand, "candidates": [cand]}]}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return path


def test_settings_say_where_each_value_came_from(tmp_path):
    d = candidate_settings(_plan(tmp_path), "04m12s-06m47s", 0)
    rows = {r["name"]: r for r in d["rows"]}
    assert rows["atten_lim_db"]["origin"] == "candidate" and rows["atten_lim_db"]["shown"] == "20 dB"
    assert rows["reduce_hiss"]["shown"] == "on" and rows["hiss_nr"]["origin"] == "candidate"
    assert rows["skip_first_n_sec"]["origin"] == "tuning"
    assert rows["skip_first_n_sec"]["shown"] == "4:12 into the video"
    assert rows["process_n_seconds"]["shown"] == "2:35"
    assert rows["target_lufs"]["shown"].startswith("automatic")
    assert rows["chunk_len_sec"]["origin"] == "default"
    # declick is off, so its amount does nothing and is not the candidate's choice
    assert not rows["declick_nr"]["applies"] and rows["declick_nr"]["origin"] == "default"
    assert not rows["music_level_db"]["applies"]                 # no music separation
    assert "--from_candidate" in d["render_command"] and "'/videos/a b.mp4'" in d["render_command"]
    assert "--atten_lim_db=20" in d["explicit_command"]


def test_music_level_is_the_candidates_when_music_is_separated(tmp_path):
    d = candidate_settings(_plan(tmp_path, separate_music=True), "04m12s-06m47s", 0)
    rows = {r["name"]: r for r in d["rows"]}
    assert rows["music_level_db"]["applies"] and rows["music_level_db"]["origin"] == "candidate"
    assert rows["music_level_db"]["shown"] == "-20 dB"
