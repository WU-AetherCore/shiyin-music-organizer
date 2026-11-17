"""音乐目录扫描器。

增量策略：先 ``stat`` 对比数据库里的 size/mtime，没有变化就跳过标签解析，
所以定时扫描几千首歌也只需要几秒。
"""

from __future__ import annotations

import fnmatch
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from . import audio, db
from .config import Config
from .paths import AppPaths, get_paths

ProgressCallback = Callable[[int, int, str], None]


@dataclass
class ScanResult:
    total: int = 0
    added: int = 0
    updated: int = 0
    skipped: int = 0
    removed: int = 0
    errors: int = 0
    elapsed: float = 0.0
    new_ids: List[int] = field(default_factory=list)
    changed_ids: List[int] = field(default_factory=list)
    error_samples: List[Dict[str, str]] = field(default_factory=list)
    root: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "total": self.total,
            "added": self.added,
            "updated": self.updated,
            "skipped": self.skipped,
            "removed": self.removed,
            "errors": self.errors,
            "elapsed": round(self.elapsed, 2),
            "new_ids": self.new_ids[:500],
            "changed_ids": self.changed_ids[:500],
            "error_samples": self.error_samples[:20],
            "root": self.root,
        }


def _is_excluded(name: str, patterns: Iterable[str]) -> bool:
    low = name.lower()
    for pattern in patterns:
        pattern = (pattern or "").strip()
        if not pattern:
            continue
        if fnmatch.fnmatch(low, pattern.lower()):
            return True
    return False


def collect_audio_files(config: Config) -> List[Path]:
    """遍历音乐目录，返回受支持的音频文件（已排序）。"""
    root = config.music_dir
    if not root or not root.is_dir():
        return []
    exts = {ext.lower() for ext in (config.get("library.extensions") or [])}
    if not exts:
        exts = audio.READABLE_EXTS
    excludes = list(config.get("library.exclude_dirs") or [])
    follow = bool(config.get("library.follow_symlinks", False))
    files: List[Path] = []
    for current, dirs, names in os.walk(str(root), followlinks=follow):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not _is_excluded(d, excludes)]
        for name in names:
            if name.startswith("."):
                continue
            if Path(name).suffix.lower() not in exts:
                continue
            files.append(Path(current) / name)
    files.sort(key=lambda p: str(p).lower())
    return files


def _safe_stat(path: Path) -> Optional[os.stat_result]:
    try:
        return path.stat()
    except OSError:
        return None


def library_signature(config: Config) -> Dict[str, object]:
    """快速检测目录是否变化：文件数量 + 总大小 + 最大 mtime。

    只做 ``stat``，不读标签，用来驱动“有新歌自动扫描”。
    """
    root = config.music_dir
    if not root or not root.is_dir():
        return {"signature": "", "count": 0, "max_mtime": 0.0, "total_size": 0}
    exts = {ext.lower() for ext in (config.get("library.extensions") or [])}
    excludes = list(config.get("library.exclude_dirs") or [])
    follow = bool(config.get("library.follow_symlinks", False))
    count = 0
    total_size = 0
    max_mtime = 0.0
    for current, dirs, names in os.walk(str(root), followlinks=follow):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not _is_excluded(d, excludes)]
        for name in names:
            if name.startswith(".") or Path(name).suffix.lower() not in exts:
                continue
            try:
                stat = (Path(current) / name).stat()
            except OSError:
                continue
            count += 1
            total_size += stat.st_size
            if stat.st_mtime > max_mtime:
                max_mtime = stat.st_mtime
    signature = f"{count}:{total_size}:{round(max_mtime, 1)}"
    return {"signature": signature, "count": count, "max_mtime": max_mtime, "total_size": total_size}


def scan_library(
    config: Config,
    paths: Optional[AppPaths] = None,
    full: bool = False,
    progress: Optional[ProgressCallback] = None,
    cancel: Optional[threading.Event] = None,
) -> ScanResult:
    """扫描曲库并写入索引。"""
    paths = paths or get_paths()
    started = time.time()
    root = config.music_dir
    result = ScanResult(root=str(root) if root else "")
    if not root or not root.is_dir():
        raise FileNotFoundError(f"音乐文件夹不存在：{root}")

    files = collect_audio_files(config)
    result.total = len(files)

    with db.get_conn(paths) as conn:
        existing = {
            row["path"]: (float(row["mtime"] or 0), int(row["size"] or 0), int(row["id"]))
            for row in conn.execute("SELECT id, path, mtime, size FROM tracks").fetchall()
        }

    seen: set = set()
    for index, path in enumerate(files, start=1):
        if cancel is not None and cancel.is_set():
            break
        key = str(path)
        seen.add(key)
        stat = _safe_stat(path)
        if stat is None:
            result.errors += 1
            continue
        previous = existing.get(key)
        unchanged = (
            previous is not None
            and not full
            and abs(previous[0] - stat.st_mtime) < 1.0
            and previous[1] == stat.st_size
        )
        if unchanged:
            result.skipped += 1
            if progress:
                progress(index, result.total, key)
            continue

        try:
            meta = audio.read_meta(path)
            track_id = db.upsert_track(meta, paths)
            if config.get("repair.write_lrc"):
                try:
                    audio.sync_lrc_sidecar(path, str(meta.get("lyrics_text") or ""))
                except Exception:
                    pass
            if previous is None:
                result.added += 1
                result.new_ids.append(track_id)
            else:
                result.updated += 1
                result.changed_ids.append(track_id)
        except Exception as exc:  # 损坏文件 / 权限问题
            result.errors += 1
            if len(result.error_samples) < 20:
                result.error_samples.append({"path": key, "error": str(exc)[:200]})
        if progress:
            progress(index, result.total, key)

    # 清理已删除的文件记录（只清理当前音乐目录下的）
    root_prefix = str(root)
    stale = [path_key for path_key in existing if path_key not in seen]
    for stale_path in stale:
        try:
            if not stale_path.startswith(root_prefix):
                continue
        except Exception:
            continue
        db.delete_by_path(stale_path, paths)
        result.removed += 1

    result.elapsed = time.time() - started
    db.touch_scanned(paths)
    db.set_meta("last_scan_root", root_prefix, paths)
    try:
        db.log_activity(
            "扫描曲库",
            "ok",
            f"共 {result.total} 首，新增 {result.added}，更新 {result.updated}，"
            f"移除 {result.removed}，错误 {result.errors}，耗时 {result.elapsed:.1f}s",
            source="scan",
            paths=paths,
        )
    except Exception:
        pass
    return result


def scan_single(
    path: str | Path,
    config: Config,
    paths: Optional[AppPaths] = None,
    write_lrc: Optional[bool] = None,
) -> Optional[Dict[str, object]]:
    """重新扫描单个文件（修复后刷新索引）。"""
    paths = paths or get_paths()
    target = Path(path)
    if not target.is_file():
        return None
    meta = audio.read_meta(target)
    track_id = db.upsert_track(meta, paths)
    should_write_lrc = bool(config.get("repair.write_lrc")) if write_lrc is None else bool(write_lrc)
    if should_write_lrc:
        try:
            audio.sync_lrc_sidecar(target, str(meta.get("lyrics_text") or ""))
        except Exception:
            pass
    return db.get_track(track_id, paths)


def audit_lyrics_timing(
    config: Config,
    paths: Optional[AppPaths] = None,
    progress: Optional[ProgressCallback] = None,
    cancel: Optional[threading.Event] = None,
) -> Dict[str, int]:
    """只重算已索引歌曲的歌词时间轴状态，不重新扫描标签/封面。

    用于旧数据库升级后补齐 ``lyrics_timing_*`` 字段；正常增量扫描
    遇到文件变化也会自动更新这些字段。
    """
    from .textutil import analyze_lyric_timing

    paths = paths or get_paths()
    tracks = db.all_tracks(paths)
    checked = changed = bad = 0
    for index, track in enumerate(tracks, start=1):
        if cancel is not None and cancel.is_set():
            break
        path = track.get("path") or ""
        try:
            text, _source = audio.read_lyrics(path)
            timing = analyze_lyric_timing(text, float(track.get("duration") or 0))
            status = timing.get("status") or ""
            issues = timing.get("issues") or []
            checked += 1
            if status != (track.get("lyrics_timing_status") or "") or issues != (track.get("lyrics_timing_issues") or []):
                db.update_track_media(
                    int(track["id"]),
                    {"lyrics_timing_status": status, "lyrics_timing_issues": issues},
                    paths,
                )
                changed += 1
            if timing.get("needs_fix"):
                bad += 1
        except Exception:
            continue
        if progress:
            progress(index, len(tracks), path)
    return {"checked": checked, "changed": changed, "issues": bad}
