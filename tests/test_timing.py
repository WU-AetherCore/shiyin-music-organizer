from music_organizer.textutil import analyze_lyric_timing, clamp_lyric_timing


def _lrc(points):
    return "\n".join(f"[{int(t // 60):02d}:{t % 60:05.2f}]line {i}" for i, t in enumerate(points))


def test_normal_timing_is_ok():
    text = _lrc([2, 15, 40, 80, 120, 160, 200, 240, 260])
    timing = analyze_lyric_timing(text, 270)
    assert timing["status"] == "ok"
    assert timing["needs_fix"] is False
    assert timing["coverage"] > 0.9


def test_truncated_timing_is_broken():
    text = _lrc([1, 5, 9, 14, 20, 26, 33, 41, 50, 58])
    timing = analyze_lyric_timing(text, 271)
    assert timing["status"] == "broken"
    assert timing["needs_fix"] is True
    assert any(item["code"] == "truncated" for item in timing["issues"])


def test_overrun_is_suspect_and_clamped():
    text = _lrc([1, 20, 60, 100, 140, 180, 195, 205])
    timing = analyze_lyric_timing(text, 200)
    assert timing["needs_fix"] is True
    assert any(item["code"] == "overrun" for item in timing["issues"])
    fixed = clamp_lyric_timing(text, 200)
    again = analyze_lyric_timing(fixed, 200)
    assert again["status"] == "ok"
    assert again["last"] <= 200


def test_plain_lyrics_are_not_flagged():
    timing = analyze_lyric_timing("第一行\n第二行\n第三行", 180)
    assert timing["status"] == "plain"
    assert timing["needs_fix"] is False
