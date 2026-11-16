"""外置 .lrc 清理：找出已有内嵌歌词后多余的、以及失联的歌词文件。

只把文件移入数据目录的 trash，不直接删除；真正的删除由用户自己决定。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import audio, db
from .config import Config
from .paths import AppPaths, get_paths
from .textutil import clean_spaces

STATUS_LABELS = {
    "redundant": "已有内嵌歌词",
    "orphan": "没有对应音频",
    "sidecar_only": "仅外置歌词（保留）",
}


@dataclass
class LrcEntry:
    path: str
    audio_path: str
    status: str
    size: int
    mtime: float
    title: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "audio_path": self.audio_path,
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "size": self.size,
            "mtime": self.mtime,
            "title": self.title,
        }


def _sibling_audio(lrc_path: Path) -> Optional[Path]:
    for ext in sorted(audio.READABLE_EXTS):
        candidate = lrc_path.with_suffix(ext)
        if candidate.is_file():
            return candidate
    return None


def _excluded(name: str, patterns: Iterable[str]) -> bool:
    import fnmatch

    low = name.lower()
    for pattern in patterns:
        if pattern and fnmatch.fnmatch(low, pattern.lower()):
            return True
    return False


def scan_lrc_files(config: Config, paths: Optional[AppPaths] = None, limit: int = 5000) -> Dict[str, Any]:
    paths = paths or get_paths()
    root = config.music_dir
    if not root or not root.is_dir():
        raise FileNotFoundError("音乐文件夹不存在")
    excludes = list(config.get("library.exclude_dirs") or [])
    try:
        track_map = {str(t.get("path")): t for t in db.all_tracks(paths)}
    except Exception:
        track_map = {}
    entries: List[LrcEntry] = []
    total_size = 0
    for current, dirs, names in os.walk(str(root)):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not _excluded(d, excludes)]
        for name in names:
            if not name.lower().endswith(".lrc"):
                continue
            lrc_path = Path(current) / name
            if len(entries) >= limit:
                break
            try:
                stat = lrc_path.stat()
            except OSError:
                continue
            sibling = _sibling_audio(lrc_path)
            status = "orphan"
            if sibling:
                track = track_map.get(str(sibling)) or db.get_track_by_path(str(sibling), paths)
                if track and track.get("has_lyrics") and track.get("lyrics_source") == "embed":
                    status = "redundant"
                elif not track:
                    # 尚未扫描时直接读音频标签判断
                    status = "redundant" if audio.read_embedded_lyrics(sibling).strip() else "sidecar_only"
                else:
                    status = "sidecar_only"
            entry = LrcEntry(
                path=str(lrc_path),
                audio_path=str(sibling) if sibling else "",
                status=status,
                size=stat.st_size,
                mtime=stat.st_mtime,
                title=clean_spaces(lrc_path.stem),
            )
            entries.append(entry)
            total_size += stat.st_size

    counts = {
        "total": len(entries),
        "redundant": sum(1 for e in entries if e.status == "redundant"),
        "orphan": sum(1 for e in entries if e.status == "orphan"),
        "sidecar_only": sum(1 for e in entries if e.status == "sidecar_only"),
        "size": total_size,
    }
    entries.sort(key=lambda item: (item.status, item.path))
    return {"entries": entries, "counts": counts}


def clean_lrc_files(
    config: Config,
    entries: Sequence[LrcEntry],
    statuses: Sequence[str],
    paths: Optional[AppPaths] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    paths = paths or get_paths()
    root = config.music_dir
    if not root:
        raise ValueError("尚未设置音乐目录")
    root = root.resolve()
    wanted = {s for s in statuses if s in ("redundant", "orphan", "sidecar_only")}
    if not wanted:
        raise ValueError("请选择要清理的类型")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    trash = paths.trash_dir / f"lrc-{stamp}"
    moved: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for entry in entries:
        if entry.status not in wanted:
            continue
        source = Path(entry.path)
        try:
            source.resolve().relative_to(root)
        except (ValueError, OSError):
            errors.append({"path": entry.path, "error": "不在音乐目录内"})
            continue
        if source.suffix.lower() != ".lrc" or not source.is_file():
            errors.append({"path": entry.path, "error": "文件不存在或不是 .lrc"})
            continue
        if dry_run:
            moved.append({"path": str(source), "target": "", "status": entry.status, "dry_run": True})
            continue
        try:
            relative = source.resolve().relative_to(root)
        except ValueError:
            relative = Path(source.name)
        target = trash / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        counter = 1
        while target.exists():
            target = target.with_name(f"{target.stem} ({counter}){target.suffix}")
            counter += 1
        try:
            shutil.move(str(source), str(target))
            moved.append({"path": str(source), "target": str(target), "status": entry.status})
            if entry.audio_path and entry.status == "redundant":
                try:
                    from . import scanner

                    scanner.scan_single(entry.audio_path, config, paths)
                except Exception:
                    pass
        except OSError as exc:
            errors.append({"path": str(source), "error": str(exc)[:200]})
    if moved and not dry_run:
        db.log_activity(
            "清理外置歌词",
            "ok",
            f"移动 {len(moved)} 个 .lrc 到 {trash}",
            source="manual",
            paths=paths,
        )
    return {
        "dry_run": dry_run,
        "moved": moved,
        "errors": errors,
        "trash_dir": str(trash),
    }


def embed_sidecar_lyrics(
    config: Config,
    entries: Sequence[LrcEntry],
    paths: Optional[AppPaths] = None,
    progress=None,
    cancel=None,
) -> Dict[str, Any]:
    """把“仅外置”的 .lrc 写入音频内嵌标签（不联网）。"""
    from .repair import RepairService

    paths = paths or get_paths()
    service = RepairService(config, paths)
    targets = [entry for entry in entries if entry.status == "sidecar_only" and entry.audio_path]
    done = 0
    failed = 0
    errors: List[Dict[str, str]] = []
    for index, entry in enumerate(targets, start=1):
        if cancel is not None and cancel.is_set():
            break
        try:
            text = Path(entry.path).read_text(encoding="utf-8", errors="ignore")
            track = db.get_track_by_path(entry.audio_path, paths)
            if not track or not text.strip():
                failed += 1
                errors.append({"path": entry.path, "error": "音频索引不存在或歌词为空"})
                continue
            service.apply_lyrics_text(track, text, source="外置 .lrc 嵌入")
            done += 1
        except Exception as exc:
            failed += 1
            errors.append({"path": entry.path, "error": str(exc)[:200]})
        if progress:
            progress(index, len(targets), entry.title)
    return {"total": len(targets), "embedded": done, "failed": failed, "errors": errors[:30]}
