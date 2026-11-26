"""歌手归一化、合并与头像缓存。"""

from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .paths import AppPaths
from .textutil import clean_spaces, normalize_key, to_simplified

_CACHE_LOCK = threading.Lock()


def display_artist(name: str) -> str:
    value = clean_spaces(to_simplified(clean_spaces(name)))
    return value or "未知歌手"


def artist_key(name: str) -> str:
    value = display_artist(name).casefold()
    return "".join(ch for ch in value if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _merge_key(key: str, keys: Iterable[str]) -> str:
    # G.E.M.邓紫棋 / G.E.M. 邓紫棋 这类前缀差异合并到短名；
    # 只接受“拉丁字母前缀 + 至少 3 个中文字符”的组合，避免误并正常歌手。
    for other in keys:
        if other == key or len(other) < 3 or not key.endswith(other):
            continue
        prefix = key[: -len(other)]
        if prefix and all(ord(ch) < 128 for ch in prefix):
            return other
    # 拉丁字母歌手名的轻微拼写差异（如 LamaMaboo / LamaManboo）
    if len(key) >= 5 and key.isascii():
        for other in keys:
            if other == key or len(other) < 5 or not other.isascii():
                continue
            if SequenceMatcher(None, key, other).ratio() >= 0.9:
                return other if len(other) <= len(key) else key
    return key


def group_artist_tracks(tracks: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    raw: Dict[str, Dict[str, Any]] = {}
    for track in tracks:
        name = display_artist(str(track.get("artist") or "未知歌手"))
        key = artist_key(name)
        if not key:
            key = "未知歌手"
        item = raw.setdefault(key, {"key": key, "name": name, "names": {}, "tracks": []})
        item["tracks"].append(track)
        item["names"][name] = int(item["names"].get(name, 0)) + 1

    keys = list(raw)
    merged: Dict[str, Dict[str, Any]] = {}
    for key, item in raw.items():
        target = _merge_key(key, keys)
        bucket = merged.setdefault(target, {"key": target, "names": {}, "tracks": []})
        bucket["tracks"].extend(item["tracks"])
        for name, count in item["names"].items():
            bucket["names"][name] = int(bucket["names"].get(name, 0)) + int(count)

    for key, item in merged.items():
        name = max(item["names"].items(), key=lambda pair: (pair[1], -len(pair[0])))[0]
        item["name"] = display_artist(name)
    return merged


def artist_cover(track: Dict[str, Any], base: str = "") -> str:
    # track_json 的 cover_url 由 API 生成；这里只保留相对/绝对 URL。
    return str(track.get("cover_url") or "")


def _cache_path(paths: AppPaths) -> Path:
    return paths.cache_dir / "artist-images.json"


def load_image_cache(paths: AppPaths) -> Dict[str, str]:
    try:
        raw = json.loads(_cache_path(paths).read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in raw.items() if v}
    except Exception:
        return {}


def _save_image_cache(paths: AppPaths, data: Dict[str, str]) -> None:
    try:
        path = _cache_path(paths)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    except Exception:
        pass


def fetch_artist_image(name: str, paths: AppPaths) -> str:
    key = artist_key(name)
    if not key:
        return ""
    with _CACHE_LOCK:
        cache = load_image_cache(paths)
        if cache.get(key):
            return cache[key]
    query = display_artist(name)
    url = "https://music.163.com/api/search/get/web?csrf_token=&s=" + urllib.parse.quote(query) + "&type=100&offset=0&total=true&limit=5"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com/"})
    try:
        with urllib.request.urlopen(req, timeout=6) as response:
            payload = json.loads(response.read().decode("utf-8", "ignore"))
    except Exception:
        return ""
    artists = (payload.get("result") or {}).get("artists") or []
    wanted = normalize_key(query)
    chosen = ""
    for artist in artists:
        candidate = str(artist.get("name") or "")
        pic = str(artist.get("picUrl") or "")
        if not pic.startswith("http"):
            continue
        if normalize_key(candidate) == wanted or normalize_key(candidate).startswith(wanted):
            chosen = pic
            break
    if not chosen and artists:
        pic = str(artists[0].get("picUrl") or "")
        if pic.startswith("http"):
            chosen = pic
    if chosen:
        with _CACHE_LOCK:
            cache = load_image_cache(paths)
            cache[key] = chosen
            _save_image_cache(paths, cache)
    return chosen
