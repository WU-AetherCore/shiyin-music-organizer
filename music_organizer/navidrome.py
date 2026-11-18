"""Navidrome 联动：通过 Subsonic API 触发媒体库重新扫描。

整理插件把歌词/封面写回文件后，Navidrome 不会自动感知，需要一次 rescan。
配置里填好地址与账号即可在设置页一键触发，也支持“修复完成后自动扫描”。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path
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


def trigger_rescan(config: Config, paths: Optional[AppPaths] = None, full: bool = True) -> Tuple[bool, str]:
    if not config.get("navidrome.enabled"):
        return False, "未启用 Navidrome 联动"
    base = _base_url(config)
    if not base:
        return False, "未配置 Navidrome 地址"
    params = _auth_params(config)
    if full:
        params["fullScan"] = "true"
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


def _docker_container(config: Config) -> Tuple[Optional[str], Optional[str]]:
    docker = shutil.which("docker")
    if not docker:
        return None, "未找到 docker 命令"
    container = str(config.get("navidrome.container") or "").strip()
    if container:
        return docker, container
    try:
        listed = subprocess.run(
            [docker, "ps", "--format", "{{.Names}}\t{{.Image}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        for line in (listed.stdout or "").splitlines():
            name, _, image = line.partition("\t")
            if "navidrome" in (name + image).casefold():
                return docker, name.strip()
    except Exception as exc:
        return None, f"发现 Navidrome 容器失败：{str(exc)[:120]}"
    return None, "未找到 Navidrome 容器"


def _normalize_text(value: str) -> str:
    text = str(value or "").strip()
    # 旧记录常带 "歌手 - " 前缀；去掉后再比较
    text = re.sub(r"^.*?\s+-\s+", "", text)
    try:
        import zhconv

        text = zhconv.convert(text, "zh-cn")
    except Exception:
        pass
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE).casefold()


def _wait_for_scan(config: Config, timeout: float = 20.0) -> None:
    """Navidrome startScan 是异步的；等扫描结束再 remap，目标文件才在库中。"""
    deadline = time.time() + timeout
    time.sleep(0.8)
    while time.time() < deadline:
        try:
            status = scan_status(config)
            if not status.get("scanning"):
                return
        except Exception:
            return
        time.sleep(0.8)


def remap_missing_files(config: Config, paths: Optional[AppPaths] = None) -> Tuple[bool, str]:
    """把 Navidrome 的幽灵记录重新映射到同名同歌手的现有文件。

    文件被整理移动/改标签后，Navidrome 可能留下 missing=1 的旧记录和空专辑，
    表现为默认蓝色封面。这里用官方 ``navidrome missing fix`` 按标题+歌手匹配。
    """
    if not config.get("navidrome.remap_missing", True):
        return True, "未开启缺失记录重映射"
    docker, container = _docker_container(config)
    if not docker or not container:
        return False, str(container or "未找到 docker")
    try:
        listed = subprocess.run(
            [docker, "exec", container, "navidrome", "missing", "list", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        return False, f"读取缺失记录失败：{str(exc)[:120]}"
    raw = (listed.stdout or "").strip()
    start = raw.find("[")
    if listed.returncode != 0 or start < 0:
        detail = (listed.stderr or raw).strip().replace("\n", " ")[-160:]
        return False, f"读取缺失记录失败：{detail or '退出码 ' + str(listed.returncode)}"
    try:
        missing = json.loads(raw[start:])
    except Exception as exc:
        return False, f"解析缺失记录失败：{str(exc)[:120]}"
    if not missing:
        return True, "没有需要重映射的缺失记录"
    from . import db

    _wait_for_scan(config)
    music_dir = config.music_dir
    if not music_dir:
        return False, "未配置音乐目录"
    index: Dict[Tuple[str, str], list] = {}
    by_title: Dict[str, list] = {}
    for track in db.all_tracks(paths or get_paths()):
        title_key = _normalize_text(str(track.get("title") or ""))
        artist_key = _normalize_text(str(track.get("artist") or ""))
        if title_key and Path(track.get("path") or "").is_file():
            by_title.setdefault(title_key, []).append(track)
        if title_key and artist_key and Path(track.get("path") or "").is_file():
            index.setdefault((title_key, artist_key), []).append(track)
    fixed = 0
    failed = 0
    for entry in missing:
        title_key = _normalize_text(str(entry.get("title") or ""))
        key = (title_key, _normalize_text(str(entry.get("artist") or "")))
        candidates = index.get(key) or []
        if not candidates:
            # 老记录可能是 [Unknown Artist]；标题唯一时按标题兜底
            title_candidates = by_title.get(title_key) or []
            if len(title_candidates) == 1:
                candidates = title_candidates
        album = _normalize_text(str(entry.get("album") or ""))
        exact = [t for t in candidates if _normalize_text(str(t.get("album") or "")) == album]
        target = exact[0] if len(exact) == 1 else (candidates[0] if len(candidates) == 1 else None)
        if target is None:
            continue
        try:
            relative = Path(os.path.relpath(str(target["path"]), str(music_dir)))
        except ValueError:
            continue
        if str(relative).startswith(".."):
            continue
        proc = subprocess.run(
            [
                docker,
                "exec",
                container,
                "navidrome",
                "missing",
                "fix",
                str(entry.get("id") or ""),
                str(relative),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            fixed += 1
        else:
            failed += 1
    message = f"缺失记录重映射：成功 {fixed}，失败 {failed}"
    return True, message


def refresh_missing_artwork(config: Config, paths: Optional[AppPaths] = None) -> Tuple[bool, str]:
    """让 Navidrome 重试“无封面”缓存。

    全量扫描不会自动重试已判定为 absent 的专辑封面，这是 Navidrome 的设计。
    这里调用它的官方 CLI：navidrome artwork reprocess --kind al --source absent -y。
    仅在配置了容器名，或本机 docker 可发现 Navidrome 容器时执行。
    """
    if not config.get("navidrome.refresh_artwork", True):
        return True, "未开启封面缓存重试"
    docker, container = _docker_container(config)
    if not docker or not container:
        return False, str(container or "未找到 docker")
    try:
        proc = subprocess.run(
            [
                docker,
                "exec",
                container,
                "navidrome",
                "artwork",
                "reprocess",
                "--kind",
                "al",
                "--source",
                "absent",
                "-y",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:
        return False, f"封面缓存重试失败：{str(exc)[:120]}"
    if proc.returncode == 0:
        return True, "已让 Navidrome 重试缺失的专辑封面"
    detail = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")[-180:]
    return False, f"封面缓存重试失败：{detail or '退出码 ' + str(proc.returncode)}"


def scan_status(config: Config, paths: Optional[AppPaths] = None) -> Dict[str, Any]:
    base = _base_url(config)
    params = _auth_params(config)
    try:
        payload = _client(config).get_json(f"{base}/getScanStatus.view", params=params)
        return (payload.get("subsonic-response") or {}).get("scanStatus") or {}
    except Exception as exc:
        return {"error": str(exc)[:160]}
