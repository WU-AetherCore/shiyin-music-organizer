"""Web API 与媒体端点。"""

from __future__ import annotations

import html
import os
import shutil
import subprocess
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, current_app, jsonify, request, send_file, session, stream_with_context

from .. import __version__, audio, db, jobs
from ..automation import Automation
from ..config import Config, hash_password
from ..logging_setup import get_logger, tail_log
from ..matcher import build_query
from ..paths import AppPaths
from ..providers import (
    COVER_PROVIDERS,
    LYRICS_PROVIDERS,
    provider_catalog,
    provider_self_test,
)
from ..repair import RepairService, track_display
from ..textutil import (
    format_duration,
    format_quality,
    format_size,
    parse_lrc,
)

log = get_logger("web")

bp = Blueprint("api", __name__, url_prefix="/api")
media = Blueprint("media", __name__, url_prefix="/media")


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# ---------------------------------------------------------------------------
# 上下文
# ---------------------------------------------------------------------------

def _cfg() -> Config:
    return current_app.config["MORG_CONFIG"]


def _paths() -> AppPaths:
    return current_app.config["MORG_PATHS"]


def _manager() -> jobs.JobManager:
    return current_app.config["MORG_MANAGER"]


def _automation() -> Automation:
    auto = current_app.config.get("MORG_AUTOMATION")
    if auto is None:
        auto = Automation(_cfg(), _paths(), _manager())
        current_app.config["MORG_AUTOMATION"] = auto
    return auto


def _body() -> Dict[str, Any]:
    if request.is_json:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}
    return {}


def _track_or_404(track_id: int) -> Dict[str, Any]:
    track = db.get_track(track_id, _paths())
    if not track:
        raise ApiError("曲目不存在", 404)
    return track


def _display_title(track: Dict[str, Any]) -> str:
    return track.get("title") or Path(track.get("filename") or "未命名").stem


def track_json(track: Dict[str, Any], base: str = "") -> Dict[str, Any]:
    mtime = int(float(track.get("mtime") or 0))
    track_id = track.get("id")
    quality = format_quality(
        track.get("ext"),
        track.get("sample_rate"),
        track.get("bits"),
        track.get("channels"),
        track.get("bitrate"),
    )
    return {
        "id": track_id,
        "title": _display_title(track),
        "title_raw": track.get("title") or "",
        "artist": track.get("artist") or "",
        "artist_display": track.get("artist") or "未知歌手",
        "album": track.get("album") or "",
        "album_display": track.get("album") or "未知专辑",
        "year": track.get("year") or "",
        "genre": track.get("genre") or "",
        "track_no": track.get("track_no"),
        "disc_no": track.get("disc_no"),
        "filename": track.get("filename") or "",
        "path": track.get("path") or "",
        "ext": (track.get("ext") or "").lstrip("."),
        "size": int(track.get("size") or 0),
        "size_text": format_size(track.get("size")),
        "duration": float(track.get("duration") or 0),
        "duration_text": format_duration(track.get("duration")),
        "sample_rate": track.get("sample_rate") or 0,
        "bits": track.get("bits") or 0,
        "channels": track.get("channels") or 0,
        "quality": quality,
        "has_lyrics": bool(track.get("has_lyrics")),
        "lyrics_synced": bool(track.get("lyrics_synced")),
        "lyrics_source": track.get("lyrics_source") or "",
        "lyrics_timing_status": track.get("lyrics_timing_status") or "",
        "lyrics_timing_issues": track.get("lyrics_timing_issues") or [],
        "has_cover": bool(track.get("has_cover")),
        "cover_source": track.get("cover_source") or "",
        "health": track.get("health") or "ok",
        "tag_issues": track.get("tag_issues") or [],
        "favorite": bool(track.get("favorite")),
        "play_count": int(track.get("play_count") or 0),
        "added_at": track.get("added_at") or "",
        "last_played": track.get("last_played") or "",
        "lyrics_offset_ms": int(track.get("lyrics_offset_ms") or 0),
        "repair_attempts": int(track.get("repair_attempts") or 0),
        "last_repair_status": track.get("last_repair_status") or "",
        "cover_url": f"{base}/media/cover/{track_id}?v={mtime}-{track.get('cover_size') or 0}",
        "stream_url": f"{base}/media/stream/{track_id}?v={mtime}-{track.get('size') or 0}",
    }


def _overview_payload() -> Dict[str, Any]:
    cfg = _cfg()
    paths = _paths()
    stats = db.stats(paths)
    filters = db.filter_counts(paths)
    duplicates = db.find_duplicates(paths=paths)
    auto = _automation()
    recent = [track_json(item) for item in stats.get("recent", [])]
    return {
        "initialized": cfg.is_initialized(),
        "music_dir": cfg.music_dir_str,
        "version": __version__,
        "stats": stats,
        "filters": {**filters, "duplicates": sum(len(group["tracks"]) for group in duplicates)},
        "duplicate_groups": len(duplicates),
        "automation": {
            **(cfg.get("automation") or {}),
            "status": auto.status(),
        },
        "jobs": _manager().list(6),
        "activity": db.recent_activity(8, paths),
        "providers": provider_catalog(),
        "playback": cfg.get("playback") or {"quality": "original"},
        "navidrome": {
            "enabled": bool(cfg.get("navidrome.enabled")),
            "url": cfg.get("navidrome.url") or "",
        },
        "paths": paths.to_dict(),
        "web": {"has_password": cfg.has_web_password, "port": cfg.get("web.port")},
        "recent": recent,
    }


# ---------------------------------------------------------------------------
# 概览 / 初始化
# ---------------------------------------------------------------------------

@bp.get("/overview")
def overview():
    return jsonify(_overview_payload())


@bp.get("/health")
def health():
    try:
        total = db.stats(_paths())["total"]
    except Exception:
        total = 0
    return jsonify({"ok": True, "version": __version__, "initialized": _cfg().is_initialized(), "tracks": total})


ROOT_CANDIDATES = ["/", "/mnt", "/media", "/srv", "/data", "/home", "/opt"]


def _shortcut_roots() -> List[Dict[str, str]]:
    candidates = []
    home = Path.home()
    candidates.append(("用户目录", str(home)))
    for path in ("/mnt", "/media", "/srv", "/data"):
        if Path(path).is_dir():
            candidates.append((path, path))
    music = _cfg().music_dir
    if music:
        candidates.append(("当前音乐目录", str(music)))
    if os.name == "nt":
        import string

        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            if drive.exists():
                candidates.append((f"{letter}: 盘", str(drive)))
    seen = set()
    out = []
    for label, path in candidates:
        if path in seen:
            continue
        seen.add(path)
        out.append({"label": label, "path": path})
    return out


def _audio_count_in(directory: Path) -> int:
    exts = {ext.lower() for ext in (_cfg().get("library.extensions") or audio.READABLE_EXTS)}
    count = 0
    try:
        with os.scandir(directory) as it:
            for entry in it:
                if entry.is_file() and Path(entry.name).suffix.lower() in exts:
                    count += 1
    except OSError:
        pass
    return count


@bp.get("/fs/list")
def fs_list():
    raw = (request.args.get("path") or "").strip()
    roots = _shortcut_roots()
    if not raw:
        return jsonify({"path": "", "parent": "", "roots": roots, "dirs": [], "audio_count": 0})
    target = Path(raw).expanduser()
    try:
        target = target.resolve()
    except OSError:
        raise ApiError("路径无法解析", 400)
    if not target.is_dir():
        raise ApiError("目录不存在", 404)
    dirs = []
    try:
        with os.scandir(target) as it:
            for entry in it:
                try:
                    if not entry.is_dir():
                        continue
                    if entry.name.startswith("."):
                        continue
                    child = Path(entry.path)
                    dirs.append(
                        {
                            "name": entry.name,
                            "path": str(child),
                            "audio_count": _audio_count_in(child),
                            "writable": os.access(child, os.W_OK),
                        }
                    )
                except OSError:
                    continue
    except PermissionError:
        raise ApiError("没有权限读取这个目录", 403)
    dirs.sort(key=lambda item: item["name"].casefold())
    parent = str(target.parent) if target.parent != target else ""
    return jsonify(
        {
            "path": str(target),
            "parent": parent,
            "roots": roots,
            "dirs": dirs[:500],
            "audio_count": _audio_count_in(target),
            "truncated": len(dirs) > 500,
        }
    )


@bp.post("/setup")
def setup():
    data = _body()
    raw = str(data.get("path") or "").strip()
    if not raw:
        raise ApiError("请提供音乐文件夹路径")
    target = Path(raw).expanduser()
    try:
        target = target.resolve()
    except OSError:
        raise ApiError("路径无法解析")
    if not target.is_dir():
        raise ApiError("文件夹不存在")
    if str(target) in ("/", "\\") or (len(str(target)) <= 3 and os.name == "nt"):
        raise ApiError("不能把整个磁盘根目录作为音乐文件夹")
    if not os.access(target, os.R_OK):
        raise ApiError("没有读取权限")
    cfg = _cfg()
    old = cfg.music_dir_str
    reset = bool(data.get("reset")) or bool(old and Path(old) != target)
    cfg.set("music_dir", str(target)).save()
    if reset:
        db.clear_library(_paths())
    job_id = jobs.start_scan(cfg, _paths(), _manager(), full=False)
    db.log_activity("选择音乐目录", "ok", str(target), source="web", paths=_paths())
    return jsonify({"ok": True, "music_dir": str(target), "job_id": job_id, "reset": reset})


# ---------------------------------------------------------------------------
# 曲库
# ---------------------------------------------------------------------------

def _duplicate_ids() -> List[int]:
    groups = db.find_duplicates(paths=_paths())
    ids: List[int] = []
    for group in groups:
        ids.extend(int(track["id"]) for track in group["tracks"])
    return ids


@bp.get("/tracks")
def list_tracks():
    cfg = _cfg()
    query = request.args.get("q", "").strip()
    filter_name = request.args.get("filter", "all") or "all"
    sort = request.args.get("sort", "artist") or "artist"
    page = int(request.args.get("page", 1) or 1)
    page_size = int(request.args.get("page_size", cfg.get("web.page_size") or 60) or 60)
    ids = None
    if filter_name == "duplicates":
        ids = _duplicate_ids()
    items, total = db.list_tracks(
        query=query,
        filter_name=filter_name,
        sort=sort,
        page=page,
        page_size=page_size,
        ids=ids,
        paths=_paths(),
    )
    return jsonify(
        {
            "items": [track_json(item) for item in items],
            "total": total,
            "page": page,
            "page_size": max(1, min(200, page_size)),
            "filter": filter_name,
            "counts": db.filter_counts(_paths()),
        }
    )


@bp.get("/tracks/<int:track_id>")
def track_detail(track_id: int):
    track = _track_or_404(track_id)
    lyrics, source = audio.read_lyrics(track["path"])
    parsed = parse_lrc(lyrics)
    cover = audio.read_cover(track["path"])
    service = RepairService(_cfg(), _paths())
    query = build_query(track)
    latest_backup = audio.latest_backup(track["path"], _paths().backup_dir)
    data = track_json(track)
    data.update(
        {
            "lyrics": {
                "text": lyrics,
                "source": source,
                "synced": parsed["synced"],
                "lines": parsed["lines"],
                "meta": parsed["meta"],
            },
            "cover": {
                "present": bool(cover),
                "width": cover.width if cover else 0,
                "height": cover.height if cover else 0,
                "bytes": len(cover.data) if cover else 0,
                "source": cover.source if cover else "",
            },
            "query": {
                "title": query.title,
                "artist": query.artist,
                "album": query.album,
                "duration": query.duration,
                "search_text": query.search_text,
            },
            "backup": latest_backup or "",
            "exists": Path(track["path"]).is_file(),
        }
    )
    return jsonify(data)


@bp.patch("/tracks/<int:track_id>")
def update_track(track_id: int):
    track = _track_or_404(track_id)
    data = _body()
    if "favorite" in data:
        db.set_favorite(track_id, bool(data["favorite"]), _paths())
    if "offset_ms" in data:
        try:
            offset = int(data["offset_ms"])
        except (TypeError, ValueError):
            raise ApiError("offset_ms 必须是整数")
        db.set_offset(track_id, offset, _paths())
    updated = db.get_track(track_id, _paths()) or track
    return jsonify({"ok": True, "track": track_json(updated)})


@bp.post("/tracks/<int:track_id>/play")
def mark_play(track_id: int):
    _track_or_404(track_id)
    db.mark_played(track_id, _paths())
    return jsonify({"ok": True})


@bp.get("/tracks/<int:track_id>/lyrics")
def get_lyrics(track_id: int):
    track = _track_or_404(track_id)
    text, source = audio.read_lyrics(track["path"])
    parsed = parse_lrc(text)
    return jsonify(
        {
            "text": text,
            "source": source,
            "synced": parsed["synced"],
            "lines": parsed["lines"],
            "meta": parsed["meta"],
            "offset_ms": int(track.get("lyrics_offset_ms") or 0),
        }
    )


def _sync_media_track(track: Dict[str, Any]) -> List[str]:
    """标签/歌词/封面被手动修改后，同步通知已启用的媒体服务器。"""
    try:
        return jobs.notify_media_servers(_cfg(), _paths(), changed_paths=[str(track.get("path") or "")])
    except Exception as exc:
        return [f"媒体服务器同步失败：{str(exc)[:160]}"]


@bp.put("/tracks/<int:track_id>/lyrics")
def put_lyrics(track_id: int):
    track = _track_or_404(track_id)
    data = _body()
    text = str(data.get("text") or "")
    if len(text) > 300_000:
        raise ApiError("歌词过长")
    service = RepairService(_cfg(), _paths())
    try:
        result = service.apply_lyrics_text(track, text, source="网页编辑")
    except (ValueError, FileNotFoundError) as exc:
        raise ApiError(str(exc))
    if "offset_ms" in data:
        db.set_offset(track_id, int(data["offset_ms"]), _paths())
    updated = db.get_track(track_id, _paths()) or track
    return jsonify({"ok": True, "result": result, "track": track_json(updated), "sync": _sync_media_track(updated)})


@bp.post("/tracks/<int:track_id>/tags")
def put_tags(track_id: int):
    track = _track_or_404(track_id)
    data = _body()
    patch = data.get("tags") if isinstance(data.get("tags"), dict) else data
    service = RepairService(_cfg(), _paths())
    try:
        result = service.update_tags(track, patch or {})
    except (ValueError, FileNotFoundError) as exc:
        raise ApiError(str(exc))
    updated = db.get_track(track_id, _paths()) or track
    return jsonify({"ok": True, "result": result, "track": track_json(updated), "sync": _sync_media_track(updated)})


@bp.post("/tracks/<int:track_id>/undo")
def undo_track(track_id: int):
    track = _track_or_404(track_id)
    service = RepairService(_cfg(), _paths())
    try:
        result = service.undo(track)
    except (FileNotFoundError, audio.AudioError) as exc:
        raise ApiError(str(exc), 404)
    updated = db.get_track(track_id, _paths()) or track
    return jsonify({"ok": True, "result": result, "track": track_json(updated), "sync": _sync_media_track(updated)})


# ---------------------------------------------------------------------------
# 修复
# ---------------------------------------------------------------------------

@bp.post("/tracks/<int:track_id>/repair")
def repair_track(track_id: int):
    track = _track_or_404(track_id)
    data = _body()
    targets = data.get("targets") or _cfg().get("repair.targets") or ["lyrics", "cover"]
    targets = [t for t in targets if t in ("lyrics", "cover", "tags")]
    job_id = jobs.start_single(
        _cfg(),
        _paths(),
        _manager(),
        track_id=int(track["id"]),
        targets=targets,
        force=bool(data.get("force")),
        source="web",
    )
    if not job_id:
        running = [job for job in _manager().running("repair") if job.meta.get("track_id") == track_id]
        if running:
            return jsonify({"ok": True, "job_id": running[0].id, "already_running": True})
        raise ApiError("无法创建修复任务")
    return jsonify({"ok": True, "job_id": job_id})


@bp.post("/tracks/<int:track_id>/auto-fix")
def auto_fix_track(track_id: int):
    """播放时按需修复：只在开启开关且确实缺内容时排队。"""
    cfg = _cfg()
    if not cfg.get("automation.fix_on_play", True):
        return jsonify({"queued": False, "reason": "播放时自动修复未开启"})
    track = _track_or_404(track_id)
    missing = []
    if not track.get("has_lyrics"):
        missing.append("lyrics")
    if not track.get("has_cover"):
        missing.append("cover")
    timing_bad = str(track.get("lyrics_timing_status") or "") in ("broken", "suspect")
    if timing_bad and "lyrics" not in missing:
        missing.insert(0, "lyrics")
    if not missing:
        return jsonify({"queued": False, "reason": "歌词与封面都已存在"})
    job_id = jobs.start_single(
        cfg,
        _paths(),
        _manager(),
        track_id=track_id,
        targets=missing,
        force=timing_bad,
        source="play",
    )
    if not job_id:
        return jsonify({"queued": False, "reason": "这首歌正在修复中"})
    return jsonify({"queued": True, "job_id": job_id, "targets": missing, "timing": timing_bad})


@bp.get("/tracks/<int:track_id>/candidates")
def candidates(track_id: int):
    track = _track_or_404(track_id)
    kind = request.args.get("kind", "lyrics")
    if kind not in ("lyrics", "cover"):
        raise ApiError("kind 只能是 lyrics 或 cover")
    service = RepairService(_cfg(), _paths())
    try:
        report = service.search_lyrics(track, limit=10) if kind == "lyrics" else service.search_covers(track, limit=10)
    except Exception as exc:
        raise ApiError(f"检索失败：{exc}", 502)
    payload = report.to_dict(limit=12)
    if kind == "cover":
        for item, hit in zip(payload["hits"], report.hits):
            referer = str(hit.extra.get("referer") or "")
            params = {"url": hit.url}
            if referer:
                params["referer"] = referer
            item["preview_url"] = "/media/preview?" + urllib.parse.urlencode(params)
            item["referer"] = referer
    payload["existing"] = {
        "has_lyrics": bool(track.get("has_lyrics")),
        "has_cover": bool(track.get("has_cover")),
    }
    return jsonify(payload)


@bp.post("/tracks/<int:track_id>/apply")
def apply_candidate(track_id: int):
    track = _track_or_404(track_id)
    data = _body()
    kind = data.get("kind")
    candidate = data.get("candidate") or {}
    service = RepairService(_cfg(), _paths())
    try:
        if kind == "lyrics":
            text = candidate.get("text") if isinstance(candidate, dict) else ""
            if not text:
                raise ApiError("缺少歌词文本")
            result = service.apply_lyrics_text(track, str(text), source=str(candidate.get("provider_label") or "网页选择"))
        elif kind == "cover":
            if not candidate.get("url"):
                raise ApiError("缺少封面地址")
            result = service.apply_cover_candidate(track, candidate)
        else:
            raise ApiError("kind 只能是 lyrics 或 cover")
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(f"应用失败：{exc}", 422)
    updated = db.get_track(track_id, _paths()) or track
    return jsonify({"ok": True, "result": result, "track": track_json(updated), "sync": _sync_media_track(updated)})


@bp.post("/tracks/<int:track_id>/cover-upload")
def cover_upload(track_id: int):
    track = _track_or_404(track_id)
    file = request.files.get("file")
    if not file:
        raise ApiError("没有上传文件")
    data = file.read(12 * 1024 * 1024 + 1)
    if len(data) > 12 * 1024 * 1024:
        raise ApiError("图片不能超过 12MB")
    service = RepairService(_cfg(), _paths())
    try:
        result = service.apply_cover_bytes(track, data, source="上传图片", allow_small=True)
    except Exception as exc:
        raise ApiError(f"封面写入失败：{exc}", 422)
    updated = db.get_track(track_id, _paths()) or track
    return jsonify({"ok": True, "result": result, "track": track_json(updated), "sync": _sync_media_track(updated)})


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------

@bp.post("/scan")
def scan():
    data = _body()
    job_id = jobs.start_scan(_cfg(), _paths(), _manager(), full=bool(data.get("full")))
    running = [job for job in _manager().running("scan") if job.id == job_id]
    return jsonify({"ok": True, "job_id": job_id, "already_running": bool(running)})


@bp.post("/repair/batch")
def repair_batch():
    data = _body()
    cfg = _cfg()
    targets = [t for t in (data.get("targets") or cfg.get("repair.targets") or ["lyrics", "cover"]) if t in ("lyrics", "cover", "tags")]
    if not targets:
        raise ApiError("请至少选择一个修复目标")
    filter_name = str(data.get("filter") or "all")
    if filter_name not in (
        "all",
        "missing_any",
        "missing_cover",
        "missing_lyrics",
        "missing_both",
        "issues",
        "unsynced_lyrics",
        "no_embedded_cover",
        "bad_lyrics",
        "lyrics_timing",
    ):
        raise ApiError("不支持的筛选条件")
    limit = int(data.get("limit") or 0)
    ids = data.get("ids") if isinstance(data.get("ids"), list) else None
    job_id = jobs.start_batch(
        cfg,
        _paths(),
        _manager(),
        ids=ids,
        filter_name=filter_name,
        targets=targets,
        force=bool(data.get("force")),
        dry_run=bool(data.get("dry_run")),
        limit=max(0, min(2000, limit)),
        source="web",
    )
    return jsonify({"ok": True, "job_id": job_id})


@bp.get("/jobs")
def list_jobs():
    return jsonify({"items": _manager().list(int(request.args.get("limit", 20) or 20))})


@bp.get("/jobs/<job_id>")
def get_job(job_id: str):
    job = _manager().get(job_id)
    if not job:
        raise ApiError("任务不存在", 404)
    return jsonify(job.to_dict())


@bp.post("/jobs/<job_id>/cancel")
def cancel_job(job_id: str):
    ok = _manager().cancel(job_id)
    return jsonify({"ok": ok})


# ---------------------------------------------------------------------------
# 重复 / 活动 / 设置
# ---------------------------------------------------------------------------

@bp.get("/duplicates")
def duplicates():
    groups = db.find_duplicates(paths=_paths())
    data = []
    for group in groups:
        data.append(
            {
                "key": group["key"],
                "reason": group["reason"],
                "keep_id": group["keep_id"],
                "tracks": [track_json(track) for track in group["tracks"]],
            }
        )
    return jsonify({"groups": data, "total": sum(len(item["tracks"]) for item in data)})


@bp.post("/duplicates/trash")
def trash_duplicates():
    data = _body()
    ids = data.get("remove_ids") or []
    if not isinstance(ids, list) or not ids:
        raise ApiError("请选择要移入回收站的文件")
    if len(ids) > 100:
        raise ApiError("一次最多处理 100 个文件")
    cfg = _cfg()
    music_dir = cfg.music_dir
    if not music_dir:
        raise ApiError("尚未设置音乐目录")
    moved = []
    errors = []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for track_id in ids:
        track = db.get_track(int(track_id), _paths())
        if not track:
            errors.append({"id": track_id, "error": "记录不存在"})
            continue
        source = Path(track["path"])
        try:
            source.resolve().relative_to(music_dir.resolve())
        except (ValueError, OSError):
            errors.append({"id": track_id, "error": "文件不在音乐目录内"})
            continue
        if not source.is_file():
            errors.append({"id": track_id, "error": "文件不存在"})
            continue
        try:
            relative = source.resolve().relative_to(music_dir.resolve())
        except ValueError:
            relative = Path(source.name)
        target = _paths().trash_dir / stamp / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        counter = 1
        while target.exists():
            target = target.with_name(f"{target.stem} ({counter}){target.suffix}")
            counter += 1
        try:
            shutil.move(str(source), str(target))
            db.delete_track(int(track_id), _paths())
            moved.append({"id": track_id, "target": str(target), "title": track_display(track)})
        except OSError as exc:
            errors.append({"id": track_id, "error": str(exc)})
    if moved:
        db.log_activity(
            "移入回收站",
            "ok",
            f"移动 {len(moved)} 个文件到 {_paths().trash_dir / stamp}",
            source="web",
            paths=_paths(),
        )
    return jsonify({"ok": True, "moved": moved, "errors": errors, "trash_dir": str(_paths().trash_dir / stamp)})


@bp.get("/activity")
def activity():
    limit = int(request.args.get("limit", 40) or 40)
    return jsonify({"items": db.recent_activity(max(1, min(200, limit)), _paths())})


@bp.get("/logs")
def logs():
    limit = int(request.args.get("limit", 200) or 200)
    return jsonify({"lines": tail_log(_paths(), max(1, min(1000, limit)))})


@bp.get("/settings")
def get_settings():
    cfg = _cfg()
    return jsonify(
        {
            "config": cfg.as_dict(),
            "paths": cfg.paths.to_dict(),
            "providers": provider_catalog(),
            "has_password": cfg.has_web_password,
            "has_jellyfin_key": bool(cfg.get("jellyfin.api_key")),
            "music_dir": cfg.music_dir_str,
        }
    )


@bp.put("/settings")
def put_settings():
    data = _body()
    cfg = _cfg()
    patch = data.get("config") if isinstance(data.get("config"), dict) else data
    allowed = {"library", "repair", "providers", "playback", "automation", "navidrome", "jellyfin", "ui"}
    clean: Dict[str, Any] = {}
    for key in allowed:
        if isinstance(patch.get(key), dict):
            clean[key] = patch[key]
    automation_patch = clean.get("automation")
    if isinstance(automation_patch, dict):
        for key in ("last_scan_at", "last_fix_at", "last_schedule_date"):
            automation_patch.pop(key, None)
    web_patch = patch.get("web") if isinstance(patch.get("web"), dict) else {}
    if web_patch.get("port") is not None:
        try:
            port = int(web_patch["port"])
            if not 1 <= port <= 65535:
                raise ValueError
            cfg.set("web.port", port)
        except (TypeError, ValueError):
            raise ApiError("端口必须是 1-65535 的整数")
    if web_patch.get("host"):
        cfg.set("web.host", str(web_patch["host"]))

    if "new_password" in data:
        password = str(data.get("new_password") or "")
        if len(password) < 4:
            raise ApiError("密码至少 4 位")
        cfg.set("web.password_hash", hash_password(password))
        # 当前会话已经通过验证（或原本无密码），避免设置后立刻被踢下线
        session.permanent = True
        session["auth"] = True
    if data.get("clear_password"):
        cfg.set("web.password_hash", "")

    if isinstance(clean.get("providers"), dict):
        for key, registry in (("lyrics", LYRICS_PROVIDERS), ("cover", COVER_PROVIDERS)):
            names = clean["providers"].get(key)
            if names is None:
                continue
            if not isinstance(names, list) or not names:
                raise ApiError(f"至少保留一个{'歌词' if key == 'lyrics' else '封面'}数据源")
            unknown = [name for name in names if name not in registry]
            if unknown:
                raise ApiError(f"未知数据源：{', '.join(unknown)}")
    if isinstance(clean.get("navidrome"), dict):
        for key in ("password",):
            if key in clean["navidrome"] and clean["navidrome"][key] in (None, ""):
                clean["navidrome"].pop(key, None)
    if isinstance(clean.get("jellyfin"), dict):
        # 留空表示保持原 Key，避免用户每次保存都要重新粘贴
        if "api_key" in clean["jellyfin"] and clean["jellyfin"]["api_key"] in (None, ""):
            clean["jellyfin"].pop("api_key", None)

    cfg.update(clean, save=True)
    errors = cfg.validate()
    db.log_activity("更新设置", "ok" if not errors else "warn", "；".join(errors)[:300], source="web", paths=_paths())
    return jsonify({"ok": True, "config": cfg.as_dict(), "errors": errors})


@bp.post("/providers/test")
def test_providers():
    results = provider_self_test(_cfg())
    return jsonify({"ok": True, "results": results})


def _record_integration_status(prefix: str, ok: bool, message: str) -> Dict[str, Any]:
    cfg = _cfg()
    at = datetime.now().isoformat(timespec="seconds")
    cfg.set(f"{prefix}.last_sync_at", at)
    cfg.set(f"{prefix}.last_sync_ok", bool(ok))
    cfg.set(f"{prefix}.last_sync_message", str(message or "")[:300])
    cfg.save()
    return {"at": at, "ok": bool(ok), "message": str(message or "")[:300]}


@bp.post("/navidrome/test")
def navidrome_test():
    from ..navidrome import check

    ok, info = check(_cfg(), _paths())
    return jsonify({"ok": True, "connected": ok, "info": info})


@bp.post("/navidrome/rescan")
def navidrome_rescan():
    from ..navidrome import refresh_missing_artwork, remap_missing_files, trigger_rescan

    ok, message = trigger_rescan(_cfg(), _paths(), full=True)
    try:
        _remap_ok, remap_message = remap_missing_files(_cfg(), _paths())
        message = f"{message}；{remap_message}"
        _art_ok, art_message = refresh_missing_artwork(_cfg(), _paths())
        message = f"{message}；{art_message}"
    except Exception as exc:
        message = f"{message}；封面/缺失记录重试失败：{exc}"
    return jsonify({"ok": ok, "message": message, "last_sync": _record_integration_status("navidrome", ok, message)})


@bp.post("/jellyfin/test")
def jellyfin_test():
    from ..jellyfin import check

    data = _body()
    base = _cfg()
    cfg = Config(base.as_dict(include_secrets=True), base.paths)
    if data:
        patch = {"enabled": True}
        for key in ("url", "api_key"):
            if data.get(key):
                patch[key] = str(data[key])
        cfg.update({"jellyfin": patch}, save=False)
    ok, info = check(cfg, _paths())
    return jsonify({"ok": True, "connected": ok, "info": info})


@bp.post("/jellyfin/refresh")
def jellyfin_refresh():
    from ..jellyfin import refresh

    data = _body()
    base = _cfg()
    cfg = Config(base.as_dict(include_secrets=True), base.paths)
    if data:
        patch = {"enabled": True}
        for key in ("url", "api_key"):
            if data.get(key):
                patch[key] = str(data[key])
        cfg.update({"jellyfin": patch}, save=False)
    ok, message = refresh(cfg, _paths())
    return jsonify(
        {"ok": True, "refreshed": ok, "message": message, "last_sync": _record_integration_status("jellyfin", ok, message)}
    )


@bp.get("/lrc/scan")
def lrc_scan():
    from ..lrcfiles import scan_lrc_files

    try:
        data = scan_lrc_files(_cfg(), _paths())
    except FileNotFoundError as exc:
        raise ApiError(str(exc), 404)
    return jsonify(
        {
            "ok": True,
            "counts": data["counts"],
            "entries": [entry.to_dict() for entry in data["entries"]],
        }
    )


@bp.post("/lrc/clean")
def lrc_clean():
    from ..lrcfiles import clean_lrc_files, scan_lrc_files

    body = _body()
    statuses = body.get("statuses") or ["redundant", "orphan"]
    if not isinstance(statuses, list):
        raise ApiError("statuses 必须是数组")
    dry_run = bool(body.get("dry_run"))
    try:
        data = scan_lrc_files(_cfg(), _paths())
        result = clean_lrc_files(_cfg(), data["entries"], statuses, _paths(), dry_run=dry_run)
    except (ValueError, FileNotFoundError) as exc:
        raise ApiError(str(exc))
    return jsonify({"ok": True, **result})


@bp.post("/lrc/embed")
def lrc_embed():
    job_id = jobs.start_lrc_embed(_cfg(), _paths(), _manager())
    if not job_id:
        return jsonify({"ok": True, "job_id": "", "reason": "没有仅外置的 .lrc 需要嵌入"})
    return jsonify({"ok": True, "job_id": job_id})


@bp.get("/export/m3u")
def export_m3u():
    cfg = _cfg()
    filter_name = request.args.get("filter", "all")
    query = request.args.get("q", "").strip()
    tracks = db.all_tracks(_paths())
    if query:
        needle = query.casefold()
        tracks = [
            track
            for track in tracks
            if needle in (track.get("title") or "").casefold()
            or needle in (track.get("artist") or "").casefold()
            or needle in (track.get("album") or "").casefold()
            or needle in (track.get("filename") or "").casefold()
        ]
    if filter_name == "missing_cover":
        tracks = [track for track in tracks if not track.get("has_cover")]
    elif filter_name == "missing_lyrics":
        tracks = [track for track in tracks if not track.get("has_lyrics")]
    elif filter_name == "favorite":
        tracks = [track for track in tracks if track.get("favorite")]
    lines = ["#EXTM3U"]
    for track in tracks:
        lines.append(f"#EXTINF:{int(round(float(track.get('duration') or 0)))},{track.get('artist') or ''} - {_display_title(track)}")
        lines.append(track["path"])
    text = "\n".join(lines) + "\n"
    filename = f"morg-playlist-{datetime.now().strftime('%Y%m%d-%H%M')}.m3u"
    return Response(
        text,
        mimetype="audio/x-mpegurl",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# 媒体端点
# ---------------------------------------------------------------------------

def _fallback_cover(title: str) -> bytes:
    seed = sum(ord(ch) for ch in (title or "music"))
    palettes = [
        ("#1b2140", "#7c5cff", "#38e8ff"),
        ("#241426", "#ff5c8a", "#ffb648"),
        ("#0e2a2a", "#3ddc97", "#38e8ff"),
        ("#2b1a12", "#ff8a4c", "#ffd166"),
    ]
    a, b, c = palettes[seed % len(palettes)]
    letter = html.escape((title or "♪")[:1].upper())
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600" viewBox="0 0 600 600">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/></linearGradient></defs>
<rect width="600" height="600" fill="url(#g)"/>
<circle cx="300" cy="300" r="180" fill="none" stroke="{c}" stroke-opacity=".55" stroke-width="3"/>
<circle cx="300" cy="300" r="130" fill="none" stroke="{c}" stroke-opacity=".35" stroke-width="2"/>
<text x="300" y="330" text-anchor="middle" font-family="Arial, sans-serif" font-size="120" fill="#ffffff" fill-opacity=".92">{letter}</text>
</svg>"""
    return svg.encode("utf-8")


@media.get("/cover/<int:track_id>")
def media_cover(track_id: int):
    track = db.get_track(track_id, _paths())
    if not track:
        return Response(status=404)
    etag = f'"{int(float(track.get("mtime") or 0))}-{track.get("cover_size") or 0}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304)
    cover = audio.read_cover(track["path"])
    if cover:
        resp = Response(cover.data, mimetype=cover.mime or "image/jpeg")
    else:
        resp = Response(_fallback_cover(_display_title(track)), mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    resp.headers["ETag"] = etag
    return resp


PLAYBACK_QUALITIES: Dict[str, tuple] = {
    "mp3_320": ("audio/mpeg", ["-c:a", "libmp3lame", "-b:a", "320k", "-f", "mp3"]),
    "mp3_192": ("audio/mpeg", ["-c:a", "libmp3lame", "-b:a", "192k", "-f", "mp3"]),
    "mp3_128": ("audio/mpeg", ["-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3"]),
    "aac_256": (
        "audio/mp4",
        ["-c:a", "aac", "-b:a", "256k", "-f", "mp4", "-movflags", "frag_keyframe+empty_moov"],
    ),
    "opus_192": ("audio/ogg", ["-c:a", "libopus", "-b:a", "192k", "-f", "ogg"]),
}


def _transcoded_response(path: Path, quality: str):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return Response("系统未安装 ffmpeg，无法转码播放", status=503, mimetype="text/plain")
    mimetype, codec_args = PLAYBACK_QUALITIES[quality]

    def generate():
        proc = subprocess.Popen(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-i",
                str(path),
                "-map",
                "0:a",
                "-map_metadata",
                "0",
                "-vn",
                *codec_args,
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            assert proc.stdout is not None
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass

    resp = Response(stream_with_context(generate()), mimetype=mimetype)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Accept-Ranges"] = "none"
    resp.headers["X-Playback-Quality"] = quality
    return resp


@media.get("/stream/<int:track_id>")
def media_stream(track_id: int):
    track = db.get_track(track_id, _paths())
    if not track:
        return Response(status=404)
    path = Path(track["path"])
    if not path.is_file():
        return Response(status=404)
    requested_quality = str(request.args.get("quality") or (_cfg().get("playback.quality") or "original"))
    if requested_quality in PLAYBACK_QUALITIES:
        return _transcoded_response(path, requested_quality)
    mimetypes = {
        ".flac": "audio/flac",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".wav": "audio/wav",
        ".aiff": "audio/aiff",
        ".aif": "audio/aiff",
        ".aac": "audio/aac",
        ".ape": "audio/x-ape",
        ".wv": "audio/x-wavpack",
    }
    resp = send_file(
        path,
        mimetype=mimetypes.get(path.suffix.lower(), "application/octet-stream"),
        conditional=True,
        etag=True,
        last_modified=path.stat().st_mtime,
        max_age=0,
    )
    resp.headers["Accept-Ranges"] = "bytes"
    # 原文件直通，不做转码/压缩；no-transform 避免中间代理二次处理
    resp.headers["Cache-Control"] = "public, max-age=0, no-transform"
    return resp


@media.get("/preview")
def media_preview():
    from ..providers import download_image, is_allowed_image_url

    url = request.args.get("url", "")
    referer = request.args.get("referer", "")
    if not is_allowed_image_url(url):
        return Response(status=403)
    try:
        data, mime = download_image(url, referer=referer, config=_cfg())
    except Exception:
        return Response(_fallback_cover(url), mimetype="image/svg+xml")
    resp = Response(data, mimetype=mime or "image/jpeg")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


# ---------------------------------------------------------------------------
# 错误处理
# ---------------------------------------------------------------------------

@bp.errorhandler(ApiError)
def handle_api_error(exc: ApiError):
    return jsonify({"ok": False, "error": exc.message}), exc.status


@bp.errorhandler(Exception)
def handle_unexpected(exc: Exception):
    log.exception("API 未处理异常：%s", exc)
    return jsonify({"ok": False, "error": f"服务器错误：{exc}"}), 500
