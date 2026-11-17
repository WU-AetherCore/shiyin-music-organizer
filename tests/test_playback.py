import shutil


def test_overview_exposes_playback_quality(env):
    client = env["client"]
    data = client.get("/api/overview").get_json()
    assert data["playback"]["quality"] == "original"


def test_playback_quality_can_be_saved(env):
    client = env["client"]
    saved = client.put("/api/settings", json={"config": {"playback": {"quality": "mp3_192"}}})
    assert saved.status_code == 200
    assert client.get("/api/settings").get_json()["config"]["playback"]["quality"] == "mp3_192"
    assert client.get("/api/overview").get_json()["playback"]["quality"] == "mp3_192"


def test_auto_fix_timing_toggle_is_saved(env):
    client = env["client"]
    saved = client.put("/api/settings", json={"config": {"automation": {"auto_fix_timing": False}}})
    assert saved.status_code == 200
    assert client.get("/api/settings").get_json()["config"]["automation"]["auto_fix_timing"] is False


def test_stream_defaults_to_original(env):
    client = env["client"]
    track = client.get("/api/tracks").get_json()["items"][0]
    res = client.get(f"/media/stream/{track['id']}")
    assert res.status_code == 200
    assert res.mimetype in ("audio/flac", "audio/mpeg", "application/octet-stream")


def test_stream_can_transcode(env):
    if not shutil.which("ffmpeg"):
        return
    client = env["client"]
    track = client.get("/api/tracks").get_json()["items"][0]
    res = client.get(f"/media/stream/{track['id']}?quality=mp3_128")
    assert res.status_code == 200
    assert res.mimetype == "audio/mpeg"
    assert len(res.data) > 1000
