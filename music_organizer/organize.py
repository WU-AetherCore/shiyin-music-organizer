"""按标签整理文件：重命名 / 移动到规范目录。

默认只做“预演”，加 ``--apply`` 才会真正移动文件。移动前会检查目标是否已存在、
是否越出音乐目录，并一并处理同名 ``.lrc`` / 封面文件。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import db
from .config import Config
from .paths import AppPaths, get_paths
from .textutil import clean_placeholder, clean_spaces, safe_filename

SIDECAR_EXTS = (".lrc", ".jpg", ".jpeg", ".png")


class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


@dataclass
class RenamePlan:
    track_id: int
    title: str
    source: str
    target: str
    status: str = "planned"  # planned / skipped / done / error
    reason: str = ""
    sidecars: List[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.sidecars is None:
            self.sidecars = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "track_id": self.track_id,
            "title": self.title,
            "source": self.source,
            "target": self.target,
            "status": self.status,
            "reason": self.reason,
            "sidecars": self.sidecars,
        }


def _format_values(track: Dict[str, Any]) -> Dict[str, Any]:
    title = clean_placeholder(track.get("title")) or Path(track.get("filename") or "未命名").stem
    artist = clean_placeholder(track.get("artist")) or "未知歌手"
    album = clean_placeholder(track.get("album")) or "未知专辑"
    track_no = track.get("track_no") or 0
    disc_no = track.get("disc_no") or 0
    ext = (track.get("ext") or "").lower()
    return {
        "title": title,
        "artist": artist,
        "album": album,
        "album_artist": clean_placeholder(track.get("album_artist")) or artist,
        "track": int(track_no),
        "track_no": int(track_no),
        "disc": int(disc_no),
        "year": clean_spaces(str(track.get("year") or "")),
        "genre": clean_placeholder(track.get("genre")) or "",
        "ext": ext,
        "filename": Path(track.get("filename") or "").stem,
    }


def _safe_relative(template: str, values: Dict[str, Any], ext: str) -> List[str]:
    try:
        rendered = template.format_map(SafeDict(values))
    except (ValueError, KeyError) as exc:
        raise ValueError(f"模板格式不正确：{exc}") from exc
    rendered = rendered.replace("\\", "/")
    parts: List[str] = []
    for part in rendered.split("/"):
        part = clean_spaces(part)
        if not part or part in (".", ".."):
            continue
        cleaned = safe_filename(part, max_len=120)
        if cleaned and cleaned not in (".", ".."):
            parts.append(cleaned)
    if not parts:
        raise ValueError("模板结果为空")
    filename = parts[-1]
    if not filename.lower().endswith(ext):
        filename += ext
    parts[-1] = filename
    return parts


def plan_renames(
    tracks: Sequence[Dict[str, Any]],
    config: Config,
    template: str,
    paths: Optional[AppPaths] = None,
    move_to_music_root: Optional[bool] = None,
) -> List[RenamePlan]:
    paths = paths or get_paths()
    music_dir = config.music_dir
    if not music_dir:
        raise ValueError("尚未设置音乐目录")
    root = music_dir.resolve()
    plans: List[RenamePlan] = []
    for track in tracks:
        source = Path(track["path"])
        values = _format_values(track)
        ext = values["ext"] or source.suffix.lower()
        try:
            parts = _safe_relative(template, values, ext)
        except ValueError as exc:
            plans.append(RenamePlan(int(track["id"]), values["title"], str(source), "", "error", str(exc)))
            continue
        is_tree = ("/" in template or "\\" in template)
        base = root if (is_tree or move_to_music_root) else source.parent.resolve()
        target = (base / Path(*parts)).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            plans.append(RenamePlan(int(track["id"]), values["title"], str(source), str(target), "error", "目标路径越出音乐目录"))
            continue
        plan = RenamePlan(int(track["id"]), values["title"], str(source), str(target))
        if target == source:
            plan.status = "skipped"
            plan.reason = "文件名已经是目标名称"
        elif target.exists():
            plan.status = "skipped"
            plan.reason = "目标文件已存在"
        else:
            for sidecar_ext in SIDECAR_EXTS:
                old_sidecar = source.with_suffix(sidecar_ext)
                if old_sidecar.exists():
                    plan.sidecars.append(str(old_sidecar))
        plans.append(plan)

    # 同一批任务里出现相同目标时，只保留第一个，其余标记冲突
    seen_targets: Dict[str, int] = {}
    for plan in plans:
        if plan.status != "planned":
            continue
        key = plan.target.casefold() if os.name == "nt" else plan.target
        if key in seen_targets:
            plan.status = "error"
            plan.reason = "目标文件名与另一首歌重复"
        else:
            seen_targets[key] = plan.track_id
    return plans


def apply_renames(
    plans: Iterable[RenamePlan],
    config: Config,
    paths: Optional[AppPaths] = None,
) -> List[RenamePlan]:
    paths = paths or get_paths()
    results: List[RenamePlan] = []
    for plan in plans:
        if plan.status not in ("planned",):
            results.append(plan)
            continue
        source = Path(plan.source)
        target = Path(plan.target)
        if not source.is_file():
            plan.status = "error"
            plan.reason = "源文件不存在"
            results.append(plan)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            moved_sidecars = []
            for sidecar in plan.sidecars:
                old = Path(sidecar)
                if not old.exists():
                    continue
                new = target.with_suffix(old.suffix)
                if new.exists():
                    continue
                shutil.move(str(old), str(new))
                moved_sidecars.append(str(new))
            plan.sidecars = moved_sidecars
            plan.status = "done"
            db.update_track_path(plan.track_id, str(target), paths)
            db.log_activity(
                "整理文件",
                "ok",
                f"{plan.source} → {plan.target}",
                track_id=plan.track_id,
                track_title=plan.title,
                source="organize",
                paths=paths,
            )
        except OSError as exc:
            plan.status = "error"
            plan.reason = str(exc)[:200]
            db.log_activity(
                "整理文件",
                "error",
                f"{plan.source}：{exc}",
                track_id=plan.track_id,
                track_title=plan.title,
                source="organize",
                paths=paths,
            )
        results.append(plan)
    return results
