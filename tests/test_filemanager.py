from pathlib import Path
import io

import pytest
from werkzeug.datastructures import FileStorage

from music_organizer.filemanager import FileManager, FileManagerError
from music_organizer.textutil import parse_lrc


def _manager(env):
    return FileManager(env["config"], env["paths"])


def test_file_list_is_confined_to_music_root(env):
    fm = _manager(env)
    data = fm.list_dir("")
    assert data["path"] == ""
    assert any(item["kind"] == "audio" for item in data["entries"])
    with pytest.raises(FileManagerError):
        fm.list_dir("../")
    with pytest.raises(FileManagerError):
        fm._resolve("/etc")


def test_rename_delete_and_undo(env):
    fm = _manager(env)
    source = next(env["music"].glob("*.flac"))
    rel = source.name
    result = fm.rename(rel, "改名后的歌.flac")
    assert result["to"] == "改名后的歌.flac"
    assert (env["music"] / "改名后的歌.flac").is_file()

    deleted = fm.delete(["改名后的歌.flac"])
    assert deleted["moved"]
    assert not (env["music"] / "改名后的歌.flac").exists()

    undo = fm.undo_last()
    assert undo["undone"] is True
    assert (env["music"] / "改名后的歌.flac").is_file()

    undo2 = fm.undo_last()
    assert undo2["undone"] is True
    assert source.is_file()


def test_mkdir_and_copy(env):
    fm = _manager(env)
    fm.mkdir("", "子目录")
    source = next(env["music"].glob("*.flac"))
    result = fm.copy([source.name], "子目录")
    assert result["items"]
    assert list((env["music"] / "子目录").glob("*.flac"))


def test_organize_preview_and_apply_then_restore(env):
    fm = _manager(env)
    preview = fm.preview_organize("artist_album")
    assert preview["plan_id"]
    assert preview["planned"] >= 1
    applied = fm.apply_plan(preview["plan_id"])
    assert applied["applied"] >= 1
    # artist/album folders now exist
    folders = [p for p in env["music"].iterdir() if p.is_dir()]
    assert folders
    restored = fm.restore_original()
    assert restored["restored"] >= 1
    assert not restored["missing"]
    assert (env["music"] / next(env["music"].glob("*.flac")).name).is_file()
    assert not any(path.is_dir() and not any(path.iterdir()) for path in env["music"].rglob("*"))


def test_text_edit_and_undo(env):
    fm = _manager(env)
    lrc = env["music"] / "test.lrc"
    lrc.write_text("[00:01.00]第一行\n", encoding="utf-8")
    assert "第一行" in fm.read_text("test.lrc")["text"]
    fm.write_text("test.lrc", "[00:02.00]第二行\n")
    assert "第二行" in lrc.read_text(encoding="utf-8")
    fm.undo_last()
    assert "第一行" in lrc.read_text(encoding="utf-8")


def test_upload_auto_classifies_by_tags(env):
    fm = _manager(env)
    source = next(env["music"].glob("*.flac"))
    storage = FileStorage(stream=io.BytesIO(source.read_bytes()), filename="导入测试.flac")
    result = fm.save_uploads("", [storage], auto_classify=True, classify_mode="artist_album")
    assert result["saved"], result
    path = result["saved"][0]["path"]
    assert path == "其他/导入测试.flac", path
    assert (env["music"] / path).is_file()


def test_upload_reuses_existing_artist_folder(env):
    fm = _manager(env)
    (env["music"] / "周杰伦").mkdir()
    source = next(env["music"].glob("*.flac"))
    storage = FileStorage(stream=io.BytesIO(source.read_bytes()), filename="导入测试2.flac")
    result = fm.save_uploads("", [storage], auto_classify=True, classify_mode="artist_album")
    path = result["saved"][0]["path"]
    assert path == "周杰伦/导入测试2.flac", path


def test_organize_keep_artists(env):
    fm = _manager(env)
    preview = fm.preview_organize("artist_album", keep_artists="周杰伦")
    kept = [item for item in preview["items"] if item.get("artist") == "周杰伦"]
    assert kept
    assert all(item["status"] == "skipped" for item in kept)


def test_organize_keep_structure_is_default(env):
    fm = _manager(env)
    preview = fm.preview_organize("keep")
    assert preview["planned"] == 0
    assert preview["skipped"] >= 1


def test_organize_scope_artist_and_base(env):
    fm = _manager(env)
    preview = fm.preview_organize("artist_album", scope="artist", scope_artist="周杰伦", base_dir="其他")
    assert preview["planned"] >= 1
    for item in preview["items"]:
        if item["status"] == "planned":
            assert item["target"].startswith("其他/周杰伦/")


def test_auto_organize_ids(env):
    from music_organizer import db

    fm = _manager(env)
    track = next(t for t in db.all_tracks(env["paths"]) if t.get("artist") and t.get("album"))
    result = fm.auto_organize_ids([int(track["id"])], base_dir="其他")
    assert result["applied"] == 1
    target = result["preview"]["items"][0]["target"]
    assert target.startswith("其他/")
    assert (env["music"] / target).is_file()


def test_export_original_copy(env):
    fm = _manager(env)
    source = next(env["music"].glob("*.flac"))
    result = fm.export_files([source.name], fmt="original", dest_rel="导出")
    assert result["saved"], result
    assert (env["music"] / "导出").is_dir()
    assert (env["music"] / result["saved"][0]["path"]).is_file()


def test_files_api_crud_and_undo(env):
    client = env["client"]
    listing = client.get("/api/files")
    assert listing.status_code == 200
    assert listing.get_json()["ok"] is True

    created = client.post("/api/files/mkdir", json={"path": "", "name": "API Folder"})
    assert created.status_code == 200
    assert (env["music"] / "API Folder").is_dir()

    renamed = client.post("/api/files/rename", json={"path": "API Folder", "name": "API Renamed"})
    assert renamed.status_code == 200
    assert (env["music"] / "API Renamed").is_dir()

    deleted = client.post("/api/files/delete", json={"paths": ["API Renamed"]})
    assert deleted.status_code == 200
    assert not (env["music"] / "API Renamed").exists()

    undone = client.post("/api/files/undo", json={})
    assert undone.status_code == 200
    assert undone.get_json()["undone"] is True
    assert (env["music"] / "API Renamed").is_dir()


def test_files_api_traversal_rejected(env):
    client = env["client"]
    response = client.get("/api/files?path=../")
    assert response.status_code == 400
