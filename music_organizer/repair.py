"""修复服务：搜索候选、写入标签、备份与撤销。"""

from __future__ import annotations

import errno
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import audio, db, scanner
from .config import Config
from .matcher import build_query, choose_best_cover, choose_best_lyrics, confidence
from .paths import AppPaths, get_paths
from .providers import (
    PROVIDER_LABELS,
    fetch_cover_bytes,
    search_covers as provider_search_covers,
    search_lyrics as provider_search_lyrics,
)
from .providers.base import CoverHit, LyricsHit, TrackQuery
from .textutil import (
    analyze_lyric_timing,
    clamp_lyric_timing,
    clean_lyrics_text,
    clean_spaces,
    collapse_cjk_spaces,
    count_timestamps,
    is_placeholder,
    normalize_key,
    parse_filename,
    parse_lrc,
)


def track_title(track: Dict[str, Any]) -> str:
    title = clean_spaces(track.get("title"))
    if title:
        return title
    return Path(track.get("filename") or "未命名").stem


def track_display(track: Dict[str, Any]) -> str:
    artist = clean_spaces(track.get("artist"))
    title = track_title(track)
    return f"{title} - {artist}" if artist else title


@dataclass
class RepairOutcome:
    track_id: int
    title: str
    status: str = "skipped"  # fixed / partial / not_found / skipped / error / dry_run
    message: str = ""
    lyrics: Dict[str, Any] = field(default_factory=dict)
    cover: Dict[str, Any] = field(default_factory=dict)
    tags: Dict[str, Any] = field(default_factory=dict)
    backup: str = ""
    elapsed: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "track_id": self.track_id,
            "title": self.title,
            "status": self.status,
            "message": self.message,
            "lyrics": self.lyrics,
            "cover": self.cover,
            "tags": self.tags,
            "backup": self.backup,
            "elapsed": round(self.elapsed, 2),
        }


def _write_error_message(path: Path, exc: Exception) -> str:
    """把权限错误翻译成可执行的修复建议。"""
    is_permission = isinstance(exc, PermissionError) or getattr(exc, "errno", None) in (
        errno.EACCES,
        errno.EPERM,
        errno.EROFS,
    )
    if not is_permission:
        return f"写入失败：{exc}"
    owner = "?"
    user = "?"
    try:
        import pwd

        owner = pwd.getpwuid(path.stat().st_uid).pw_name
        user = pwd.getpwuid(os.geteuid()).pw_name
    except Exception:
        pass
    hint = f"没有写入权限：{path}（属主 {owner}，当前运行用户 {user}）"
    if getattr(exc, "errno", None) == errno.EROFS:
        return hint + "；文件系统是只读挂载，请检查 mount / fstab。"
    if user != "?":
        hint += f'。请在树莓派执行：sudo chown {user}:{user} "{path}" 后重试'
    return hint


class RepairService:
    def __init__(self, config: Config, paths: Optional[AppPaths] = None):
        self.config = config
        self.paths = paths or get_paths()

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------
    def query_for(self, track: Dict[str, Any]) -> TrackQuery:
        return build_query(track)

    def search_lyrics(self, track: Dict[str, Any], limit: int = 10):
        return provider_search_lyrics(self.query_for(track), self.config, limit=limit)

    def search_covers(self, track: Dict[str, Any], limit: int = 10):
        return provider_search_covers(self.query_for(track), self.config, limit=limit)

    @staticmethod
    def _timing_rank(timing: Dict[str, Any]) -> int:
        status = str(timing.get("status") or "")
        if status == "ok":
            return 3
        if status == "suspect":
            return 2
        if status == "broken":
            return 1
        return 0

    def _choose_timing_lyrics(
        self,
        hits: Iterable[LyricsHit],
        duration: float,
        min_score: float,
    ) -> Optional[LyricsHit]:
        """在候选里优先选择时间轴与音频时长最吻合的一版。"""
        best: Optional[LyricsHit] = None
        best_key = (-1, -1.0, -1.0, 0)
        for hit in hits:
            if hit.score < min_score or not (hit.text or "").strip():
                continue
            timing = analyze_lyric_timing(hit.text, duration)
            rank = self._timing_rank(timing)
            coverage = float(timing.get("coverage") or 0)
            # 覆盖率越接近 1 越好；超过 1 的 overrun 已在 rank 中降级。
            fit = 1.0 - min(1.0, abs(coverage - 0.92))
            key = (rank, round(fit, 3), float(hit.score), len(timing.get("issues") or []))
            if key > best_key:
                best_key = key
                best = hit
        return best

    # ------------------------------------------------------------------
    def repair(
        self,
        track: Dict[str, Any],
        targets: Optional[Iterable[str]] = None,
        force: bool = False,
        dry_run: bool = False,
        min_score: Optional[float] = None,
        cancel: Optional[threading.Event] = None,
    ) -> RepairOutcome:
        started = time.time()
        track_id = int(track.get("id") or 0)
        outcome = RepairOutcome(track_id=track_id, title=track_display(track))
        target_list = list(targets or self.config.get("repair.targets") or ["lyrics", "cover"])
        min_score = float(self.config.get("providers.min_score") or 55) if min_score is None else float(min_score)
        path = Path(track.get("path") or "")
        if not path.is_file():
            outcome.status = "error"
            outcome.message = "文件不存在"
            return outcome

        fresh = audio.read_meta(path)
        duration = float(fresh.get("duration") or track.get("duration") or 0)
        timing_before = analyze_lyric_timing(fresh.get("lyrics_text", ""), duration)
        timing_bad = bool(fresh.get("has_lyrics") and timing_before.get("needs_fix"))
        query = build_query({**track, **fresh, "filename": fresh.get("filename") or track.get("filename")})
        wants_lyrics = "lyrics" in target_list
        wants_cover = "cover" in target_list
        wants_tags = "tags" in target_list

        need_lyrics = wants_lyrics and (
            force
            or not fresh.get("lyrics_text", "").strip()
            or bool(self.config.get("repair.overwrite_lyrics", False))
            or (timing_bad and bool(self.config.get("repair.fix_timing", True)))
        )
        need_cover = wants_cover and (
            force
            or not fresh.get("has_cover")
            or bool(self.config.get("repair.overwrite_cover", False))
        )
        if not (need_lyrics or need_cover or wants_tags):
            outcome.status = "skipped"
            outcome.message = "歌词与封面都已存在"
            return outcome

        lyrics_hit: Optional[LyricsHit] = None
        cover_hit: Optional[CoverHit] = None
        cover_report = None
        cover_pool: Optional[ThreadPoolExecutor] = None
        cover_future = None
        if need_cover:
            cover_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="morg-cover")
            cover_future = cover_pool.submit(provider_search_covers, query, self.config, 10)
        try:
            if need_lyrics:
                if cancel is not None and cancel.is_set():
                    outcome.status = "skipped"
                    outcome.message = "已取消"
                    return outcome
                report = provider_search_lyrics(query, self.config, limit=10)
                if timing_bad:
                    candidate = self._choose_timing_lyrics(report.hits, duration, min_score)
                    if candidate is not None:
                        candidate_timing = analyze_lyric_timing(candidate.text, duration)
                        current_rank = self._timing_rank(timing_before)
                        candidate_rank = self._timing_rank(candidate_timing)
                        better = candidate_rank > current_rank or (
                            candidate_rank == current_rank
                            and int(candidate_timing.get("lines") or 0) > int(timing_before.get("lines") or 0)
                            and float(candidate_timing.get("last") or 0) <= duration + 3
                        )
                        if not better:
                            candidate = None
                    lyrics_hit = candidate
                else:
                    lyrics_hit = choose_best_lyrics(report.hits, min_score)
                outcome.lyrics = {
                    "requested": True,
                    "found": bool(lyrics_hit),
                    "errors": report.errors,
                    "provider": PROVIDER_LABELS.get(lyrics_hit.provider, "") if lyrics_hit else "",
                    "score": lyrics_hit.score if lyrics_hit else 0,
                    "synced": lyrics_hit.synced if lyrics_hit else False,
                    "candidate": _hit_brief(lyrics_hit) if lyrics_hit else None,
                    "timing_before": timing_before,
                }
            if need_cover:
                if cancel is not None and cancel.is_set():
                    outcome.status = "skipped"
                    outcome.message = "已取消"
                    return outcome
                cover_report = cover_future.result() if cover_future is not None else None
                cover_hit = self._choose_cover(cover_report.hits, query, min_score)
                outcome.cover = {
                    "requested": True,
                    "found": bool(cover_hit),
                    "errors": cover_report.errors,
                    "provider": PROVIDER_LABELS.get(cover_hit.provider, "") if cover_hit else "",
                    "score": cover_hit.score if cover_hit else 0,
                    "candidate": _hit_brief(cover_hit) if cover_hit else None,
                }
        finally:
            if cover_pool is not None:
                cover_pool.shutdown(wait=True)

        if dry_run:
            outcome.status = "dry_run" if (lyrics_hit or cover_hit or wants_tags) else "not_found"
            outcome.message = "预览模式，未写入文件"
            outcome.elapsed = time.time() - started
            return outcome

        # 只补标签时，也需要一次检索来获得权威的标题 / 歌手 / 专辑
        if wants_tags and not (lyrics_hit or cover_hit):
            report = provider_search_lyrics(query, self.config, limit=6)
            for candidate in report.hits:
                if candidate.score >= 45 and candidate.title:
                    lyrics_hit = candidate
                    break

        if not lyrics_hit and not cover_hit and not wants_tags:
            outcome.status = "not_found"
            outcome.message = "没有找到可信的候选"
            outcome.elapsed = time.time() - started
            db.log_activity(
                "自动修复",
                "not_found",
                outcome.message,
                track_id=track_id,
                track_title=outcome.title,
                source="auto",
                paths=self.paths,
            )
            db.record_repair_attempt(track_id, "not_found", self.paths)
            return outcome

        # 下载并校验封面
        cover_payload: Optional[Tuple[bytes, str]] = None
        if cover_hit is not None:
            candidates = [cover_hit]
            if cover_report is not None:
                for candidate in sorted(cover_report.hits, key=lambda item: item.score, reverse=True):
                    if candidate is cover_hit or not candidate.url:
                        continue
                    if self._cover_acceptable(candidate, query, min_score):
                        candidates.append(candidate)
            for candidate in candidates:
                payload = self._download_cover(candidate, outcome)
                if payload is not None:
                    if candidate is not cover_hit:
                        cover_hit = candidate
                        outcome.cover.update(
                            {
                                "provider": PROVIDER_LABELS.get(candidate.provider, ""),
                                "score": candidate.score,
                                "candidate": _hit_brief(candidate),
                                "error": "",
                            }
                        )
                    cover_payload = payload
                    break
            if cover_payload is None:
                outcome.cover["found"] = False
                outcome.cover["error"] = "封面下载或校验失败"

        lyrics_text = ""
        if lyrics_hit is not None and need_lyrics:
            lyrics_text = clean_lyrics_text(lyrics_hit.text, simplified=bool(self.config.get("repair.simplified", True)))
            if len(lyrics_text.strip()) < int(self.config.get("repair.min_lyrics_chars") or 20):
                outcome.lyrics["found"] = False
                outcome.lyrics["error"] = "歌词内容过短"
                lyrics_text = ""
            elif not count_timestamps(lyrics_text) and not self.config.get("repair.allow_plain_lyrics", True):
                outcome.lyrics["found"] = False
                outcome.lyrics["error"] = "只有纯文本歌词（已禁用）"
                lyrics_text = ""
        if timing_bad and need_lyrics and not lyrics_text:
            # 没有更好的候选时，只对“轻微超出结尾”做保守校准，不猜测缺失内容。
            codes = {item.get("code") for item in (timing_before.get("issues") or [])}
            if (
                "overrun" in codes
                and "truncated" not in codes
                and 0 < float(timing_before.get("last") or 0) - duration <= 15
            ):
                clamped = clamp_lyric_timing(fresh.get("lyrics_text", ""), duration)
                if clamped.strip() and clamped != fresh.get("lyrics_text"):
                    lyrics_text = clamped
                    outcome.lyrics["found"] = True
                    outcome.lyrics["provider"] = "本地时间轴校准"
                    outcome.lyrics["normalized"] = True
        if timing_bad and need_lyrics and lyrics_text and lyrics_hit is not None:
            candidate_timing = analyze_lyric_timing(lyrics_text, duration)
            codes = {item.get("code") for item in (candidate_timing.get("issues") or [])}
            if (
                "overrun" in codes
                and "truncated" not in codes
                and 0 < float(candidate_timing.get("last") or 0) - duration <= 15
            ):
                clamped = clamp_lyric_timing(lyrics_text, duration)
                if clamped.strip() and clamped != lyrics_text:
                    lyrics_text = clamped
                    outcome.lyrics["normalized"] = True

        tag_patch = self._tag_patch(fresh, lyrics_hit or cover_hit) if wants_tags else {}
        # 即使没有勾选“补全标签”，也修正 track 04 / Audio 3 这类占位标题
        tag_patch.update(self._metadata_patch(fresh, lyrics_hit or cover_hit))

        if not lyrics_text and cover_payload is None and not tag_patch:
            outcome.status = "not_found"
            outcome.message = "候选均不可用"
            outcome.elapsed = time.time() - started
            db.record_repair_attempt(track_id, "not_found", self.paths)
            return outcome

        # 写入前备份
        if self.config.get("repair.backup", True):
            outcome.backup = audio.backup_track(path, self.paths.backup_dir, reason="repair") or ""
            try:
                audio.prune_backups(self.paths.backup_dir, int(self.config.get("repair.max_backups") or 300))
            except Exception:
                pass

        try:
            audio.update_media(
                path,
                lyrics=lyrics_text or None,
                cover=cover_payload,
                tags=tag_patch or None,
            )
            if lyrics_text and self.config.get("repair.write_lrc", False):
                audio.write_lrc_sidecar(path, lyrics_text)
            if cover_payload and self.config.get("repair.write_cover_file", False):
                audio.write_cover_sidecar(path, cover_payload[0])
        except Exception as exc:
            outcome.status = "error"
            outcome.message = _write_error_message(path, exc)
            outcome.elapsed = time.time() - started
            db.log_activity(
                "自动修复",
                "error",
                outcome.message,
                track_id=track_id,
                track_title=outcome.title,
                source="auto",
                paths=self.paths,
            )
            db.record_repair_attempt(track_id, "error", self.paths)
            return outcome

        # 回读校验并刷新索引
        try:
            scanner.scan_single(path, self.config, self.paths)
        except Exception:
            pass
        verify_lyrics, _ = audio.read_lyrics(path)
        verify_cover = audio.read_cover(path)

        timing_after = analyze_lyric_timing(verify_lyrics, duration)
        if lyrics_text:
            outcome.lyrics["timing_after"] = timing_after
            outcome.lyrics["timing_fixed"] = bool(
                timing_before.get("needs_fix") and not timing_after.get("needs_fix")
            )

        if lyrics_text:
            outcome.lyrics.update(
                {
                    "applied": bool(verify_lyrics.strip()),
                    "source": outcome.lyrics.get("provider"),
                    "synced": count_timestamps(verify_lyrics) >= 3,
                    "chars": len(verify_lyrics),
                    "lines": len(parse_lrc(verify_lyrics)["lines"]),
                }
            )
        if cover_payload:
            outcome.cover.update(
                {
                    "applied": bool(verify_cover),
                    "bytes": len(verify_cover.data) if verify_cover else 0,
                    "width": verify_cover.width if verify_cover else 0,
                    "height": verify_cover.height if verify_cover else 0,
                }
            )
        if tag_patch:
            outcome.tags = {"applied": True, "fields": sorted(tag_patch.keys())}

        applied = 0
        requested = 0
        if need_lyrics:
            requested += 1
            applied += 1 if outcome.lyrics.get("applied") else 0
        if need_cover:
            requested += 1
            applied += 1 if outcome.cover.get("applied") else 0
        if tag_patch:
            requested += 1
            applied += 1

        if applied == 0:
            outcome.status = "not_found"
            outcome.message = "没有可写入的有效内容"
        elif applied < requested:
            outcome.status = "partial"
            outcome.message = "部分内容已修复"
        else:
            outcome.status = "fixed"
            outcome.message = "修复完成"
        if timing_before.get("needs_fix") and outcome.lyrics.get("applied"):
            if timing_after.get("needs_fix"):
                outcome.status = "partial"
                outcome.message = "歌词已更新，但时间轴仍需人工确认"
            else:
                outcome.message = "歌词时间轴已自动校准"
        outcome.elapsed = time.time() - started

        detail_parts = []
        if outcome.lyrics.get("applied"):
            detail_parts.append(f"歌词来自{outcome.lyrics.get('provider') or '在线'}（{outcome.lyrics.get('lines', 0)} 行）")
        if outcome.cover.get("applied"):
            detail_parts.append(f"封面来自{outcome.cover.get('provider') or '在线'}（{outcome.cover.get('width')}x{outcome.cover.get('height')}）")
        if tag_patch:
            detail_parts.append("补全标签：" + "、".join(sorted(tag_patch.keys())))
        db.log_activity(
            "自动修复",
            outcome.status,
            outcome.message + ("；" + "；".join(detail_parts) if detail_parts else ""),
            track_id=track_id,
            track_title=outcome.title,
            source="auto",
            paths=self.paths,
        )
        if outcome.status not in ("skipped",):
            db.record_repair_attempt(track_id, outcome.status, self.paths)
        return outcome

    # ------------------------------------------------------------------
    def _cover_acceptable(self, hit: CoverHit, query: TrackQuery, min_score: float) -> bool:
        if not hit.url:
            return False
        if hit.score >= min_score:
            return True
        # 低分兜底只接受“专辑完全一致”或“时长几乎一致”的候选，避免乱配封面
        if hit.score < 48:
            return False
        album_ok = bool(query.album) and normalize_key(hit.album) == normalize_key(query.album)
        duration_ok = bool(hit.duration and query.duration and abs(hit.duration - query.duration) <= 2)
        return bool(album_ok or duration_ok)

    def _choose_cover(self, hits: List[CoverHit], query: TrackQuery, min_score: float) -> Optional[CoverHit]:
        for candidate in sorted(hits, key=lambda item: item.score, reverse=True):
            if self._cover_acceptable(candidate, query, min_score):
                return candidate
        return None

    def _download_cover(self, hit: CoverHit, outcome: RepairOutcome) -> Optional[Tuple[bytes, str]]:
        try:
            data, mime = fetch_cover_bytes(hit, self.config)
        except Exception as exc:
            outcome.cover["error"] = str(exc)[:160]
            return None
        try:
            prepared = audio.prepare_cover(
                data,
                min_size=int(self.config.get("repair.min_cover_size") or 300),
                max_size=int(self.config.get("repair.max_cover_size") or 1280),
                quality=int(self.config.get("repair.jpeg_quality") or 90),
            )
        except audio.AudioError as exc:
            outcome.cover["error"] = str(exc)
            return None
        outcome.cover.update(
            {
                "original_width": prepared["original_width"],
                "original_height": prepared["original_height"],
                "width": prepared["width"],
                "height": prepared["height"],
                "bytes": len(prepared["data"]),
            }
        )
        return prepared["data"], prepared["mime"]

    def _metadata_patch(self, fresh: Dict[str, Any], hit: Any) -> Dict[str, str]:
        """只修正空值/占位值，避免覆盖用户已有的有效标签。"""
        if not self.config.get("repair.fix_placeholder_tags", True):
            return {}
        patch: Dict[str, str] = {}
        parsed = parse_filename(
            str(fresh.get("filename") or Path(fresh.get("path") or "").name),
            Path(str(fresh.get("path") or "")).parent.name,
        )
        if is_placeholder(fresh.get("title")):
            title = getattr(hit, "title", "") if hit else ""
            title = collapse_cjk_spaces(title) or parsed.get("title") or ""
            if title:
                patch["title"] = title
        if is_placeholder(fresh.get("artist")):
            artist = getattr(hit, "artist", "") if hit else ""
            artist = collapse_cjk_spaces(artist) or parsed.get("artist") or ""
            if artist:
                patch["artist"] = artist
        if is_placeholder(fresh.get("album")) and hit and getattr(hit, "album", ""):
            patch["album"] = clean_spaces(hit.album)
        return patch

    def _tag_patch(self, fresh: Dict[str, Any], hit: Any) -> Dict[str, Any]:
        """仅补全缺失 / 占位字段，绝不覆盖用户已有的有效标签。"""
        if hit is None:
            return {}
        patch: Dict[str, Any] = {}
        title = clean_spaces(fresh.get("title"))
        artist = clean_spaces(fresh.get("artist"))
        album = clean_spaces(fresh.get("album"))
        if (not title or is_placeholder(title)) and clean_spaces(getattr(hit, "title", "")):
            patch["title"] = clean_spaces(hit.title)
        if (not artist or is_placeholder(artist)) and clean_spaces(getattr(hit, "artist", "")):
            patch["artist"] = clean_spaces(hit.artist)
        if (not album or is_placeholder(album)) and clean_spaces(getattr(hit, "album", "")):
            patch["album"] = clean_spaces(hit.album)
        return patch

    # ------------------------------------------------------------------
    # 手动应用 / 编辑
    # ------------------------------------------------------------------
    def apply_lyrics_text(self, track: Dict[str, Any], text: str, source: str = "manual") -> Dict[str, Any]:
        path = Path(track.get("path") or "")
        if not path.is_file():
            raise FileNotFoundError("音频文件不存在")
        text = clean_lyrics_text(text, simplified=bool(self.config.get("repair.simplified", True)))
        if not text.strip():
            raise ValueError("歌词不能为空")
        backup = ""
        if self.config.get("repair.backup", True):
            backup = audio.backup_track(path, self.paths.backup_dir, reason=source) or ""
        audio.update_media(path, lyrics=text)
        if self.config.get("repair.write_lrc", False):
            audio.write_lrc_sidecar(path, text)
        scanner.scan_single(path, self.config, self.paths)
        db.log_activity(
            "编辑歌词",
            "ok",
            f"来源：{source}，{len(text)} 字符",
            track_id=int(track.get("id") or 0),
            track_title=track_display(track),
            source="manual",
            paths=self.paths,
        )
        return {"backup": backup, "synced": count_timestamps(text) >= 3, "chars": len(text)}

    def apply_cover_bytes(self, track: Dict[str, Any], data: bytes, source: str = "manual", allow_small: bool = False) -> Dict[str, Any]:
        path = Path(track.get("path") or "")
        if not path.is_file():
            raise FileNotFoundError("音频文件不存在")
        min_size = 0 if allow_small else int(self.config.get("repair.min_cover_size") or 300)
        prepared = audio.prepare_cover(
            data,
            min_size=min_size,
            max_size=int(self.config.get("repair.max_cover_size") or 1280),
            quality=int(self.config.get("repair.jpeg_quality") or 90),
        )
        backup = ""
        if self.config.get("repair.backup", True):
            backup = audio.backup_track(path, self.paths.backup_dir, reason=source) or ""
        audio.update_media(path, cover=(prepared["data"], prepared["mime"]))
        if self.config.get("repair.write_cover_file", False):
            audio.write_cover_sidecar(path, prepared["data"])
        scanner.scan_single(path, self.config, self.paths)
        db.log_activity(
            "更换封面",
            "ok",
            f"来源：{source}，{prepared['width']}x{prepared['height']}",
            track_id=int(track.get("id") or 0),
            track_title=track_display(track),
            source="manual",
            paths=self.paths,
        )
        return {"backup": backup, **{k: prepared[k] for k in ("width", "height", "bytes")}}

    def apply_lyrics_candidate(self, track: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
        text = candidate.get("text") or ""
        provider = candidate.get("provider_label") or candidate.get("provider") or "手动选择"
        return self.apply_lyrics_text(track, text, source=str(provider))

    def apply_cover_candidate(self, track: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
        url = str(candidate.get("url") or "")
        if not url:
            raise ValueError("候选缺少封面地址")
        from .providers import download_image

        data, _mime = download_image(
            url,
            referer=str(candidate.get("referer") or ""),
            config=self.config,
        )
        provider = candidate.get("provider_label") or candidate.get("provider") or "手动选择"
        return self.apply_cover_bytes(track, data, source=str(provider), allow_small=True)

    def update_tags(self, track: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        path = Path(track.get("path") or "")
        if not path.is_file():
            raise FileNotFoundError("音频文件不存在")
        allowed = {"title", "artist", "album", "album_artist", "year", "genre", "track_no", "disc_no"}
        clean = {k: v for k, v in patch.items() if k in allowed}
        if not clean:
            raise ValueError("没有可更新的字段")
        backup = ""
        if self.config.get("repair.backup", True):
            backup = audio.backup_track(path, self.paths.backup_dir, reason="tags") or ""
        audio.update_media(path, tags=clean)
        scanner.scan_single(path, self.config, self.paths)
        db.log_activity(
            "编辑标签",
            "ok",
            "字段：" + "、".join(sorted(clean.keys())),
            track_id=int(track.get("id") or 0),
            track_title=track_display(track),
            source="manual",
            paths=self.paths,
        )
        return {"backup": backup, "fields": sorted(clean.keys())}

    def undo(self, track: Dict[str, Any]) -> Dict[str, Any]:
        path = track.get("path") or ""
        latest = audio.latest_backup(path, self.paths.backup_dir)
        if not latest:
            raise FileNotFoundError("这首歌没有可用的备份")
        result = audio.restore_backup(latest)
        scanner.scan_single(path, self.config, self.paths)
        db.log_activity(
            "撤销修复",
            "ok",
            f"已恢复 {Path(latest).name}",
            track_id=int(track.get("id") or 0),
            track_title=track_display(track),
            source="manual",
            paths=self.paths,
        )
        return {**result, "backup": latest}


def _hit_brief(hit: Any) -> Dict[str, Any]:
    if hit is None:
        return {}
    return {
        "provider": hit.provider,
        "provider_label": PROVIDER_LABELS.get(hit.provider, hit.provider),
        "title": hit.title,
        "artist": hit.artist,
        "album": hit.album,
        "duration": hit.duration,
        "score": hit.score,
        "confidence": confidence(hit.score),
    }


def repair_many(
    service: RepairService,
    tracks: List[Dict[str, Any]],
    targets: Iterable[str],
    force: bool = False,
    dry_run: bool = False,
    progress: Optional[Any] = None,
    cancel: Optional[threading.Event] = None,
) -> List[RepairOutcome]:
    outcomes: List[RepairOutcome] = []
    total = len(tracks)
    for index, track in enumerate(tracks, start=1):
        if cancel is not None and cancel.is_set():
            break
        try:
            outcome = service.repair(track, targets=targets, force=force, dry_run=dry_run, cancel=cancel)
        except Exception as exc:
            outcome = RepairOutcome(int(track.get("id") or 0), track_display(track), status="error", message=str(exc))
        outcomes.append(outcome)
        if progress:
            progress(index, total, outcome)
        time.sleep(0.05)
    return outcomes
