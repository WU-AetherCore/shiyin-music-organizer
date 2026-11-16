"""Navidrome 联动：通过 Subsonic API 触发媒体库重新扫描。

整理插件把歌词/封面写回文件后，Navidrome 不会自动感知，需要一次 rescan。
配置里填好地址与账号即可在设置页一键触发，也支持“修复完成后自动扫描”。
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
from typing import Any, Dict, Optional, Tuple

from .config import Config
from .paths import AppPaths, get_paths
from .providers.http import HttpClient, HttpError


def _client(config: Config) -> HttpClient:
    return HttpClient(timeout=8, retries=1, proxy=str(config.get("providers.proxy") or ""))


def _auth_params(config: Config) -> Dict[str, str]:
    username = str(config.get("navidrome.username") or "")
    password = str(config.get("navidrome.password") or "")
    token = hashlib.md5((password + "morg-salt").encode("utf-8")).hexdigest()
    # Navidrome 支持明文密码参数 p= 或 token+salt；这里用 p 保持兼容（局域网内）。
    return {
        "u": username,
        "p": password,
        "v": "1.16.1",
        "c": "MusicOrganizer",
        "f": "json",
    }


def _base_url(config: Config) -> str:
    url = str(config.get("navidrome.url") or "").strip().rstrip("/")
    if url and not url.endswith("/rest"):
        url += "/rest"
    return url


def check(config: Config, paths: Optional[AppPaths] = None) -> Tuple[bool, Dict[str, Any]]:
    """测试连接并返回服务器信息。"""
    if not config.get("navidrome.enabled"):
        return False, {"error": "未启用 Navidrome 联动"}
    base = _base_url(config)
    if not base:
        return False, {"error": "未配置 Navidrome 地址"}
    params = _auth_params(config)
    params["f"] = "json"
    try:
        payload = _client(config).get_json(f"{base}/ping.view", params=params)
    except Exception as exc:
        return False, {"error": str(exc)[:200]}
    sub = payload.get("subsonic-response") or {}
    if sub.get("status") != "ok":
        return False, {"error": sub.get("error", {}).get("message") or "鉴权失败", "raw": sub}
    info: Dict[str, Any] = {"version": sub.get("version"), "server": sub.get("server"), "type": sub.get("type")}
    try:
        scan = _client(config).get_json(f"{base}/getScanStatus.view", params=params)
        info["scan"] = (scan.get("subsonic-response") or {}).get("scanStatus") or {}
    except Exception:
        pass
    return True, info


def trigger_rescan(config: Config, paths: Optional[AppPaths] = None) -> Tuple[bool, str]:
    if not config.get("navidrome.enabled"):
        return False, "未启用 Navidrome 联动"
    base = _base_url(config)
    if not base:
        return False, "未配置 Navidrome 地址"
    params = _auth_params(config)
    try:
        payload = _client(config).get_json(f"{base}/startScan.view", params=params)
    except HttpError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)[:200]
    sub = payload.get("subsonic-response") or {}
    if sub.get("status") == "ok":
        return True, "已通知 Navidrome 开始扫描"
    return False, (sub.get("error") or {}).get("message") or "未知错误"


def scan_status(config: Config, paths: Optional[AppPaths] = None) -> Dict[str, Any]:
    base = _base_url(config)
    params = _auth_params(config)
    try:
        payload = _client(config).get_json(f"{base}/getScanStatus.view", params=params)
        return (payload.get("subsonic-response") or {}).get("scanStatus") or {}
    except Exception as exc:
        return {"error": str(exc)[:160]}
