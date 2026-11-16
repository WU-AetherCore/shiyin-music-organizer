"""修复服务测试：用假数据源验证写入、标签补全与撤销。"""

from __future__ import annotations

from pathlib import Path

import pytest

from music_organizer import audio, db
from music_organizer.providers.base import CoverHit, LyricsHit
from music_organizer.repair import RepairService

from conftest import make_cover

LYRICS = "\n".join(f"[00:{i:02d}.00]第 {i} 行歌词" for i in range(10))


@pytest.fixture()
def service(env):
    return RepairService(env["config"], env["paths"])


def test_repair_with_fake_providers(env, service, monkeypatch):
    track = next(t for t in db.all_tracks(env["paths"]) if t["title"] == "晴天")
    lyrics_hit = LyricsHit(
        provider="netease",
        title="晴天",
        artist="周杰伦",
        album="叶惠美",
        duration=float(track["duration"]),
        text=LYRICS,
        synced=True,
        score=99,
    )
    cover_hit = CoverHit(
        provider="netease",
        title="晴天",
        artist="周杰伦",
        album="叶惠美",
        duration=float(track["duration"]),
        url="https://p1.music.126.net/fake.jpg",
        score=99,
    )

    class FakeReport:
        def __init__(self, hits):
            self.hits = hits
            self.errors = []

    monkeypatch.setattr("music_organizer.repair.provider_search_lyrics", lambda *a, **k: FakeReport([lyrics_hit]))
    monkeypatch.setattr("music_organizer.repair.provider_search_covers", lambda *a, **k: FakeReport([cover_hit]))
    monkeypatch.setattr(
        "music_organizer.repair.fetch_cover_bytes",
        lambda hit, config: (make_cover((20, 30, 40)), "image/jpeg"),
    )

    outcome = service.repair(track, targets=["lyrics", "cover"], force=True)
    assert outcome.status == "fixed", outcome.to_dict()
    assert outcome.lyrics.get("applied") is True
    assert outcome.cover.get("applied") is True

    text, source = audio.read_lyrics(track["path"])
    assert "第 9 行歌词" in text
    assert source == "embed"
    assert audio.read_cover(track["path"]) is not None

    updated = db.get_track(track["id"], env["paths"])
    assert updated["has_lyrics"] == 1
    assert updated["has_cover"] == 1


def test_repair_skips_existing(env, service):
    track = next(t for t in db.all_tracks(env["paths"]) if t["title"] == "晴天")
    audio.update_media(track["path"], lyrics=LYRICS)
    outcome = service.repair(track, targets=["lyrics"], force=False)
    assert outcome.status == "skipped"


def test_tag_patch_fills_placeholder(env, service, monkeypatch):
    track = next(t for t in db.all_tracks(env["paths"]) if t["title"] == "晴天")
    # 清空专辑，模拟脏标签
    audio.update_media(track["path"], tags={"album": ""})
    fresh = db.get_track(track["id"], env["paths"])

    hit = LyricsHit(provider="qqmusic", title="晴天", artist="周杰伦", album="叶惠美", duration=2, text=LYRICS, synced=True, score=99)

    class FakeReport:
        hits = [hit]
        errors = []

    monkeypatch.setattr("music_organizer.repair.provider_search_lyrics", lambda *a, **k: FakeReport())
    monkeypatch.setattr("music_organizer.repair.provider_search_covers", lambda *a, **k: FakeReport([]))
    outcome = service.repair(fresh, targets=["tags"], force=False)
    assert outcome.status == "fixed"
    updated = db.get_track(track["id"], env["paths"])
    assert updated["album"] == "叶惠美"


def test_manual_lyrics_and_undo(env, service):
    track = next(t for t in db.all_tracks(env["paths"]) if t["title"] == "晴天")
    audio.update_media(track["path"], lyrics="旧的歌词\n")
    service.apply_lyrics_text(track, LYRICS, source="测试")
    text, _ = audio.read_lyrics(track["path"])
    assert "第 1 行歌词" in text

    refreshed = db.get_track(track["id"], env["paths"])
    result = service.undo(refreshed)
    assert Path(result["backup"]).exists()
    restored, _ = audio.read_lyrics(track["path"])
    assert "旧的歌词" in restored


def test_update_tags(env, service):
    track = next(t for t in db.all_tracks(env["paths"]) if t["title"] == "晴天")
    service.update_tags(track, {"album": "新专辑", "year": "2003"})
    refreshed = db.get_track(track["id"], env["paths"])
    assert refreshed["album"] == "新专辑"
    assert refreshed["year"] == "2003"
