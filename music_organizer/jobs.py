"""后台任务队列：扫描、批量修复、单曲修复、播放时修复。

所有耗时操作都在后台线程里执行，Web 前端通过 ``/api/jobs/<id>`` 轮询进度。
同一时间只允许一个网络修复任务，避免触发数据源限流；扫描任务可以并行。
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence

from . import db, scanner
from . import conversion
from . import duplicates as duplicate_service
from .config import Config
from .paths import AppPaths, get_paths
from .repair import RepairService, track_display


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    kind: str
    title: str
    status: str = "queued"
    total: int = 0
    done: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    message: str = "等待开始"
    current: str = ""
    created_at: str = field(default_factory=_now)
    started_at: str = ""
    finished_at: str = ""
    result: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    source: str = "web"
    cancel_event: threading.Event = field(default_factory=threading.Event)
    logs: Deque[str] = field(default_factory=lambda: deque(maxlen=160))
    items: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0 if self.status != "done" else 100
        return max(0, min(100, int(round(self.done * 100 / self.total))))

    @property
    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        try:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.finished_at) if self.finished_at else datetime.now()
            return max(0.0, (end - start).total_seconds())
        except ValueError:
            return 0.0

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{stamp}] {message}")

    def progress(self, done: int, total: int, current: str = "") -> None:
        self.total = max(0, int(total))
        self.done = max(0, int(done))
        if current:
            self.current = Path(current).name if ("/" in current or "\\" in current) else current

    def add_item(self, item: Dict[str, Any]) -> None:
        if len(self.items) < 600:
            self.items.append(item)

    def to_dict(self, include_logs: bool = True) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "percent": self.percent,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "skipped": self.skipped,
            "message": self.message,
            "current": self.current,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed": round(self.elapsed, 1),
            "result": self.result,
            "error": self.error,
            "source": self.source,
            "meta": self.meta,
        }
        if include_logs:
            data["logs"] = list(self.logs)
            data["items"] = self.items[-200:]
        return data


class JobManager:
    def __init__(self, config: Config, paths: Optional[AppPaths] = None):
        self.config = config
        self.paths = paths or get_paths()
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="morg-job")
        self._repair_semaphore = threading.Semaphore(1)
        self._active_tracks: set = set()
        self._shutdown = False

    # ------------------------------------------------------------------
    def submit(
        self,
        kind: str,
        title: str,
        runner: Callable[[Job], Dict[str, Any]],
        source: str = "web",
        meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        job = Job(id=uuid.uuid4().hex[:8], kind=kind, title=title, source=source, meta=meta or {})
        with self._lock:
            self._jobs[job.id] = job
            self._trim()
        job.log(f"任务已创建：{title}")

        def wrapper() -> None:
            if self._shutdown:
                job.status = "cancelled"
                return
            job.status = "running"
            job.started_at = _now()
            job.message = "执行中"
            try:
                if kind in ("repair", "repair_one"):
                    with self._repair_semaphore:
                        job.result = runner(job) or {}
                else:
                    job.result = runner(job) or {}
                if job.cancel_event.is_set():
                    job.status = "cancelled"
                    job.message = "已取消"
                elif job.status != "error":
                    job.status = "done"
                    job.message = job.message if job.message not in ("执行中", "") else "完成"
                job.log(job.message)
            except Exception as exc:  # 保证任务异常不会杀死线程池
                job.status = "error"
                job.error = str(exc)[:500]
                job.message = f"失败：{job.error}"
                job.log(job.message)
            finally:
                job.finished_at = _now()
                self._release_track(job)

        self._executor.submit(wrapper)
        return job.id

    def _release_track(self, job: Job) -> None:
        track_id = job.meta.get("track_id")
        if track_id:
            with self._lock:
                self._active_tracks.discard(int(track_id))

    def _trim(self) -> None:
        if len(self._jobs) <= 60:
            return
        finished = [j for j in self._jobs.values() if j.status in ("done", "error", "cancelled")]
        finished.sort(key=lambda j: j.finished_at or j.created_at)
        for job in finished[: len(self._jobs) - 60]:
            self._jobs.pop(job.id, None)

    # ------------------------------------------------------------------
    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return [job.to_dict(include_logs=False) for job in jobs[:limit]]

    def running(self, kind_prefix: str = "") -> List[Job]:
        with self._lock:
            return [
                job
                for job in self._jobs.values()
                if job.status in ("queued", "running") and job.kind.startswith(kind_prefix)
            ]

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job or job.status not in ("queued", "running"):
            return False
        job.cancel_event.set()
        job.message = "正在取消…"
        job.log("收到取消请求")
        return True

    def active_repair_count(self) -> int:
        return len(self.running("repair"))

    def shutdown(self) -> None:
        self._shutdown = True
        for job in self.running():
            job.cancel_event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# 具体任务
# ---------------------------------------------------------------------------

def _auto_fix_new(config: Config, paths: AppPaths, manager: JobManager, new_ids: Sequence[int], source: str = "auto-new") -> Optional[str]:
    if not new_ids:
        return None
    if not config.get("automation.auto_fix_new", True):
        return None
    targets = list(config.get("automation.targets") or ["lyrics", "cover"])
    limit = int(config.get("automation.max_fix_per_run") or 40)
    tracks = db.tracks_by_ids(new_ids[: max(limit * 3, limit)], paths)
    need = [
        track
        for track in tracks
        if not track.get("no_lyrics")
        and (
            ("lyrics" in targets and not track.get("has_lyrics"))
            or ("cover" in targets and not track.get("has_cover"))
            or ("lyrics" in targets and str(track.get("lyrics_timing_status") or "") in ("broken", "suspect"))
        )
    ][:limit]
    if not need:
        return None
    return start_batch(
        config,
        paths,
        manager,
        ids=[int(track["id"]) for track in need],
        targets=targets,
        force=False,
        dry_run=False,
        source=source,
        title=f"新音乐自动修复 · {len(need)} 首",
    )


def start_scan(config: Config, paths: Optional[AppPaths] = None, manager: Optional[JobManager] = None, full: bool = False) -> str:
    paths = paths or get_paths()
    manager = manager or get_manager(config, paths)
    for job in manager.running("scan"):
        return job.id

    def runner(job: Job) -> Dict[str, Any]:
        job.message = "正在扫描音乐文件夹…"
        result = scanner.scan_library(
            config,
            paths,
            full=full,
            progress=lambda done, total, current: job.progress(done, total, current),
            cancel=job.cancel_event,
        )
        job.succeeded = result.added + result.updated
        job.failed = result.errors
        job.done = result.total
        job.message = f"扫描完成：共 {result.total} 首，新增 {result.added}，更新 {result.updated}"
        job.log(job.message)
        config.set("automation.last_scan_at", _now()).save()
        try:
            org = config.get("organization") or {}
            if org.get("enabled") and result.new_ids and str(org.get("mode") or "") != "keep":
                from .filemanager import FileManager

                outcome = FileManager(config, paths).auto_organize_ids(
                    result.new_ids,
                    mode=str(org.get("mode") or "artist_album"),
                    base_dir=str(org.get("base_dir") or "其他"),
                    file_template=str(org.get("file_template") or "{artist} - {title}"),
                    keep_artists=str(org.get("keep_artists") or ""),
                )
                config.set("organization.last_run_at", _now())
                config.set("organization.last_ok", not outcome.get("errors"))
                config.set("organization.last_result", f"新歌自动整理：成功 {outcome.get('applied', 0)}，失败 {len(outcome.get('errors') or [])}")
                config.set("organization.last_count", int(outcome.get("applied", 0)))
                config.save()
                job.log(f"新歌自动整理：成功 {outcome.get('applied', 0)} 首")
        except Exception as exc:
            job.log(f"新歌自动整理失败：{exc}")
        try:
            if config.get("duplicates.auto_clean"):
                outcome = duplicate_service.clean_confident(config, paths, source="duplicates-auto")
                config.set("duplicates.last_run_at", _now())
                config.set("duplicates.last_ok", not outcome.get("errors"))
                config.set("duplicates.last_result", f"自动清理 {len(outcome.get('moved') or [])} 首")
                config.set("duplicates.last_count", len(outcome.get("moved") or []))
                config.save()
                if outcome.get("moved"):
                    job.log(f"重复清理：{len(outcome['moved'])} 首移入回收站")
        except Exception as exc:
            job.log(f"重复清理失败：{exc}")
        try:
            _notify_media_servers(config, paths, manager, job)
        except Exception as exc:
            job.log(f"媒体服务器同步失败：{exc}")
        try:
            _auto_fix_new(config, paths, manager, result.new_ids)
        except Exception as exc:
            job.log(f"自动修复排队失败：{exc}")
        try:
            if (
                config.get("conversion.enabled")
                and str(config.get("conversion.mode") or "preserve") != "preserve"
                and config.get("conversion.replace_original", False)
            ):
                pending = conversion.candidates(config, paths)
                if pending:
                    start_convert(config, paths, manager, source="auto")
                    job.log(f"格式转换已排队：{len(pending)} 首")
        except Exception as exc:
            job.log(f"自动格式转换排队失败：{exc}")
        return result.to_dict()

    return manager.submit("scan", "扫描曲库", runner, source="auto" if manager else "web")


def start_batch(
    config: Config,
    paths: Optional[AppPaths] = None,
    manager: Optional[JobManager] = None,
    ids: Optional[Sequence[int]] = None,
    filter_name: str = "all",
    targets: Optional[Sequence[str]] = None,
    force: bool = False,
    dry_run: bool = False,
    limit: int = 0,
    source: str = "web",
    title: Optional[str] = None,
) -> str:
    paths = paths or get_paths()
    manager = manager or get_manager(config, paths)
    target_list = list(targets or config.get("repair.targets") or ["lyrics", "cover"])

    tracks: List[Dict[str, Any]] = []
    if ids:
        tracks = db.tracks_by_ids(ids, paths)
    else:
        id_list = db.ids_for_filter(filter_name, limit=limit or None, paths=paths)
        tracks = db.tracks_by_ids(id_list, paths)
    if limit:
        tracks = tracks[:limit]
    tracks = [track for track in tracks if Path(track.get("path") or "").is_file()]

    def runner(job: Job) -> Dict[str, Any]:
        service = RepairService(config, paths)
        total = len(tracks)
        job.total = total
        job.message = "准备修复…"
        job.log(f"待处理 {total} 首，目标：{'、'.join(_target_label(t) for t in target_list)}")
        changed_paths: List[str] = []
        for index, track in enumerate(tracks, start=1):
            if job.cancel_event.is_set():
                break
            job.current = track_display(track)[:80]
            try:
                outcome = service.repair(
                    track,
                    targets=target_list,
                    force=force,
                    dry_run=dry_run,
                    cancel=job.cancel_event,
                )
                data = outcome.to_dict()
            except Exception as exc:
                data = {
                    "track_id": track.get("id"),
                    "title": track_display(track),
                    "status": "error",
                    "message": str(exc)[:200],
                }
            status = data.get("status")
            if status in ("fixed", "partial"):
                job.succeeded += 1
                changed_paths.append(str(track.get("path") or ""))
            elif status in ("skipped", "dry_run"):
                job.skipped += 1
            elif status in ("not_found",):
                job.skipped += 1
            else:
                job.failed += 1
            job.add_item(
                {
                    "track_id": data.get("track_id"),
                    "title": data.get("title"),
                    "status": status,
                    "message": data.get("message", ""),
                    "lyrics": (data.get("lyrics") or {}).get("applied"),
                    "cover": (data.get("cover") or {}).get("applied"),
                }
            )
            if status in ("fixed", "partial", "error"):
                job.log(f"{data.get('title')} → {_status_label(status)} {data.get('message', '')}")
            job.progress(index, total)
        job.message = (
            f"完成 {job.done}/{job.total}：成功 {job.succeeded}，跳过 {job.skipped}，失败 {job.failed}"
            if not job.cancel_event.is_set()
            else f"已取消（完成 {job.done}/{job.total}）"
        )
        config.set("automation.last_fix_at", _now()).save()
        _notify_media_servers(config, paths, manager, job, changed_paths=changed_paths)
        return {
            "total": job.total,
            "succeeded": job.succeeded,
            "skipped": job.skipped,
            "failed": job.failed,
            "dry_run": dry_run,
            "targets": target_list,
        }

    label = title or f"批量修复 · {len(tracks)} 首"
    return manager.submit("repair", label, runner, source=source, meta={"filter": filter_name, "count": len(tracks)})


def start_convert(
    config: Config,
    paths: Optional[AppPaths] = None,
    manager: Optional[JobManager] = None,
    ids: Optional[Sequence[int]] = None,
    dry_run: bool = False,
    limit: int = 0,
    source: str = "web",
) -> Optional[str]:
    paths = paths or get_paths()
    manager = manager or get_manager(config, paths)
    running = manager.running("convert")
    if running:
        return running[0].id
    pending = conversion.candidates(config, paths, ids=ids, limit=limit)
    if not pending:
        return None

    def runner(job: Job) -> Dict[str, Any]:
        # 新歌导入时会先排队自动修复歌词/封面；转换要等它们写完再复制，
        # 否则转换后的文件会丢掉刚补的歌词/封面，修复任务也可能找不到原文件。
        wait_until = time.time() + 300
        while manager.running("repair") and not job.cancel_event.is_set() and time.time() < wait_until:
            job.message = "等待歌词/封面修复完成…"
            time.sleep(0.5)
        job.total = len(pending)
        job.message = "准备转换格式…"
        changed: List[str] = []
        for index, track in enumerate(pending, start=1):
            if job.cancel_event.is_set():
                break
            job.current = track_display(track)[:80]
            result = conversion.convert_file(track, config, paths, dry_run=dry_run)
            status = result.get("status")
            if status == "fixed":
                job.succeeded += 1
                if result.get("target"):
                    changed.append(str(result["target"]))
            elif status in ("skipped", "dry_run"):
                job.skipped += 1
            else:
                job.failed += 1
            job.add_item(
                {
                    "track_id": track.get("id"),
                    "title": track_display(track),
                    "status": status,
                    "message": result.get("error") or result.get("reason") or result.get("target") or "",
                }
            )
            job.progress(index, len(pending))
        job.message = (
            f"完成 {job.done}/{job.total}：成功 {job.succeeded}，跳过 {job.skipped}，失败 {job.failed}"
            if not job.cancel_event.is_set()
            else f"已取消（完成 {job.done}/{job.total}）"
        )
        config.set("conversion.last_run_at", _now())
        config.set("conversion.last_ok", job.failed == 0)
        config.set("conversion.last_result", job.message)
        config.set("conversion.last_count", job.succeeded)
        config.save()
        if changed:
            _notify_media_servers(config, paths, manager, job, changed_paths=changed)
        return {
            "total": job.total,
            "succeeded": job.succeeded,
            "skipped": job.skipped,
            "failed": job.failed,
            "dry_run": dry_run,
        }

    return manager.submit(
        "convert",
        f"格式转换 · {len(pending)} 首",
        runner,
        source=source,
        meta={"dry_run": dry_run, "count": len(pending)},
    )


def start_single(
    config: Config,
    paths: Optional[AppPaths] = None,
    manager: Optional[JobManager] = None,
    track_id: int = 0,
    targets: Optional[Sequence[str]] = None,
    force: bool = False,
    source: str = "web",
) -> Optional[str]:
    paths = paths or get_paths()
    manager = manager or get_manager(config, paths)
    track = db.get_track(track_id, paths)
    if not track:
        return None
    with manager._lock:
        if track_id in manager._active_tracks:
            return None
        manager._active_tracks.add(track_id)

    def runner(job: Job) -> Dict[str, Any]:
        service = RepairService(config, paths)
        job.total = 1
        job.current = track_display(track)
        outcome = service.repair(track, targets=targets, force=force, cancel=job.cancel_event)
        data = outcome.to_dict()
        if outcome.status in ("fixed", "partial"):
            job.succeeded = 1
        elif outcome.status in ("skipped", "not_found"):
            job.skipped = 1
        else:
            job.failed = 1
        job.done = 1
        job.message = f"{data['title']}：{_status_label(outcome.status)} {outcome.message}"
        job.add_item(
            {
                "track_id": track_id,
                "title": data["title"],
                "status": outcome.status,
                "message": outcome.message,
                "lyrics": (data.get("lyrics") or {}).get("applied"),
                "cover": (data.get("cover") or {}).get("applied"),
            }
        )
        job.result = data
        if outcome.status in ("fixed", "partial"):
            _notify_media_servers(config, paths, manager, job, changed_paths=[str(track.get("path") or "")])
        return data

    return manager.submit(
        "repair_one",
        f"修复 · {track_display(track)}",
        runner,
        source=source,
        meta={"track_id": track_id},
    )


def start_lrc_embed(
    config: Config,
    paths: Optional[AppPaths] = None,
    manager: Optional[JobManager] = None,
) -> Optional[str]:
    """把当前所有“仅外置”的 .lrc 写入音频内嵌歌词。"""
    from .lrcfiles import embed_sidecar_lyrics, scan_lrc_files

    paths = paths or get_paths()
    manager = manager or get_manager(config, paths)
    data = scan_lrc_files(config, paths)
    entries = [e for e in data["entries"] if e.status == "sidecar_only" and e.audio_path]
    if not entries:
        return None

    def runner(job: Job) -> Dict[str, Any]:
        job.total = len(entries)
        job.message = "正在把外置歌词写入音频标签…"
        result = embed_sidecar_lyrics(
            config,
            entries,
            paths,
            progress=lambda done, total, title: (job.progress(done, total), setattr(job, "current", title)),
            cancel=job.cancel_event,
        )
        job.succeeded = int(result.get("embedded") or 0)
        job.failed = int(result.get("failed") or 0)
        job.done = min(job.total, job.succeeded + job.failed)
        job.message = f"嵌入完成：成功 {job.succeeded}，失败 {job.failed}"
        job.result = result
        if job.succeeded:
            _notify_media_servers(
                config,
                paths,
                manager,
                job,
                changed_paths=[str(entry.audio_path) for entry in entries if entry.audio_path],
            )
        return result

    return manager.submit("lrc_embed", f"嵌入外置歌词 · {len(entries)} 首", runner, source="web")


# ---------------------------------------------------------------------------
# 全局管理器
# ---------------------------------------------------------------------------

_manager: Optional[JobManager] = None
_manager_lock = threading.Lock()


def get_manager(config: Config, paths: Optional[AppPaths] = None) -> JobManager:
    global _manager
    paths = paths or get_paths()
    with _manager_lock:
        if _manager is None or _manager.paths.home != paths.home:
            if _manager is not None:
                _manager.shutdown()
            _manager = JobManager(config, paths)
        else:
            _manager.config = config
        return _manager


def reset_manager() -> None:
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.shutdown()
        _manager = None


def _target_label(target: str) -> str:
    return {"lyrics": "歌词", "cover": "封面", "tags": "标签"}.get(target, target)


def _status_label(status: str) -> str:
    return {
        "fixed": "已修复",
        "partial": "部分修复",
        "not_found": "未找到",
        "skipped": "跳过",
        "error": "失败",
        "dry_run": "预览",
    }.get(status, status)


def notify_media_servers(
    config: Config,
    paths: AppPaths,
    changed_paths: Sequence[str] = (),
    log: Optional[Callable[[str], None]] = None,
) -> List[str]:
    """通知 Navidrome/Jellyfin；返回日志行，供任务和同步接口复用。"""
    messages: List[str] = []
    emit = log or messages.append
    status_changed = False

    def record(prefix: str, ok: bool, message: str) -> None:
        nonlocal status_changed
        config.set(f"{prefix}.last_sync_at", _now())
        config.set(f"{prefix}.last_sync_ok", bool(ok))
        config.set(f"{prefix}.last_sync_message", str(message or "")[:300])
        status_changed = True

    if config.get("navidrome.enabled") and config.get("navidrome.rescan_after_fix"):
        try:
            from .navidrome import trigger_rescan

            ok, message = trigger_rescan(config, paths, full=True)
            try:
                from .navidrome import refresh_missing_artwork, remap_missing_files

                _remap_ok, remap_message = remap_missing_files(config, paths)
                message = f"{message}；{remap_message}"
                _art_ok, art_message = refresh_missing_artwork(config, paths)
                message = f"{message}；{art_message}"
            except Exception as exc:
                message = f"{message}；封面/缺失记录重试失败：{exc}"
            record("navidrome", ok, message)
            emit(("Navidrome 重新扫描：成功" if ok else "Navidrome 重新扫描失败：") + message)
        except Exception as exc:
            record("navidrome", False, str(exc))
            emit(f"Navidrome 联动失败：{exc}")
    if config.get("jellyfin.enabled") and config.get("jellyfin.refresh_after_fix"):
        try:
            if changed_paths:
                from .jellyfin import refresh_tracks

                ok, message = refresh_tracks(config, paths, changed_paths)
            else:
                from .jellyfin import refresh

                ok, message = refresh(config, paths)
            record("jellyfin", ok, message)
            emit(("Jellyfin 刷新：成功" if ok else "Jellyfin 刷新失败：") + message)
        except Exception as exc:
            record("jellyfin", False, str(exc))
            emit(f"Jellyfin 联动失败：{exc}")
    if status_changed:
        config.save()
    return messages


def _notify_media_servers(
    config: Config,
    paths: AppPaths,
    manager: JobManager,
    job: Job,
    changed_paths: Sequence[str] = (),
) -> None:
    notify_media_servers(config, paths, changed_paths=changed_paths, log=job.log)
