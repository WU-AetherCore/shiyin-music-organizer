"""QQ 音乐数据源（歌词 + 封面）。"""

from __future__ import annotations

import html
from typing import Any, Dict, List

from ..textutil import clean_lyrics_text, clean_spaces, core_title, count_timestamps, similarity
from .base import CoverHit, CoverProvider, LyricsHit, LyricsProvider, TrackQuery, join_artists, safe_float

_HEADERS = {
    "Referer": "https://y.qq.com/",
    "Origin": "https://y.qq.com",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}

_SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/search_for_qq_cp"
_LYRIC_URL = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"

_COVER_TEMPLATES = (
    "https://y.qq.com/music/photo_new/T002R800x800M000{mid}.jpg",
    "https://y.qq.com/music/photo_new/T002R500x500M000{mid}.jpg",
    "https://y.gtimg.cn/music/photo_new/T002R800x800M000{mid}.jpg",
)


class _QQBase:
    name = "qqmusic"
    label = "QQ 音乐"

    def _search_songs(self, query: TrackQuery, limit: int) -> List[Dict[str, Any]]:
        params = {
            "w": query.search_text,
            "g_tk": "5381",
            "loginUin": "0",
            "hostUin": "0",
            "format": "json",
            "inCharset": "utf8",
            "outCharset": "utf-8",
            "notice": "0",
            "platform": "yqq",
            "needNewCode": "0",
            "p": "1",
            "n": str(max(25, limit)),
        }
        payload = self.client.get_json(_SEARCH_URL, headers=_HEADERS, params=params)
        data = payload.get("data") or {}
        song = data.get("song") or (data.get("body") or {}).get("song") or {}
        return [item for item in (song.get("list") or []) if item.get("songmid")]

    @staticmethod
    def _title_match(query: TrackQuery, title: str) -> bool:
        if not title:
            return False
        for candidate, _artist in query.pairs:
            if candidate and similarity(core_title(title), core_title(candidate)) >= 0.5:
                return True
        return similarity(title, query.title) >= 0.5


class QQMusicLyricsProvider(_QQBase, LyricsProvider):
    def search(self, query: TrackQuery, limit: int = 8) -> List[LyricsHit]:
        songs = self._search_songs(query, limit)
        hits: List[LyricsHit] = []
        fetched = 0
        for song in songs:
            if fetched >= 3:
                break
            title = clean_spaces(song.get("songname"))
            if not self._title_match(query, title):
                continue
            fetched += 1
            mid = song.get("songmid")
            try:
                payload = self.client.get_json(
                    _LYRIC_URL,
                    headers=_HEADERS,
                    params={"songmid": mid, "format": "json", "nobase64": "1", "g_tk": "5381"},
                )
            except Exception:
                continue
            if payload.get("retcode") not in (0, "0", None):
                continue
            raw = payload.get("lyric") or ""
            trans = payload.get("trans") or ""
            text = html.unescape(str(raw))
            if trans:
                text = text.rstrip() + "\n" + html.unescape(str(trans))
            text = clean_lyrics_text(text)
            if len(text) < 12 or "纯音乐" in text[:40]:
                continue
            hits.append(
                LyricsHit(
                    provider=self.name,
                    title=title,
                    artist=join_artists(song.get("singer") or []),
                    album=clean_spaces(song.get("albumname") or ""),
                    duration=round(safe_float(song.get("interval")), 1),
                    text=text,
                    synced=count_timestamps(text) >= 3,
                    detail="QQ 音乐",
                    extra={"id": str(mid)},
                )
            )
        return hits


class QQMusicCoverProvider(_QQBase, CoverProvider):
    def search(self, query: TrackQuery, limit: int = 8) -> List[CoverHit]:
        songs = self._search_songs(query, limit)
        hits: List[CoverHit] = []
        for song in songs:
            mid = (song.get("albummid") or "").strip()
            if not mid:
                continue
            title = clean_spaces(song.get("songname"))
            if not self._title_match(query, title):
                continue
            urls = [template.format(mid=mid) for template in _COVER_TEMPLATES]
            hits.append(
                CoverHit(
                    provider=self.name,
                    title=title,
                    artist=join_artists(song.get("singer") or []),
                    album=clean_spaces(song.get("albumname") or ""),
                    duration=round(safe_float(song.get("interval")), 1),
                    url=urls[0],
                    detail="QQ 音乐",
                    extra={
                        "id": str(mid),
                        "fallbacks": urls[1:],
                        "referer": "https://y.qq.com/",
                    },
                )
            )
            if len(hits) >= limit:
                break
        return hits
