"""Web API 集成测试。"""

from __future__ import annotations

import io
import time
from pathlib import Path

from music_organizer import db
from music_organizer.config import verify_password

from conftest import make_audio, make_cover


def wait_job(client, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/jobs/{job_id}").get_json()
        if data["status"] in ("done", "error", "cancelled"):
            return data
        time.sleep(0.2)
    raise AssertionError("任务超时")


def test_overview_and_tracks(env):
    client = env["client"]
    overview = client.get("/api/overview").get_json()
    assert overview["initialized"] is True
    assert overview["stats"]["total"] == 3
    assert overview["stats"]["albums"] >= 1

    listing = client.get("/api/tracks?page_size=2").get_json()
    assert listing["total"] == 3
    assert len(listing["items"]) == 2

    filtered = client.get("/api/tracks?filter=missing_lyrics").get_json()
    assert filtered["total"] == 3


def test_track_detail_and_patch(env):
    client = env["client"]
    track = client.get("/api/tracks").get_json()["items"][0]
    detail = client.get(f"/api/tracks/{track['id']}").get_json()
    assert detail["id"] == track["id"]
    assert "lyrics" in detail
    assert detail["exists"] is True

    response = client.patch(f"/api/tracks/{track['id']}", json={"favorite": True, "offset_ms": 250})
    assert response.status_code == 200
    updated = db.get_track(track["id"], env["paths"])
    assert updated["favorite"] == 1
    assert updated["lyrics_offset_ms"] == 250


def test_lyrics_put_and_read(env):
    client = env["client"]
    track = client.get("/api/tracks").get_json()["items"][0]
    text = "\n".join(f"[00:{i:02d}.00]第 {i} 行" for i in range(8))
    response = client.put(f"/api/tracks/{track['id']}/lyrics", json={"text": text, "offset_ms": 0})
    assert response.status_code == 200
    data = client.get(f"/api/tracks/{track['id']}/lyrics").get_json()
    assert data["synced"] is True
    assert len(data["lines"]) == 8


def test_media_cover_and_stream(env):
    client = env["client"]
    track = client.get("/api/tracks").get_json()["items"][0]
    cover = client.get(f"/media/cover/{track['id']}")
    assert cover.status_code == 200
    assert cover.data

    stream = client.get(f"/media/stream/{track['id']}", headers={"Range": "bytes=0-99"})
    assert stream.status_code in (200, 206)
    assert len(stream.data) > 0


def test_scan_job_and_new_file(env):
    client = env["client"]
    new_path = env["music"] / "新歌 - 测试歌手.flac"
    make_audio(new_path, "flac", "新歌", "测试歌手", "", 2, 500)
    res = client.post("/api/scan", json={}).get_json()
    job = wait_job(client, res["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["added"] == 1
    listing = client.get("/api/tracks?q=新歌").get_json()
    assert listing["total"] == 1


def test_settings_password_and_auth(env, monkeypatch):
    client = env["client"]
    cfg = env["config"]
    settings = client.get("/api/settings").get_json()
    assert settings["config"]["repair"]["min_cover_size"] == 300
    assert "password_hash" not in settings["config"]["web"]

    res = client.put("/api/settings", json={"new_password": "test-pass"})
    assert res.status_code == 200
    assert verify_password("test-pass", cfg.get("web.password_hash"))

    # 当前会话已自动登录；新客户端应被拦截
    fresh = env["app"].test_client()
    denied = fresh.get("/api/overview")
    assert denied.status_code == 401
    login = fresh.post("/login", data={"password": "test-pass"})
    assert login.status_code == 302
    assert fresh.get("/api/overview").status_code == 200

    bad = fresh.post("/login", data={"password": "wrong"})
    assert bad.status_code == 200


def test_export_m3u(env):
    client = env["client"]
    res = client.get("/api/export/m3u")
    assert res.status_code == 200
    text = res.data.decode("utf-8")
    assert text.startswith("#EXTM3U")
    assert str(env["music"]) in text


def test_fs_list(env):
    client = env["client"]
    roots = client.get("/api/fs/list").get_json()
    assert roots["roots"]
    listing = client.get(f"/api/fs/list?path={env['music']}").get_json()
    assert listing["audio_count"] == 3
    assert listing["path"]


def test_duplicates_endpoint(env):
    client = env["client"]
    data = client.get("/api/duplicates").get_json()
    assert "groups" in data
    assert data["total"] >= 0


def test_repair_batch_dry_run(env, monkeypatch):
    client = env["client"]

    class FakeReport:
        def __init__(self):
            self.hits = []
            self.errors = []

    monkeypatch.setattr("music_organizer.providers.search_lyrics", lambda *a, **k: FakeReport())
    monkeypatch.setattr("music_organizer.providers.search_covers", lambda *a, **k: FakeReport())
    # repair 模块引用的是导入后的函数，单独打补丁
    monkeypatch.setattr("music_organizer.repair.provider_search_lyrics", lambda *a, **k: FakeReport())
    monkeypatch.setattr("music_organizer.repair.provider_search_covers", lambda *a, **k: FakeReport())

    res = client.post(
        "/api/repair/batch",
        json={"filter": "missing_any", "targets": ["lyrics", "cover"], "limit": 2, "dry_run": True},
    ).get_json()
    job = wait_job(client, res["job_id"])
    assert job["status"] == "done"
    assert job["total"] == 2
    assert job["skipped"] == 2


def test_upload_cover(env):
    client = env["client"]
    track = client.get("/api/tracks").get_json()["items"][0]
    data = make_cover((12, 200, 120))
    response = client.post(
        f"/api/tracks/{track['id']}/cover-upload",
        data={"file": (io.BytesIO(data), "cover.jpg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_json()
    detail = client.get(f"/api/tracks/{track['id']}").get_json()
    assert detail["cover"]["present"] is True
