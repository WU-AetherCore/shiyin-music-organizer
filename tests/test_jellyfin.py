"""Jellyfin 联动测试。"""

from __future__ import annotations

from music_organizer import jellyfin
from music_organizer.config import Config


def test_jellyfin_defaults_and_validation():
    cfg = Config({})
    assert cfg.get("jellyfin.enabled") is False
    cfg.set("jellyfin.enabled", True)
    errors = cfg.validate()
    assert any("Jellyfin 地址" in e or "Jellyfin API Key" in e for e in errors)


def test_jellyfin_check_unconfigured():
    cfg = Config({"jellyfin": {"enabled": False}})
    ok, info = jellyfin.check(cfg)
    assert ok is False
    assert "未启用" in info["error"]


def test_jellyfin_check_and_refresh(monkeypatch):
    cfg = Config(
        {
            "jellyfin": {
                "enabled": True,
                "url": "http://127.0.0.1:8096",
                "api_key": "test-key",
            }
        }
    )

    class FakeClient:
        def get_json(self, url, headers=None, params=None):
            assert url.endswith("/System/Info")
            assert headers["X-Emby-Token"] == "test-key"
            return {"ServerName": "Jellyfin", "Version": "10.11.0"}

        def post(self, url, headers=None, data=b""):
            assert url.endswith("/Library/Refresh")
            assert headers["X-Emby-Token"] == "test-key"
            return b""

    monkeypatch.setattr(jellyfin, "_client", lambda config: FakeClient())
    ok, info = jellyfin.check(cfg)
    assert ok is True
    assert info["name"] == "Jellyfin"
    assert info["version"] == "10.11.0"

    ok, message = jellyfin.refresh(cfg)
    assert ok is True
    assert "Jellyfin" in message


def test_api_jellyfin_test(env, monkeypatch):
    client = env["client"]
    monkeypatch.setattr("music_organizer.jellyfin.check", lambda cfg, paths=None: (True, {"name": "Jellyfin", "version": "10.11"}))
    res = client.post("/api/jellyfin/test", json={"url": "http://127.0.0.1:8096", "api_key": "k"}).get_json()
    assert res["connected"] is True
    assert res["info"]["version"] == "10.11"
