"""查询构建与候选打分测试。"""

from __future__ import annotations

from pathlib import Path

from music_organizer.matcher import build_query
from music_organizer.providers.base import CoverHit, LyricsHit
from music_organizer.scoring import (
    choose_best_cover,
    choose_best_lyrics,
    confidence,
    score_cover,
    score_lyrics,
)

TEXT = "\n".join(f"[00:{i:02d}.00]第 {i} 行歌词" for i in range(10))


def _query():
    return build_query(
        {
            "path": str(Path("/music/1-周杰伦/晴天 - 周杰伦.flac")),
            "filename": "晴天 - 周杰伦.flac",
            "title": "晴天",
            "artist": "周杰倫",
            "album": "叶惠美",
            "duration": 269.0,
        }
    )


def test_build_query_uses_tags_and_filename():
    query = _query()
    assert query.title == "晴天"
    assert query.artist == "周杰倫"
    assert ("晴天", "周杰倫") in query.pairs
    # 文件夹里的歌手也作为候选（繁简会被归一化，因此只比较归一化后的值）
    from music_organizer.textutil import normalize_key

    assert any(normalize_key(artist) == normalize_key("周杰伦") for _title, artist in query.pairs)
    assert "晴天" in query.search_text


def test_build_query_swapped_tags():
    query = build_query(
        {
            "path": "/music/其他/song.flac",
            "filename": "晴天 - 周杰伦.flac",
            "title": "周杰伦",
            "artist": "晴天",
            "album": "",
            "duration": 269,
        }
    )
    assert ("晴天", "周杰伦") in query.pairs


def test_score_correct_hit():
    query = _query()
    hit = LyricsHit(provider="qqmusic", title="晴天", artist="周杰伦", album="叶惠美", duration=270, text=TEXT, synced=True)
    score = score_lyrics(query, hit)
    assert score >= 90
    assert confidence(score) == "high"
    hit.score = score
    assert choose_best_lyrics([hit], 55) is not None


def test_score_traditional_simplified_artist():
    query = _query()  # 歌手是繁体“周杰倫”
    hit = LyricsHit(provider="lrclib", title="晴天", artist="周杰伦", album="叶惠美", duration=269, text=TEXT, synced=True)
    assert score_lyrics(query, hit) >= 90


def test_wrong_artist_is_rejected():
    query = _query()
    hit = LyricsHit(provider="qqmusic", title="晴天", artist="林俊杰", album="", duration=270, text=TEXT, synced=True)
    score = score_lyrics(query, hit)
    assert score < 55
    assert choose_best_lyrics([hit], 55) is None


def test_live_version_penalised():
    query = _query()
    hit = LyricsHit(provider="qqmusic", title="晴天 (Live)", artist="周杰伦", album="叶惠美", duration=270, text=TEXT, synced=True)
    assert score_lyrics(query, hit) < 55


def test_wrong_title_zero():
    query = _query()
    hit = LyricsHit(provider="qqmusic", title="七里香", artist="周杰伦", album="七里香", duration=299, text=TEXT, synced=True)
    assert score_lyrics(query, hit) < 55


def test_duration_penalty():
    query = _query()
    good = LyricsHit(provider="qqmusic", title="晴天", artist="周杰伦", album="叶惠美", duration=270, text=TEXT, synced=True)
    bad = LyricsHit(provider="qqmusic", title="晴天", artist="周杰伦", album="叶惠美", duration=200, text=TEXT, synced=True)
    assert score_lyrics(query, bad) < score_lyrics(query, good)


def test_cover_scoring():
    query = _query()
    hit = CoverHit(provider="netease", title="晴天", artist="周杰伦", album="叶惠美", duration=269, url="https://example.com/a.jpg")
    hit.score = score_cover(query, hit)
    assert hit.score >= 85
    assert choose_best_cover([hit], 55) is not None
    wrong = CoverHit(provider="netease", title="晴天", artist="林俊杰", url="https://example.com/b.jpg")
    assert score_cover(query, wrong) < 55

def test_placeholder_track_number_uses_filename():
    query = build_query(
        {
            "path": "/music/其他/客厅狂欢 - 王源.flac",
            "filename": "客厅狂欢 - 王源.flac",
            "title": "track 04",
            "artist": "王源",
            "album": "客厅狂欢",
            "duration": 193.1,
        }
    )
    assert query.title == "客厅狂欢"
    assert ("客厅狂欢", "王源") in query.pairs


def test_live_album_context_scores_live_hit():
    query = build_query(
        {
            "path": "/music/其他/河流 - 川川.flac",
            "filename": "河流 - 川川.flac",
            "title": "河流",
            "artist": "川川",
            "album": "2023中国好声音 第3期",
            "duration": 320.6,
        }
    )
    assert "live" in query.versions
    hit = LyricsHit(
        provider="qqmusic",
        title="河流 (Live)",
        artist="川川南",
        album="2023中国好声音 第3期",
        duration=320,
        text=TEXT,
        synced=True,
    )
    assert score_lyrics(query, hit) >= 80
    hit.score = score_lyrics(query, hit)
    assert choose_best_lyrics([hit], 55) is not None


def test_metadata_patch_only_placeholder():
    from music_organizer.config import Config
    from music_organizer.repair import RepairService

    service = RepairService(Config({}))
    hit = LyricsHit(provider="qqmusic", title="客厅狂欢", artist="王源", album="客厅狂欢", duration=193.1, text=TEXT, synced=True)
    patch = service._metadata_patch({"title": "track 04", "artist": "王源", "album": "客厅狂欢"}, hit)
    assert patch == {"title": "客厅狂欢"}
    # 已有有效标题不会被覆盖
    assert service._metadata_patch({"title": "晴天", "artist": "周杰伦", "album": "叶惠美"}, hit) == {}

def test_cover_fallback_accepts_album_match():
    from music_organizer.config import Config
    from music_organizer.providers.base import CoverHit, TrackQuery
    from music_organizer.repair import RepairService

    service = RepairService(Config({}))
    query = TrackQuery(path="", title="Legends Never Die", artist="Alan Walker", album="Legends Never Die", duration=167.2)
    good = CoverHit(provider="qqmusic", title="Legends Never Die (Alan Walker Remix)", artist="Alan Walker", album="Legends Never Die", url="https://example.com/a.jpg", score=52.4)
    wrong = CoverHit(provider="qqmusic", title="Legends Never Die (Remix)", artist="Other", album="Other Album", url="https://example.com/b.jpg", score=52.4)
    assert service._cover_acceptable(good, query, 55) is True
    assert service._cover_acceptable(wrong, query, 55) is False


def test_metadata_patch_falls_back_to_filename():
    from music_organizer.config import Config
    from music_organizer.repair import RepairService

    service = RepairService(Config({}))
    patch = service._metadata_patch(
        {
            "path": "/music/1-周杰伦/双截棍 - 周杰伦.flac",
            "filename": "双截棍 - 周杰伦.flac",
            "title": "",
            "artist": "",
            "album": "",
        },
        None,
    )
    assert patch["title"] == "双截棍"
    assert patch["artist"] == "周杰伦"

    spaced = service._metadata_patch(
        {
            "path": "/music/1-周杰伦/娘 子 - 周杰伦.flac",
            "filename": "娘 子 - 周杰伦.flac",
            "title": "track 04",
            "artist": "kuwo",
            "album": "",
        },
        None,
    )
    assert spaced["title"] == "娘子"
    assert spaced["artist"] == "周杰伦"
