"""SQLite 曲库索引。

只存“整理需要的元数据”，不存音频与封面二进制；封面通过文件实时读取，
避免数据库膨胀。所有连接使用 WAL，多线程（任务队列 / Web）可安全并发读写。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .paths import AppPaths, get_paths
from .textutil import normalize_key

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    dir TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL DEFAULT '',
    ext TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    duration REAL NOT NULL DEFAULT 0,
    bitrate INTEGER NOT NULL DEFAULT 0,
    sample_rate INTEGER NOT NULL DEFAULT 0,
    bits INTEGER NOT NULL DEFAULT 0,
    channels INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL DEFAULT '',
    artist TEXT NOT NULL DEFAULT '',
    album TEXT NOT NULL DEFAULT '',
    album_artist TEXT NOT NULL DEFAULT '',
    track_no INTEGER,
    disc_no INTEGER,
    year TEXT NOT NULL DEFAULT '',
    genre TEXT NOT NULL DEFAULT '',
    has_lyrics INTEGER NOT NULL DEFAULT 0,
    no_lyrics INTEGER NOT NULL DEFAULT 0,
    lyrics_source TEXT NOT NULL DEFAULT '',
    lyrics_synced INTEGER NOT NULL DEFAULT 0,
    lyrics_len INTEGER NOT NULL DEFAULT 0,
    lyrics_timing_status TEXT NOT NULL DEFAULT '',
    lyrics_timing_issues TEXT NOT NULL DEFAULT '[]',
    has_cover INTEGER NOT NULL DEFAULT 0,
    cover_source TEXT NOT NULL DEFAULT '',
    cover_mime TEXT NOT NULL DEFAULT '',
    cover_size INTEGER NOT NULL DEFAULT 0,
    cover_width INTEGER NOT NULL DEFAULT 0,
    cover_height INTEGER NOT NULL DEFAULT 0,
    quick_hash TEXT NOT NULL DEFAULT '',
    tag_issues TEXT NOT NULL DEFAULT '[]',
    health TEXT NOT NULL DEFAULT 'ok',
    added_at TEXT NOT NULL DEFAULT '',
    scanned_at TEXT NOT NULL DEFAULT '',
    play_count INTEGER NOT NULL DEFAULT 0,
    last_played TEXT NOT NULL DEFAULT '',
    favorite INTEGER NOT NULL DEFAULT 0,
    lyrics_offset_ms INTEGER NOT NULL DEFAULT 0,
    repair_attempts INTEGER NOT NULL DEFAULT 0,
    last_repair_at TEXT NOT NULL DEFAULT '',
    last_repair_status TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tracks_health ON tracks(health);
CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);
CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album);
CREATE INDEX IF NOT EXISTS idx_tracks_added ON tracks(added_at);
CREATE INDEX IF NOT EXISTS idx_tracks_hash ON tracks(quick_hash);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    track_id INTEGER,
    track_title TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'web'
);
CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity(ts);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""

_activity_counter = 0
_activity_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _db_path(paths: Optional[AppPaths] = None) -> Path:
    return (paths or get_paths()).db_file


@contextmanager
def get_conn(paths: Optional[AppPaths] = None):
    paths = paths or get_paths()
    paths.ensure()
    conn = sqlite3.connect(str(_db_path(paths)), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=8000")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(paths: Optional[AppPaths] = None) -> None:
    with get_conn(paths) as conn:
        conn.executescript(_SCHEMA)
        _ensure_columns(conn)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """为旧版本数据库补齐新增列（向前兼容）。"""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()}
    additions = {
        "repair_attempts": "INTEGER NOT NULL DEFAULT 0",
        "last_repair_at": "TEXT NOT NULL DEFAULT ''",
        "last_repair_status": "TEXT NOT NULL DEFAULT ''",
        "lyrics_offset_ms": "INTEGER NOT NULL DEFAULT 0",
        "favorite": "INTEGER NOT NULL DEFAULT 0",
        "lyrics_timing_status": "TEXT NOT NULL DEFAULT ''",
        "lyrics_timing_issues": "TEXT NOT NULL DEFAULT '[]'",
        "no_lyrics": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, ddl in additions.items():
        if name not in existing:
            try:
                conn.execute(f"ALTER TABLE tracks ADD COLUMN {name} {ddl}")
            except sqlite3.OperationalError:
                pass


def get_meta(key: str, default: str = "", paths: Optional[AppPaths] = None) -> str:
    with get_conn(paths) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_meta(key: str, value: str, paths: Optional[AppPaths] = None) -> None:
    with get_conn(paths) as conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------

def _health_of(meta: Dict[str, Any]) -> str:
    if not meta.get("audio_ok", True):
        return "error"
    has_lyrics = bool(meta.get("has_lyrics"))
    no_lyrics = bool(meta.get("no_lyrics"))
    has_cover = bool(meta.get("has_cover"))
    issues = meta.get("tag_issues") or []
    if not no_lyrics and not has_lyrics and not has_cover:
        return "missing_both"
    if not no_lyrics and not has_lyrics:
        return "missing_lyrics"
    if not has_cover:
        return "missing_cover"
    if issues:
        return "issues"
    return "ok"


_TRACK_COLUMNS = [
    "path",
    "dir",
    "filename",
    "ext",
    "size",
    "mtime",
    "duration",
    "bitrate",
    "sample_rate",
    "bits",
    "channels",
    "title",
    "artist",
    "album",
    "album_artist",
    "track_no",
    "disc_no",
    "year",
    "genre",
    "has_lyrics",
    "lyrics_source",
    "lyrics_synced",
    "lyrics_len",
    "lyrics_timing_status",
    "lyrics_timing_issues",
    "has_cover",
    "cover_source",
    "cover_mime",
    "cover_size",
    "cover_width",
    "cover_height",
    "quick_hash",
    "tag_issues",
    "health",
]


def upsert_track(meta: Dict[str, Any], paths: Optional[AppPaths] = None) -> int:
    """插入或更新一首歌，返回 track id。"""
    lyrics = meta.get("lyrics_text") or ""
    issues = meta.get("tag_issues") or []
    if not isinstance(issues, str):
        issues = json.dumps(issues, ensure_ascii=False)
    timing_issues = meta.get("lyrics_timing_issues") or []
    if not isinstance(timing_issues, str):
        timing_issues = json.dumps(timing_issues, ensure_ascii=False)
    values = {
        "path": str(meta.get("path") or ""),
        "dir": str(meta.get("dir") or ""),
        "filename": str(meta.get("filename") or ""),
        "ext": str(meta.get("ext") or ""),
        "size": int(meta.get("size") or 0),
        "mtime": float(meta.get("mtime") or 0),
        "duration": float(meta.get("duration") or 0),
        "bitrate": int(meta.get("bitrate") or 0),
        "sample_rate": int(meta.get("sample_rate") or 0),
        "bits": int(meta.get("bits") or 0),
        "channels": int(meta.get("channels") or 0),
        "title": str(meta.get("title") or ""),
        "artist": str(meta.get("artist") or ""),
        "album": str(meta.get("album") or ""),
        "album_artist": str(meta.get("album_artist") or ""),
        "track_no": meta.get("track_no"),
        "disc_no": meta.get("disc_no"),
        "year": str(meta.get("year") or ""),
        "genre": str(meta.get("genre") or ""),
        "has_lyrics": int(bool(meta.get("has_lyrics"))),
        "lyrics_source": str(meta.get("lyrics_source") or ""),
        "lyrics_synced": int(bool(meta.get("lyrics_synced"))),
        "lyrics_len": len(lyrics),
        "lyrics_timing_status": str(meta.get("lyrics_timing_status") or ""),
        "lyrics_timing_issues": timing_issues,
        "has_cover": int(bool(meta.get("has_cover"))),
        "cover_source": str(meta.get("cover_source") or ""),
        "cover_mime": str(meta.get("cover_mime") or ""),
        "cover_size": int(meta.get("cover_size") or 0),
        "cover_width": int(meta.get("cover_width") or 0),
        "cover_height": int(meta.get("cover_height") or 0),
        "quick_hash": str(meta.get("quick_hash") or ""),
        "tag_issues": issues,
        "health": _health_of(
            {
                "audio_ok": meta.get("audio_ok", True),
                "has_lyrics": meta.get("has_lyrics"),
                "no_lyrics": meta.get("no_lyrics"),
                "has_cover": meta.get("has_cover"),
                "tag_issues": meta.get("tag_issues") or [],
            }
        ),
    }
    with get_conn(paths) as conn:
        existing = conn.execute("SELECT id, added_at, no_lyrics FROM tracks WHERE path=?", (values["path"],)).fetchone()
        if existing:
            if existing["no_lyrics"]:
                values["health"] = "ok" if values["has_cover"] else "missing_cover"
            assignments = ", ".join(f"{col}=?" for col in _TRACK_COLUMNS if col != "path")
            params = [values[col] for col in _TRACK_COLUMNS if col != "path"]
            conn.execute(
                f"UPDATE tracks SET {assignments}, scanned_at=? WHERE id=?",
                params + [now_iso(), existing["id"]],
            )
            return int(existing["id"])
        values["added_at"] = now_iso()
        values["scanned_at"] = now_iso()
        cols = list(values.keys())
        placeholders = ", ".join("?" for _ in cols)
        cur = conn.execute(
            f"INSERT INTO tracks ({', '.join(cols)}) VALUES ({placeholders})",
            [values[col] for col in cols],
        )
        return int(cur.lastrowid)


def update_track_media(track_id: int, fields: Dict[str, Any], paths: Optional[AppPaths] = None) -> None:
    allowed = {
        "has_lyrics",
        "lyrics_source",
        "lyrics_synced",
        "lyrics_len",
        "lyrics_timing_status",
        "lyrics_timing_issues",
        "has_cover",
        "cover_source",
        "cover_mime",
        "cover_size",
        "cover_width",
        "cover_height",
        "tag_issues",
        "health",
        "title",
        "artist",
        "album",
        "album_artist",
        "year",
        "genre",
        "track_no",
        "disc_no",
        "duration",
        "size",
        "mtime",
        "quick_hash",
    }
    patch = {k: v for k, v in fields.items() if k in allowed}
    if not patch:
        return
    if "tag_issues" in patch and not isinstance(patch["tag_issues"], str):
        patch["tag_issues"] = json.dumps(patch["tag_issues"], ensure_ascii=False)
    if "lyrics_timing_issues" in patch and not isinstance(patch["lyrics_timing_issues"], str):
        patch["lyrics_timing_issues"] = json.dumps(patch["lyrics_timing_issues"], ensure_ascii=False)
    assignments = ", ".join(f"{k}=?" for k in patch)
    with get_conn(paths) as conn:
        conn.execute(f"UPDATE tracks SET {assignments} WHERE id=?", list(patch.values()) + [track_id])


def delete_track(track_id: int, paths: Optional[AppPaths] = None) -> None:
    with get_conn(paths) as conn:
        conn.execute("DELETE FROM tracks WHERE id=?", (track_id,))


def delete_by_path(path: str, paths: Optional[AppPaths] = None) -> None:
    with get_conn(paths) as conn:
        conn.execute("DELETE FROM tracks WHERE path=?", (path,))


def update_track_path(track_id: int, new_path: str, paths: Optional[AppPaths] = None) -> None:
    """文件移动后更新索引路径（保留播放统计、收藏等字段）。"""
    target = Path(new_path)
    with get_conn(paths) as conn:
        conn.execute(
            "UPDATE tracks SET path=?, dir=?, filename=?, ext=?, mtime=?, size=? WHERE id=?",
            (
                str(target),
                str(target.parent),
                target.name,
                target.suffix.lower(),
                target.stat().st_mtime if target.exists() else 0,
                target.stat().st_size if target.exists() else 0,
                track_id,
            ),
        )


def clear_library(paths: Optional[AppPaths] = None) -> None:
    with get_conn(paths) as conn:
        conn.execute("DELETE FROM tracks")
        conn.execute("DELETE FROM activity")


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    try:
        data["tag_issues"] = json.loads(data.get("tag_issues") or "[]")
    except json.JSONDecodeError:
        data["tag_issues"] = []
    try:
        data["lyrics_timing_issues"] = json.loads(data.get("lyrics_timing_issues") or "[]")
    except json.JSONDecodeError:
        data["lyrics_timing_issues"] = []
    return data


def get_track(track_id: int, paths: Optional[AppPaths] = None) -> Optional[Dict[str, Any]]:
    with get_conn(paths) as conn:
        row = conn.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
        return _row_to_dict(row) if row else None


def get_track_by_path(path: str, paths: Optional[AppPaths] = None) -> Optional[Dict[str, Any]]:
    with get_conn(paths) as conn:
        row = conn.execute("SELECT * FROM tracks WHERE path=?", (str(path),)).fetchone()
        return _row_to_dict(row) if row else None


def all_tracks(paths: Optional[AppPaths] = None) -> List[Dict[str, Any]]:
    with get_conn(paths) as conn:
        rows = conn.execute("SELECT * FROM tracks ORDER BY artist, album, disc_no, track_no, title").fetchall()
        return [_row_to_dict(row) for row in rows]


def tracks_by_ids(ids: Sequence[int], paths: Optional[AppPaths] = None) -> List[Dict[str, Any]]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    with get_conn(paths) as conn:
        rows = conn.execute(f"SELECT * FROM tracks WHERE id IN ({placeholders})", list(ids)).fetchall()
        return [_row_to_dict(row) for row in rows]


_FILTER_SQL = {
    "all": "1=1",
    "missing_any": "(has_cover=0 OR (has_lyrics=0 AND no_lyrics=0))",
    "missing_cover": "has_cover=0",
    "missing_lyrics": "has_lyrics=0 AND no_lyrics=0",
    "missing_both": "has_cover=0 AND has_lyrics=0 AND no_lyrics=0",
    "issues": "tag_issues NOT IN ('[]', '')",
    "error": "health='error'",
    "ok": "health='ok'",
    "favorite": "favorite=1",
    "unsynced_lyrics": "has_lyrics=1 AND lyrics_synced=0",
    "no_embedded_cover": "has_cover=1 AND cover_source!='embed'",
    "bad_lyrics": "has_lyrics=1 AND lyrics_timing_status IN ('broken','suspect')",
    "lyrics_timing": "has_lyrics=1 AND lyrics_timing_status IN ('broken','suspect')",
}

_SORT_SQL = {
    "title": "title COLLATE NOCASE ASC",
    "artist": "artist COLLATE NOCASE ASC, album COLLATE NOCASE ASC, track_no ASC",
    "album": "album COLLATE NOCASE ASC, disc_no ASC, track_no ASC",
    "added": "added_at DESC",
    "duration": "duration DESC",
    "size": "size DESC",
    "path": "path ASC",
}


def list_tracks(
    query: str = "",
    filter_name: str = "all",
    sort: str = "artist",
    page: int = 1,
    page_size: int = 60,
    ids: Optional[Sequence[int]] = None,
    paths: Optional[AppPaths] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    where: List[str] = []
    params: List[Any] = []
    if filter_name in _FILTER_SQL:
        where.append(_FILTER_SQL[filter_name])
    elif filter_name == "duplicates" and ids is not None:
        if not ids:
            return [], 0
        where.append(f"id IN ({','.join('?' for _ in ids)})")
        params.extend(list(ids))
    if query:
        like = f"%{query.strip()}%"
        where.append("(title LIKE ? OR artist LIKE ? OR album LIKE ? OR filename LIKE ?)")
        params.extend([like, like, like, like])
    if ids is not None and filter_name != "duplicates":
        if not ids:
            return [], 0
        where.append(f"id IN ({','.join('?' for _ in ids)})")
        params.extend(list(ids))
    clause = " AND ".join(where) if where else "1=1"
    order = _SORT_SQL.get(sort, _SORT_SQL["artist"])
    page = max(1, int(page))
    page_size = max(1, min(200, int(page_size)))
    offset = (page - 1) * page_size

    with get_conn(paths) as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM tracks WHERE {clause}", params).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM tracks WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        return [_row_to_dict(row) for row in rows], int(total)


def stats(paths: Optional[AppPaths] = None) -> Dict[str, Any]:
    with get_conn(paths) as conn:
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              COUNT(DISTINCT CASE WHEN album!='' THEN album END) AS albums,
              COUNT(DISTINCT CASE WHEN artist!='' THEN artist END) AS artists,
              COALESCE(SUM(duration), 0) AS duration,
              COALESCE(SUM(size), 0) AS size,
              COALESCE(SUM(CASE WHEN has_cover=0 THEN 1 ELSE 0 END), 0) AS missing_cover,
              COALESCE(SUM(CASE WHEN has_lyrics=0 AND no_lyrics=0 THEN 1 ELSE 0 END), 0) AS missing_lyrics,
              COALESCE(SUM(CASE WHEN has_cover=0 AND has_lyrics=0 AND no_lyrics=0 THEN 1 ELSE 0 END), 0) AS missing_both,
              COALESCE(SUM(CASE WHEN tag_issues NOT IN ('[]','') THEN 1 ELSE 0 END), 0) AS issues,
              COALESCE(SUM(CASE WHEN health='error' THEN 1 ELSE 0 END), 0) AS errors,
              COALESCE(SUM(CASE WHEN has_lyrics=1 AND lyrics_synced=1 THEN 1 ELSE 0 END), 0) AS synced_lyrics,
              COALESCE(SUM(CASE WHEN has_lyrics=1 AND lyrics_timing_status IN ('broken','suspect') THEN 1 ELSE 0 END), 0) AS timing_issues,
              COALESCE(SUM(CASE WHEN has_cover=1 AND cover_source='embed' THEN 1 ELSE 0 END), 0) AS embedded_covers
            FROM tracks
            """
        ).fetchone()
        formats = conn.execute(
            "SELECT ext, COUNT(*) AS count, COALESCE(SUM(size),0) AS size "
            "FROM tracks GROUP BY ext ORDER BY count DESC"
        ).fetchall()
        recent = conn.execute(
            "SELECT * FROM tracks ORDER BY added_at DESC LIMIT 8"
        ).fetchall()

    total = int(row["total"] or 0)
    missing_cover = int(row["missing_cover"] or 0)
    missing_lyrics = int(row["missing_lyrics"] or 0)
    issues = int(row["issues"] or 0)
    errors = int(row["errors"] or 0)
    timing_issues = int(row["timing_issues"] or 0)

    penalty = 0.0
    if total:
        penalty += (missing_cover / total) * 22
        penalty += (missing_lyrics / total) * 22
        penalty += (issues / total) * 12
        penalty += (errors / total) * 15
        penalty += (timing_issues / total) * 8
    health_score = max(0, min(100, round(100 - penalty)))

    return {
        "total": total,
        "albums": int(row["albums"] or 0),
        "artists": int(row["artists"] or 0),
        "duration": round(float(row["duration"] or 0), 1),
        "size": int(row["size"] or 0),
        "missing_cover": missing_cover,
        "missing_lyrics": missing_lyrics,
        "missing_both": int(row["missing_both"] or 0),
        "issues": issues,
        "errors": errors,
        "synced_lyrics": int(row["synced_lyrics"] or 0),
        "timing_issues": timing_issues,
        "embedded_covers": int(row["embedded_covers"] or 0),
        "health_score": health_score,
        "formats": [
            {"ext": item["ext"], "count": int(item["count"]), "size": int(item["size"])} for item in formats
        ],
        "recent": [_row_to_dict(item) for item in recent],
    }


def filter_counts(paths: Optional[AppPaths] = None) -> Dict[str, int]:
    with get_conn(paths) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total,
              SUM(CASE WHEN has_cover=0 THEN 1 ELSE 0 END) AS missing_cover,
              SUM(CASE WHEN has_lyrics=0 AND no_lyrics=0 THEN 1 ELSE 0 END) AS missing_lyrics,
              SUM(CASE WHEN tag_issues NOT IN ('[]','') THEN 1 ELSE 0 END) AS issues,
              SUM(CASE WHEN has_lyrics=1 AND lyrics_timing_status IN ('broken','suspect') THEN 1 ELSE 0 END) AS timing_issues,
              SUM(CASE WHEN favorite=1 THEN 1 ELSE 0 END) AS favorite
            FROM tracks
            """
        ).fetchone()
    return {key: int(row[key] or 0) for key in row.keys()}


def ids_for_filter(filter_name: str, limit: Optional[int] = None, paths: Optional[AppPaths] = None) -> List[int]:
    clause = _FILTER_SQL.get(filter_name, "1=1")
    sql = f"SELECT id FROM tracks WHERE {clause} ORDER BY artist, album, disc_no, track_no"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with get_conn(paths) as conn:
        return [int(row["id"]) for row in conn.execute(sql).fetchall()]


def set_offset(track_id: int, offset_ms: int, paths: Optional[AppPaths] = None) -> None:
    with get_conn(paths) as conn:
        conn.execute(
            "UPDATE tracks SET lyrics_offset_ms=? WHERE id=?",
            (max(-60000, min(60000, int(offset_ms))), track_id),
        )


def set_favorite(track_id: int, favorite: bool, paths: Optional[AppPaths] = None) -> None:
    with get_conn(paths) as conn:
        conn.execute("UPDATE tracks SET favorite=? WHERE id=?", (1 if favorite else 0, track_id))


def set_no_lyrics(track_id: int, value: bool, paths: Optional[AppPaths] = None) -> None:
    with get_conn(paths) as conn:
        conn.execute(
            "UPDATE tracks SET no_lyrics=?, health=CASE WHEN ?=1 THEN CASE WHEN has_cover=1 THEN 'ok' ELSE 'missing_cover' END ELSE CASE WHEN has_lyrics=1 THEN 'ok' ELSE 'missing_lyrics' END END WHERE id=?",
            (1 if value else 0, 1 if value else 0, track_id),
        )


def mark_played(track_id: int, paths: Optional[AppPaths] = None) -> None:
    with get_conn(paths) as conn:
        conn.execute(
            "UPDATE tracks SET play_count=play_count+1, last_played=? WHERE id=?",
            (now_iso(), track_id),
        )


def touch_scanned(paths: Optional[AppPaths] = None) -> None:
    set_meta("last_scan_at", now_iso(), paths)


def record_repair_attempt(
    track_id: int,
    status: str,
    paths: Optional[AppPaths] = None,
) -> None:
    """记录一次自动修复尝试，供调度器限制重试次数。"""
    with get_conn(paths) as conn:
        conn.execute(
            "UPDATE tracks SET repair_attempts=repair_attempts+1, last_repair_at=?, "
            "last_repair_status=? WHERE id=?",
            (now_iso(), status[:20], track_id),
        )


def ids_needing_repair(
    targets: Sequence[str],
    limit: int = 40,
    max_attempts: int = 3,
    paths: Optional[AppPaths] = None,
) -> List[int]:
    """挑选需要自动修复的歌曲：缺歌词 / 缺封面，且失败次数未超过上限。"""
    clauses = []
    if "lyrics" in targets:
        clauses.append("has_lyrics=0 AND no_lyrics=0")
    if "cover" in targets:
        clauses.append("has_cover=0")
    if not clauses:
        return []
    sql = (
        "SELECT id FROM tracks WHERE ("
        + " OR ".join(clauses)
        + ") AND health!='error' AND repair_attempts < ? "
        "ORDER BY repair_attempts ASC, added_at DESC LIMIT ?"
    )
    with get_conn(paths) as conn:
        rows = conn.execute(sql, (int(max_attempts), int(limit))).fetchall()
        return [int(row["id"]) for row in rows]


# ---------------------------------------------------------------------------
# 活动日志
# ---------------------------------------------------------------------------

def log_activity(
    action: str,
    status: str = "ok",
    detail: str = "",
    track_id: Optional[int] = None,
    track_title: str = "",
    source: str = "web",
    paths: Optional[AppPaths] = None,
) -> None:
    global _activity_counter
    with get_conn(paths) as conn:
        conn.execute(
            "INSERT INTO activity(ts, track_id, track_title, action, status, detail, source) "
            "VALUES(?,?,?,?,?,?,?)",
            (now_iso(), track_id, track_title[:200], action[:40], status[:20], detail[:800], source[:20]),
        )
        with _activity_lock:
            _activity_counter += 1
            should_prune = _activity_counter % 80 == 0
        if should_prune:
            conn.execute(
                "DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT 3000)"
            )


def recent_activity(limit: int = 30, paths: Optional[AppPaths] = None) -> List[Dict[str, Any]]:
    with get_conn(paths) as conn:
        rows = conn.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        return [dict(row) for row in rows]


def activity_count(paths: Optional[AppPaths] = None) -> int:
    with get_conn(paths) as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM activity").fetchone()["c"])


# ---------------------------------------------------------------------------
# 重复检测
# ---------------------------------------------------------------------------

def find_duplicates(min_duration: float = 20, paths: Optional[AppPaths] = None) -> List[Dict[str, Any]]:
    tracks = all_tracks(paths)
    groups: List[Dict[str, Any]] = []
    used: set = set()
    try:
        ignored = set(json.loads(get_meta("duplicates.ignored", "[]", paths) or "[]"))
    except Exception:
        ignored = set()

    def signature(items: Sequence[Dict[str, Any]]) -> str:
        return "|".join(
            sorted(
                f"{normalize_key(item.get('title') or '')}|{normalize_key(item.get('artist') or '')}|{normalize_key(item.get('album') or '')}"
                for item in items
            )
        )

    # 1) 文件级完全重复（头尾哈希）
    by_hash: Dict[str, List[Dict[str, Any]]] = {}
    for track in tracks:
        if track.get("quick_hash"):
            by_hash.setdefault(track["quick_hash"], []).append(track)
    for digest, items in by_hash.items():
        if len(items) > 1:
            items = sorted(items, key=lambda t: t["size"], reverse=True)
            sig = f"hash:{digest}"
            if sig in ignored or signature(items) in ignored:
                continue
            groups.append(
                {
                    "key": f"hash:{digest[:10]}",
                    "signature": sig,
                    "confidence": "exact",
                    "reason": "文件内容完全相同",
                    "keep_id": items[0]["id"],
                    "tracks": items,
                }
            )
            used.update(item["id"] for item in items)

    # 2) 同曲不同格式 / 不同码率：标题 + 歌手 + 时长接近。
    # 只按标题会把「龙卷风（邓紫棋）」和「龙卷风（周杰伦）」误判，必须带歌手。
    by_song: Dict[Tuple[str, str, int], List[Dict[str, Any]]] = {}
    for track in tracks:
        if track["id"] in used:
            continue
        if float(track.get("duration") or 0) < min_duration:
            continue
        title_key = normalize_key(track.get("title") or Path(track["filename"]).stem)
        artist_key = normalize_key(track.get("artist") or "")
        if not title_key or not artist_key:
            continue
        bucket = int(round(float(track.get("duration") or 0) / 2.0))
        by_song.setdefault((title_key, artist_key, bucket), []).append(track)

    for (_title, _artist, _bucket), items in by_song.items():
        if len(items) < 2:
            continue
        album_keys = {normalize_key(item.get("album") or "") for item in items}
        album_keys.discard("")
        high_confidence = len(album_keys) == 1 and len(items) > 1
        items = sorted(items, key=lambda t: (t["size"], t["bits"], t["bitrate"]), reverse=True)
        sig = signature(items)
        if sig in ignored:
            continue
        groups.append(
            {
                "key": f"song:{_title}:{_artist}:{_bucket}",
                "signature": sig,
                "confidence": "high" if high_confidence else "medium",
                "reason": "同歌手、同专辑、同名、时长接近" if high_confidence else "同歌手、同名、时长接近（可能是不同专辑/版本）",
                "keep_id": items[0]["id"],
                "tracks": items,
            }
        )

    groups.sort(
        key=lambda g: (
            {"exact": 0, "high": 1, "medium": 2}.get(g.get("confidence", ""), 3),
            -len(g["tracks"]),
        )
    )
    return groups


def ignore_duplicate_signature(value: str, paths: Optional[AppPaths] = None) -> None:
    if not value:
        return
    try:
        values = set(json.loads(get_meta("duplicates.ignored", "[]", paths) or "[]"))
    except Exception:
        values = set()
    values.add(value)
    set_meta("duplicates.ignored", json.dumps(sorted(values), ensure_ascii=False), paths)


def ignored_duplicate_count(paths: Optional[AppPaths] = None) -> int:
    try:
        return len(json.loads(get_meta("duplicates.ignored", "[]", paths) or "[]"))
    except Exception:
        return 0


def import_rows(rows: Iterable[Dict[str, Any]], paths: Optional[AppPaths] = None) -> int:
    count = 0
    for row in rows:
        upsert_track(row, paths)
        count += 1
    return count
