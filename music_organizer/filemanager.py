"""文件管理：浏览、上传、重命名、移动/复制、删除到回收站、整理与还原。

所有路径都被限制在设置里的音乐目录内；删除一律移动到应用回收站；
每次写操作都会记录可撤销的反向操作，首次写操作前保存一份永久原始快照。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import audio, db, scanner
from .config import Config
from .paths import AppPaths, get_paths
from .textutil import clean_spaces, parse_filename, safe_filename

AUDIO_EXTS = set(audio.READABLE_EXTS)
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
TEXT_EXTS = {".lrc", ".txt", ".md", ".json", ".nfo", ".cue", ".m3u", ".m3u8", ".ini", ".yaml", ".yml"}
UPLOAD_EXTS = AUDIO_EXTS | IMAGE_EXTS | TEXT_EXTS
SIDECAR_EXTS = (".lrc", ".jpg", ".jpeg", ".png", ".webp")
HISTORY_LIMIT = 50
MAX_TEXT_BYTES = 2 * 1024 * 1024
PLAN_TTL_SECONDS = 3600


class FileManagerError(Exception):
    """可以直接展示给用户的文件管理错误。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


class FileManager:
    _lock = threading.RLock()

    def __init__(self, config: Config, paths: Optional[AppPaths] = None) -> None:
        self.config = config
        self.paths = paths or get_paths()
        self.paths.ensure()

    # ------------------------------------------------------------------
    # 路径与状态
    # ------------------------------------------------------------------
    def _root(self) -> Path:
        root = self.config.music_dir
        if not root or not root.is_dir():
            raise FileManagerError("音乐目录不存在，请先在设置里选择目录")
        root = root.resolve()
        if root.parent == root:
            raise FileManagerError("出于安全考虑，不能管理文件系统根目录")
        try:
            self.paths.home.resolve().relative_to(root)
        except ValueError:
            pass
        else:
            raise FileManagerError("音乐目录不能包含插件自己的数据目录")
        return root

    def _state_dir(self) -> Path:
        directory = self.paths.home / "filemanager"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _root_key(self) -> str:
        return hashlib.sha1(str(self._root()).encode("utf-8")).hexdigest()[:12]

    def _original_path(self) -> Path:
        return self._state_dir() / f"original-{self._root_key()}.json"

    def _history_path(self) -> Path:
        return self._state_dir() / f"history-{self._root_key()}.json"

    def _plans_dir(self) -> Path:
        directory = self._state_dir() / "plans"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _load_json(self, path: Path, default: Any) -> Any:
        try:
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        return default

    def _save_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _resolve(self, rel: str, must_exist: bool = False, must_dir: bool = False) -> Path:
        root = self._root()
        raw = str(rel or "").replace("\\", "/").strip()
        candidate = (root / raw).resolve() if raw else root
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise FileManagerError("路径超出音乐目录") from exc
        if must_exist and not candidate.exists():
            raise FileManagerError("文件或文件夹不存在")
        if must_dir and not candidate.is_dir():
            raise FileManagerError("目标不是文件夹")
        return candidate

    def _rel(self, path: Path) -> str:
        try:
            rel = path.resolve().relative_to(self._root())
        except ValueError as exc:
            raise FileManagerError("路径超出音乐目录") from exc
        return "" if str(rel) == "." else rel.as_posix()

    def _safe_name(self, name: str, fallback: str = "未命名") -> str:
        value = str(name or "").strip().replace("\\", "/")
        value = value.split("/")[-1]
        value = value.replace("\x00", "").strip().strip(".")
        if not value:
            value = fallback
        suffix = Path(value).suffix
        stem = value[: -len(suffix)] if suffix else value
        stem = safe_filename(stem, max_len=150).strip(" .") or fallback
        suffix = safe_filename(suffix.lstrip("."), max_len=16).strip(" .") if suffix else ""
        return f"{stem}.{suffix}" if suffix else stem

    def _unique_target(self, target: Path) -> Path:
        if not target.exists():
            return target
        if target.is_dir():
            base, ext = target, ""
        else:
            base, ext = target.with_suffix(""), target.suffix
        counter = 1
        while True:
            candidate = Path(f"{base} ({counter}){ext}")
            if not candidate.exists():
                return candidate
            counter += 1

    def _hash(self, path: Path, size: Optional[int] = None) -> str:
        try:
            return audio.quick_hash(path, size)
        except Exception:
            return ""

    def _original_info(self) -> Dict[str, Any]:
        data = self._load_json(self._original_path(), None)
        if not data:
            return {"exists": False, "created_at": "", "file_count": 0, "root": str(self._root())}
        return {
            "exists": True,
            "created_at": data.get("created_at") or "",
            "file_count": len(data.get("entries") or []),
            "root": data.get("root") or "",
        }

    def _ensure_original(self) -> None:
        path = self._original_path()
        if path.is_file():
            return
        root = self._root()
        entries: List[Dict[str, Any]] = []
        for current, dirs, names in os.walk(str(root)):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            base = Path(current)
            for name in sorted(names):
                if name.startswith("."):
                    continue
                file_path = base / name
                if file_path.is_symlink() or not file_path.is_file():
                    continue
                try:
                    stat = file_path.stat()
                except OSError:
                    continue
                rel = self._rel(file_path)
                entry: Dict[str, Any] = {
                    "path": rel,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "hash": self._hash(file_path, stat.st_size),
                }
                if file_path.suffix.lower() in TEXT_EXTS and stat.st_size <= MAX_TEXT_BYTES:
                    try:
                        entry["text"] = base64.b64encode(file_path.read_bytes()).decode("ascii")
                    except OSError:
                        pass
                entries.append(entry)
        self._save_json(
            path,
            {
                "version": 1,
                "root": str(root),
                "created_at": _now(),
                "entries": entries,
            },
        )

    def _history(self) -> Dict[str, Any]:
        data = self._load_json(self._history_path(), {"version": 1, "items": []})
        if not isinstance(data.get("items"), list):
            data["items"] = []
        return data

    def _record(self, action: str, summary: str, inverses: Sequence[Dict[str, Any]], undoable: bool = True) -> Dict[str, Any]:
        self._ensure_original()
        data = self._history()
        item = {
            "id": uuid.uuid4().hex,
            "ts": _now(),
            "action": action,
            "summary": summary,
            "inverses": list(inverses),
            "undoable": bool(undoable),
            "undone": False,
        }
        data["items"].append(item)
        data["items"] = data["items"][-HISTORY_LIMIT:]
        self._save_json(self._history_path(), data)
        db.log_activity(action, "ok", summary, source="files", paths=self.paths)
        return item

    def history(self, limit: int = 30) -> Dict[str, Any]:
        with self._lock:
            data = self._history()
            items = list(reversed(data.get("items") or []))[: max(1, min(100, int(limit)))]
            for item in items:
                item["can_undo"] = bool(item.get("undoable") and not item.get("undone"))
            return {
                "items": items,
                "total": len(data.get("items") or []),
                "undoable": sum(1 for item in items if item.get("can_undo")),
                "original": self._original_info(),
                "limit": HISTORY_LIMIT,
            }

    # ------------------------------------------------------------------
    # 浏览
    # ------------------------------------------------------------------
    def list_dir(self, rel: str = "", query: str = "", sort: str = "name") -> Dict[str, Any]:
        with self._lock:
            root = self._root()
            current = self._resolve(rel, must_exist=True, must_dir=True)
            current_rel = self._rel(current)
            tracks = {}
            try:
                tracks = {str(item.get("path")): item for item in db.all_tracks(self.paths)}
            except Exception:
                tracks = {}

            entries: List[Dict[str, Any]] = []
            for item in sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
                if item.name.startswith("."):
                    continue
                if item.is_symlink():
                    try:
                        item.resolve().relative_to(root)
                    except (OSError, ValueError):
                        continue
                try:
                    stat = item.stat()
                except OSError:
                    continue
                is_dir = item.is_dir()
                ext = "" if is_dir else item.suffix.lower()
                if is_dir:
                    kind = "folder"
                elif ext in AUDIO_EXTS:
                    kind = "audio"
                elif ext in IMAGE_EXTS:
                    kind = "image"
                elif ext in TEXT_EXTS:
                    kind = "text"
                else:
                    kind = "other"
                track = tracks.get(str(item.resolve())) if kind == "audio" else None
                entry = {
                    "name": item.name,
                    "path": self._rel(item),
                    "is_dir": is_dir,
                    "size": 0 if is_dir else stat.st_size,
                    "mtime": stat.st_mtime,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    "ext": ext,
                    "kind": kind,
                    "writable": bool(os.access(item, os.W_OK)),
                    "track_id": int(track["id"]) if track else 0,
                }
                if query:
                    needle = query.casefold()
                    if needle not in item.name.casefold():
                        continue
                entries.append(entry)

            if sort == "size":
                entries.sort(key=lambda x: (not x["is_dir"], -x["size"], x["name"].casefold()))
            elif sort == "modified":
                entries.sort(key=lambda x: (not x["is_dir"], -x["mtime"], x["name"].casefold()))
            elif sort == "type":
                entries.sort(key=lambda x: (not x["is_dir"], x["kind"], x["name"].casefold()))
            else:
                entries.sort(key=lambda x: (not x["is_dir"], x["name"].casefold()))

            breadcrumbs = [{"name": root.name or "音乐", "path": ""}]
            built = current.relative_to(root)
            if str(built) != ".":
                acc = Path()
                for part in built.parts:
                    acc /= part
                    breadcrumbs.append({"name": part, "path": acc.as_posix()})

            try:
                usage = shutil.disk_usage(str(root))
                disk = {"total": usage.total, "used": usage.used, "free": usage.free}
            except OSError:
                disk = {"total": 0, "used": 0, "free": 0}

            return {
                "root": str(root),
                "path": current_rel,
                "breadcrumbs": breadcrumbs,
                "entries": entries,
                "dirs": sum(1 for item in entries if item["is_dir"]),
                "files": sum(1 for item in entries if not item["is_dir"]),
                "readonly": sum(1 for item in entries if not item.get("writable")),
                "disk": disk,
                "writable": os.access(root, os.W_OK),
                "original": self._original_info(),
                "history_count": len(self._history().get("items") or []),
            }

    def list_dirs(self) -> List[Dict[str, str]]:
        with self._lock:
            root = self._root()
            result = [{"path": "", "name": root.name or "音乐根目录"}]
            for current, dirs, _names in os.walk(str(root)):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                base = Path(current)
                for name in dirs:
                    folder = base / name
                    if folder.is_symlink():
                        continue
                    rel = self._rel(folder)
                    depth = rel.count("/")
                    result.append({"path": rel, "name": f"{'　' * depth}{name}"})
            result.sort(key=lambda item: (item["path"].count("/"), item["path"].casefold()))
            return result

    # ------------------------------------------------------------------
    # 简单操作
    # ------------------------------------------------------------------
    def mkdir(self, parent_rel: str, name: str) -> Dict[str, Any]:
        with self._lock:
            self._ensure_original()
            parent = self._resolve(parent_rel, must_exist=True, must_dir=True)
            clean = self._safe_name(name, "新建文件夹")
            target = parent / clean
            if target.exists():
                raise FileManagerError("同名文件或文件夹已存在")
            target.mkdir(parents=False)
            self._record("新建文件夹", f"新建 {self._rel(target)}", [{"op": "rmdir", "path": self._rel(target)}])
            return {"path": self._rel(target)}

    def rename(self, rel: str, new_name: str) -> Dict[str, Any]:
        with self._lock:
            self._ensure_original()
            source = self._resolve(rel, must_exist=True)
            if source == self._root():
                raise FileManagerError("不能重命名音乐根目录")
            clean = self._safe_name(new_name, source.name)
            target = source.with_name(clean)
            if target == source:
                return {"path": self._rel(source), "unchanged": True}
            if target.exists():
                raise FileManagerError("同名文件或文件夹已存在")
            old_rel, new_rel = self._rel(source), self._rel(target)
            shutil.move(str(source), str(target))
            self._sync_move(source, target)
            self._record("重命名", f"{source.name} → {target.name}", [{"op": "move", "from": new_rel, "to": old_rel}])
            return {"from": old_rel, "to": new_rel}

    def delete(self, rels: Sequence[str]) -> Dict[str, Any]:
        with self._lock:
            self._ensure_original()
            root = self._root()
            sources = []
            for rel in rels:
                path = self._resolve(rel, must_exist=True)
                if path == root:
                    raise FileManagerError("不能删除音乐根目录")
                sources.append(path)
            inverses = []
            moved = []
            for source in sources:
                rel = self._rel(source)
                trash = self._trash(source)
                inverses.append({"op": "restore", "trash": str(trash), "to": rel})
                self._remove_db_under(source)
                moved.append(rel)
            self._record("删除", f"移入回收站：{len(moved)} 项", inverses)
            return {"moved": moved}

    def move(self, rels: Sequence[str], dest_rel: str) -> Dict[str, Any]:
        return self._transfer(rels, dest_rel, copy=False)

    def copy(self, rels: Sequence[str], dest_rel: str) -> Dict[str, Any]:
        return self._transfer(rels, dest_rel, copy=True)

    def _transfer(self, rels: Sequence[str], dest_rel: str, copy: bool) -> Dict[str, Any]:
        with self._lock:
            self._ensure_original()
            destination = self._resolve(dest_rel, must_exist=True, must_dir=True)
            inverses = []
            results = []
            for rel in rels:
                source = self._resolve(rel, must_exist=True)
                if source.is_dir():
                    try:
                        destination.relative_to(source)
                    except ValueError:
                        pass
                    else:
                        raise FileManagerError("不能把文件夹移动到它自己里面")
                target = self._unique_target(destination / source.name)
                if target == source:
                    continue
                if copy:
                    if source.is_dir():
                        shutil.copytree(str(source), str(target))
                    else:
                        shutil.copy2(str(source), str(target))
                    inverses.append({"op": "trash", "path": self._rel(target)})
                    self._scan_audio_under(target)
                else:
                    shutil.move(str(source), str(target))
                    inverses.append({"op": "move", "from": self._rel(target), "to": self._rel(source)})
                    self._sync_move(source, target)
                results.append({"from": self._rel(source), "to": self._rel(target), "copy": copy})
            if inverses:
                self._record("复制" if copy else "移动", f"{len(results)} 项 → {self._rel(destination)}", inverses)
            return {"items": results}

    # ------------------------------------------------------------------
    # 上传 / 文本
    # ------------------------------------------------------------------
    def save_uploads(
        self,
        dir_rel: str,
        files: Iterable[Any],
        auto_classify: bool = True,
        classify_mode: str = "artist_album",
        only_existing: bool = True,
    ) -> Dict[str, Any]:
        with self._lock:
            self._ensure_original()
            destination = self._resolve(dir_rel, must_exist=True, must_dir=True)
            if not os.access(destination, os.W_OK):
                raise FileManagerError("当前文件夹没有写入权限")
            saved = []
            errors = []
            warnings = []
            inverses = []
            for storage in files:
                original_name = getattr(storage, "filename", "") or "上传文件"
                name = self._safe_name(original_name, "上传文件")
                ext = Path(name).suffix.lower()
                if ext not in UPLOAD_EXTS:
                    errors.append({"name": original_name, "error": "不支持的文件类型"})
                    continue
                target = self._unique_target(destination / name)
                temp = target.with_name(target.name + ".uploading")
                try:
                    storage.save(str(temp))
                    if not temp.is_file() or temp.stat().st_size <= 0:
                        raise FileManagerError("空文件")
                    os.replace(temp, target)
                    os.chmod(target, 0o644)
                except Exception as exc:
                    try:
                        temp.unlink(missing_ok=True)
                    except OSError:
                        pass
                    errors.append({"name": original_name, "error": str(exc)[:200]})
                    continue
                final_target = target
                classified = False
                if auto_classify and ext in AUDIO_EXTS and classify_mode != "keep":
                    try:
                        classified_target = self._auto_classify_target(target, classify_mode, only_existing=only_existing)
                        if classified_target != target:
                            classified_target.parent.mkdir(parents=True, exist_ok=True)
                            final_target = self._unique_target(classified_target)
                            shutil.move(str(target), str(final_target))
                            classified = True
                    except Exception as exc:
                        warnings.append({"name": original_name, "error": f"自动分类失败，保留当前位置：{str(exc)[:160]}"})
                inverses.append({"op": "trash", "path": self._rel(final_target)})
                self._scan_audio_under(final_target)
                saved.append(
                    {
                        "name": final_target.name,
                        "path": self._rel(final_target),
                        "size": final_target.stat().st_size,
                        "classified": classified,
                    }
                )
            if inverses:
                self._record("导入文件", f"上传 {len(saved)} 个文件" + ("（自动分类）" if auto_classify else ""), inverses)
            return {"saved": saved, "errors": errors, "warnings": warnings}

    @staticmethod
    def _norm_folder_name(value: str) -> str:
        text = clean_spaces(str(value or "")).casefold()
        text = re.sub(r"^\s*\d{1,3}\s*[-._、]\s*", "", text)
        return re.sub(r"\s+", "", text)

    def _existing_artist_folder(self, artist: str) -> Optional[Path]:
        wanted = self._norm_folder_name(artist)
        if not wanted or wanted in ("未知歌手", "其他"):
            return None
        root = self._root()
        for folder in root.iterdir():
            if not folder.is_dir() or folder.name.startswith(".") or folder.name in ("其他", "Exports", "导出"):
                continue
            name = self._norm_folder_name(folder.name)
            if name == wanted or wanted in name or name in wanted:
                return folder
        return None

    def _other_folder(self) -> Path:
        other = self._root() / "其他"
        other.mkdir(parents=True, exist_ok=True)
        return other

    def _auto_classify_target(self, path: Path, mode: str, only_existing: bool = True) -> Path:
        """按标签/文件名给刚上传的音频选一个目录。

        only_existing=True 时不新建歌手/专辑目录：优先放进已有的歌手目录，
        找不到对应目录就放进 ``其他``。
        """
        meta: Dict[str, Any] = {}
        try:
            meta = audio.read_meta(path)
        except Exception:
            pass
        if not meta.get("artist") or not meta.get("title"):
            guessed = parse_filename(path.name, path.parent.name)
            if not meta.get("title"):
                meta["title"] = guessed.get("title") or path.stem
            if not meta.get("artist"):
                meta["artist"] = guessed.get("artist") or "未知歌手"
        if not meta.get("album"):
            meta["album"] = "未知专辑"
        values = self._values(meta, path)
        if not only_existing:
            templates = {
                "artist_album": "{artist}/{album}",
                "artist": "{artist}",
                "album": "{album}",
                "genre": "{genre}",
                "year": "{year}",
            }
            template = templates.get(mode)
            if not template:
                return path
            folder = self._render(template, values, "未分类")
            parts = [self._safe_name(part, "未分类") for part in folder.replace("\\", "/").split("/") if part.strip()]
            target_dir = self._root().joinpath(*parts) if parts else self._root()
            return target_dir / path.name

        artist_dir = self._existing_artist_folder(values["artist"])
        if mode in ("artist", "artist_album") and artist_dir is not None:
            if mode == "artist_album":
                album_key = self._norm_folder_name(values["album"])
                for child in artist_dir.iterdir():
                    if child.is_dir() and self._norm_folder_name(child.name) == album_key:
                        return child / path.name
            return artist_dir / path.name
        if mode in ("album", "genre", "year"):
            key = self._norm_folder_name(values.get(mode) or "")
            if key:
                for folder in self._root().iterdir():
                    if folder.is_dir() and not folder.name.startswith(".") and folder.name not in ("其他", "导出", "Exports"):
                        if self._norm_folder_name(folder.name) == key:
                            return folder / path.name
                        for child in folder.iterdir():
                            if child.is_dir() and self._norm_folder_name(child.name) == key:
                                return child / path.name
        return self._other_folder() / path.name

    def export_files(
        self,
        rels: Sequence[str],
        fmt: str = "original",
        bitrate: str = "320",
        dest_rel: str = "导出",
    ) -> Dict[str, Any]:
        """复制或转换音频到导出目录；默认保持原格式，不修改源文件。"""
        with self._lock:
            self._ensure_original()
            ffmpeg = shutil.which("ffmpeg")
            specs = {
                "original": (None, []),
                "flac": (".flac", ["-c:a", "flac"]),
                "mp3": (".mp3", ["-c:a", "libmp3lame"]),
                "m4a": (".m4a", ["-c:a", "aac"]),
                "ogg": (".ogg", ["-c:a", "libvorbis"]),
                "opus": (".opus", ["-c:a", "libopus"]),
                "wav": (".wav", ["-c:a", "pcm_s16le"]),
            }
            if fmt not in specs:
                raise FileManagerError("不支持的导出格式")
            if fmt not in ("original", "flac", "wav") and not ffmpeg:
                raise FileManagerError("系统未安装 ffmpeg，无法转换格式")
            destination = self._resolve(dest_rel or "导出")
            destination.mkdir(parents=True, exist_ok=True)
            if not os.access(destination, os.W_OK):
                raise FileManagerError("导出目录没有写入权限")
            rate = str(bitrate or "320").replace("k", "").strip() or "320"
            saved = []
            errors = []
            inverses = []
            for rel in list(rels)[:100]:
                source = self._resolve(rel, must_exist=True)
                if not source.is_file() or source.suffix.lower() not in AUDIO_EXTS:
                    errors.append({"path": rel, "error": "不是支持的音频文件"})
                    continue
                ext, codec_args = specs[fmt]
                out_ext = source.suffix.lower() if fmt == "original" else ext
                target = self._unique_target(destination / f"{source.stem}{out_ext}")
                try:
                    if fmt == "original":
                        shutil.copy2(str(source), str(target))
                    else:
                        temp = target.with_name(f".{target.stem}.tmp{out_ext}")
                        cmd = [
                            ffmpeg,
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
                            *codec_args,
                        ]
                        if fmt in ("mp3", "m4a", "ogg", "opus"):
                            cmd += ["-b:a", f"{rate}k"]
                        cmd.append(str(temp))
                        proc = subprocess.run(cmd, capture_output=True, timeout=900)
                        if proc.returncode != 0 or not temp.is_file() or temp.stat().st_size <= 0:
                            try:
                                temp.unlink(missing_ok=True)
                            except OSError:
                                pass
                            raise FileManagerError((proc.stderr or b"").decode("utf-8", "ignore")[-240:] or "ffmpeg 转换失败")
                        os.replace(temp, target)
                    os.chmod(target, 0o644)
                except Exception as exc:
                    errors.append({"path": rel, "error": str(exc)[:240]})
                    continue
                inverses.append({"op": "trash", "path": self._rel(target)})
                saved.append({"source": rel, "path": self._rel(target), "size": target.stat().st_size, "format": fmt})
            if inverses:
                self._record("导出音频", f"导出 {len(saved)} 个文件（{fmt}）", inverses)
            return {"saved": saved, "errors": errors, "format": fmt}

    def read_text(self, rel: str) -> Dict[str, Any]:
        with self._lock:
            path = self._resolve(rel, must_exist=True)
            if not path.is_file() or path.suffix.lower() not in TEXT_EXTS:
                raise FileManagerError("该文件不支持文本编辑")
            if path.stat().st_size > MAX_TEXT_BYTES:
                raise FileManagerError("文件太大，无法在线编辑")
            return {
                "path": self._rel(path),
                "text": path.read_text(encoding="utf-8", errors="replace"),
                "size": path.stat().st_size,
            }

    def write_text(self, rel: str, text: str) -> Dict[str, Any]:
        with self._lock:
            self._ensure_original()
            path = self._resolve(rel, must_exist=True)
            if not path.is_file() or path.suffix.lower() not in TEXT_EXTS:
                raise FileManagerError("该文件不支持文本编辑")
            raw = str(text or "").encode("utf-8")
            if len(raw) > MAX_TEXT_BYTES:
                raise FileManagerError("文本超过 2 MB 限制")
            before = path.read_text(encoding="utf-8", errors="replace")
            temp = path.with_name(path.name + ".editing")
            temp.write_bytes(raw)
            os.replace(temp, path)
            self._record("编辑文本", f"编辑 {path.name}", [{"op": "write", "path": self._rel(path), "text": before}])
            return {"path": self._rel(path), "size": len(raw)}

    # ------------------------------------------------------------------
    # 整理 / 命名
    # ------------------------------------------------------------------
    @staticmethod
    def _values(meta: Dict[str, Any], path: Path) -> Dict[str, Any]:
        title = str(meta.get("title") or path.stem).strip() or path.stem
        artist = str(meta.get("artist") or "未知歌手").strip() or "未知歌手"
        album = str(meta.get("album") or "未知专辑").strip() or "未知专辑"
        return {
            "title": title,
            "artist": artist,
            "album": album,
            "album_artist": str(meta.get("album_artist") or artist).strip() or artist,
            "track": int(meta.get("track_no") or 0),
            "track_no": int(meta.get("track_no") or 0),
            "disc": int(meta.get("disc_no") or 0),
            "year": str(meta.get("year") or "").strip(),
            "genre": str(meta.get("genre") or "未知流派").strip() or "未知流派",
            "ext": path.suffix.lower(),
        }

    @staticmethod
    def _render(template: str, values: Dict[str, Any], fallback: str = "未分类") -> str:
        try:
            rendered = template.format_map(values)
        except (KeyError, ValueError) as exc:
            raise FileManagerError(f"模板格式不正确：{exc}") from exc
        result = ""
        for part in rendered.replace("\\", "/").split("/"):
            part = part.strip()
            if not part or part in (".", ".."):
                continue
            result += ("/" if result else "") + part
        return result or fallback

    def _plan_audio(self, target_builder, keep_artists: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        root = self._root()
        keep = {str(item or "").strip().casefold() for item in (keep_artists or []) if str(item or "").strip()}
        files = [Path(p) for p in scanner.collect_audio_files(self.config)]
        try:
            tracks = {str(item.get("path")): item for item in db.all_tracks(self.paths)}
        except Exception:
            tracks = {}
        items = []
        used: set = set()
        for source in files:
            try:
                source = source.resolve()
                source.relative_to(root)
            except (OSError, ValueError):
                continue
            meta = tracks.get(str(source))
            if not meta:
                try:
                    meta = audio.read_meta(source)
                except Exception:
                    meta = {}
            values = self._values(meta or {}, source)
            if keep and values["artist"].strip().casefold() in keep:
                items.append(
                    {
                        "source": self._rel(source),
                        "target": self._rel(source),
                        "status": "skipped",
                        "reason": f"保持歌手「{values['artist']}」当前目录",
                        "title": values["title"],
                        "artist": values["artist"],
                        "album": values["album"],
                    }
                )
                continue
            try:
                target = target_builder(source, values)
            except FileManagerError as exc:
                items.append({"source": self._rel(source), "target": "", "status": "error", "reason": str(exc)})
                continue
            target = target.resolve()
            try:
                target.relative_to(root)
            except ValueError:
                items.append({"source": self._rel(source), "target": "", "status": "error", "reason": "目标超出音乐目录"})
                continue
            if target == source:
                items.append({"source": self._rel(source), "target": self._rel(target), "status": "skipped", "reason": "已经在目标位置"})
                continue
            key = str(target).casefold()
            if key in used or target.exists():
                target = self._unique_target(target)
                key = str(target).casefold()
            used.add(key)
            sidecars = []
            for ext in SIDECAR_EXTS:
                old = source.with_suffix(ext)
                if not old.exists():
                    continue
                new = target.with_suffix(ext)
                if new.exists():
                    continue
                sidecars.append({"source": self._rel(old), "target": self._rel(new)})
            items.append(
                {
                    "source": self._rel(source),
                    "target": self._rel(target),
                    "status": "planned",
                    "reason": "",
                    "title": values["title"],
                    "artist": values["artist"],
                    "album": values["album"],
                    "sidecars": sidecars,
                }
            )
        return {
            "items": items,
            "planned": sum(1 for item in items if item["status"] == "planned"),
            "skipped": sum(1 for item in items if item["status"] == "skipped"),
            "errors": sum(1 for item in items if item["status"] == "error"),
        }

    def preview_organize(
        self,
        mode: str,
        dir_template: str = "",
        file_template: str = "",
        keep_artists: str = "",
    ) -> Dict[str, Any]:
        with self._lock:
            self._ensure_original()
            folder_presets = {
                "artist": "{artist}",
                "artist_album": "{artist}/{album}",
                "album": "{album}",
                "genre": "{genre}",
                "year": "{year}",
            }
            file_presets = {
                "keep": "",
                "artist_title": "{artist} - {title}",
                "title_artist": "{title} - {artist}",
                "track_title": "{track:02d} - {title}",
                "title": "{title}",
            }
            filename = file_template.strip()
            if filename not in file_presets.values() and not filename:
                filename = ""

            if mode == "keep":
                def build(source: Path, values: Dict[str, Any]) -> Path:
                    if not filename:
                        return source
                    name = self._render(filename, values, values["title"])
                    ext = source.suffix
                    if not name.lower().endswith(ext):
                        name += ext
                    return source.with_name(self._safe_name(name, values["title"] + ext))
            else:
                folder = dir_template.strip() if mode == "custom" else folder_presets.get(mode, "{artist}/{album}")
                if not folder:
                    raise FileManagerError("请选择目录结构")

                def build(source: Path, values: Dict[str, Any]) -> Path:
                    folder_text = self._render(folder, values, "未分类")
                    name = self._render(filename, values, values["title"]) if filename else source.name
                    ext = source.suffix
                    if filename and not name.lower().endswith(ext):
                        name += ext
                    clean_parts = []
                    for part in folder_text.replace("\\", "/").split("/"):
                        part = self._safe_name(part, "未分类")
                        if part:
                            clean_parts.append(part)
                    clean_name = self._safe_name(name, values["title"] + ext)
                    return self._root().joinpath(*clean_parts, clean_name)

            result = self._plan_audio(
                build,
                keep_artists=[item.strip() for item in str(keep_artists or "").replace("，", ",").replace("、", ",").replace(";", ",").replace("；", ",").split(",") if item.strip()],
            )
            plan = {"type": "organize", "created": time.time(), "mode": mode, "data": result}
            plan_id = uuid.uuid4().hex
            self._save_json(self._plans_dir() / f"{plan_id}.json", plan)
            result["plan_id"] = plan_id
            return result

    def preview_rename(self, mode: str, file_template: str = "") -> Dict[str, Any]:
        with self._lock:
            self._ensure_original()
            presets = {
                "artist_title": "{artist} - {title}",
                "title_artist": "{title} - {artist}",
                "track_title": "{track:02d} - {title}",
                "title": "{title}",
            }
            template = file_template.strip() if mode == "custom" else presets.get(mode, "{artist} - {title}")
            if not template:
                raise FileManagerError("请选择命名规则")

            def build(source: Path, values: Dict[str, Any]) -> Path:
                name = self._render(template, values, values["title"])
                ext = source.suffix
                if not name.lower().endswith(ext):
                    name += ext
                return source.with_name(self._safe_name(name, values["title"] + ext))

            result = self._plan_audio(build)
            plan = {"type": "rename", "created": time.time(), "mode": mode, "data": result}
            plan_id = uuid.uuid4().hex
            self._save_json(self._plans_dir() / f"{plan_id}.json", plan)
            result["plan_id"] = plan_id
            return result

    def apply_plan(self, plan_id: str) -> Dict[str, Any]:
        with self._lock:
            self._ensure_original()
            plan_path = self._plans_dir() / f"{self._safe_name(plan_id, 'plan')}.json"
            plan = self._load_json(plan_path, None)
            if not plan:
                raise FileManagerError("整理预览已过期，请重新预览")
            if time.time() - float(plan.get("created") or 0) > PLAN_TTL_SECONDS:
                plan_path.unlink(missing_ok=True)
                raise FileManagerError("整理预览已过期，请重新预览")
            items = plan.get("data", {}).get("items") or []
            inverses = []
            applied = 0
            skipped = 0
            errors = []
            for item in items:
                if item.get("status") != "planned":
                    skipped += 1
                    continue
                source = self._resolve(item["source"], must_exist=True)
                target = self._resolve(item["target"])
                if target.exists():
                    target = self._unique_target(target)
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(target))
                    inverses.append({"op": "move", "from": self._rel(target), "to": self._rel(source)})
                    self._sync_move(source, target)
                    for sidecar in item.get("sidecars") or []:
                        old = self._resolve(sidecar["source"], must_exist=True)
                        new = self._resolve(sidecar["target"])
                        if new.exists():
                            continue
                        shutil.move(str(old), str(new))
                        inverses.append({"op": "move", "from": self._rel(new), "to": self._rel(old)})
                    applied += 1
                except Exception as exc:
                    errors.append({"source": item["source"], "target": item["target"], "error": str(exc)[:200]})
            if inverses:
                self._record(
                    "一键整理" if plan.get("type") == "organize" else "批量命名",
                    f"处理 {applied} 个文件",
                    inverses,
                )
            plan_path.unlink(missing_ok=True)
            return {"applied": applied, "skipped": skipped, "errors": errors, "plan_id": plan_id}

    # ------------------------------------------------------------------
    # 撤销 / 原始还原
    # ------------------------------------------------------------------
    def undo_last(self) -> Dict[str, Any]:
        with self._lock:
            data = self._history()
            items = data.get("items") or []
            for item in reversed(items):
                if item.get("undoable") and not item.get("undone"):
                    errors = self._apply_inverses(list(reversed(item.get("inverses") or [])))
                    item["undone"] = not errors
                    item["undo_errors"] = errors
                    self._save_json(self._history_path(), data)
                    return {"undone": bool(not errors), "action": item.get("action"), "errors": errors}
            raise FileManagerError("没有可撤销的操作")

    def undo_to(self, operation_id: str) -> Dict[str, Any]:
        with self._lock:
            data = self._history()
            items = data.get("items") or []
            target_index = next((index for index, item in enumerate(items) if item.get("id") == operation_id), None)
            if target_index is None:
                raise FileManagerError("操作记录不存在")
            errors = []
            undone = 0
            for item in reversed(items[target_index:]):
                if not item.get("undoable") or item.get("undone"):
                    continue
                item_errors = self._apply_inverses(list(reversed(item.get("inverses") or [])))
                if item_errors:
                    item["undo_errors"] = item_errors
                    errors.extend(item_errors)
                else:
                    item["undone"] = True
                    undone += 1
            self._save_json(self._history_path(), data)
            return {"undone": undone, "errors": errors}

    def restore_original(self) -> Dict[str, Any]:
        with self._lock:
            original = self._load_json(self._original_path(), None)
            if not original:
                raise FileManagerError("还没有原始快照；当前目录就是原始状态")
            data = self._history()
            errors = []
            for item in reversed(data.get("items") or []):
                if item.get("undoable") and not item.get("undone"):
                    item_errors = self._apply_inverses(list(reversed(item.get("inverses") or [])))
                    if item_errors:
                        errors.extend(item_errors)
                    else:
                        item["undone"] = True
            self._save_json(self._history_path(), data)

            root = self._root()
            entries = original.get("entries") or []
            current_files = []
            for current, dirs, names in os.walk(str(root)):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for name in names:
                    if name.startswith("."):
                        continue
                    path = Path(current) / name
                    if path.is_symlink() or not path.is_file():
                        continue
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    current_files.append((path, stat.st_size, self._hash(path, stat.st_size)))
            by_hash: Dict[Tuple[int, str], List[Path]] = {}
            for path, size, digest in current_files:
                by_hash.setdefault((size, digest), []).append(path)
            used: set = set()
            restored = 0
            missing = []
            for entry in entries:
                rel = str(entry.get("path") or "")
                target = self._resolve(rel)
                size = int(entry.get("size") or 0)
                digest = str(entry.get("hash") or "")
                if target.is_file():
                    try:
                        if target.stat().st_size == size and self._hash(target, size) == digest:
                            used.add(target)
                            restored += 1
                            continue
                    except OSError:
                        pass
                candidates = [path for path in by_hash.get((size, digest), []) if path not in used]
                chosen = candidates[0] if candidates else None
                if chosen is None:
                    text = entry.get("text")
                    if text and target.suffix.lower() in TEXT_EXTS:
                        try:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            if target.exists():
                                self._trash(target)
                            target.write_bytes(base64.b64decode(text))
                            restored += 1
                            continue
                        except Exception:
                            pass
                    missing.append(rel)
                    continue
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        self._trash(target)
                    shutil.move(str(chosen), str(target))
                    used.add(chosen)
                    used.add(target)
                    self._sync_move(chosen, target)
                    restored += 1
                except Exception as exc:
                    errors.append(f"{rel}: {exc}")
                    missing.append(rel)

            original_paths = {str(entry.get("path") or "") for entry in entries}
            extras = []
            for path, _size, _digest in current_files:
                if path in used:
                    continue
                rel = self._rel(path)
                if rel in original_paths:
                    continue
                try:
                    self._remove_db_under(path)
                    self._trash(path)
                    extras.append(rel)
                except Exception as exc:
                    errors.append(f"{rel}: {exc}")
            self._record(
                "恢复原始目录",
                f"恢复 {restored} 个文件" + (f"，{len(missing)} 个缺失" if missing else ""),
                [],
                undoable=False,
            )
            return {"restored": restored, "missing": missing, "extras": extras, "errors": errors}

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _trash(self, source: Path) -> Path:
        if not source.exists():
            raise FileManagerError("要删除的文件不存在")
        rel = Path(self._rel(source))
        target = self.paths.trash_dir / f"filemanager-{_stamp()}" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target = self._unique_target(target)
        shutil.move(str(source), str(target))
        return target

    def _sync_move(self, old: Path, new: Path) -> None:
        old = old.resolve()
        new = new.resolve()
        if new.is_dir():
            for track in db.all_tracks(self.paths):
                try:
                    path = Path(track["path"])
                    path.relative_to(old)
                except (ValueError, OSError):
                    continue
                db.update_track_path(int(track["id"]), str(new / path.relative_to(old)), self.paths)
        else:
            track = db.get_track_by_path(str(old), self.paths)
            if track:
                db.update_track_path(int(track["id"]), str(new), self.paths)
            self._scan_audio_under(new)

    def _scan_audio_under(self, path: Path) -> None:
        try:
            if path.is_file():
                if path.suffix.lower() in AUDIO_EXTS:
                    scanner.scan_single(path, self.config, self.paths)
                return
            for current, dirs, names in os.walk(str(path)):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for name in names:
                    child = Path(current) / name
                    if child.suffix.lower() in AUDIO_EXTS:
                        try:
                            scanner.scan_single(child, self.config, self.paths)
                        except Exception:
                            pass
        except Exception:
            pass

    def _remove_db_under(self, path: Path) -> None:
        if path.is_dir():
            try:
                db.delete_by_path(str(path), self.paths)
            except Exception:
                pass
            for track in db.all_tracks(self.paths):
                try:
                    Path(track["path"]).relative_to(path)
                except (ValueError, OSError):
                    continue
                db.delete_by_path(str(track["path"]), self.paths)
        else:
            db.delete_by_path(str(path), self.paths)

    def _apply_inverses(self, inverses: Sequence[Dict[str, Any]]) -> List[str]:
        errors = []
        for inv in inverses:
            try:
                op = inv.get("op")
                if op == "move":
                    source = self._resolve(inv.get("from") or "", must_exist=True)
                    target = self._resolve(inv.get("to") or "")
                    if target.exists():
                        target = self._unique_target(target)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(target))
                    self._sync_move(source, target)
                elif op == "restore":
                    trash = Path(str(inv.get("trash") or ""))
                    try:
                        trash.resolve().relative_to(self.paths.trash_dir.resolve())
                    except ValueError:
                        raise FileManagerError("回收站路径不合法")
                    target = self._resolve(inv.get("to") or "")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        target = self._unique_target(target)
                    shutil.move(str(trash), str(target))
                    self._scan_audio_under(target)
                elif op == "trash":
                    path = self._resolve(inv.get("path") or "", must_exist=True)
                    self._remove_db_under(path)
                    self._trash(path)
                elif op == "rmdir":
                    path = self._resolve(inv.get("path") or "", must_exist=True)
                    path.rmdir()
                elif op == "write":
                    path = self._resolve(inv.get("path") or "")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(str(inv.get("text") or ""), encoding="utf-8")
                else:
                    raise FileManagerError("未知的撤销操作")
            except Exception as exc:
                errors.append(str(exc)[:200])
        return errors
