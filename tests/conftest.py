"""pytest 公共夹具：临时音乐库 + Flask 测试客户端。"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pytest

from music_organizer import db, jobs
from music_organizer.config import Config, reset_config_cache
from music_organizer.paths import get_paths, set_home
from music_organizer.web.app import create_app

FFMPEG = shutil.which("ffmpeg")


def make_audio(path: Path, fmt: str = "flac", title: str = "", artist: str = "", album: str = "", seconds: int = 2, tone: int = 440) -> Path:
    """用 ffmpeg 合成一段正弦波音频，供标签读写测试使用。"""
    if not FFMPEG:
        pytest.skip("需要 ffmpeg 才能生成测试音频")
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={tone}:duration={seconds}",
    ]
    if fmt == "mp3":
        cmd += ["-c:a", "libmp3lame", "-b:a", "192k"]
    elif fmt == "flac":
        cmd += ["-c:a", "flac"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True, capture_output=True)
    if title or artist or album:
        from music_organizer import audio

        audio.update_media(path, tags={"title": title, "artist": artist, "album": album})
    return path


def make_cover(color: tuple = (124, 92, 255), size: int = 600) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (size, size), color)
    draw = ImageDraw.Draw(img)
    draw.ellipse([size * 0.2, size * 0.2, size * 0.8, size * 0.8], outline=(255, 255, 255), width=8)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    music = tmp_path / "music"
    music.mkdir()
    set_home(home)
    reset_config_cache()
    jobs.reset_manager()

    make_audio(music / "晴天 - 周杰伦.flac", "flac", "晴天", "周杰伦", "叶惠美", 2, 440)
    make_audio(music / "七里香 - 周杰伦.flac", "flac", "七里香", "周杰伦", "七里香", 2, 494)
    make_audio(music / "plain.mp3", "mp3", "", "", "", 2, 330)

    config = Config({"music_dir": str(music)}, paths=get_paths())
    config.paths.ensure()
    config.save()
    db.init_db(config.paths)
    from music_organizer.scanner import scan_library

    scan_library(config, config.paths)

    app = create_app(config, config.paths, enable_automation=False)
    app.config.update(TESTING=True)
    client = app.test_client()

    yield {"app": app, "client": client, "config": config, "paths": config.paths, "music": music}

    jobs.reset_manager()


@pytest.fixture()
def track_id(env, client):
    data = client.get("/api/tracks").get_json()
    return data["items"][0]["id"]
