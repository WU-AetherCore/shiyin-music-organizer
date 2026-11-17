from pathlib import Path
from types import SimpleNamespace

from music_organizer import jellyfin, navidrome


class FakeClient:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def get_json(self, url, headers=None, params=None):
        self.calls.append(("GET", url, params))
        return {"Items": self.items}

    def post(self, url, headers=None, data=b"", max_bytes=0):
        self.calls.append(("POST", url, data))
        return b""


def test_jellyfin_refreshes_item_and_upload_base64(env, monkeypatch):
    path = next(env["music"].glob("*.flac"))
    client = FakeClient([
        {
            "Id": "item-1",
            "AlbumId": "album-1",
            "Path": f"/media/home/WU/音乐/{path.name}",
        }
    ])
    monkeypatch.setattr(jellyfin, "_client", lambda config: client)
    monkeypatch.setattr(
        jellyfin.audio,
        "read_cover",
        lambda raw: SimpleNamespace(data=b"fake-jpeg", mime="image/jpeg"),
    )
    cfg = env["config"]
    cfg.update({"jellyfin": {"enabled": True, "url": "http://jellyfin", "api_key": "key", "refresh_after_fix": True}})
    ok, message = jellyfin.refresh_tracks(cfg, env["paths"], [str(path)])
    assert ok, message
    urls = [call[1] for call in client.calls]
    assert any("/Items/item-1/Refresh" in url for url in urls)
    assert any("/Items/item-1/Images/Primary" in url for url in urls)
    assert any("/Items/album-1/Images/Primary" in url for url in urls)


def test_navidrome_trigger_uses_full_scan(env, monkeypatch):
    calls = []

    class Client:
        def get_json(self, url, headers=None, params=None):
            calls.append((url, params))
            return {"subsonic-response": {"status": "ok"}}

    monkeypatch.setattr(navidrome, "_client", lambda config: Client())
    cfg = env["config"]
    cfg.update({"navidrome": {"enabled": True, "url": "http://navidrome:4533", "username": "u", "password": "p"}})
    ok, message = navidrome.trigger_rescan(cfg, env["paths"], full=True)
    assert ok, message
    assert calls and calls[0][1].get("fullScan") == "true"
