"""终端 CLI（命令名：morg）。

示例::

    morg init --path /home/pi/Music
    morg scan
    morg check
    morg fix --filter missing-cover --limit 20
    morg find 12 --kind lyrics
    morg web

所有命令都支持 ``--json``，方便被脚本或其它程序调用。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import __version__, audio, db, jobs, scanner
from .config import (
    Config,
    ConfigError,
    get_config,
    reload_config,
)
from .version import APP_TITLE
from .matcher import build_query, confidence
from .paths import AppPaths, get_paths, set_home
from .repair import RepairService, track_display
from .textutil import (
    count_timestamps,
    format_duration,
    format_quality,
    format_size,
    parse_lrc,
)

FILTER_ALIASES = {
    "all": "all",
    "missing-cover": "missing_cover",
    "missing_cover": "missing_cover",
    "missing_lyrics": "missing_lyrics",
    "missing-lyrics": "missing_lyrics",
    "missing-both": "missing_both",
    "missing_both": "missing_both",
    "missing-any": "missing_any",
    "missing_any": "missing_any",
    "issues": "issues",
    "tags": "issues",
    "fav": "favorite",
    "favorite": "favorite",
    "unsynced": "unsynced_lyrics",
    "bad-lyrics": "bad_lyrics",
    "bad_lyrics": "bad_lyrics",
    "timing": "bad_lyrics",
    "lyrics_timing": "bad_lyrics",
    "no-embed-cover": "no_embedded_cover",
    "duplicates": "duplicates",
    "ok": "ok",
    "error": "error",
}


# ---------------------------------------------------------------------------
# 输出工具
# ---------------------------------------------------------------------------

def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


class Console:
    def __init__(self, color: Optional[bool] = None, quiet: bool = False):
        self.color = _supports_color() if color is None else color
        self.quiet = quiet

    def _paint(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def title(self, text: str) -> None:
        if not self.quiet:
            print(self._paint("1;38;5;99", text))

    def info(self, text: str) -> None:
        if not self.quiet:
            print(text)

    def ok(self, text: str) -> None:
        if not self.quiet:
            print(self._paint("32", "✓ ") + text)

    def warn(self, text: str) -> None:
        if not self.quiet:
            print(self._paint("33", "! ") + text)

    def err(self, text: str) -> None:
        if not self.quiet:
            print(self._paint("31", "✗ ") + text, file=sys.stderr)

    def dim(self, text: str) -> str:
        return self._paint("2", text)

    def key(self, key: str, value: Any) -> None:
        if not self.quiet:
            print(f"  {self._paint('36', str(key).ljust(12))} {value}")


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(text))


def _pad(text: str, width: int) -> str:
    text = str(text)
    gap = width - _width(text)
    return text + " " * max(0, gap)


def _cut(text: str, width: int) -> str:
    text = str(text)
    if _width(text) <= width:
        return text
    out = ""
    used = 0
    for ch in text:
        step = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + step > width - 1:
            break
        out += ch
        used += step
    return out + "…"


def print_table(headers: Sequence[str], rows: Sequence[Sequence[Any]], console: Console) -> None:
    widths = [_width(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            if index < len(widths):
                widths[index] = min(max(widths[index], _width(cell)), 42)
    header = "  ".join(_pad(console._paint("1", str(h)), widths[i]) for i, h in enumerate(headers))
    print(header)
    for row in rows:
        cells = []
        for index, cell in enumerate(row):
            text = _cut(cell, widths[index] if index < len(widths) else 40)
            cells.append(_pad(text, widths[index] if index < len(widths) else 40))
        print("  ".join(cells))


class Progress:
    def __init__(self, console: Console, label: str = ""):
        self.console = console
        self.label = label
        self.last = 0
        self.tty = getattr(sys.stdout, "isatty", lambda: False)()
        self.started = time.time()

    def __call__(self, done: int, total: int, current: str = "") -> None:
        if self.console.quiet:
            return
        percent = int(done * 100 / total) if total else 100
        if self.tty:
            bar_width = 24
            filled = int(bar_width * percent / 100)
            bar = "#" * filled + "-" * (bar_width - filled)
            name = _cut(Path(current).name if current else "", 28)
            sys.stdout.write(f"\r  {self.label}[{bar}] {percent:3d}%  {done}/{total}  {name}\033[K")
            sys.stdout.flush()
        elif done - self.last >= 25 or done == total:
            print(f"  进度 {done}/{total}（{percent}%）")
            self.last = done

    def finish(self) -> None:
        if self.tty and not self.console.quiet:
            sys.stdout.write("\n")
            sys.stdout.flush()


def _json_out(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 上下文与解析
# ---------------------------------------------------------------------------

class Ctx:
    def __init__(self, args: argparse.Namespace):
        self.paths: AppPaths = set_home(args.home) if getattr(args, "home", None) else get_paths()
        self.paths.ensure()
        self.config: Config = reload_config() if getattr(args, "home", None) else get_config(self.paths)
        db.init_db(self.paths)
        self.json: bool = bool(getattr(args, "json", False))
        self.console = Console(quiet=self.json)
        self.args = args

    def require_initialized(self) -> None:
        if not self.config.is_initialized():
            raise ConfigError(
                "还没有选择音乐文件夹。先运行：morg init --path <你的音乐目录>"
            )

    def out_json(self, data: Any) -> None:
        if self.json:
            _json_out(data)


def _cmd_init(args: argparse.Namespace, ctx: Ctx) -> int:
    conf = ctx.config
    target = (args.path or "").strip()
    if not target:
        if not sys.stdin.isatty():
            ctx.console.err("非交互环境请使用 --path 指定音乐文件夹")
            return 1
        suggestions = []
        if conf.music_dir:
            suggestions.append(str(conf.music_dir))
        for candidate in (Path.home() / "Music", Path("/mnt"), Path("/media")):
            if candidate.is_dir() and str(candidate) not in suggestions:
                suggestions.append(str(candidate))
        ctx.console.title(f"选择音乐文件夹（乐曲与封面都会整理到这里）")
        for index, item in enumerate(suggestions, start=1):
            ctx.console.info(f"  {index}. {item}")
        default_hint = suggestions[0] if suggestions else str(Path.home())
        raw = input(f"请输入路径（回车使用 {default_hint}）: ").strip()
        if not raw and suggestions:
            target = suggestions[0]
        else:
            target = raw
    if not target:
        ctx.console.err("未提供音乐文件夹")
        return 1
    target_path = Path(target).expanduser()
    try:
        target_path = target_path.resolve()
    except OSError:
        pass
    if not target_path.is_dir():
        ctx.console.err(f"文件夹不存在：{target_path}")
        return 1
    if not os.access(target_path, os.R_OK):
        ctx.console.err(f"没有读取权限：{target_path}")
        return 1
    if not os.access(target_path, os.W_OK):
        ctx.console.warn("这个文件夹当前不可写，修复标签时会失败；请检查权限")

    old_dir = conf.music_dir_str
    changed = bool(old_dir and Path(old_dir).resolve() != target_path)
    if changed and not args.force:
        existing = db.stats(ctx.paths)["total"]
        if existing:
            answer = input(f"已有 {existing} 首索引，切换目录会重建索引。继续？[y/N] ").strip().lower()
            if answer not in ("y", "yes", "是"):
                ctx.console.info("已取消")
                return 0
    conf.set("music_dir", str(target_path))
    conf.save()
    if changed:
        db.clear_library(ctx.paths)
    count = len(scanner.collect_audio_files(conf))
    ctx.console.ok(f"音乐文件夹：{target_path}")
    ctx.console.info(f"  发现 {count} 个音频文件，应用数据目录：{ctx.paths.home}")
    if args.no_scan:
        ctx.console.info("已跳过扫描。稍后运行 morg scan")
        ctx.out_json({"music_dir": str(target_path), "files": count, "scanned": False})
        return 0
    ctx.console.title("开始扫描曲库…")
    progress = Progress(ctx.console)
    result = scanner.scan_library(conf, ctx.paths, full=False, progress=progress)
    progress.finish()
    ctx.console.ok(
        f"扫描完成：共 {result.total} 首（新增 {result.added}，更新 {result.updated}，"
        f"移除 {result.removed}，错误 {result.errors}），耗时 {result.elapsed:.1f}s"
    )
    stats = db.stats(ctx.paths)
    ctx.console.info(
        f"  缺歌词 {stats['missing_lyrics']} 首，缺封面 {stats['missing_cover']} 首，"
        f"时间轴异常 {stats.get('timing_issues', 0)} 首，"
        f"健康度 {stats['health_score']}/100"
    )
    ctx.console.info("")
    ctx.console.info("下一步：")
    ctx.console.info("  morg check            查看体检报告")
    ctx.console.info("  morg fix --limit 20   修复前 20 首缺失的歌词/封面")
    ctx.console.info("  morg web              启动 Web 管理界面")
    ctx.out_json(result.to_dict())
    return 0


def _cmd_scan(args: argparse.Namespace, ctx: Ctx) -> int:
    ctx.require_initialized()
    ctx.console.title("扫描曲库" + ("（全量）" if args.full else "（增量）"))
    progress = Progress(ctx.console)
    result = scanner.scan_library(ctx.config, ctx.paths, full=args.full, progress=progress)
    progress.finish()
    ctx.console.ok(
        f"共 {result.total} 首，新增 {result.added}，更新 {result.updated}，"
        f"跳过 {result.skipped}，移除 {result.removed}，错误 {result.errors}，"
        f"耗时 {result.elapsed:.1f}s"
    )
    for sample in result.error_samples[:5]:
        ctx.console.warn(f"{sample['path']}：{sample['error']}")
    if args.auto_fix and result.new_ids:
        ctx.console.info(f"为 {len(result.new_ids)} 首新歌排队自动修复…")
        job_id = jobs.start_scan(ctx.config, ctx.paths, None, full=False)
        ctx.console.info(f"任务 {job_id} 已提交")
    ctx.out_json(result.to_dict())
    return 0


def _cmd_status(args: argparse.Namespace, ctx: Ctx) -> int:
    stats = db.stats(ctx.paths)
    automation = ctx.config.get("automation") or {}
    last_scan = db.get_meta("last_scan_at", "", ctx.paths) or automation.get("last_scan_at")
    data = {
        "music_dir": ctx.config.music_dir_str,
        "data_dir": str(ctx.paths.home),
        "initialized": ctx.config.is_initialized(),
        "stats": stats,
        "automation": {
            key: automation.get(key)
            for key in ("scan_on_start", "watch_enabled", "watch_interval_min", "auto_fix_new", "fix_on_play", "schedule_enabled", "schedule_time")
        },
        "last_scan_at": last_scan,
        "last_fix_at": automation.get("last_fix_at"),
    }
    if ctx.json:
        _json_out(data)
        return 0
    ctx.console.title(f"{APP_TITLE} v{__version__}")
    ctx.console.key("音乐目录", ctx.config.music_dir_str or "（未设置）")
    ctx.console.key("数据目录", str(ctx.paths.home))
    ctx.console.key("曲目", f"{stats['total']} 首 / {stats['albums']} 张专辑 / {stats['artists']} 位歌手")
    ctx.console.key("总时长", format_duration(stats["duration"]))
    ctx.console.key("占用空间", format_size(stats["size"]))
    ctx.console.key("健康度", f"{stats['health_score']}/100")
    ctx.console.key("缺歌词", f"{stats['missing_lyrics']} 首")
    ctx.console.key("缺封面", f"{stats['missing_cover']} 首")
    ctx.console.key("时间轴异常", f"{stats.get('timing_issues', 0)} 首")
    ctx.console.key("标签异常", f"{stats['issues']} 首")
    ctx.console.key("扫描时间", last_scan or "从未")
    ctx.console.key("修复时间", automation.get("last_fix_at") or "从未")
    if not ctx.config.is_initialized():
        ctx.console.warn("尚未初始化，请运行 morg init")
    return 0


def _resolve_tracks(args: argparse.Namespace, ctx: Ctx, targets: Sequence[str]) -> List[Dict[str, Any]]:
    if getattr(args, "ids", None):
        return db.tracks_by_ids(args.ids, ctx.paths)
    if getattr(args, "query", None):
        tracks, _total = db.list_tracks(query=args.query, page_size=200, paths=ctx.paths)
        return tracks
    if getattr(args, "all", False):
        return db.all_tracks(ctx.paths)
    filter_name = FILTER_ALIASES.get(getattr(args, "filter", "") or "", getattr(args, "filter", "") or "")
    if filter_name and filter_name != "all":
        ids = db.ids_for_filter(filter_name, limit=args.limit or None, paths=ctx.paths)
        return db.tracks_by_ids(ids, ctx.paths)
    ids = db.ids_needing_repair(targets, limit=args.limit or 100, paths=ctx.paths)
    return db.tracks_by_ids(ids, ctx.paths)


def _cmd_fix(args: argparse.Namespace, ctx: Ctx) -> int:
    ctx.require_initialized()
    targets: List[str] = []
    if args.lyrics:
        targets.append("lyrics")
    if args.cover:
        targets.append("cover")
    if args.tags:
        targets.append("tags")
    if not targets:
        targets = list(ctx.config.get("repair.targets") or ["lyrics", "cover"])
    tracks = _resolve_tracks(args, ctx, targets)
    if args.limit:
        tracks = tracks[: args.limit]
    if not tracks:
        ctx.console.ok("没有需要处理的曲目")
        ctx.out_json({"total": 0, "results": []})
        return 0

    ctx.console.title(
        f"准备修复 {len(tracks)} 首 · 目标：{'、'.join({'lyrics': '歌词', 'cover': '封面', 'tags': '标签'}.get(t, t) for t in targets)}"
    )
    if args.dry_run:
        ctx.console.info("  预览模式：只搜索候选，不写入文件")
    if not args.yes and not args.dry_run:
        answer = input(f"确认继续？[y/N] ").strip().lower()
        if answer not in ("y", "yes", "是"):
            ctx.console.info("已取消")
            return 0

    service = RepairService(ctx.config, ctx.paths)
    counts: Dict[str, int] = {}
    results: List[Dict[str, Any]] = []
    width = len(str(len(tracks)))
    for index, track in enumerate(tracks, start=1):
        try:
            outcome = service.repair(
                track,
                targets=targets,
                force=args.force,
                dry_run=args.dry_run,
                min_score=args.min_score,
            )
            data = outcome.to_dict()
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            data = {
                "track_id": track.get("id"),
                "title": track_display(track),
                "status": "error",
                "message": str(exc),
            }
        results.append(data)
        status = str(data.get("status"))
        counts[status] = counts.get(status, 0) + 1
        icon = {"fixed": "✓", "partial": "◐", "not_found": "·", "skipped": "·", "error": "✗", "dry_run": "…"}.get(status, "·")
        detail = data.get("message", "")
        line = f"  [{index:>{width}}/{len(tracks)}] {icon} {_cut(track_display(track), 46)}  {detail}"
        if args.dry_run:
            lyric_found = (data.get("lyrics") or {}).get("found")
            cover_found = (data.get("cover") or {}).get("found")
            tags = []
            if "lyrics" in targets:
                tags.append("歌词✓" if lyric_found else "歌词×")
            if "cover" in targets:
                tags.append("封面✓" if cover_found else "封面×")
            line += "  " + " ".join(tags)
        ctx.console.info(line)
        time.sleep(0.02)

    ctx.console.info("")
    ctx.console.ok(
        "完成：" + "，".join(f"{_status_text(k)} {v}" for k, v in sorted(counts.items(), key=lambda kv: kv[0]))
    )
    ctx.out_json({"total": len(results), "counts": counts, "results": results})
    return 0


def _status_text(status: str) -> str:
    return {
        "fixed": "已修复",
        "partial": "部分修复",
        "not_found": "未找到",
        "skipped": "跳过",
        "error": "失败",
        "dry_run": "可修复",
    }.get(status, status)


def _cmd_list(args: argparse.Namespace, ctx: Ctx) -> int:
    filter_name = FILTER_ALIASES.get(args.filter or "all", args.filter or "all")
    ids = None
    if filter_name == "duplicates":
        ids = [int(track["id"]) for group in db.find_duplicates(paths=ctx.paths) for track in group["tracks"]]
    tracks, total = db.list_tracks(
        query=args.query or "",
        filter_name=filter_name if filter_name != "duplicates" else "all",
        sort=args.sort,
        page=1,
        page_size=args.limit,
        ids=ids if filter_name == "duplicates" else None,
        paths=ctx.paths,
    )
    if ctx.json:
        _json_out({"total": total, "items": tracks})
        return 0
    rows = []
    for track in tracks:
        marks = []
        marks.append("词" if track.get("has_lyrics") else "-")
        marks.append("图" if track.get("has_cover") else "-")
        if track.get("tag_issues"):
            marks.append("签")
        rows.append(
            [
                track.get("id"),
                "".join(marks),
                _cut(track.get("title") or Path(track.get("filename") or "").stem, 34),
                _cut(track.get("artist") or "-", 22),
                format_duration(track.get("duration")),
                (track.get("ext") or "").lstrip(".").upper(),
            ]
        )
    print_table(["ID", "状态", "标题", "歌手", "时长", "格式"], rows, ctx.console)
    ctx.console.info(ctx.console.dim(f"共 {total} 首，显示 {len(tracks)} 首（用 morg list --limit 200 查看更多）"))
    return 0


def _cmd_show(args: argparse.Namespace, ctx: Ctx) -> int:
    track = db.get_track(args.id, ctx.paths)
    if not track:
        ctx.console.err(f"找不到曲目 #{args.id}")
        return 1
    lyrics, source = audio.read_lyrics(track["path"])
    cover = audio.read_cover(track["path"])
    data = {
        "track": track,
        "lyrics": {"source": source, "chars": len(lyrics), "synced": count_timestamps(lyrics) >= 3},
        "cover": {"present": bool(cover), "width": cover.width if cover else 0, "height": cover.height if cover else 0, "bytes": len(cover.data) if cover else 0},
    }
    if ctx.json:
        _json_out(data)
        return 0
    ctx.console.title(f"#{track['id']}  {track_display(track)}")
    ctx.console.key("文件", track["path"])
    ctx.console.key("专辑", track.get("album") or "-")
    ctx.console.key("时长", format_duration(track.get("duration")))
    ctx.console.key(
        "规格",
        format_quality(track.get("ext"), track.get("sample_rate"), track.get("bits"), track.get("channels"), track.get("bitrate")),
    )
    ctx.console.key("歌词", f"{'有' if lyrics else '无'}（{source or '-'}，{len(lyrics)} 字符，{count_timestamps(lyrics)} 个时间轴）")
    ctx.console.key("封面", f"{cover.width}x{cover.height}，{format_size(len(cover.data))}" if cover else "无")
    ctx.console.key("修复次数", track.get("repair_attempts") or 0)
    if track.get("tag_issues"):
        ctx.console.key("标签问题", "；".join(item.get("label", "") for item in track["tag_issues"]))
    if lyrics:
        ctx.console.info("")
        ctx.console.title("歌词预览")
        for line in lyrics.splitlines()[:12]:
            ctx.console.info("  " + line)
    return 0


def _cmd_check(args: argparse.Namespace, ctx: Ctx) -> int:
    ctx.require_initialized()
    stats = db.stats(ctx.paths)
    dup_groups = db.find_duplicates(paths=ctx.paths)
    dup_count = sum(len(group["tracks"]) for group in dup_groups)
    data = {
        "stats": stats,
        "duplicates": {"groups": len(dup_groups), "tracks": dup_count},
        "providers": None,
    }
    if args.network:
        from .providers import provider_self_test

        data["providers"] = provider_self_test(ctx.config)
    if ctx.json:
        _json_out(data)
        return 0
    ctx.console.title("曲库体检报告")
    ctx.console.key("曲目", f"{stats['total']} 首 / {stats['albums']} 张专辑")
    ctx.console.key("健康度", f"{stats['health_score']}/100")
    ctx.console.key("缺歌词", f"{stats['missing_lyrics']} 首")
    ctx.console.key("缺封面", f"{stats['missing_cover']} 首")
    ctx.console.key("歌词未内嵌", f"{stats['total'] - stats['synced_lyrics'] - stats['missing_lyrics']} 首（仅有纯文本或外挂）")
    ctx.console.key("时间轴异常", f"{stats.get('timing_issues', 0)} 首")
    ctx.console.key("标签异常", f"{stats['issues']} 首")
    ctx.console.key("损坏/读取失败", f"{stats['errors']} 首")
    ctx.console.key("重复文件", f"{dup_count} 首（{len(dup_groups)} 组）")
    if stats.get("formats"):
        ctx.console.info("")
        ctx.console.title("格式分布")
        for item in stats["formats"]:
            ctx.console.info(
                f"  {(item['ext'] or '?').lstrip('.').upper():<6} {item['count']:>5} 首   {format_size(item['size'])}"
            )
    if args.network and data["providers"]:
        ctx.console.info("")
        ctx.console.title("数据源连通性")
        for item in data["providers"]:
            mark = "✓" if item["ok"] else "✗"
            ctx.console.info(f"  {mark} {item['label']:<10} {item['count']} 条  {item['elapsed']}s  {item['error']}")
    if dup_groups:
        ctx.console.info("")
        ctx.console.title("重复文件（前 5 组）")
        for group in dup_groups[:5]:
            ctx.console.info(f"  [{group['reason']}]")
            for track in group["tracks"][:4]:
                ctx.console.info(f"    #{track['id']}  {track['path']}  ({format_size(track['size'])})")
    return 0


def _cmd_find(args: argparse.Namespace, ctx: Ctx) -> int:
    track = db.get_track(args.id, ctx.paths)
    if not track:
        ctx.console.err(f"找不到曲目 #{args.id}")
        return 1
    service = RepairService(ctx.config, ctx.paths)
    query = build_query(track)
    ctx.console.title(f"检索 {track_display(track)}")
    ctx.console.key("查询", query.search_text)
    ctx.console.key("时长", format_duration(query.duration))
    report = service.search_lyrics(track, limit=args.limit) if args.kind == "lyrics" else service.search_covers(track, limit=args.limit)
    if report.errors:
        for error in report.errors:
            ctx.console.warn(f"{error.get('label')}: {error.get('error')}")
    if not report.hits:
        ctx.console.warn("没有找到候选")
        ctx.out_json(report.to_dict())
        return 0
    if ctx.json:
        _json_out(report.to_dict())
        return 0
    for index, hit in enumerate(report.hits, start=1):
        ctx.console.info(
            f"  {index:>2}. [{hit.score:5.1f} {confidence(hit.score)}] {hit.provider:<8} "
            f"{_cut(hit.title, 34)} - {_cut(hit.artist, 24)}"
            + (f"  {format_duration(hit.duration)}" if hit.duration else "")
        )
        if args.kind == "lyrics":
            first = next((line for line in hit.text.splitlines() if line.strip()), "")
            ctx.console.info(f"      {ctx.console.dim(_cut(first, 76))}")
        else:
            ctx.console.info(f"      {ctx.console.dim(hit.url)}")
    ctx.console.info("")
    if args.kind == "lyrics":
        ctx.console.info(f"应用第 N 个候选：morg apply {args.id} --kind lyrics --index N")
    else:
        ctx.console.info(f"应用第 N 个候选：morg apply {args.id} --kind cover --index N")
    return 0


def _cmd_apply(args: argparse.Namespace, ctx: Ctx) -> int:
    track = db.get_track(args.id, ctx.paths)
    if not track:
        ctx.console.err(f"找不到曲目 #{args.id}")
        return 1
    service = RepairService(ctx.config, ctx.paths)
    if args.kind == "lyrics":
        if args.text_file:
            text = Path(args.text_file).read_text(encoding="utf-8")
            result = service.apply_lyrics_text(track, text, source=f"文件 {Path(args.text_file).name}")
        else:
            report = service.search_lyrics(track, limit=max(args.index, 10))
            if len(report.hits) < args.index:
                ctx.console.err(f"候选不足（共 {len(report.hits)} 个）")
                return 1
            hit = report.hits[args.index - 1]
            result = service.apply_lyrics_text(track, hit.text, source=hit.provider)
            ctx.console.info(f"应用候选 {args.index}：{hit.title} - {hit.artist}（{hit.score:.0f} 分）")
    else:
        if args.file:
            data = Path(args.file).read_bytes()
            result = service.apply_cover_bytes(track, data, source=f"文件 {Path(args.file).name}", allow_small=True)
        elif args.url:
            from .providers import download_image

            data, _mime = download_image(args.url, config=ctx.config)
            result = service.apply_cover_bytes(track, data, source="URL", allow_small=True)
        else:
            report = service.search_covers(track, limit=max(args.index, 10))
            if len(report.hits) < args.index:
                ctx.console.err(f"候选不足（共 {len(report.hits)} 个）")
                return 1
            hit = report.hits[args.index - 1]
            result = service.apply_cover_candidate(track, {"url": hit.url, "provider": hit.provider, "referer": hit.extra.get("referer", "")})
            ctx.console.info(f"应用候选 {args.index}：{hit.title} - {hit.artist}（{hit.score:.0f} 分）")
    ctx.console.ok("已写入：" + json.dumps(result, ensure_ascii=False))
    ctx.out_json(result)
    return 0


def _cmd_lyrics(args: argparse.Namespace, ctx: Ctx) -> int:
    track = db.get_track(args.id, ctx.paths)
    if not track:
        ctx.console.err(f"找不到曲目 #{args.id}")
        return 1
    service = RepairService(ctx.config, ctx.paths)
    if args.set_file:
        text = Path(args.set_file).read_text(encoding="utf-8")
        result = service.apply_lyrics_text(track, text, source=f"文件 {Path(args.set_file).name}")
        ctx.console.ok(f"歌词已写入（{result['chars']} 字符）")
        ctx.out_json(result)
        return 0
    if args.offset is not None:
        db.set_offset(args.id, args.offset, ctx.paths)
        ctx.console.ok(f"歌词偏移已设置为 {args.offset} ms")
        ctx.out_json({"offset_ms": args.offset})
        return 0
    text, source = audio.read_lyrics(track["path"])
    if args.save:
        target = Path(args.save)
        target.write_text(text, encoding="utf-8")
        ctx.console.ok(f"已保存到 {target}")
        return 0
    if ctx.json:
        _json_out({"text": text, "source": source, "lines": parse_lrc(text)["lines"]})
        return 0
    if not text:
        ctx.console.warn("这首歌还没有歌词")
        return 0
    ctx.console.title(f"{track_display(track)} · 歌词（{source}）")
    print(text)
    return 0


def _cmd_cover(args: argparse.Namespace, ctx: Ctx) -> int:
    track = db.get_track(args.id, ctx.paths)
    if not track:
        ctx.console.err(f"找不到曲目 #{args.id}")
        return 1
    if args.set:
        data = Path(args.set).read_bytes()
        service = RepairService(ctx.config, ctx.paths)
        result = service.apply_cover_bytes(track, data, source=f"文件 {Path(args.set).name}", allow_small=True)
        ctx.console.ok(f"封面已写入：{result['width']}x{result['height']}")
        ctx.out_json(result)
        return 0
    cover = audio.read_cover(track["path"])
    if not cover:
        ctx.console.warn("这首歌没有封面")
        return 0
    if args.out:
        target = Path(args.out)
        target.write_bytes(cover.data)
        ctx.console.ok(f"已导出到 {target}（{cover.width}x{cover.height}）")
        ctx.out_json({"path": str(target), "width": cover.width, "height": cover.height})
        return 0
    ctx.console.info(f"封面：{cover.width}x{cover.height}，{format_size(len(cover.data))}，来源 {cover.source}")
    ctx.console.info(f"导出：morg cover {args.id} --out cover.jpg")
    return 0


def _cmd_tag(args: argparse.Namespace, ctx: Ctx) -> int:
    track = db.get_track(args.id, ctx.paths)
    if not track:
        ctx.console.err(f"找不到曲目 #{args.id}")
        return 1
    patch: Dict[str, Any] = {}
    for item in args.set or []:
        if "=" not in item:
            ctx.console.err(f"格式应为 key=value：{item}")
            return 1
        key, value = item.split("=", 1)
        key = key.strip()
        if key in ("track_no", "disc_no"):
            try:
                patch[key] = int(value)
            except ValueError:
                ctx.console.err(f"{key} 需要整数")
                return 1
        else:
            patch[key] = value
    if not patch:
        ctx.console.warn("没有指定要修改的字段，例如 --set artist=周杰伦")
        return 0
    service = RepairService(ctx.config, ctx.paths)
    result = service.update_tags(track, patch)
    ctx.console.ok("标签已更新：" + "、".join(result["fields"]))
    ctx.out_json(result)
    return 0


def _cmd_undo(args: argparse.Namespace, ctx: Ctx) -> int:
    track = db.get_track(args.id, ctx.paths)
    if not track:
        ctx.console.err(f"找不到曲目 #{args.id}")
        return 1
    service = RepairService(ctx.config, ctx.paths)
    result = service.undo(track)
    ctx.console.ok(f"已从备份恢复：{result['backup']}")
    ctx.out_json(result)
    return 0


def _cmd_dup(args: argparse.Namespace, ctx: Ctx) -> int:
    groups = db.find_duplicates(paths=ctx.paths)
    if ctx.json:
        _json_out({"groups": groups})
        return 0
    if not groups:
        ctx.console.ok("没有发现重复文件")
        return 0
    ctx.console.title(f"发现 {len(groups)} 组重复 / 相似文件")
    for group in groups[: args.limit]:
        ctx.console.info(f"[{group['reason']}] 建议保留 #{group['keep_id']}")
        for track in group["tracks"]:
            ctx.console.info(
                f"  #{track['id']:<5} {format_size(track['size']):>9}  {format_duration(track['duration'])}  {track['path']}"
            )
        ctx.console.info("")
    return 0


def _cmd_export(args: argparse.Namespace, ctx: Ctx) -> int:
    filter_name = FILTER_ALIASES.get(args.filter or "all", args.filter or "all")
    tracks, _total = db.list_tracks(
        query=args.query or "",
        filter_name=filter_name,
        sort="artist",
        page=1,
        page_size=10000,
        paths=ctx.paths,
    )
    if not tracks:
        ctx.console.warn("没有可导出的曲目")
        return 0
    lines = ["#EXTM3U"]
    for track in tracks:
        lines.append(f"#EXTINF:{int(round(float(track.get('duration') or 0)))},{track.get('artist') or ''} - {track.get('title') or Path(track['filename']).stem}")
        lines.append(track["path"])
    target = Path(args.out or "playlist.m3u")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ctx.console.ok(f"已导出 {len(tracks)} 首到 {target}")
    ctx.out_json({"path": str(target), "count": len(tracks)})
    return 0


def _cmd_auto(args: argparse.Namespace, ctx: Ctx) -> int:
    ctx.require_initialized()
    from .automation import Automation

    manager = jobs.get_manager(ctx.config, ctx.paths)
    automation = Automation(ctx.config, ctx.paths, manager)
    if args.once:
        automation._started_at = automation._started_at.replace(year=2000)  # 强制走启动扫描逻辑
        ctx.console.title("执行一次自动任务检查…")
        automation.tick()
        deadline = time.time() + 600
        while manager.running() and time.time() < deadline:
            running = manager.running()
            for job in running:
                ctx.console.info(f"  {job.title} {job.percent}% · {job.message}")
            time.sleep(2)
        for job in manager.list(5):
            ctx.console.info(f"  [{job['status']}] {job['title']} · {job['message']}")
        ctx.out_json(automation.status())
        return 0
    ctx.console.title("自动任务守护进程已启动（Ctrl+C 退出）")
    ctx.console.info(f"  音乐目录：{ctx.config.music_dir_str}")
    ctx.console.info(f"  监听变化：{'开启' if ctx.config.get('automation.watch_enabled') else '关闭'}")
    ctx.console.info(f"  每日修复：{ctx.config.get('automation.schedule_time')}（周 {'/'.join(str(d) for d in ctx.config.get('automation.schedule_days') or [])}）")
    automation.start()
    try:
        while True:
            time.sleep(60)
            status = automation.status()
            if status.get("last_error"):
                ctx.console.warn(status["last_error"])
    except KeyboardInterrupt:
        ctx.console.info("正在退出…")
        automation.stop()
        manager.shutdown()
    return 0


def _cmd_web(args: argparse.Namespace, ctx: Ctx) -> int:
    try:
        from .web.app import create_app
    except ImportError as exc:
        ctx.console.err(f"Web 依赖缺失：{exc}")
        return 1
    host = args.host or ctx.config.get("web.host") or "0.0.0.0"
    port = int(args.port or ctx.config.get("web.port") or 8090)
    app = create_app(ctx.config, ctx.paths, enable_automation=not args.no_auto)
    local_url = f"http://127.0.0.1:{port}"
    lan_ip = _lan_ip()
    ctx.console.title(f"{APP_TITLE} · Web 管理")
    ctx.console.key("本机访问", local_url)
    if lan_ip:
        ctx.console.key("局域网访问", f"http://{lan_ip}:{port}")
    ctx.console.key("音乐目录", ctx.config.music_dir_str or "（首次打开时选择）")
    ctx.console.key("数据目录", str(ctx.paths.home))
    ctx.console.info("按 Ctrl+C 停止服务")
    if args.open_browser:
        import webbrowser

        webbrowser.open(local_url)
    try:
        if args.debug:
            app.run(host=host, port=port, debug=True, use_reloader=False)
        else:
            try:
                from waitress import serve

                serve(
                    app,
                    host=host,
                    port=port,
                    threads=12,
                    channel_timeout=1800,
                    cleanup_interval=30,
                    connection_limit=200,
                )
            except ImportError:
                app.run(host=host, port=port, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        ctx.console.info("服务已停止")
    return 0


def _lan_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return ""


def _cmd_doctor(args: argparse.Namespace, ctx: Ctx) -> int:
    checks: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, level: str = "error") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "level": level})

    add("Python", sys.version_info >= (3, 9), f"{sys.version.split()[0]}（需要 3.9+）")
    for module, label in (("mutagen", "mutagen"), ("flask", "Flask"), ("PIL", "Pillow")):
        try:
            __import__(module)
            add(label, True, "已安装")
        except Exception as exc:
            add(label, False, f"未安装：{exc}")
    try:
        import waitress  # noqa: F401

        add("waitress", True, "已安装（推荐的生产服务器）")
    except Exception:
        add("waitress", False, "未安装，将使用 Flask 自带服务器", level="warn")
    try:
        import zhconv  # noqa: F401

        add("zhconv", True, "已安装（支持繁转简）")
    except Exception:
        add("zhconv", False, "未安装，繁简转换将跳过（可选）", level="warn")
    ffmpeg = shutil.which("ffmpeg")
    add("ffmpeg", bool(ffmpeg), ffmpeg or "未安装（仅影响格式转换等扩展功能，可选）", level="warn")

    add("配置目录", ctx.paths.home.exists(), str(ctx.paths.home))
    add("配置文件", ctx.paths.config_file.exists(), str(ctx.paths.config_file))
    add("数据库", ctx.paths.db_file.exists(), str(ctx.paths.db_file))
    if ctx.paths.db_file.exists():
        try:
            with db.get_conn(ctx.paths) as conn:
                row = conn.execute("PRAGMA integrity_check").fetchone()
            add("数据库完整性", row[0] == "ok", str(row[0]))
        except Exception as exc:
            add("数据库完整性", False, str(exc))

    music_dir = ctx.config.music_dir
    if music_dir:
        add("音乐目录", music_dir.is_dir(), str(music_dir))
        add("目录可写", os.access(music_dir, os.W_OK), "修复标签需要写权限")
        if music_dir.is_dir():
            unwritable: List[str] = []
            total = 0
            for current, _dirs, names in os.walk(str(music_dir)):
                for name in names:
                    if not name.lower().endswith(tuple(ext for ext in ctx.config.get("library.extensions") or [])):
                        continue
                    total += 1
                    if not os.access(os.path.join(current, name), os.W_OK):
                        unwritable.append(os.path.join(current, name))
            if unwritable:
                import getpass

                user = getpass.getuser()
                add(
                    "文件可写",
                    False,
                    f"{len(unwritable)} / {total} 个音频不可写（例如 {unwritable[0]}）。"
                    f'执行：sudo chown -R {user}:{user} "{music_dir}" 后重试',
                    level="warn",
                )
            else:
                add("文件可写", True, f"{total} 个音频均可写")
    else:
        add("音乐目录", False, "尚未选择，运行 morg init")
    if ctx.paths.home.exists():
        try:
            usage = shutil.disk_usage(ctx.paths.home)
            add("磁盘剩余", usage.free > 512 * 1024 * 1024, f"{format_size(usage.free)} 可用")
        except OSError:
            pass

    errors = ctx.config.validate()
    add("配置校验", not errors, "通过" if not errors else "；".join(errors), level="warn")

    port = int(ctx.config.get("web.port") or 8090)
    in_use = _port_in_use(port)
    add(f"端口 {port}", not in_use, "被占用" if in_use else "可用", level="warn")

    if args.network:
        from .providers import provider_self_test

        for item in provider_self_test(ctx.config):
            add(f"数据源 {item['label']}", item["ok"], f"{item['count']} 条 / {item['elapsed']}s {item['error']}", level="warn")

    if ctx.config.get("navidrome.enabled"):
        from .navidrome import check

        ok, info = check(ctx.config, ctx.paths)
        add("Navidrome", ok, json.dumps(info, ensure_ascii=False)[:200], level="warn")

    if ctx.json:
        _json_out({"checks": checks})
        return 0
    ctx.console.title("环境自检")
    failed = 0
    for item in checks:
        mark = "✓" if item["ok"] else ("!" if item["level"] == "warn" else "✗")
        line = f"  {mark} {_pad(item['name'], 16)} {item['detail']}"
        if item["ok"]:
            ctx.console.info(line)
        elif item["level"] == "warn":
            ctx.console.warn(f"{_pad(item['name'], 16)} {item['detail']}")
        else:
            failed += 1
            ctx.console.err(f"{_pad(item['name'], 16)} {item['detail']}")
    ctx.console.info("")
    if failed:
        ctx.console.err(f"{failed} 项必须解决的问题")
        return 1
    ctx.console.ok("自检通过")
    return 0


def _port_in_use(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.3)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def _cmd_config(args: argparse.Namespace, ctx: Ctx) -> int:
    if args.set:
        for item in args.set:
            if "=" not in item:
                ctx.console.err(f"格式应为 key=value：{item}")
                return 1
            key, raw = item.split("=", 1)
            key = key.strip()
            value: Any = raw
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                if "," in raw and isinstance(ctx.config.get(key), list):
                    value = [part.strip() for part in raw.split(",") if part.strip()]
            ctx.config.set(key, value)
        ctx.config.save()
        errors = ctx.config.validate()
        ctx.console.ok("配置已保存")
        for error in errors:
            ctx.console.warn(error)
    if ctx.json and not args.set:
        _json_out(ctx.config.as_dict())
        return 0
    if args.set:
        if not ctx.json:
            ctx.console.info(f"配置文件：{ctx.paths.config_file}")
        else:
            _json_out(ctx.config.as_dict())
        return 0
    ctx.console.title("当前配置")
    ctx.console.info(json.dumps(ctx.config.as_dict(), ensure_ascii=False, indent=2))
    ctx.console.info("")
    ctx.console.info(f"配置文件：{ctx.paths.config_file}")
    ctx.console.info("修改示例：morg config --set repair.write_lrc=true")
    return 0


def _cmd_providers(args: argparse.Namespace, ctx: Ctx) -> int:
    from .providers import provider_catalog, provider_self_test

    data = {"catalog": provider_catalog()}
    if not args.list_only:
        data["test"] = provider_self_test(ctx.config)
    if ctx.json:
        _json_out(data)
        return 0
    ctx.console.title("在线数据源")
    rows = []
    for item in data["catalog"]:
        rows.append([item["label"], item["name"], "✓" if item["lyrics"] else "-", "✓" if item["cover"] else "-"])
    print_table(["名称", "标识", "歌词", "封面"], rows, ctx.console)
    if data.get("test"):
        ctx.console.info("")
        ctx.console.title("连通性测试（试搜《晴天》）")
        for item in data["test"]:
            mark = "✓" if item["ok"] else "✗"
            ctx.console.info(f"  {mark} {_pad(item['label'], 12)} {item['count']} 条  {item['elapsed']}s  {item['error']}")
    return 0


def _cmd_rename(args: argparse.Namespace, ctx: Ctx) -> int:
    ctx.require_initialized()
    from .organize import apply_renames, plan_renames

    if getattr(args, "ids", None):
        tracks = db.tracks_by_ids(args.ids, ctx.paths)
    elif args.query:
        tracks = db.list_tracks(query=args.query, page_size=200, paths=ctx.paths)[0]
    else:
        filter_name = FILTER_ALIASES.get(args.filter or "all", args.filter or "all")
        if filter_name and filter_name != "all":
            ids = db.ids_for_filter(filter_name, limit=args.limit or None, paths=ctx.paths)
            tracks = db.tracks_by_ids(ids, ctx.paths)
        else:
            tracks = db.all_tracks(ctx.paths)
    if args.limit:
        tracks = tracks[: args.limit]
    tracks = [track for track in tracks if Path(track.get("path") or "").is_file()]
    if not tracks:
        ctx.console.ok("没有可整理的文件")
        ctx.out_json({"total": 0, "plans": []})
        return 0

    try:
        plans = plan_renames(tracks, ctx.config, args.template, ctx.paths)
    except ValueError as exc:
        ctx.console.err(str(exc))
        return 1

    for plan in plans:
        if plan.status == "planned":
            mark = ctx.console._paint("32", "→")
            ctx.console.info(f"  {mark} {_cut(plan.source, 62)}")
            ctx.console.info(f"    {_cut(plan.target, 66)}")
        elif plan.status == "skipped":
            ctx.console.info(f"  · 跳过 {_cut(plan.title, 40)}：{plan.reason}")
        else:
            ctx.console.err(f"  ✗ {_cut(plan.title, 40)}：{plan.reason}")

    planned = [plan for plan in plans if plan.status == "planned"]
    skipped = [plan for plan in plans if plan.status == "skipped"]
    errors = [plan for plan in plans if plan.status == "error"]
    ctx.console.info("")
    ctx.console.info(f"可整理 {len(planned)} 首，跳过 {len(skipped)} 首，错误 {len(errors)} 首")
    if not args.apply:
        ctx.console.info("当前是预演模式。确认无误后加 --apply 执行移动。")
        ctx.out_json({"applied": False, "counts": {"planned": len(planned), "skipped": len(skipped), "errors": len(errors)}, "plans": [p.to_dict() for p in plans]})
        return 0
    if not planned:
        ctx.out_json({"applied": False, "plans": [p.to_dict() for p in plans]})
        return 0
    if not args.yes:
        answer = input(f"确认移动 {len(planned)} 个文件？[y/N] ").strip().lower()
        if answer not in ("y", "yes", "是"):
            ctx.console.info("已取消")
            return 0
    results = apply_renames(planned, ctx.config, ctx.paths)
    done = [item for item in results if item.status == "done"]
    failed = [item for item in results if item.status == "error"]
    ctx.console.ok(f"已移动 {len(done)} 个文件" + (f"，失败 {len(failed)} 个" if failed else ""))
    for item in failed:
        ctx.console.err(f"{item.title}：{item.reason}")
    ctx.out_json(
        {
            "applied": True,
            "counts": {"done": len(done), "failed": len(failed)},
            "plans": [p.to_dict() for p in results],
        }
    )
    return 0


def _cmd_lrc(args: argparse.Namespace, ctx: Ctx) -> int:
    ctx.require_initialized()
    from .lrcfiles import STATUS_LABELS, clean_lrc_files, scan_lrc_files

    try:
        data = scan_lrc_files(ctx.config, ctx.paths)
    except FileNotFoundError as exc:
        ctx.console.err(str(exc))
        return 1
    counts = data["counts"]
    ctx.console.title("外置歌词检查")
    ctx.console.key("找到 .lrc", f"{counts['total']} 个（{format_size(counts['size'])}）")
    ctx.console.key("已有内嵌", f"{counts['redundant']} 个（可安全清理）")
    ctx.console.key("失联歌词", f"{counts['orphan']} 个（没有对应音频）")
    ctx.console.key("仅外置", f"{counts['sidecar_only']} 个（音频没有内嵌歌词，建议保留）")
    if args.list:
        for entry in data["entries"]:
            if entry.status in ("redundant", "orphan"):
                ctx.console.info(f"  [{STATUS_LABELS[entry.status]}] {entry.path}")

    result = None
    if args.embed:
        from .lrcfiles import embed_sidecar_lyrics

        sidecars = [e for e in data["entries"] if e.status == "sidecar_only" and e.audio_path]
        if not sidecars:
            ctx.console.info("没有需要嵌入的外置歌词")
        else:
            if not args.yes:
                answer = input(f"将把 {len(sidecars)} 个 .lrc 写入音频标签，继续？[y/N] ").strip().lower()
                if answer not in ("y", "yes", "是"):
                    ctx.console.info("已取消")
                    return 0
            result = embed_sidecar_lyrics(ctx.config, sidecars, ctx.paths)
            ctx.console.ok(f"嵌入完成：成功 {result['embedded']}，失败 {result['failed']}")
    if args.clean:
        statuses = [s.strip() for s in (args.statuses or "redundant,orphan").split(",") if s.strip()]
        if not args.yes and not args.dry_run:
            answer = input(f"将把 {counts['redundant'] + counts['orphan']} 个 .lrc 移入回收站，继续？[y/N] ").strip().lower()
            if answer not in ("y", "yes", "是"):
                ctx.console.info("已取消")
                return 0
        try:
            result = clean_lrc_files(ctx.config, data["entries"], statuses, ctx.paths, dry_run=args.dry_run)
        except ValueError as exc:
            ctx.console.err(str(exc))
            return 1
        if args.dry_run:
            ctx.console.info(f"预览：将移动 {len(result['moved'])} 个文件")
        else:
            ctx.console.ok(f"已移动 {len(result['moved'])} 个 .lrc 到 {result['trash_dir']}")
        for error in result["errors"][:5]:
            ctx.console.warn(f"{error['path']}：{error['error']}")
    ctx.out_json({"counts": counts, "result": result})
    return 0


def _cmd_version(args: argparse.Namespace, ctx: Ctx) -> int:
    if ctx.json:
        _json_out({"version": __version__, "app": APP_TITLE})
    else:
        print(f"{APP_TITLE} v{__version__}")
        print(f"数据目录：{ctx.paths.home}")
    return 0


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="以 JSON 输出")
    common.add_argument("--home", help="覆盖应用数据目录（默认见文档）")

    parser = argparse.ArgumentParser(
        prog="morg",
        description=f"{APP_TITLE} v{__version__} —— 本地音乐歌词 / 封面整理工具",
        epilog="示例：morg init --path ~/Music && morg scan && morg fix --limit 20",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"morg {__version__}")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--home", help="覆盖应用数据目录")
    sub = parser.add_subparsers(dest="command", metavar="<命令>")

    def add(name: str, help_text: str, func, **kwargs):
        p = sub.add_parser(name, help=help_text, parents=[common], **kwargs)
        p.set_defaults(func=func)
        return p

    p = add("init", "选择音乐文件夹并建立索引", _cmd_init)
    p.add_argument("--path", help="音乐文件夹路径")
    p.add_argument("--no-scan", action="store_true", help="只保存目录，不立即扫描")
    p.add_argument("--force", action="store_true", help="切换目录时跳过确认")

    p = add("scan", "扫描音乐目录", _cmd_scan)
    p.add_argument("--full", action="store_true", help="全量重读所有标签")
    p.add_argument("--auto-fix", action="store_true", help="扫描后自动修复新歌（需 Web 任务队列）")

    add("status", "查看曲库状态", _cmd_status)
    add("stats", "查看曲库状态（别名）", _cmd_status)

    p = add("list", "列出曲目", _cmd_list)
    p.add_argument("query", nargs="?", default="", help="搜索标题 / 歌手 / 专辑 / 文件名")
    p.add_argument("--filter", default="all", help="all / missing-cover / missing-lyrics / missing-both / issues / favorite / unsynced")
    p.add_argument("--sort", default="artist", help="artist / title / album / added / duration / size")
    p.add_argument("--limit", type=int, default=40)

    p = add("show", "查看单曲详情", _cmd_show)
    p.add_argument("id", type=int)

    p = add("check", "曲库体检", _cmd_check)
    p.add_argument("--network", action="store_true", help="同时测试在线数据源连通性")

    p = add("fix", "批量或单曲修复歌词/封面", _cmd_fix)
    p.add_argument("--id", dest="ids", type=int, nargs="+", help="指定曲目 ID（可多个）")
    p.add_argument("--query", help="按关键词选择曲目")
    p.add_argument("--filter", default="", help="按状态筛选：missing-cover / missing-lyrics / missing-both / issues")
    p.add_argument("--all", action="store_true", help="处理全部曲目（重建歌词/封面）")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--lyrics", action="store_true", help="只修歌词")
    p.add_argument("--cover", action="store_true", help="只修封面")
    p.add_argument("--tags", action="store_true", help="同时补全缺失标签")
    p.add_argument("--force", action="store_true", help="已有歌词/封面也重新获取")
    p.add_argument("--dry-run", action="store_true", help="只预览，不写入")
    p.add_argument("--min-score", type=float, default=None, help="自动应用的最低匹配分（默认 55）")
    p.add_argument("--yes", "-y", action="store_true", help="跳过确认")

    p = add("find", "搜索歌词/封面候选", _cmd_find)
    p.add_argument("id", type=int)
    p.add_argument("--kind", choices=["lyrics", "cover"], default="lyrics")
    p.add_argument("--limit", type=int, default=8)

    p = add("apply", "应用指定候选", _cmd_apply)
    p.add_argument("id", type=int)
    p.add_argument("--kind", choices=["lyrics", "cover"], default="lyrics")
    p.add_argument("--index", type=int, default=1, help="候选序号（从 1 开始）")
    p.add_argument("--text-file", help="歌词文本文件")
    p.add_argument("--file", help="本地封面图片")
    p.add_argument("--url", help="封面图片地址")

    p = add("lyrics", "查看/导入歌词", _cmd_lyrics)
    p.add_argument("id", type=int)
    p.add_argument("--save", help="导出到文件")
    p.add_argument("--set-file", help="从文件导入歌词")
    p.add_argument("--offset", type=int, help="歌词时间偏移（毫秒）")

    p = add("cover", "查看/导出/设置封面", _cmd_cover)
    p.add_argument("id", type=int)
    p.add_argument("--out", help="导出到文件")
    p.add_argument("--set", help="用本地图片替换封面")

    p = add("tag", "修改标签", _cmd_tag)
    p.add_argument("id", type=int)
    p.add_argument("--set", action="append", help="key=value，可重复")

    p = add("undo", "撤销最近一次修复（从备份恢复）", _cmd_undo)
    p.add_argument("id", type=int)

    p = add("dup", "查找重复文件", _cmd_dup)
    p.add_argument("--limit", type=int, default=20)

    p = add("lrc", "检查/清理外置 .lrc 歌词", _cmd_lrc)
    p.add_argument("--list", action="store_true", help="列出可清理的 .lrc")
    p.add_argument("--embed", action="store_true", help="把仅外置的 .lrc 写入音频内嵌标签")
    p.add_argument("--clean", action="store_true", help="把冗余/失联 .lrc 移入回收站")
    p.add_argument("--statuses", default="redundant,orphan", help="redundant,orphan,sidecar_only")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", "-y", action="store_true")

    p = add("rename", "按标签重命名 / 移动文件", _cmd_rename)
    p.add_argument("--template", default="{title} - {artist}", help="命名模板，例如 {artist}/{album}/{track:02d} - {title}")
    p.add_argument("--filter", default="all")
    p.add_argument("--query")
    p.add_argument("--id", dest="ids", type=int, nargs="+")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--apply", action="store_true", help="真正移动文件（默认只预演）")
    p.add_argument("--yes", "-y", action="store_true", help="跳过确认")

    p = add("export", "导出 M3U 播放列表", _cmd_export)
    p.add_argument("--out", default="playlist.m3u")
    p.add_argument("--query", default="")
    p.add_argument("--filter", default="all")

    p = add("auto", "运行自动扫描/自动修复", _cmd_auto)
    p.add_argument("--once", action="store_true", help="只执行一次检查")
    p.add_argument("--daemon", action="store_true", help="常驻运行（默认行为）")

    p = add("web", "启动 Web 管理界面", _cmd_web)
    p.add_argument("--host", default="", help="监听地址（默认 0.0.0.0）")
    p.add_argument("--port", type=int, default=0, help="端口（默认 8090）")
    p.add_argument("--no-auto", action="store_true", help="不启动自动任务调度")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--open-browser", action="store_true")

    p = add("config", "查看或修改配置", _cmd_config)
    p.add_argument("--set", action="append", help="key=value，可重复")

    p = add("doctor", "环境自检", _cmd_doctor)
    p.add_argument("--network", action="store_true", help="测试在线数据源")

    p = add("providers", "查看在线数据源", _cmd_providers)
    p.add_argument("--list-only", action="store_true", help="只列清单，不测试")

    add("version", "显示版本", _cmd_version)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        try:
            ctx = Ctx(argparse.Namespace(home=getattr(args, "home", None), json=False))
            if ctx.config.is_initialized():
                stats = db.stats(ctx.paths)
                print()
                print(f"当前曲库：{stats['total']} 首 · 缺歌词 {stats['missing_lyrics']} · 缺封面 {stats['missing_cover']}")
            else:
                print("\n尚未初始化，运行：morg init --path <音乐文件夹>")
        except Exception:
            pass
        return 0
    try:
        ctx = Ctx(args)
        return int(args.func(args, ctx) or 0)
    except ConfigError as exc:
        Console().err(str(exc))
        return 1
    except KeyboardInterrupt:
        Console().info("\n已中断")
        return 130
    except Exception as exc:
        Console().err(f"执行失败：{exc}")
        if os.environ.get("MORG_DEBUG"):
            raise
        return 1


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    raise SystemExit(main())
