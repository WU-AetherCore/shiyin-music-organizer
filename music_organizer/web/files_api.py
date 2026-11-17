"""文件管理 Web API。"""

from __future__ import annotations

from typing import Any, Dict

from flask import Blueprint, current_app, jsonify, request, send_file

from .. import db, jobs
from ..filemanager import FileManager, FileManagerError
from .api import ApiError

bp = Blueprint("files_api", __name__)


def _manager() -> FileManager:
    return FileManager(current_app.config["MORG_CONFIG"], current_app.config["MORG_PATHS"])


def _json_body() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _failure(exc: Exception):
    if isinstance(exc, FileManagerError):
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": False, "error": str(exc)[:300]}), 500


@bp.get("/api/files")
def list_files():
    try:
        return jsonify(
            {
                "ok": True,
                **_manager().list_dir(
                    request.args.get("path", ""),
                    request.args.get("q", ""),
                    request.args.get("sort", "name"),
                ),
            }
        )
    except Exception as exc:
        return _failure(exc)


@bp.get("/api/files/dirs")
def list_dirs():
    try:
        return jsonify({"ok": True, "dirs": _manager().list_dirs()})
    except Exception as exc:
        return _failure(exc)


@bp.post("/api/files/mkdir")
def make_dir():
    try:
        data = _json_body()
        return jsonify({"ok": True, **_manager().mkdir(str(data.get("path") or ""), str(data.get("name") or ""))})
    except Exception as exc:
        return _failure(exc)


@bp.post("/api/files/rename")
def rename():
    try:
        data = _json_body()
        return jsonify({"ok": True, **_manager().rename(str(data.get("path") or ""), str(data.get("name") or ""))})
    except Exception as exc:
        return _failure(exc)


@bp.post("/api/files/delete")
def delete():
    try:
        data = _json_body()
        paths = data.get("paths")
        if not isinstance(paths, list) or not paths:
            raise FileManagerError("请选择要删除的文件")
        return jsonify({"ok": True, **_manager().delete([str(item) for item in paths])})
    except Exception as exc:
        return _failure(exc)


@bp.post("/api/files/move")
def move():
    try:
        data = _json_body()
        paths = data.get("paths")
        if not isinstance(paths, list) or not paths:
            raise FileManagerError("请选择要移动的文件")
        return jsonify({"ok": True, **_manager().move([str(item) for item in paths], str(data.get("dest") or ""))})
    except Exception as exc:
        return _failure(exc)


@bp.post("/api/files/copy")
def copy():
    try:
        data = _json_body()
        paths = data.get("paths")
        if not isinstance(paths, list) or not paths:
            raise FileManagerError("请选择要复制的文件")
        return jsonify({"ok": True, **_manager().copy([str(item) for item in paths], str(data.get("dest") or ""))})
    except Exception as exc:
        return _failure(exc)


@bp.post("/api/files/upload")
def upload():
    try:
        files = request.files.getlist("files") or request.files.getlist("file")
        if not files:
            raise FileManagerError("没有收到文件")
        result = _manager().save_uploads(
            request.form.get("path", ""),
            files,
            auto_classify=request.form.get("auto_classify", "1") not in ("0", "false", "off"),
            classify_mode=request.form.get("classify_mode", "artist_album"),
            only_existing=request.form.get("only_existing", "1") not in ("0", "false", "off"),
        )
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return _failure(exc)


@bp.get("/api/files/download")
def download():
    try:
        manager = _manager()
        path = manager._resolve(request.args.get("path", ""), must_exist=True)
        if not path.is_file():
            raise FileManagerError("只能下载文件")
        return send_file(path, as_attachment=True, download_name=path.name, conditional=True)
    except Exception as exc:
        return _failure(exc)


@bp.get("/api/files/text")
def read_text():
    try:
        return jsonify({"ok": True, **_manager().read_text(request.args.get("path", ""))})
    except Exception as exc:
        return _failure(exc)


@bp.put("/api/files/text")
def write_text():
    try:
        data = _json_body()
        return jsonify({"ok": True, **_manager().write_text(str(data.get("path") or ""), str(data.get("text") or ""))})
    except Exception as exc:
        return _failure(exc)


@bp.post("/api/files/export")
def export_audio():
    try:
        data = _json_body()
        paths = data.get("paths")
        if not isinstance(paths, list) or not paths:
            raise FileManagerError("请选择要导出的音频")
        return jsonify(
            {
                "ok": True,
                **_manager().export_files(
                    [str(item) for item in paths],
                    str(data.get("format") or "original"),
                    str(data.get("bitrate") or "320"),
                    str(data.get("dest") or "导出"),
                ),
            }
        )
    except Exception as exc:
        return _failure(exc)


@bp.post("/api/files/organize/preview")
def organize_preview():
    try:
        data = _json_body()
        return jsonify(
            {
                "ok": True,
                **_manager().preview_organize(
                    str(data.get("mode") or "artist_album"),
                    str(data.get("dir_template") or ""),
                    str(data.get("file_template") or ""),
                    str(data.get("keep_artists") or ""),
                ),
            }
        )
    except Exception as exc:
        return _failure(exc)


@bp.post("/api/files/rename/preview")
def rename_preview():
    try:
        data = _json_body()
        return jsonify(
            {
                "ok": True,
                **_manager().preview_rename(
                    str(data.get("mode") or "artist_title"),
                    str(data.get("file_template") or ""),
                ),
            }
        )
    except Exception as exc:
        return _failure(exc)


@bp.post("/api/files/plan/apply")
def apply_plan():
    try:
        data = _json_body()
        plan_id = str(data.get("plan_id") or "")
        if not plan_id:
            raise FileManagerError("缺少整理预览 ID")
        return jsonify({"ok": True, **_manager().apply_plan(plan_id)})
    except Exception as exc:
        return _failure(exc)


@bp.get("/api/files/history")
def history():
    try:
        return jsonify({"ok": True, **_manager().history(int(request.args.get("limit") or 30))})
    except Exception as exc:
        return _failure(exc)


@bp.post("/api/files/undo")
def undo():
    try:
        data = _json_body()
        manager = _manager()
        if data.get("operation_id"):
            return jsonify({"ok": True, **manager.undo_to(str(data["operation_id"]))})
        return jsonify({"ok": True, **manager.undo_last()})
    except Exception as exc:
        return _failure(exc)


@bp.post("/api/files/restore-original")
def restore_original():
    try:
        return jsonify({"ok": True, **_manager().restore_original()})
    except Exception as exc:
        return _failure(exc)


@bp.post("/api/files/repair")
def repair():
    try:
        config = current_app.config["MORG_CONFIG"]
        paths = current_app.config["MORG_PATHS"]
        ids = set(db.ids_for_filter("missing_any", paths=paths))
        ids.update(db.ids_for_filter("bad_lyrics", paths=paths))
        if not ids:
            return jsonify({"ok": True, "job_id": "", "reason": "没有需要修复的内容"})
        limit = int(_json_body().get("limit") or 100)
        job_id = jobs.start_batch(
            config,
            paths,
            current_app.config["MORG_MANAGER"],
            ids=list(ids)[:limit],
            targets=["lyrics", "cover"],
            force=False,
            source="files",
            title=f"文件管理 · 一键修复 {min(len(ids), limit)} 首",
        )
        return jsonify({"ok": True, "job_id": job_id, "count": min(len(ids), limit)})
    except Exception as exc:
        return _failure(exc)
