"""iTunes / Apple Music 数据源（仅封面）。

曲库全球化程度高，封面通常无水印、清晰度好，适合作为网易云 / QQ 的补充。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from ..textutil import clean_spaces, core_title, similarity
from .base import CoverHit, CoverProvider, TrackQuery, safe_float

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_SEARCH_URL = "https://itunes.apple.com/search"
_ARTWORK_RE = re.compile(r"/\d+x\d+bb\.(jpg|png)$", re.IGNORECASE)


def _large_artwork(url: str, size: int = 1200) -> str:
    if not url:
        return ""
    return _ARTWORK_RE.sub(f"/{size}x{size}bb.jpg", url)


class ITunesCoverProvider(CoverProvider):
    name = "itunes"
    label = "iTunes"

    def search(self, query: TrackQuery, limit: int = 8) -> List[CoverHit]:
        term_parts = [query.title]
        if query.artist:
            term_parts.append(query.artist)
        term = " ".join(part for part in term_parts if part).strip()
        if not term:
            return []
        params = {
            "term": term,
            "media": "music",
            "entity": "song",
            "limit": str(max(10, limit)),
            "country": "CN",
        }
        payload = self.client.get_json(_SEARCH_URL, headers=_HEADERS, params=params)
        results = payload.get("results") if isinstance(payload, dict) else None
        hits: List[CoverHit] = []
        for item in results or []:
            art = item.get("artworkUrl100") or item.get("artworkUrl60") or ""
            if not art:
                continue
            title = clean_spaces(item.get("trackName"))
            if not title:
                continue
            if not any(
                similarity(core_title(title), core_title(candidate)) >= 0.45
                for candidate, _artist in query.pairs
                if candidate
            ) and similarity(title, query.title) < 0.45:
                continue
            big = _large_artwork(art, 1200)
            hits.append(
                CoverHit(
                    provider=self.name,
                    title=title,
                    artist=clean_spaces(item.get("artistName")),
                    album=clean_spaces(item.get("collectionName")),
                    duration=round(safe_float(item.get("trackTimeMillis")) / 1000.0, 1),
                    url=big,
                    detail="iTunes",
                    extra={
                        "id": str(item.get("trackId") or ""),
                        "fallbacks": [_large_artwork(art, 600), art],
                    },
                )
            )
            if len(hits) >= limit:
                break
        return hits
