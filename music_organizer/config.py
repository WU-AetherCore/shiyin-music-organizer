"""配置管理：默认值、读写、校验、密码散列。

配置文件是单个 JSON（权限 600），所有可调项集中在 :data:`DEFAULT_CONFIG`，
Web 设置页与 CLI ``morg config`` 都基于同一份结构。
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .paths import AppPaths, get_paths

AUDIO_EXTENSIONS = [
    ".flac",
    ".mp3",
    ".m4a",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".aiff",
    ".aif",
    ".ape",
    ".wv",
]

PROVIDER_NAMES = ["netease", "qqmusic", "lrclib", "itunes"]

DEFAULT_CONFIG: Dict[str, Any] = {
    "schema": 1,
    "music_dir": "",
    "library": {
        "exclude_dirs": [
            "@eaDir",
            "#recycle",
            "lost+found",
            "$RECYCLE.BIN",
            "System Volume Information",
            ".Trash",
            ".trash",
            "node_modules",
            "__pycache__",
            "导出",
            "Exports",
        ],
        "extensions": AUDIO_EXTENSIONS,
        "follow_symlinks": False,
    },
    "web": {
        "host": "0.0.0.0",
        "port": 8090,
        "password_hash": "",
        "secret": "",
        "page_size": 60,
    },
    "repair": {
        "targets": ["lyrics", "cover"],
        "overwrite_lyrics": False,
        "overwrite_cover": False,
        "min_cover_size": 300,
        "max_cover_size": 1280,
        "jpeg_quality": 90,
        "write_lrc": False,
        "write_cover_file": False,
        "simplified": True,
        "allow_plain_lyrics": True,
        "min_lyrics_chars": 20,
        "fix_placeholder_tags": True,
        "fix_timing": True,
        "backup": True,
        "max_backups": 300,
    },
    "providers": {
        "lyrics": ["netease", "qqmusic", "lrclib"],
        "cover": ["netease", "qqmusic", "itunes"],
        "timeout": 12,
        "retries": 1,
        "proxy": "",
        "min_score": 55,
        "parallel": True,
    },
    "playback": {
        "quality": "original",
    },
    "automation": {
        "scan_on_start": True,
        "watch_enabled": True,
        "watch_interval_min": 30,
        "auto_fix_new": True,
        "fix_on_play": True,
        "auto_fix_timing": True,
        "schedule_enabled": True,
        "schedule_time": "03:30",
        "schedule_days": [1, 2, 3, 4, 5, 6, 7],
        "max_fix_per_run": 40,
        "targets": ["lyrics", "cover"],
        "last_scan_at": "",
        "last_fix_at": "",
        "last_schedule_date": "",
        "last_timing_fix_at": "",
        "watch_changes": True,
    },
    "navidrome": {
        "enabled": False,
        "url": "",
        "username": "",
        "password": "",
        "rescan_after_fix": False,
        "last_sync_at": "",
        "last_sync_ok": False,
        "last_sync_message": "",
    },
    "jellyfin": {
        "enabled": False,
        "url": "",
        "api_key": "",
        "refresh_after_fix": False,
        "last_sync_at": "",
        "last_sync_ok": False,
        "last_sync_message": "",
    },
    "ui": {
        "accent": "#7c5cff",
        "language": "zh-CN",
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """把 override 合并进 base 的副本，保留未知的旧键（向前兼容）。"""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def hash_password(password: str) -> str:
    """PBKDF2-SHA256 密码散列，返回可直接存入配置的字符串。"""
    salt = secrets.token_bytes(16)
    iterations = 240_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = (stored or "").split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


class ConfigError(ValueError):
    pass


class Config:
    """线程安全的配置对象。"""

    def __init__(self, data: Dict[str, Any], paths: Optional[AppPaths] = None):
        self._lock = threading.RLock()
        self.paths = paths or get_paths()
        self.data = _deep_merge(DEFAULT_CONFIG, data or {})
        if not self.data.get("web", {}).get("secret"):
            self.data["web"]["secret"] = secrets.token_hex(32)

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, paths: Optional[AppPaths] = None, create: bool = True) -> "Config":
        paths = paths or get_paths()
        paths.ensure()
        data: Dict[str, Any] = {}
        if paths.config_file.exists():
            try:
                raw = paths.config_file.read_text(encoding="utf-8")
                data = json.loads(raw) if raw.strip() else {}
                if not isinstance(data, dict):
                    raise ConfigError("配置文件格式不是 JSON 对象")
            except (OSError, json.JSONDecodeError) as exc:
                raise ConfigError(f"读取配置失败：{exc}") from exc
        cfg = cls(data, paths)
        if create and not paths.config_file.exists():
            cfg.save()
        return cfg

    # ------------------------------------------------------------------ save
    def save(self) -> "Config":
        with self._lock:
            self.paths.ensure()
            tmp = self.paths.config_file.with_suffix(".json.tmp")
            text = json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
            tmp.write_text(text, encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.paths.config_file)
            try:
                os.chmod(self.paths.config_file, 0o600)
            except OSError:
                pass
        return self

    # ------------------------------------------------------------------- get
    def get(self, dotted: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self.data
            for part in dotted.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(self, dotted: str, value: Any) -> "Config":
        with self._lock:
            parts = dotted.split(".")
            node = self.data
            for part in parts[:-1]:
                nxt = node.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    node[part] = nxt
                node = nxt
            node[parts[-1]] = value
        return self

    def update(self, patch: Dict[str, Any], save: bool = True) -> "Config":
        with self._lock:
            self.data = _deep_merge(self.data, patch)
            if not self.data.get("web", {}).get("secret"):
                self.data["web"]["secret"] = secrets.token_hex(32)
        if save:
            self.save()
        return self

    # --------------------------------------------------------------- helpers
    def as_dict(self, include_secrets: bool = False) -> Dict[str, Any]:
        with self._lock:
            data = copy.deepcopy(self.data)
        if not include_secrets:
            data.get("web", {}).pop("password_hash", None)
            data.get("web", {}).pop("secret", None)
            data.get("navidrome", {}).pop("password", None)
            data.get("jellyfin", {}).pop("api_key", None)
        return data

    @property
    def music_dir(self) -> Optional[Path]:
        raw = (self.get("music_dir") or "").strip()
        if not raw:
            return None
        return Path(raw).expanduser()

    @property
    def music_dir_str(self) -> str:
        d = self.music_dir
        return str(d) if d else ""

    def is_initialized(self) -> bool:
        d = self.music_dir
        return bool(d and d.is_dir())

    @property
    def has_web_password(self) -> bool:
        return bool(self.get("web.password_hash"))

    @property
    def data_dir(self) -> Path:
        return self.paths.home

    def validate(self) -> List[str]:
        """返回错误信息列表；空列表表示配置可用。"""
        errors: List[str] = []
        raw_dir = (self.get("music_dir") or "").strip()
        if not raw_dir:
            errors.append("尚未选择音乐文件夹")
        else:
            d = Path(raw_dir).expanduser()
            if not d.exists():
                errors.append(f"音乐文件夹不存在：{d}")
            elif not d.is_dir():
                errors.append(f"音乐文件夹不是目录：{d}")

        port = self.get("web.port")
        if not isinstance(port, int) or not (1 <= port <= 65535):
            errors.append("Web 端口必须是 1-65535 的整数")

        for key in ("lyrics", "cover"):
            names = self.get(f"providers.{key}") or []
            if not isinstance(names, list) or not names:
                errors.append(f"providers.{key} 至少需要启用一个数据源")
            else:
                bad = [n for n in names if n not in PROVIDER_NAMES]
                if bad:
                    errors.append(f"providers.{key} 含未知数据源：{', '.join(bad)}")

        targets = self.get("repair.targets") or []
        if not set(targets) <= {"lyrics", "cover", "tags"}:
            errors.append("repair.targets 只能包含 lyrics / cover / tags")

        t = str(self.get("automation.schedule_time") or "")
        if len(t.split(":")) != 2:
            errors.append("自动任务时间格式应为 HH:MM")
        else:
            try:
                hh, mm = int(t.split(":")[0]), int(t.split(":")[1])
                if not (0 <= hh <= 23 and 0 <= mm <= 59):
                    raise ValueError
            except ValueError:
                errors.append("自动任务时间超出范围")

        interval = self.get("automation.watch_interval_min")
        if not isinstance(interval, int) or interval < 0:
            errors.append("监听间隔必须是不小于 0 的整数（0 表示关闭）")

        min_cover = self.get("repair.min_cover_size")
        max_cover = self.get("repair.max_cover_size")
        if not isinstance(min_cover, int) or min_cover < 0:
            errors.append("封面最小尺寸不合法")
        if not isinstance(max_cover, int) or max_cover < 200:
            errors.append("封面最大尺寸不合法")

        if self.get("navidrome.enabled"):
            url = (self.get("navidrome.url") or "").strip()
            if not url.startswith(("http://", "https://")):
                errors.append("Navidrome 地址需以 http:// 或 https:// 开头")
            if not (self.get("navidrome.username") or "").strip():
                errors.append("启用 Navidrome 联动时必须填写用户名")

        if self.get("jellyfin.enabled"):
            url = (self.get("jellyfin.url") or "").strip()
            if not url.startswith(("http://", "https://")):
                errors.append("Jellyfin 地址需以 http:// 或 https:// 开头")
            if not (self.get("jellyfin.api_key") or "").strip():
                errors.append("启用 Jellyfin 联动时必须填写 API Key")

        if self.get("repair.write_cover_file") and not self.get("repair.write_lrc"):
            # 允许，但提示型错误会让用户困惑，这里不强制
            pass
        return errors

    def ensure_ready(self) -> None:
        errors = self.validate()
        # 只把音乐目录当作硬性条件，其他错误在设置页提示
        if not self.is_initialized():
            raise ConfigError(errors[0] if errors else "尚未初始化音乐文件夹")


_config: Optional[Config] = None
_config_lock = threading.Lock()


def get_config(paths: Optional[AppPaths] = None) -> Config:
    """全局配置单例。"""
    global _config
    with _config_lock:
        if _config is None:
            _config = Config.load(paths or get_paths())
        return _config


def reload_config() -> Config:
    global _config
    with _config_lock:
        _config = Config.load(get_paths())
        return _config


def reset_config_cache() -> None:
    global _config
    with _config_lock:
        _config = None


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "是", "开启")
    return False


def coerce_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        return int(value)
    except (TypeError, ValueError):
        return default


def unique_list(items: Iterable[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for item in items:
        item = str(item)
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
