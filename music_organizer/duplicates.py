"""重复歌曲确认、忽略与安全清理。"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import db
from .config import Config
from .paths import AppPaths, get_paths


def _trash(config: Config, paths: AppPaths, track_ids: Sequence[int], source: str = "重复清理") -> Dict[str, Any]:
    music_dir = config.music_dir
    moved: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    if not music_dir:
        return {"ok": False, "moved": moved, "errors": [{"error": "尚未设置音乐目录"}]}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for track_id in track_ids:
        track = db.get_track(int(track_id), paths)
        if not track:
            errors.append({"id": track_id, "error": "记录不存在"})
            continue
        source_path = Path(track["path"])
        try:
            source_path.resolve().relative_to(music_dir.resolve())
        except (ValueError, OSError):
            errors.append({"id": track_id, "error": "文件不在音乐目录内"})
            continue
        if not source_path.is_file():
            errors.append({"id": track_id, "error": "文件不存在"})
            continue
        try:
            relative = source_path.resolve().relative_to(music_dir.resolve())
        except ValueError:
            relative = Path(source_path.name)
        target = paths.trash_dir / stamp / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        counter = 1
        while target.exists():
            target = target.with_name(f"{target.stem} ({counter}){target.suffix}")
            counter += 1
        try:
            shutil.move(str(source_path), str(target))
            # 同名歌词/封面侧车跟随，避免回收站里剩孤儿文件
            for ext in (".lrc", ".jpg", ".jpeg", ".png"):
                side = source_path.with_suffix(ext)
                if not side.is_file():
                    continue
                side_target = target.with_suffix(ext)
                side_counter = 1
                while side_target.exists():
                    side_target = side_target.with_name(f"{side_target.stem} ({side_counter}){side_target.suffix}")
                    side_counter += 1
                try:
                    shutil.move(str(side), str(side_target))
                except OSError:
                    pass
            db.delete_track(int(track_id), paths)
            moved.append({"id": track_id, "target": str(target), "title": track.get("title") or ""})
        except OSError as exc:
            errors.append({"id": track_id, "error": str(exc)})
    if moved:
        db.log_activity(
            source,
            "ok",
            f"移动 {len(moved)} 个重复文件到 {paths.trash_dir / stamp}",
            source="duplicates",
            paths=paths,
        )
    return {"ok": True, "moved": moved, "errors": errors, "trash_dir": str(paths.trash_dir / stamp)}


def ignore_for_track(track_id: int, paths: Optional[AppPaths] = None) -> Optional[str]:
    groups = db.find_duplicates(paths=paths)
    target = int(track_id)
    for group in groups:
        if any(int(track["id"]) == target for track in group["tracks"]):
            signature = str(group.get("signature") or "")
            db.ignore_duplicate_signature(signature, paths)
            return signature
    return None


def clean_confident(config: Config, paths: Optional[AppPaths] = None, source: str = "duplicates") -> Dict[str, Any]:
    """只清理 exact/high 置信组；medium 不自动删除。"""
    paths = paths or get_paths()
    groups = db.find_duplicates(paths=paths)
    remove_ids: List[int] = []
    cleaned_groups = 0
    for group in groups:
        if group.get("confidence") not in ("exact", "high"):
            continue
        keep_id = int(group.get("keep_id") or 0)
        ids = [int(track["id"]) for track in group["tracks"] if int(track["id"]) != keep_id]
        if ids:
            remove_ids.extend(ids)
            cleaned_groups += 1
    result = _trash(config, paths, remove_ids, source=source)
    result["groups"] = cleaned_groups
    result["candidates"] = len(remove_ids)
    return result
