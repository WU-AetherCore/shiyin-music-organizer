"""候选打分：标题是硬条件，歌手 / 时长 / 专辑 / 版本作为加权。

单独成模块是为了打破 ``matcher`` 与 ``providers`` 之间的循环依赖。
"""

from __future__ import annotations

from typing import List, Optional

from .providers.base import CoverHit, LyricsHit, TrackQuery
from .textutil import core_title, detect_versions, normalize_key, similarity, split_artists


def _title_score(hit_title: str, want_title: str) -> float:
    if not hit_title or not want_title:
        return 0.0
    hit_core = core_title(hit_title)
    want_core = core_title(want_title) or want_title
    if normalize_key(hit_core) == normalize_key(want_core):
        return 55.0
    if normalize_key(hit_title) == normalize_key(want_title):
        return 52.0
    if want_core and (
        normalize_key(want_core) in normalize_key(hit_core)
        or normalize_key(hit_core) in normalize_key(want_core)
    ):
        return 38.0
    sim = similarity(hit_core, want_core)
    if sim >= 0.86:
        return 30.0
    if sim >= 0.72:
        return 18.0
    if sim >= 0.6:
        return 8.0
    return 0.0


def _artist_score(hit_artist: str, want_artist: str) -> float:
    if not hit_artist or not want_artist:
        return 0.0
    hit_artists = split_artists(hit_artist)
    want_artists = split_artists(want_artist)
    if not hit_artists or not want_artists:
        return 0.0

    best = 0.0
    for want in want_artists:
        nw = normalize_key(want)
        if not nw:
            continue
        for hit in hit_artists:
            nh = normalize_key(hit)
            if not nh:
                continue
            if nh == nw:
                best = max(best, 20.0)
            elif nw in nh or nh in nw:
                best = max(best, 14.0)
            elif similarity(nh, nw) >= 0.85:
                best = max(best, 10.0)

    if best == 0.0:
        joined_hit = normalize_key(hit_artist)
        joined_want = normalize_key(want_artist)
        if any(normalize_key(item) in joined_hit for item in want_artists if normalize_key(item)):
            best = 12.0
        elif joined_want in joined_hit or joined_hit in joined_want:
            best = 8.0
    return best


def _album_score(hit_album: str, want_album: str) -> float:
    if not hit_album or not want_album:
        return 0.0
    if normalize_key(hit_album) == normalize_key(want_album):
        return 5.0
    if (
        normalize_key(want_album) in normalize_key(hit_album)
        or normalize_key(hit_album) in normalize_key(want_album)
    ):
        return 3.0
    return 0.0


def _duration_score(hit_duration: float, want_duration: float) -> float:
    if not hit_duration or not want_duration:
        return 0.0
    diff = abs(float(hit_duration) - float(want_duration))
    if diff <= 2:
        return 15.0
    if diff <= 5:
        return 10.0
    if diff <= 10:
        return 4.0
    if diff > 25:
        return -12.0
    return 0.0


def _version_score(hit_title: str, query: TrackQuery) -> float:
    hit_versions = detect_versions(hit_title)
    want = set(query.versions or set())
    score = 0.0
    if "instrumental" in hit_versions and "instrumental" not in want:
        score -= 45.0
    extra = hit_versions - want
    missing = want - hit_versions
    if extra:
        score -= 45.0 * len(extra)
    if missing:
        score -= 15.0 * len(missing)
    if want and want == hit_versions:
        score += 5.0
    return score


def score_lyrics(query: TrackQuery, hit: LyricsHit, provider_bonus: float = 0.0) -> float:
    best = 0.0
    best_artist = 0.0
    for title, artist in query.pairs or [(query.title, query.artist)]:
        artist_score = _artist_score(hit.artist, artist)
        total = _title_score(hit.title, title) + artist_score
        if total > best:
            best = total
            best_artist = artist_score
    if best <= 0:
        return 0.0
    total = best
    total += _album_score(hit.album, query.album)
    total += _duration_score(hit.duration, query.duration)
    total += _version_score(hit.title, query)
    total += provider_bonus
    # 查询里有歌手、候选也有歌手，却完全对不上：很可能是同名不同人的歌
    if query.artist and hit.artist and best_artist <= 0:
        total -= 25.0
    if not hit.synced:
        total -= 4.0
    if len(hit.text.strip().splitlines()) < 4:
        total -= 10.0
    return round(max(0.0, total), 1)


def score_cover(query: TrackQuery, hit: CoverHit, provider_bonus: float = 0.0) -> float:
    best = 0.0
    best_artist = 0.0
    for title, artist in query.pairs or [(query.title, query.artist)]:
        artist_score = _artist_score(hit.artist, artist)
        total = _title_score(hit.title, title) + artist_score
        if total > best:
            best = total
            best_artist = artist_score
    if best <= 0:
        return 0.0
    total = best
    total += _album_score(hit.album, query.album)
    total += _duration_score(hit.duration, query.duration)
    total += _version_score(hit.title, query)
    total += provider_bonus
    if query.artist and hit.artist and best_artist <= 0:
        total -= 25.0
    return round(max(0.0, total), 1)


def confidence(score: float) -> str:
    if score >= 80:
        return "high"
    if score >= 62:
        return "medium"
    return "low"


def choose_best_lyrics(hits: List[LyricsHit], min_score: float = 55.0) -> Optional[LyricsHit]:
    for hit in hits:
        if hit.score >= min_score and hit.text.strip():
            return hit
    return None


def choose_best_cover(hits: List[CoverHit], min_score: float = 55.0) -> Optional[CoverHit]:
    for hit in hits:
        if hit.score >= min_score and hit.url:
            return hit
    return None
