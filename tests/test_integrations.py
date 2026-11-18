from pathlib import Path
from types import SimpleNamespace

from music_organizer import jellyfin, navidrome


def test_notify_records_last_sync_status(env, monkeypatch):
    from music_organizer import jobs

    monkeypatch.setattr(navidrome, "trigger_rescan", lambda config, paths, full=True: (True, "Navidrome ok"))
    monkeypatch.setattr(jellyfin, "refresh", lambda config, paths: (True, "Jellyfin ok"))
    cfg = env["config"]
    cfg.update({
        "navidrome": {"enabled": True, "rescan_after_fix": True},
        "jellyfin": {"enabled": True, "refresh_after_fix": True},
    })
    messages = jobs.notify_media_servers(cfg, env["paths"])
    assert any("Navidrome" in item for item in messages)
    assert cfg.get("navidrome.last_sync_ok") is True
    assert cfg.get("jellyfin.last_sync_ok") is True
    assert cfg.get("jellyfin.last_sync_at")


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


def test_navidrome_refreshes_absent_artwork_via_docker(env, monkeypatch):
    commands = []

    class Proc:
        returncode = 0
        stdout = "2 queued"
        stderr = ""

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return Proc()

    monkeypatch.setattr(navidrome.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(navidrome.subprocess, "run", fake_run)
    cfg = env["config"]
    cfg.update({"navidrome": {"enabled": True, "refresh_artwork": True, "container": "wu-navidrome"}})
    ok, message = navidrome.refresh_missing_artwork(cfg, env["paths"])
    assert ok, message
    assert any("artwork" in command and "reprocess" in command for command in commands)


def test_navidrome_remaps_missing_by_title_artist(env, monkeypatch):
    from music_organizer import db

    track = next(t for t in db.all_tracks(env["paths"]) if t.get("title") and t.get("artist"))
    missing = [{
        "id": "missing-1",
        "title": track["title"],
        "artist": track["artist"],
        "album": track["album"],
        "path": "旧目录/old.flac",
    }]
    commands = []

    class Proc:
        def __init__(self, stdout="", returncode=0):
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        if "missing" in cmd and "list" in cmd:
            import json
            return Proc(json.dumps(missing))
        return Proc("ok")

    monkeypatch.setattr(navidrome.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(navidrome.subprocess, "run", fake_run)
    monkeypatch.setattr(navidrome, "scan_status", lambda config: {"scanning": False})
    cfg = env["config"]
    cfg.update({"navidrome": {"enabled": True, "remap_missing": True, "container": "wu-navidrome"}})
    ok, message = navidrome.remap_missing_files(cfg, env["paths"])
    assert ok, message
    assert "成功 1" in message, (message, commands, track)
    assert any("missing" in command and "fix" in command for command in commands)
