"""在线数据源注册表与聚合检索。"""

from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from ..config import Config
from ..scoring import score_cover, score_lyrics
from ..paths import get_paths
from ..textutil import core_title, normalize_key, similarity
from .base import CoverHit, CoverProvider, LyricsHit, LyricsProvider, ProviderError, TrackQuery
from .http import HttpClient
from .itunes import ITunesCoverProvider
from .lrclib import LRCLibProvider
from .netease import NetEaseCoverProvider, NetEaseLyricsProvider
from .qqmusic import QQMusicCoverProvider, QQMusicLyricsProvider

LYRICS_PROVIDERS = {
    "netease": NetEaseLyricsProvider,
    "qqmusic": QQMusicLyricsProvider,
    "lrclib": LRCLibProvider,
    # iTunes 不提供歌词
}

COVER_PROVIDERS = {
    "netease": NetEaseCoverProvider,
    "qqmusic": QQMusicCoverProvider,
    "itunes": ITunesCoverProvider,
    # LRCLIB 不提供封面
}

PROVIDER_LABELS = {
    "netease": "网易云音乐",
    "qqmusic": "QQ 音乐",
    "lrclib": "LRCLIB",
    "itunes": "iTunes",
}

PROVIDER_HOMEPAGES = {
    "netease": "https://music.163.com",
    "qqmusic": "https://y.qq.com",
    "lrclib": "https://lrclib.net",
    "itunes": "https://music.apple.com",
}

_ALLOWED_IMAGE_HOSTS = (
    "music.126.net",
    "126.net",
    "y.qq.com",
    "gtimg.cn",
    "mzstatic.com",
    "itunes.apple.com",
    "lrclib.net",
)


@dataclass
class SearchReport:
    kind: str
    query: TrackQuery
    hits: List[Any] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)
    providers: List[str] = field(default_factory=list)
    elapsed: float = 0.0

    def to_dict(self, limit: int = 12) -> Dict[str, Any]:
        items = []
        for hit in self.hits[:limit]:
            item = {
                "provider": hit.provider,
                "provider_label": PROVIDER_LABELS.get(hit.provider, hit.provider),
                "title": hit.title,
                "artist": hit.artist,
                "album": hit.album,
                "duration": hit.duration,
                "score": hit.score,
                "detail": hit.detail,
            }
            if self.kind == "lyrics":
                item["text"] = hit.text
                item["synced"] = hit.synced
                item["lines"] = len(hit.text.strip().splitlines())
            else:
                item["url"] = hit.url
                item["width"] = hit.width
                item["height"] = hit.height
            items.append(item)
        return {
            "kind": self.kind,
            "query": {
                "title": self.query.title,
                "artist": self.query.artist,
                "album": self.query.album,
                "duration": self.query.duration,
                "search_text": self.query.search_text,
            },
            "hits": items,
            "errors": self.errors,
            "providers": self.providers,
            "elapsed": round(self.elapsed, 2),
        }


_report_cache: Dict[str, Tuple[float, SearchReport]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 300.0

# 数据源熔断：连续失败后临时跳过，避免每首歌都卡在同一个坏源上
_breaker: Dict[str, Dict[str, Any]] = {}
_breaker_lock = threading.Lock()
_BREAKER_THRESHOLD = 3
_BREAKER_SECONDS = 600.0


def _breaker_is_open(name: str) -> bool:
    with _breaker_lock:
        item = _breaker.get(name)
        if not item:
            return False
        if item.get("failures", 0) < _BREAKER_THRESHOLD:
            return False
        return time.time() < float(item.get("open_until", 0))


def _breaker_record(name: str, ok: bool, error: str = "") -> None:
    with _breaker_lock:
        item = _breaker.setdefault(name, {"failures": 0, "open_until": 0.0, "last_error": ""})
        if ok:
            item["failures"] = 0
            item["open_until"] = 0.0
            item["last_error"] = ""
        else:
            item["failures"] = int(item.get("failures", 0)) + 1
            item["last_error"] = error[:160]
            if item["failures"] >= _BREAKER_THRESHOLD:
                item["open_until"] = time.time() + _BREAKER_SECONDS


def breaker_status() -> Dict[str, Dict[str, Any]]:
    with _breaker_lock:
        return {name: dict(item) for name, item in _breaker.items()}


def _cache_key(kind: str, query: TrackQuery, providers: Iterable[str]) -> str:
    raw = "|".join(
        [
            kind,
            normalize_key(query.title),
            normalize_key(query.artist),
            str(int(query.duration or 0)),
            ",".join(providers),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> Optional[SearchReport]:
    with _cache_lock:
        item = _report_cache.get(key)
        if not item:
            return None
        created, report = item
        if time.time() - created > _CACHE_TTL:
            _report_cache.pop(key, None)
            return None
        return report


def _cache_put(key: str, report: SearchReport) -> None:
    with _cache_lock:
        if len(_report_cache) > 128:
            oldest = sorted(_report_cache.items(), key=lambda kv: kv[1][0])[:32]
            for k, _ in oldest:
                _report_cache.pop(k, None)
        _report_cache[key] = (time.time(), report)


def _provider_kwargs(config: Config) -> Dict[str, Any]:
    return {
        "timeout": float(config.get("providers.timeout") or 6),
        "retries": int(config.get("providers.retries") or 0),
        "proxy": str(config.get("providers.proxy") or ""),
    }


def _run_providers(
    names: List[str],
    registry: Dict[str, type],
    method: str,
    query: TrackQuery,
    config: Config,
    limit: int,
) -> Tuple[Dict[str, List[Any]], List[Dict[str, str]]]:
    kwargs = _provider_kwargs(config)
    parallel = bool(config.get("providers.parallel", True))
    timeout = float(config.get("providers.timeout") or 6)
    results: Dict[str, List[Any]] = {}
    errors: List[Dict[str, str]] = []

    active_names: List[str] = []
    for name in names:
        if _breaker_is_open(name):
            with _breaker_lock:
                detail = _breaker.get(name, {}).get("last_error", "")
            errors.append(
                {
                    "provider": name,
                    "label": PROVIDER_LABELS.get(name, name),
                    "error": f"连续失败，临时跳过（{detail}）",
                }
            )
        else:
            active_names.append(name)
    if not active_names:
        return results, errors

    def call(name: str) -> List[Any]:
        provider = registry[name](**kwargs)
        return getattr(provider, method)(query, limit=limit) or []

    if parallel and len(active_names) > 1:
        pool = ThreadPoolExecutor(max_workers=min(4, len(active_names)), thread_name_prefix="provider")
        futures = {pool.submit(call, name): name for name in active_names}
        try:
            for future in as_completed(futures, timeout=max(8.0, timeout * 2 + 2)):
                name = futures[future]
                try:
                    results[name] = future.result()
                    _breaker_record(name, True)
                except Exception as exc:
                    _breaker_record(name, False, str(exc))
                    errors.append({"provider": name, "label": PROVIDER_LABELS.get(name, name), "error": str(exc)})
        except Exception:
            for future, name in futures.items():
                if name not in results and name not in {e["provider"] for e in errors}:
                    _breaker_record(name, False, "超时")
                    errors.append({"provider": name, "label": PROVIDER_LABELS.get(name, name), "error": "超时"})
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    else:
        for name in active_names:
            try:
                results[name] = call(name)
                _breaker_record(name, True)
            except Exception as exc:
                _breaker_record(name, False, str(exc))
                errors.append({"provider": name, "label": PROVIDER_LABELS.get(name, name), "error": str(exc)})
    return results, errors


def search_lyrics(query: TrackQuery, config: Config, limit: int = 10) -> SearchReport:
    names = [name for name in (config.get("providers.lyrics") or []) if name in LYRICS_PROVIDERS]
    if not names:
        names = ["netease", "qqmusic", "lrclib"]
    key = _cache_key("lyrics", query, names)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    started = time.time()
    results, errors = _run_providers(names, LYRICS_PROVIDERS, "search", query, config, limit)
    hits: List[LyricsHit] = []
    for index, name in enumerate(names):
        bonus = max(0.0, 3.0 - index * 0.6)
        for hit in results.get(name, []):
            if not hit.text.strip():
                continue
            hit.score = score_lyrics(query, hit, bonus)
            if hit.score > 0:
                hits.append(hit)
    hits = _dedupe_lyrics(hits)
    report = SearchReport(
        kind="lyrics",
        query=query,
        hits=hits,
        errors=errors,
        providers=names,
        elapsed=time.time() - started,
    )
    if hits:
        _cache_put(key, report)
    return report


def search_covers(query: TrackQuery, config: Config, limit: int = 10) -> SearchReport:
    names = [name for name in (config.get("providers.cover") or []) if name in COVER_PROVIDERS]
    if not names:
        names = ["netease", "qqmusic", "itunes"]
    key = _cache_key("cover", query, names)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    started = time.time()
    results, errors = _run_providers(names, COVER_PROVIDERS, "search", query, config, limit)
    hits: List[CoverHit] = []
    for index, name in enumerate(names):
        bonus = max(0.0, 3.0 - index * 0.6)
        for hit in results.get(name, []):
            if not hit.url:
                continue
            hit.score = score_cover(query, hit, bonus)
            if hit.score > 0:
                hits.append(hit)
    hits = _dedupe_covers(hits)
    report = SearchReport(
        kind="cover",
        query=query,
        hits=hits,
        errors=errors,
        providers=names,
        elapsed=time.time() - started,
    )
    if hits:
        _cache_put(key, report)
    return report


def _dedupe_lyrics(hits: List[LyricsHit]) -> List[LyricsHit]:
    best: Dict[str, LyricsHit] = {}
    for hit in hits:
        key = normalize_key(hit.text[:240])
        existing = best.get(key)
        if existing is None or hit.score > existing.score:
            best[key] = hit
    return sorted(best.values(), key=lambda h: (h.score, h.synced), reverse=True)


def _dedupe_covers(hits: List[CoverHit]) -> List[CoverHit]:
    best: Dict[str, CoverHit] = {}
    for hit in hits:
        key = hit.url.split("?")[0]
        existing = best.get(key)
        if existing is None or hit.score > existing.score:
            best[key] = hit
    return sorted(best.values(), key=lambda h: h.score, reverse=True)


# ---------------------------------------------------------------------------
# 图片下载 / 预览
# ---------------------------------------------------------------------------

def is_allowed_image_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == allowed or host.endswith("." + allowed) for allowed in _ALLOWED_IMAGE_HOSTS)


def _image_cache_path(url: str) -> Any:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return get_paths().image_cache_dir / (digest + ".img")


def download_image(url: str, referer: str = "", config: Optional[Config] = None, use_cache: bool = True) -> Tuple[bytes, str]:
    """下载封面图（带磁盘缓存）。返回 ``(bytes, mime)``。"""
    if not is_allowed_image_url(url):
        raise ProviderError("image", "不允许的图片地址")
    cache_path = _image_cache_path(url)
    if use_cache and cache_path.exists():
        data = cache_path.read_bytes()
        if len(data) > 512:
            return data, _guess_mime(data)

    timeout = float(config.get("providers.timeout") or 6) if config else 8.0
    proxy = str(config.get("providers.proxy") or "") if config else ""
    client = HttpClient(timeout=timeout, retries=1, proxy=proxy)
    headers = {"Referer": referer} if referer else {}
    data = client.get_bytes(url, headers=headers, max_bytes=12 * 1024 * 1024)
    if len(data) < 512:
        raise ProviderError("image", "图片内容过小")
    mime = _guess_mime(data)
    if not mime.startswith("image/"):
        raise ProviderError("image", "返回内容不是图片")
    if use_cache:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(data)
        except OSError:
            pass
    return data, mime


def fetch_cover_bytes(hit: CoverHit, config: Config) -> Tuple[bytes, str]:
    """按候选的 URL + 回退地址依次下载。"""
    from .http import HttpError

    last_error: Optional[Exception] = None
    urls = [hit.url] + [u for u in hit.fallback_urls if u and u != hit.url]
    referer = str(hit.extra.get("referer") or "")
    for url in urls:
        if not is_allowed_image_url(url):
            continue
        try:
            return download_image(url, referer=referer, config=config)
        except Exception as exc:
            last_error = exc
            continue
    raise ProviderError(hit.provider, f"封面下载失败：{last_error}")


def _guess_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "application/octet-stream"


def provider_catalog() -> List[Dict[str, Any]]:
    items = []
    for name in ("netease", "qqmusic", "lrclib", "itunes"):
        items.append(
            {
                "name": name,
                "label": PROVIDER_LABELS.get(name, name),
                "lyrics": name in LYRICS_PROVIDERS,
                "cover": name in COVER_PROVIDERS,
                "homepage": PROVIDER_HOMEPAGES.get(name, ""),
            }
        )
    return items


def provider_self_test(config: Config) -> List[Dict[str, Any]]:
    """连通性自检：用一首常见歌曲试搜，返回每个源的状态。"""
    query = TrackQuery(
        path="",
        title="晴天",
        artist="周杰伦",
        album="",
        duration=0,
        pairs=[("晴天", "周杰伦")],
        filename="晴天 - 周杰伦.flac",
    )
    kwargs = _provider_kwargs(config)
    report: List[Dict[str, Any]] = []
    for name, cls in (("netease", NetEaseLyricsProvider), ("qqmusic", QQMusicLyricsProvider), ("lrclib", LRCLibProvider)):
        started = time.time()
        try:
            hits = cls(**kwargs).search(query, limit=5)
            report.append(
                {
                    "provider": name,
                    "label": PROVIDER_LABELS[name],
                    "ok": bool(hits),
                    "count": len(hits),
                    "elapsed": round(time.time() - started, 2),
                    "error": "" if hits else "无结果",
                }
            )
        except Exception as exc:
            report.append(
                {
                    "provider": name,
                    "label": PROVIDER_LABELS[name],
                    "ok": False,
                    "count": 0,
                    "elapsed": round(time.time() - started, 2),
                    "error": str(exc)[:160],
                }
            )
    return report
