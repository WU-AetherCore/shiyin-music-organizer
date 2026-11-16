"""LRCLIB 数据源（开放歌词库，带时间轴，适合欧美 / 日韩曲目）。"""

from __future__ import annotations

from typing import Any, Dict, List

from ..textutil import clean_lyrics_text, clean_spaces, core_title, count_timestamps, similarity
from ..version import USER_AGENT
from .base import LyricsHit, LyricsProvider, TrackQuery, safe_float

_HEADERS = {
    "User-Agent": f"{USER_AGENT} (https://lrclib.net)",
    "Accept": "application/json",
}

_BASE = "https://lrclib.net/api"


class LRCLibProvider(LyricsProvider):
    name = "lrclib"
    label = "LRCLIB"

    def _get_exact(self, query: TrackQuery) -> List[Dict[str, Any]]:
        if not query.title:
            return []
        params: Dict[str, Any] = {"track_name": query.title}
        if query.artist:
            params["artist_name"] = query.artist
        if query.album:
            params["album_name"] = query.album
        if query.duration > 0:
            params["duration"] = int(round(query.duration))
        try:
            payload = self.client.get_json(f"{_BASE}/get", headers=_HEADERS, params=params)
        except Exception:
            return []
        return [payload] if isinstance(payload, dict) and payload.get("trackName") else []

    def _search(self, query: TrackQuery, limit: int) -> List[Dict[str, Any]]:
        params = {"track_name": query.title}
        if query.artist:
            params["artist_name"] = query.artist
        try:
            payload = self.client.get_json(
                f"{_BASE}/search", headers=_HEADERS, params=params
            )
        except Exception:
            return []
        return payload if isinstance(payload, list) else []

    def search(self, query: TrackQuery, limit: int = 8) -> List[LyricsHit]:
        records: List[Dict[str, Any]] = []
        records.extend(self._get_exact(query))
        if len(records) < 2:
            records.extend(self._search(query, limit))

        hits: List[LyricsHit] = []
        seen = set()
        for record in records:
            synced = (record.get("syncedLyrics") or "").strip()
            plain = (record.get("plainLyrics") or "").strip()
            text = synced or plain
            if not text or len(text) < 12:
                continue
            title = clean_spaces(record.get("trackName"))
            if not title:
                continue
            if not any(
                similarity(core_title(title), core_title(candidate)) >= 0.5
                for candidate, _artist in query.pairs
                if candidate
            ) and similarity(title, query.title) < 0.5:
                continue
            text = clean_lyrics_text(text)
            key = text[:160]
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                LyricsHit(
                    provider=self.name,
                    title=title,
                    artist=clean_spaces(record.get("artistName")),
                    album=clean_spaces(record.get("albumName")),
                    duration=round(safe_float(record.get("duration")), 1),
                    text=text,
                    synced=bool(synced) and count_timestamps(text) >= 3,
                    detail="LRCLIB",
                    extra={"id": str(record.get("id") or title)},
                )
            )
        return hits
