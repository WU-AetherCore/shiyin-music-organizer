"""网易云音乐数据源（歌词 + 封面）。

使用的是音乐页面公开的 Web API，无需登录与 Cookie；在树莓派上通常可直连。
接口可能随官方调整而变化，失败时会自动降级到下一个数据源。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from ..textutil import (
    clean_lyrics_text,
    clean_spaces,
    core_title,
    count_timestamps,
    similarity,
    split_artists,
)
from .base import CoverHit, CoverProvider, LyricsHit, LyricsProvider, TrackQuery, join_artists, safe_float

_HEADERS = {
    "Referer": "https://music.163.com/",
    "Origin": "https://music.163.com",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}

_SEARCH_URL = "https://music.163.com/api/search/get"
_DETAIL_URL = "https://music.163.com/api/song/detail"
_LYRIC_URL = "https://music.163.com/api/song/lyric"


def _clean_pic_url(url: str) -> str:
    if not url:
        return ""
    url = url.replace("http://", "https://", 1)
    # 去掉缩略图参数，保留原图；下载端会再次校验尺寸
    return re.sub(r"\?param=.*$", "", url)


class _NetEaseBase:
    name = "netease"
    label = "网易云音乐"

    def _search_songs(self, query: TrackQuery, limit: int) -> List[Dict[str, Any]]:
        params = {
            "s": query.search_text,
            "type": 1,
            "limit": max(25, limit),
            "offset": 0,
            "total": "true",
        }
        payload = self.client.get_json(_SEARCH_URL, headers=_HEADERS, params=params)
        result = payload.get("result") or {}
        songs = result.get("songs") or []
        return [song for song in songs if song.get("id")]

    def _song_detail(self, song_id: int) -> Dict[str, Any]:
        payload = self.client.get_json(
            _DETAIL_URL,
            headers=_HEADERS,
            params={"ids": f"[{song_id}]"},
        )
        songs = payload.get("songs") or []
        return songs[0] if songs else {}

    @staticmethod
    def _title_match(query: TrackQuery, title: str) -> bool:
        if not title:
            return False
        for candidate, _artist in query.pairs:
            if not candidate:
                continue
            if similarity(core_title(title), core_title(candidate)) >= 0.5:
                return True
            if similarity(title, candidate) >= 0.6:
                return True
        return similarity(title, query.title) >= 0.5


class NetEaseLyricsProvider(_NetEaseBase, LyricsProvider):
    def search(self, query: TrackQuery, limit: int = 8) -> List[LyricsHit]:
        songs = self._search_songs(query, limit)
        hits: List[LyricsHit] = []
        fetched = 0
        for song in songs:
            if fetched >= 3:
                break
            title = clean_spaces(song.get("name"))
            if not self._title_match(query, title):
                continue
            fetched += 1
            try:
                payload = self.client.get_json(
                    _LYRIC_URL,
                    headers=_HEADERS,
                    params={"id": song["id"], "lv": 1, "kv": 1, "tv": -1},
                )
            except Exception:
                continue
            lrc = ((payload.get("lrc") or {}).get("lyric") or "").strip()
            translation = ((payload.get("tlyric") or {}).get("lyric") or "").strip()
            if not lrc or "暂无歌词" in lrc or len(lrc) < 12:
                continue
            text = lrc
            if translation and translation not in lrc:
                text = lrc.rstrip() + "\n" + translation
            text = clean_lyrics_text(text)
            artists = join_artists(song.get("artists") or [])
            album = (song.get("album") or {}).get("name") or ""
            hits.append(
                LyricsHit(
                    provider=self.name,
                    title=title,
                    artist=artists,
                    album=clean_spaces(album),
                    duration=round(safe_float(song.get("duration")) / 1000.0, 1),
                    text=text,
                    synced=count_timestamps(text) >= 3,
                    detail="网易云",
                    extra={"id": str(song.get("id"))},
                )
            )
        return hits


class NetEaseCoverProvider(_NetEaseBase, CoverProvider):
    def search(self, query: TrackQuery, limit: int = 8) -> List[CoverHit]:
        songs = self._search_songs(query, limit)
        hits: List[CoverHit] = []
        for song in songs[:5]:
            title = clean_spaces(song.get("name"))
            if not self._title_match(query, title):
                continue
            album = song.get("album") or {}
            pic = _clean_pic_url(album.get("picUrl") or album.get("blurPicUrl") or "")
            if not pic:
                try:
                    detail = self._song_detail(int(song["id"]))
                except Exception:
                    detail = {}
                album = detail.get("album") or album
                pic = _clean_pic_url(album.get("picUrl") or album.get("blurPicUrl") or "")
                if detail:
                    title = clean_spaces(detail.get("name")) or title
            if not pic:
                continue
            hits.append(
                CoverHit(
                    provider=self.name,
                    title=title,
                    artist=join_artists(song.get("artists") or []),
                    album=clean_spaces(album.get("name") or ""),
                    duration=round(safe_float(song.get("duration")) / 1000.0, 1),
                    url=pic,
                    detail="网易云",
                    extra={
                        "id": str(song.get("id")),
                        "fallbacks": [pic + "?param=800y800", pic + "?param=500y500"],
                        "referer": "https://music.163.com/",
                    },
                )
            )
            if len(hits) >= limit:
                break
        return hits


def _artist_words(text: str) -> List[str]:
    return [word for word in split_artists(text) if word]
