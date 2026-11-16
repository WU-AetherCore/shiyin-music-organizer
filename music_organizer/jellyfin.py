"""Jellyfin 联动：修复完成后触发媒体库刷新。

Jellyfin 的 Web API 使用 API Key（控制台 → 高级 → API 密钥）鉴权，
刷新接口是 ``POST /Library/Refresh``，通常是 204 No Content。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

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
