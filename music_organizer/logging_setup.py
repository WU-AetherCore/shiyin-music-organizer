"""日志配置：文件 + 控制台，按大小轮转。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

from .paths import AppPaths

_configured = False


def setup_logging(paths: AppPaths, debug: bool = False, console: bool = True) -> logging.Logger:
    global _configured
    logger = logging.getLogger("music_organizer")
    if _configured:
        return logger
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if debug else logging.INFO
    logger.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        file_handler = RotatingFileHandler(
            paths.log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        logger.addHandler(file_handler)
    except OSError:
        pass
    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        stream.setLevel(level)
        logger.addHandler(stream)
    logger.propagate = False
    _configured = True
    return logger


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger("music_organizer" + (f".{name}" if name else ""))


def tail_log(paths: AppPaths, limit: int = 200) -> list:
    if not paths.log_file.exists():
        return []
    try:
        with paths.log_file.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        return [line.rstrip("\n") for line in lines[-max(1, limit) :]]
    except OSError:
        return []
