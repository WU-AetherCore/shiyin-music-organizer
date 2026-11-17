"""Jellyfin 联动：修复完成后刷新媒体库并更新封面。"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import audio
from .config import Config
from .paths import AppPaths
from .providers.http import HttpClient, HttpError


def _base_url(config: Config) -> str:
    url = str(config.get("jellyfin.url") or "").strip().rstrip("/")
    return url


def _headers(config: Config) -> Dict[str, str]:
    key = str(config.get("jellyfin.api_key") or "").strip()
    return {
        "X-Emby-Token": key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _client(config: Config) -> HttpClient:
    return HttpClient(timeout=10, retries=1, proxy=str(config.get("providers.proxy") or ""))


def check(config: Config, paths: Optional[AppPaths] = None) -> Tuple[bool, Dict[str, Any]]:
    if not config.get("jellyfin.enabled"):
        return False, {"error": "未启用 Jellyfin 联动"}
    base = _base_url(config)
    if not base:
        return False, {"error": "未配置 Jellyfin 地址"}
    if not (config.get("jellyfin.api_key") or "").strip():
        return False, {"error": "未配置 Jellyfin API Key"}
    try:
        payload = _client(config).get_json(f"{base}/System/Info", headers=_headers(config))
    except HttpError as exc:
        if exc.status == 401:
            return False, {"error": "API Key 无效或没有权限"}
        return False, {"error": str(exc)[:200]}
    except Exception as exc:
        return False, {"error": str(exc)[:200]}
    return True, {
        "name": payload.get("ServerName") or payload.get("Name") or "Jellyfin",
        "version": payload.get("Version"),
        "id": payload.get("Id"),
    }


def refresh(config: Config, paths: Optional[AppPaths] = None) -> Tuple[bool, str]:
    if not config.get("jellyfin.enabled"):
        return False, "未启用 Jellyfin 联动"
    base = _base_url(config)
    if not base:
        return False, "未配置 Jellyfin 地址"
    try:
        _client(config).post(f"{base}/Library/Refresh", headers=_headers(config), data=b"")
    except HttpError as exc:
        if exc.status == 401:
            return False, "API Key 无效或没有权限"
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)[:200]
    return True, "已通知 Jellyfin 刷新媒体库"


def refresh_tracks(
    config: Config,
    paths: Optional[AppPaths],
    track_paths: Iterable[str],
) -> Tuple[bool, str]:
    """刷新具体曲目，并把内嵌封面重新推给 Jellyfin 的曲目/专辑。

    Jellyfin 的图片上传接口要求 body 为 Base64 文本，Content-Type 为图片类型；
    直接 POST 原始 JPEG 会返回 500。这里先刷新条目，再上传 Base64 封面，
    最后触发一次媒体库刷新，解决只刷新库但封面缓存不变的问题。
    """
    if not config.get("jellyfin.enabled"):
        return False, "未启用 Jellyfin 联动"
    base = _base_url(config)
    if not base:
        return False, "未配置 Jellyfin 地址"
    if not (config.get("jellyfin.api_key") or "").strip():
        return False, "未配置 Jellyfin API Key"
    client = _client(config)
    try:
        payload = client.get_json(
            f"{base}/Items?Recursive=true&IncludeItemTypes=Audio&Fields=Path,AlbumId,ParentId&Limit=5000",
            headers=_headers(config),
        )
    except Exception as exc:
        return False, f"读取 Jellyfin 曲目失败：{str(exc)[:160]}"
    items = payload.get("Items") or []
    music_dir = config.music_dir
    changed = 0
    errors: List[str] = []
    raw_paths = list(track_paths)
    update_album_cover = len(raw_paths) <= 3
    for raw_path in raw_paths:
        path = Path(str(raw_path or ""))
        if not path.is_file():
            continue
        relative = ""
        if music_dir:
            try:
                relative = str(path.relative_to(music_dir))
            except ValueError:
                relative = ""
        suffix = "/" + relative.replace(os.sep, "/") if relative else "/" + path.name
        item = next(
            (
                candidate
                for candidate in items
                if str(candidate.get("Path") or "").replace("\\", "/").casefold().endswith(suffix.casefold())
            ),
            None,
        )
        if not item:
            errors.append(f"{path.name}：Jellyfin 未找到条目")
            continue
        item_id = str(item.get("Id") or "")
        album_id = str(item.get("AlbumId") or item.get("ParentId") or "")
        try:
            client.post(
                f"{base}/Items/{item_id}/Refresh"
                "?MetadataRefreshMode=FullRefresh&ImageRefreshMode=FullRefresh"
                "&ReplaceAllImages=true&RegenerateTrickplay=false",
                headers=_headers(config),
                data=b"",
            )
            cover = audio.read_cover(str(path))
            if cover is not None:
                encoded = base64.b64encode(cover.data)
                image_headers = {
                    "X-Emby-Token": str(config.get("jellyfin.api_key") or "").strip(),
                    "Accept": "application/json",
                    "Content-Type": cover.mime or "image/jpeg",
                }
                client.post(f"{base}/Items/{item_id}/Images/Primary", headers=image_headers, data=encoded)
                if update_album_cover and album_id and album_id != item_id:
                    try:
                        client.post(f"{base}/Items/{album_id}/Images/Primary", headers=image_headers, data=encoded)
                    except Exception as exc:
                        errors.append(f"{path.name} 专辑封面：{str(exc)[:100]}")
            changed += 1
        except Exception as exc:
            errors.append(f"{path.name}：{str(exc)[:120]}")
    try:
        client.post(f"{base}/Library/Refresh", headers=_headers(config), data=b"")
    except Exception as exc:
        errors.append(f"媒体库刷新：{str(exc)[:100]}")
    message = f"已刷新 {changed} 首并更新封面"
    if errors:
        message += f"；{errors[0]}"
    return changed > 0, message
