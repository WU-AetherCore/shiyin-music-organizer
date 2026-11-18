from pathlib import Path

from music_organizer import conversion, db


def test_conversion_candidates_and_replace(env):
    cfg = env["config"]
    cfg.update({
        "conversion": {
            "enabled": True,
            "mode": "rules",
            "target": "mp3",
            "rules": [{"from": "flac", "to": "mp3"}],
            "replace_original": True,
            "keep_backup": True,
        }
    }, save=True)
    tracks = db.all_tracks(env["paths"])
    track = next(t for t in tracks if t["ext"] == ".flac")
    found = conversion.candidates(cfg, env["paths"], ids=[track["id"]])
    assert found and found[0]["conversion_target"] == "mp3"
    result = conversion.convert_file(found[0], cfg, env["paths"])
    assert result["status"] == "fixed", result
    target = Path(result["target"])
    assert target.is_file() and target.suffix == ".mp3"
    assert not Path(track["path"]).exists()
    assert db.get_track_by_path(str(target), env["paths"]) is not None


def test_conversion_preserve_does_nothing(env):
    cfg = env["config"]
    cfg.update({"conversion": {"enabled": True, "mode": "preserve", "rules": []}}, save=True)
    assert conversion.candidates(cfg, env["paths"]) == []


def test_lossless_guard_skips_mp3_to_flac(env):
    from music_organizer import db

    cfg = env["config"]
    cfg.update({
        "conversion": {
            "enabled": True,
            "mode": "all",
            "target": "flac",
            "lossless_only": True,
        }
    }, save=True)
    mp3 = next(t for t in db.all_tracks(env["paths"]) if t["ext"] == ".mp3")
    assert conversion.candidates(cfg, env["paths"], ids=[mp3["id"]]) == []
    cfg.set("conversion.lossless_only", False)
    assert conversion.candidates(cfg, env["paths"], ids=[mp3["id"]])
