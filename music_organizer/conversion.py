"""可选的音频格式转换：默认保持原格式，按规则或目标格式转换。"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import audio, db, scanner
from .config import Config
from .paths import AppPaths, get_paths

TARGET_FORMATS = ("flac", "mp3", "m4a", "ogg", "opus", "wav")
SOURCE_FORMATS = ("flac", "mp3", "m4a", "mp4", "ogg", "opus", "wav", "aiff", "aif", "ape", "wv")
CODECS = {
    "flac": ["-c:a", "flac"],
    "mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
    "m4a": ["-c:a", "aac", "-b:a", "256k"],
    "ogg": ["-c:a", "libvorbis", "-b:a", "256k"],
    "opus": ["-c:a", "libopus", "-b:a", "192k"],
    "wav": ["-c:a", "pcm_s16le"],
}
TAG_FIELDS = ("title", "artist", "album", "album_artist", "year", "genre", "track_no", "disc_no")


class ConversionError(RuntimeError):
    pass


def normalize_rules(config: Config) -> List[Dict[str, str]]:
    rules: List[Dict[str, str]] = []
    for item in config.get("conversion.rules") or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("from") or "").strip().lower().lstrip(".")
        target = str(item.get("to") or "").strip().lower().lstrip(".")
        if source in SOURCE_FORMATS and target in TARGET_FORMATS and source != target:
            rules.append({"from": source, "to": target})
    return rules


def target_for(track: Dict[str, Any], config: Config) -> Optional[str]:
    if not config.get("conversion.enabled"):
        return None
    mode = str(config.get("conversion.mode") or "preserve")
    if mode == "preserve":
        return None
    source = Path(str(track.get("path") or "")).suffix.lower().lstrip(".")
    if source not in SOURCE_FORMATS:
        return None
    if mode == "all":
        target = str(config.get("conversion.target") or "flac").lower()
        return target if target in TARGET_FORMATS and source != target else None
    if mode == "rules":
        for rule in normalize_rules(config):
            if rule["from"] == source:
                return rule["to"]
    return None


def candidates(
    config: Config,
    paths: Optional[AppPaths] = None,
    ids: Optional[Sequence[int]] = None,
    limit: int = 0,
) -> List[Dict[str, Any]]:
    paths = paths or get_paths()
    tracks = db.tracks_by_ids(ids, paths) if ids else db.all_tracks(paths)
    found: List[Dict[str, Any]] = []
    for track in tracks:
        path = Path(str(track.get("path") or ""))
        if not path.is_file():
            continue
        target = target_for(track, config)
        if target:
            found.append({**track, "conversion_target": target})
            if limit and len(found) >= limit:
                break
    return found


def _codec_args(target: str) -> List[str]:
    try:
        return list(CODECS[target])
    except KeyError as exc:
        raise ConversionError(f"不支持的目标格式：{target}") from exc


def _tags(track: Dict[str, Any]) -> Dict[str, Any]:
    result = {}
    for key in TAG_FIELDS:
        value = track.get(key)
        if value not in (None, ""):
            result[key] = value
    return result


def convert_file(
    track: Dict[str, Any],
    config: Config,
    paths: Optional[AppPaths] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    paths = paths or get_paths()
    source = Path(str(track.get("path") or ""))
    target_format = str(track.get("conversion_target") or "")
    if not source.is_file() or not target_format:
        return {"status": "skipped", "reason": "文件不存在或无需转换", "path": str(source)}
    target_path = source.with_suffix(f".{target_format}")
    if target_path == source:
        return {"status": "skipped", "reason": "目标格式相同", "path": str(source)}
    if dry_run:
        return {"status": "dry_run", "source": str(source), "target": str(target_path), "format": target_format}
    if target_path.exists():
        return {"status": "skipped", "reason": "目标文件已存在，未覆盖", "path": str(target_path)}

    temp = source.with_name(f".{source.stem}.morg-convert-{uuid.uuid4().hex[:8]}.{target_format}")
    try:
        proc = subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:a",
                "-map_metadata",
                "0",
                "-vn",
                *_codec_args(target_format),
                str(temp),
            ],
            capture_output=True,
            timeout=1800,
        )
        if proc.returncode != 0 or not temp.is_file() or temp.stat().st_size <= 0:
            detail = (proc.stderr or b"").decode("utf-8", "ignore")[-200:]
            raise ConversionError(detail or "ffmpeg 转换失败")
        # ffmpeg 只转音频；再用项目现有写入器保标签、歌词、封面
        lyrics, _ = audio.read_lyrics(source)
        cover = audio.read_cover(source)
        audio.update_media(
            temp,
            lyrics=lyrics or None,
            cover=(cover.data, cover.mime or "image/jpeg") if cover else None,
            tags=_tags(track),
        )
        os.chmod(temp, 0o644)
        if not config.get("conversion.replace_original", False):
            out_dir = Path(config.music_dir) / "转换输出"
            out_dir.mkdir(parents=True, exist_ok=True)
            final = out_dir / f"{source.stem}.{target_format}"
            counter = 1
            while final.exists():
                final = out_dir / f"{source.stem} ({counter}).{target_format}"
                counter += 1
            shutil.move(str(temp), str(final))
            return {"status": "fixed", "source": str(source), "target": str(final), "format": target_format}
        backup = ""
        if config.get("conversion.keep_backup", True):
            backup = audio.backup_track(source, paths.backup_dir, reason="format-conversion") or ""
        os.replace(str(temp), str(target_path))
        try:
            if source.exists() and source != target_path:
                source.unlink()
        except OSError:
            pass
        try:
            db.delete_by_path(str(source), paths)
        except Exception:
            pass
        scanner.scan_single(target_path, config, paths)
        return {"status": "fixed", "source": str(source), "target": str(target_path), "format": target_format, "backup": backup}
    except Exception as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return {"status": "error", "source": str(source), "error": str(exc)[:240]}
