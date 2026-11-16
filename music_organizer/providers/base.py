"""数据源基类与数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .http import HttpClient, HttpError


class ProviderError(Exception):
    def __init__(self, provider: str, message: str):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.message = message


@dataclass
class TrackQuery:
    """一次检索的输入。

    ``pairs`` 是按可信度排序的 (标题, 歌手) 组合，匹配打分时取最高分，
    这样即使标签里的标题/歌手写反了也能命中。
    """

    path: str
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0.0
    pairs: List[Tuple[str, str]] = field(default_factory=list)
    versions: Set[str] = field(default_factory=set)
    filename: str = ""

    def __post_init__(self) -> None:
        if not self.pairs and (self.title or self.artist):
            self.pairs = [(self.title, self.artist)]
        if not self.title and self.pairs:
            self.title = self.pairs[0][0]
        if not self.artist and self.pairs:
            self.artist = self.pairs[0][1]

    @property
    def search_text(self) -> str:
        parts = [self.title]
        if self.artist and self.artist not in self.title:
            parts.append(self.artist)
        return " ".join(p for p in parts if p).strip()


@dataclass
class LyricsHit:
    provider: str
    title: str
    artist: str
    album: str = ""
    duration: float = 0.0
    text: str = ""
    synced: bool = False
    score: float = 0.0
    detail: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def provider_key(self) -> str:
        return str(self.extra.get("id") or f"{self.title}|{self.artist}|{self.duration}")


@dataclass
class CoverHit:
    provider: str
    title: str
    artist: str
    album: str = ""
    duration: float = 0.0
    url: str = ""
    width: int = 0
    height: int = 0
    score: float = 0.0
    detail: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def fallback_urls(self) -> List[str]:
        return list(self.extra.get("fallbacks") or [])


class BaseProvider:
    name = "base"
    label = "Base"
    homepage = ""

    def __init__(self, timeout: float = 8.0, retries: int = 1, proxy: str = ""):
        self.timeout = timeout
        self.retries = retries
        self.proxy = proxy
        self._client: Optional[HttpClient] = None

    @property
    def client(self) -> HttpClient:
        if self._client is None:
            self._client = HttpClient(timeout=self.timeout, retries=self.retries, proxy=self.proxy)
        return self._client

    def _error(self, message: str) -> ProviderError:
        return ProviderError(self.name, message)


class LyricsProvider(BaseProvider):
    supports_lyrics = True

    def search(self, query: TrackQuery, limit: int = 8) -> List[LyricsHit]:  # pragma: no cover - 接口
        raise NotImplementedError


class CoverProvider(BaseProvider):
    supports_cover = True

    def search(self, query: TrackQuery, limit: int = 8) -> List[CoverHit]:  # pragma: no cover - 接口
        raise NotImplementedError


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def join_artists(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    names: List[str] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                name = item.get("name") or item.get("artistName") or ""
            else:
                name = str(item)
            if name:
                names.append(str(name))
    return " / ".join(names)
