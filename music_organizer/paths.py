"""跨平台路径管理。

所有可写数据（配置、数据库、缓存、备份、日志）都放在同一个应用目录里，
方便整体备份 / 迁移：

- Linux   : ``~/.local/share/music-organizer``
- Windows : ``%LOCALAPPDATA%\\MusicOrganizer``
- macOS   : ``~/Library/Application Support/MusicOrganizer``

设置环境变量 ``MORG_HOME`` 可以覆盖该目录（测试、便携部署都用它）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

APP_DIR_NAME = "music-organizer"


def default_home() -> Path:
    """返回当前平台默认的应用数据目录。"""
    override = os.environ.get("MORG_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()

    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "MusicOrganizer"
        return Path.home() / "AppData" / "Local" / "MusicOrganizer"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "MusicOrganizer"

    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg:
        return Path(xdg) / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME


class AppPaths:
    """应用用到的全部文件路径。"""

    def __init__(self, home: Optional[Path | str] = None) -> None:
        self.home = Path(home).expanduser().resolve() if home else default_home()
        self.config_file = self.home / "config.json"
        self.db_file = self.home / "library.db"
        self.cache_dir = self.home / "cache"
        self.cover_cache_dir = self.cache_dir / "covers"
        self.image_cache_dir = self.cache_dir / "images"
        self.backup_dir = self.home / "backups"
        self.log_dir = self.home / "logs"
        self.trash_dir = self.home / "trash"
        self.log_file = self.log_dir / "morg.log"

    def ensure(self) -> "AppPaths":
        for d in (
            self.home,
            self.cache_dir,
            self.cover_cache_dir,
            self.image_cache_dir,
            self.backup_dir,
            self.log_dir,
            self.trash_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
        return self

    def to_dict(self) -> dict:
        return {
            "home": str(self.home),
            "config": str(self.config_file),
            "database": str(self.db_file),
            "cache": str(self.cache_dir),
            "backups": str(self.backup_dir),
            "logs": str(self.log_dir),
        }


_paths: Optional[AppPaths] = None


def get_paths(home: Optional[Path | str] = None) -> AppPaths:
    """获取全局路径单例；传入 home 时重新初始化（测试用）。"""
    global _paths
    if home is not None:
        _paths = AppPaths(home)
        return _paths
    if _paths is None:
        _paths = AppPaths()
    return _paths


def set_home(home: Path | str) -> AppPaths:
    """切换应用目录并重置缓存单例。"""
    return get_paths(home)
