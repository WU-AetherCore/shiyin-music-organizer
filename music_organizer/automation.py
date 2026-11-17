"""自动化：目录变化监听、定时扫描、每日自动修复。

运行在 Web 进程内（``morg web`` 启动时自动开始），也可以单独用
``morg auto --daemon`` 作为无 Web 的常驻服务。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from . import db, jobs, scanner
from .config import Config
from .paths import AppPaths, get_paths


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value: str) -> Optional[tuple]:
    try:
        hh, mm = str(value).split(":")[:2]
        hh, mm = int(hh), int(mm)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh, mm
    except Exception:
        pass
    return None


class Automation:
    def __init__(
        self,
        config: Config,
        paths: Optional[AppPaths] = None,
        manager: Optional[jobs.JobManager] = None,
    ):
        self.config = config
        self.paths = paths or get_paths()
        self.manager = manager or jobs.get_manager(config, self.paths)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._tick_lock = threading.Lock()
        self._signature = ""
        self._changed_at: Optional[datetime] = None
        self._startup_scan_done = False
        self._timing_audited = False
        self._started_at = datetime.now()
        self._last_error = ""
        self._last_tick = ""

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="morg-automation", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        # 启动后稍等，避免和 Web 初始化抢资源
        time.sleep(2.0)
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                self._last_error = str(exc)
            self._stop.wait(20.0)

    # ------------------------------------------------------------------
    def tick(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now()
        self._last_tick = now.isoformat(timespec="seconds")
        if not self.config.is_initialized():
            return
        with self._tick_lock:
            self._startup(now)
            self._watch_changes(now)
            self._interval_scan(now)
            self._audit_timing(now)
            self._auto_timing_fix(now)
            self._scheduled_fix(now)

    def _scan_running(self) -> bool:
        return bool(self.manager.running("scan"))

    def _fix_running(self) -> bool:
        return bool(self.manager.running("repair"))

    def _start_scan(self, reason: str) -> None:
        jobs.start_scan(self.config, self.paths, self.manager, full=False)
        db.log_activity("自动扫描", "ok", reason, source="auto", paths=self.paths)

    def _startup(self, now: datetime) -> None:
        if self._startup_scan_done:
            return
        if now - self._started_at < timedelta(seconds=3):
            return
        self._startup_scan_done = True
        if not self.config.get("automation.scan_on_start", True):
            return
        if self._scan_running():
            return
        try:
            count = db.stats(self.paths)["total"]
        except Exception:
            count = 0
        if count == 0 or self._db_age_hours(now) >= 1.0:
            self._start_scan("启动时扫描曲库")

    def _db_age_hours(self, now: datetime) -> float:
        last = db.get_meta("last_scan_at", "", self.paths) or str(self.config.get("automation.last_scan_at") or "")
        parsed = _parse_iso(last)
        if not parsed:
            return 999.0
        return max(0.0, (now - parsed).total_seconds() / 3600.0)

    def _audit_timing(self, now: datetime) -> None:
        """旧库升级后补齐时间轴字段；只做一次，避免每 20 秒读全库。"""
        if self._timing_audited or self._scan_running():
            return
        self._timing_audited = True
        try:
            result = scanner.audit_lyrics_timing(self.config, self.paths)
            if result.get("issues"):
                db.log_activity(
                    "歌词时间轴检查",
                    "warn",
                    f"发现 {result['issues']} 首时间轴异常",
                    source="auto",
                    paths=self.paths,
                )
        except Exception as exc:
            self._timing_audited = False
            self._last_error = str(exc)

    def _auto_timing_fix(self, now: datetime) -> None:
        if not self.config.get("automation.auto_fix_timing", True):
            return
        if self._fix_running() or self._scan_running():
            return
        last = _parse_iso(str(self.config.get("automation.last_timing_fix_at") or ""))
        if last and now - last < timedelta(hours=1):
            return
        ids = db.ids_for_filter("bad_lyrics", limit=20, paths=self.paths)
        if not ids:
            return
        self.config.set("automation.last_timing_fix_at", now.isoformat(timespec="seconds")).save()
        jobs.start_batch(
            self.config,
            self.paths,
            self.manager,
            ids=ids,
            targets=["lyrics"],
            force=True,
            source="auto-timing",
            title=f"自动校准歌词时间轴 · {len(ids)} 首",
        )
        db.log_activity(
            "自动校准歌词时间轴",
            "ok",
            f"已排队 {len(ids)} 首",
            source="auto",
            paths=self.paths,
        )


    def _watch_changes(self, now: datetime) -> None:
        if not self.config.get("automation.watch_enabled", True):
            return
        if not self.config.get("automation.watch_changes", True):
            return
        try:
            info = scanner.library_signature(self.config)
        except Exception as exc:
            self._last_error = str(exc)
            return
        signature = str(info.get("signature") or "")
        if not self._signature:
            self._signature = signature
            return
        if signature != self._signature:
            self._signature = signature
            self._changed_at = now
            return
        if self._changed_at is None:
            return
        if now - self._changed_at < timedelta(seconds=10):
            return
        if self._scan_running():
            return
        self._changed_at = None
        self._start_scan(f"检测到目录变化（{info.get('count', 0)} 首）")

    def _interval_scan(self, now: datetime) -> None:
        if not self.config.get("automation.watch_enabled", True):
            return
        interval = int(self.config.get("automation.watch_interval_min") or 0)
        if interval <= 0:
            return
        if self._scan_running():
            return
        last = db.get_meta("last_scan_at", "", self.paths)
        parsed = _parse_iso(last)
        if parsed and now - parsed < timedelta(minutes=interval):
            return
        # 刚启动时交给 _startup 处理，避免重复
        if not self._startup_scan_done:
            return
        self._start_scan(f"定时增量扫描（间隔 {interval} 分钟）")

    def _scheduled_fix(self, now: datetime) -> None:
        if not self.config.get("automation.schedule_enabled", True):
            return
        days = self.config.get("automation.schedule_days") or [1, 2, 3, 4, 5, 6, 7]
        if now.isoweekday() not in [int(d) for d in days if str(d).isdigit()]:
            return
        parsed_time = _parse_time(str(self.config.get("automation.schedule_time") or ""))
        if not parsed_time:
            return
        target = now.replace(hour=parsed_time[0], minute=parsed_time[1], second=0, microsecond=0)
        if now < target:
            return
        if now - target > timedelta(hours=12):
            return  # 错过太久就不补跑，等明天
        today = now.strftime("%Y-%m-%d")
        if str(self.config.get("automation.last_schedule_date") or "") == today:
            return
        if self._fix_running():
            return
        targets = [t for t in (self.config.get("automation.targets") or ["lyrics", "cover"]) if t in ("lyrics", "cover", "tags")]
        limit = int(self.config.get("automation.max_fix_per_run") or 40)
        ids = db.ids_needing_repair(targets, limit=limit, paths=self.paths)
        if not ids:
            self.config.set("automation.last_schedule_date", today).save()
            db.log_activity("每日自动修复", "skipped", "没有需要修复的曲目", source="auto", paths=self.paths)
            return
        self.config.set("automation.last_schedule_date", today).save()
        jobs.start_batch(
            self.config,
            self.paths,
            self.manager,
            ids=ids,
            targets=targets,
            force=False,
            source="auto-schedule",
            title=f"每日自动修复 · {len(ids)} 首",
        )
        db.log_activity("每日自动修复", "ok", f"已排队 {len(ids)} 首", source="auto", paths=self.paths)

    # ------------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        now = datetime.now()
        interval = int(self.config.get("automation.watch_interval_min") or 0)
        last_scan = _parse_iso(db.get_meta("last_scan_at", "", self.paths))
        next_scan = None
        if interval > 0 and last_scan:
            next_scan = (last_scan + timedelta(minutes=interval)).isoformat(timespec="seconds")
        parsed_time = _parse_time(str(self.config.get("automation.schedule_time") or ""))
        next_fix = None
        if parsed_time:
            candidate = now.replace(hour=parsed_time[0], minute=parsed_time[1], second=0, microsecond=0)
            days = [int(d) for d in (self.config.get("automation.schedule_days") or []) if str(d).isdigit()]
            for offset in range(0, 8):
                day = candidate + timedelta(days=offset)
                if day < now:
                    continue
                if day.isoweekday() in days:
                    next_fix = day.isoformat(timespec="seconds")
                    break
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "last_tick": self._last_tick,
            "last_error": self._last_error,
            "next_scan_at": next_scan,
            "next_fix_at": next_fix,
            "scan_running": self._scan_running(),
            "fix_running": self._fix_running(),
            "signature": self._signature,
            "started_at": self._started_at.isoformat(timespec="seconds"),
        }


_automation: Optional[Automation] = None
_lock = threading.Lock()


def get_automation(config: Config, paths: Optional[AppPaths] = None) -> Automation:
    global _automation
    with _lock:
        if _automation is None:
            _automation = Automation(config, paths or get_paths())
        return _automation


def start_automation(config: Config, paths: Optional[AppPaths] = None) -> Automation:
    automation = get_automation(config, paths)
    automation.start()
    return automation


def stop_automation() -> None:
    global _automation
    with _lock:
        if _automation is not None:
            _automation.stop()
            _automation = None
