"""音频标签读写测试（使用真实生成的 FLAC / MP3 文件）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from music_organizer import audio

from conftest import make_audio, make_cover

LYRICS = "[00:01.00]第一句\n[00:03.50]第二句\n[00:06.00]第三句\n"


@pytest.fixture()
def flac(tmp_path):
    return make_audio(tmp_path / "晴天 - 周杰伦.flac", "flac", "晴天", "周杰伦", "叶惠美", 2, 440)


def test_read_meta_without_media(flac):
    meta = audio.read_meta(flac)
    assert meta["title"] == "晴天"
    assert meta["artist"] == "周杰伦"
    assert meta["album"] == "叶惠美"
    assert meta["audio_ok"] is True
    assert meta["duration"] > 0
    assert meta["has_lyrics"] is False
    assert meta["has_cover"] is False
    assert meta["tag_issues"] == []


def test_embed_lyrics_and_cover(flac):
    cover = make_cover()
    audio.update_media(flac, lyrics=LYRICS, cover=(cover, "image/jpeg"))

    meta = audio.read_meta(flac)
    assert meta["has_lyrics"] is True
    assert "第一句" in meta["lyrics_text"]
    assert meta["lyrics_synced"] is True
    assert meta["has_cover"] is True
    assert meta["cover_source"] == "embed"
    assert meta["cover_width"] == 600

    read_cover = audio.read_cover(flac)
    assert read_cover is not None
    assert read_cover.mime == "image/jpeg"
    assert len(read_cover.data) > 1000

    text, source = audio.read_lyrics(flac)
    assert source == "embed"
    assert "第三句" in text


def test_mp3_embed_and_read(tmp_path):
    path = make_audio(tmp_path / "TA.mp3", "mp3", "TA", "才昂仁增", "", 2, 330)
    cover = make_cover((40, 80, 160))
    audio.update_media(path, lyrics=LYRICS, cover=(cover, "image/jpeg"), tags={"album": "测试专辑"})
    meta = audio.read_meta(path)
    assert meta["has_lyrics"]
    assert meta["has_cover"]
    assert meta["album"] == "测试专辑"
    assert "第一句" in meta["lyrics_text"]


def test_sidecar_lrc(tmp_path):
    path = make_audio(tmp_path / "sidecar.flac", "flac", "Side", "Car", "", 2)
    path.with_suffix(".lrc").write_text(LYRICS, encoding="utf-8")
    meta = audio.read_meta(path)
    assert meta["has_lyrics"] is True
    assert meta["lyrics_source"] == "lrc"


def test_prepare_cover_rejects_small_and_resizes():
    from PIL import Image
    import io

    small = io.BytesIO()
    Image.new("RGB", (100, 100), (10, 10, 10)).save(small, format="JPEG")
    with pytest.raises(audio.AudioError):
        audio.prepare_cover(small.getvalue(), min_size=300)

    big = io.BytesIO()
    Image.new("RGB", (2200, 1600), (80, 40, 120)).save(big, format="JPEG")
    prepared = audio.prepare_cover(big.getvalue(), min_size=300, max_size=1280)
    assert prepared["width"] <= 1280
    assert prepared["height"] <= 1280
    assert prepared["mime"] == "image/jpeg"


def test_backup_and_restore(flac, tmp_path):
    audio.update_media(flac, lyrics="旧歌词第一行\n旧歌词第二行\n")
    backup = audio.backup_track(flac, tmp_path / "backups", reason="test")
    assert backup and Path(backup).exists()

    audio.update_media(flac, lyrics="新歌词\n", cover=(make_cover((1, 2, 3)), "image/jpeg"))
    assert "新歌词" in audio.read_lyrics(flac)[0]
    assert audio.read_cover(flac) is not None

    audio.restore_backup(backup)
    text, _ = audio.read_lyrics(flac)
    assert "旧歌词" in text
    assert audio.read_cover(flac) is None


def test_clear_cover(flac):
    audio.update_media(flac, cover=(make_cover(), "image/jpeg"))
    assert audio.read_cover(flac) is not None
    audio.clear_cover(flac)
    assert audio.read_cover(flac) is None


def test_unsupported_write(tmp_path):
    path = tmp_path / "fake.aac"
    path.write_bytes(b"not really aac")
    with pytest.raises(audio.AudioError):
        audio.update_media(path, lyrics="x")


def test_update_media_takes_over_unwritable_file(flac, monkeypatch):
    import os

    real_access = os.access

    def fake_access(path, mode):
        if Path(path) == flac and mode == os.W_OK:
            return False
        return real_access(path, mode)

    monkeypatch.setattr(audio.os, "access", fake_access)
    audio.update_media(flac, lyrics="接管权限后的歌词\n")
    assert "接管权限后的歌词" in audio.read_lyrics(flac)[0]


def test_scan_writes_lrc_sidecar(env):
    from music_organizer import scanner

    path = next(env["music"].glob("*.flac"))
    audio.update_media(path, lyrics="[00:01.00]外置歌词测试\n")
    env["config"].set("repair.write_lrc", True)
    scanner.scan_single(path, env["config"], env["paths"])
    sidecar = path.with_suffix(".lrc")
    assert sidecar.is_file()
    assert "外置歌词测试" in sidecar.read_text(encoding="utf-8")
