"""外置 .lrc 检查、嵌入与清理测试。"""

from __future__ import annotations

from pathlib import Path

from music_organizer import audio, db
from music_organizer.lrcfiles import clean_lrc_files, embed_sidecar_lyrics, scan_lrc_files

LYRICS = "\n".join(f"[00:{i:02d}.00]外置第 {i} 行" for i in range(8))


def _track(env, title="晴天"):
    return next(t for t in db.all_tracks(env["paths"]) if t["title"] == title)


def test_scan_and_embed_sidecar(env):
    track = _track(env)
    lrc = Path(track["path"]).with_suffix(".lrc")
    lrc.write_text(LYRICS, encoding="utf-8")
    orphan = env["music"] / "orphan.lrc"
    orphan.write_text("[00:01.00]没有音频", encoding="utf-8")

    data = scan_lrc_files(env["config"], env["paths"])
    assert data["counts"]["total"] == 2
    assert data["counts"]["sidecar_only"] == 1
    assert data["counts"]["orphan"] == 1
    assert data["counts"]["redundant"] == 0

    result = embed_sidecar_lyrics(env["config"], data["entries"], env["paths"])
    assert result["embedded"] == 1
    assert audio.read_embedded_lyrics(track["path"]).strip()
    assert "外置第 7 行" in audio.read_embedded_lyrics(track["path"])

    data2 = scan_lrc_files(env["config"], env["paths"])
    assert data2["counts"]["redundant"] == 1
    assert data2["counts"]["orphan"] == 1


def test_clean_redundant_and_orphan(env):
    track = _track(env, "七里香")
    lrc = Path(track["path"]).with_suffix(".lrc")
    lrc.write_text(LYRICS, encoding="utf-8")
    # 先嵌入，使其变成冗余
    data = scan_lrc_files(env["config"], env["paths"])
    embed_sidecar_lyrics(env["config"], data["entries"], env["paths"])
    orphan = env["music"] / "orphan.lrc"
    orphan.write_text("[00:01.00]没有音频", encoding="utf-8")

    data = scan_lrc_files(env["config"], env["paths"])
    assert data["counts"]["redundant"] == 1
    assert data["counts"]["orphan"] == 1

    result = clean_lrc_files(env["config"], data["entries"], ["redundant", "orphan"], env["paths"])
    assert len(result["moved"]) == 2
    assert not lrc.exists()
    assert not orphan.exists()
    assert Path(result["trash_dir"]).is_dir()
    # 音频文件没有被移动
    assert Path(track["path"]).is_file()


def test_clean_dry_run(env):
    track = _track(env, "晴天")
    lrc = Path(track["path"]).with_suffix(".lrc")
    lrc.write_text(LYRICS, encoding="utf-8")
    data = scan_lrc_files(env["config"], env["paths"])
    result = clean_lrc_files(env["config"], data["entries"], ["sidecar_only"], env["paths"], dry_run=True)
    assert len(result["moved"]) == 1
    assert lrc.exists()
    assert result["moved"][0]["dry_run"] is True


def test_api_lrc_scan_and_clean(env):
    client = env["client"]
    track = _track(env, "晴天")
    Path(track["path"]).with_suffix(".lrc").write_text(LYRICS, encoding="utf-8")
    data = client.get("/api/lrc/scan").get_json()
    assert data["counts"]["sidecar_only"] == 1
    cleaned = client.post("/api/lrc/clean", json={"statuses": ["sidecar_only"], "dry_run": True}).get_json()
    assert len(cleaned["moved"]) == 1
    assert Path(track["path"]).with_suffix(".lrc").exists()
