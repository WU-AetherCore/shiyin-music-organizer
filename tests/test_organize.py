"""按标签整理文件（重命名 / 移动）测试。"""

from __future__ import annotations

from pathlib import Path

from music_organizer import db
from music_organizer.organize import apply_renames, plan_renames

from conftest import make_audio


def test_plan_and_apply_simple_rename(env):
    track = next(t for t in db.all_tracks(env["paths"]) if t["title"] == "晴天")
    plans = plan_renames([track], env["config"], "{artist} - {title}")
    assert len(plans) == 1
    plan = plans[0]
    assert plan.status == "planned"
    assert plan.target.endswith("周杰伦 - 晴天.flac")

    results = apply_renames(plans, env["config"], env["paths"])
    assert results[0].status == "done"
    assert Path(plan.target).exists()  # noqa: F821
    updated = db.get_track(track["id"], env["paths"])
    assert updated["path"] == plan.target
    assert updated["filename"] == "周杰伦 - 晴天.flac"


def test_plan_tree_template(env):
    track = next(t for t in db.all_tracks(env["paths"]) if t["title"] == "晴天")
    plans = plan_renames([track], env["config"], "{artist}/{album}/{track:02d} - {title}")
    assert plans[0].status == "planned"
    target = Path(plans[0].target)
    assert target.name == "00 - 晴天.flac"
    assert target.parent.name == "叶惠美"
    assert target.parent.parent.name == "周杰伦"


def test_conflict_is_skipped(env):
    tracks = [t for t in db.all_tracks(env["paths"]) if t["title"]]
    track = tracks[0]
    plans = plan_renames([track], env["config"], "同一个名字")
    assert plans[0].status == "planned"
    results = apply_renames(plans, env["config"], env["paths"])
    assert results[0].status == "done"

    again = plan_renames([db.get_track(track["id"], env["paths"])], env["config"], "同一个名字")
    assert again[0].status == "skipped"


def test_path_escape_rejected(env):
    track = db.all_tracks(env["paths"])[0]
    plans = plan_renames([track], env["config"], "../../evil/{title}")
    assert plans[0].status in ("error", "planned")
    # 模板经过分段清洗，不应真的越出音乐目录
    assert ".." not in plans[0].target


def test_sidecar_moves_with_audio(env):
    track = next(t for t in db.all_tracks(env["paths"]) if t["title"] == "晴天")
    lrc = Path(track["path"]).with_suffix(".lrc")  # noqa: F821
    lrc.write_text("[00:01.00]歌词", encoding="utf-8")
    plans = plan_renames([track], env["config"], "{title} - 新名字")
    assert plans[0].sidecars
    apply_renames(plans, env["config"], env["paths"])
    assert Path(plans[0].target).with_suffix(".lrc").exists()
