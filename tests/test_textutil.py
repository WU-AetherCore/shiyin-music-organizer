"""文本工具与 LRC 解析测试。"""

from __future__ import annotations

from music_organizer.textutil import (
    clean_lyrics_text,
    clean_placeholder,
    count_timestamps,
    detect_versions,
    guess_tag_issues,
    normalize_key,
    parse_filename,
    parse_lrc,
    split_artists,
    strip_track_prefix,
    to_simplified,
)


def test_strip_track_prefix():
    assert strip_track_prefix("01 - 晴天") == "晴天"
    assert strip_track_prefix("[02] 夜曲") == "夜曲"
    assert strip_track_prefix("3.演员") == "演员"
    assert strip_track_prefix("1-周杰伦") == "周杰伦"


def test_parse_filename_title_artist():
    parsed = parse_filename("晴天 - 周杰伦.flac", "1-周杰伦")
    assert parsed["title"] == "晴天"
    assert parsed["artist"] == "周杰伦"
    assert parsed["folder_artist"] == "周杰伦"
    assert ("晴天", "周杰伦") in parsed["variants"]

    # 没有文件夹提示时，同时提供“歌手 - 歌名”的反向解释
    swap = parse_filename("周杰伦 - 晴天.flac", "")
    assert ("周杰伦", "晴天") in swap["variants"]
    assert ("晴天", "周杰伦") in swap["variants"]


def test_parse_filename_generic_folder():
    parsed = parse_filename("STAY - The Kid LAROI&Justin Bieber.flac", "其他")
    assert parsed["folder_artist"] == ""
    assert parsed["title"] == "STAY"
    assert parsed["artist"] == "The Kid LAROI&Justin Bieber"


def test_parse_filename_with_ost_brackets():
    parsed = parse_filename("IRIS OUT-《电锯人：蕾塞编》剧场版主题曲 - 米津玄師.mp3", "其他")
    assert parsed["title"].startswith("IRIS OUT")
    assert parsed["artist"] == "米津玄師"


def test_lrc_parse_with_translation():
    text = """[ti:晴天]
[ar:周杰伦]
[00:12.34]故事的小黄花
[00:12.34]The little yellow flower
[00:17.00]从出生那年就飘着
[00:20.5]飘着
"""
    parsed = parse_lrc(text)
    assert parsed["synced"] is True
    assert parsed["meta"]["ti"] == "晴天"
    assert len(parsed["lines"]) == 3
    assert parsed["lines"][0]["text"] == "故事的小黄花"
    assert parsed["lines"][0]["translation"] == "The little yellow flower"
    assert abs(parsed["lines"][1]["time"] - 17.0) < 0.01
    assert count_timestamps(text) == 4


def test_plain_text_lyrics():
    parsed = parse_lrc("第一行\n第二行\n第三行")
    assert parsed["synced"] is False
    assert parsed["plain"] is True
    assert parsed["lines"] == []
    assert "第二行" in parsed["plain_text"]


def test_clean_lyrics_html_entities():
    cleaned = clean_lyrics_text("[00:01.00]A &amp; B<br>下一行")
    assert "A & B" in cleaned
    assert "\n" in cleaned


def test_normalize_and_versions():
    assert normalize_key("晴天 (Live)") == normalize_key("晴天（live）")
    assert "live" in detect_versions("晴天 Live 现场版")
    assert "instrumental" in detect_versions("伴奏版")


def test_artists_split():
    assert split_artists("周杰伦 & 费玉清") == ["周杰伦", "费玉清"]
    assert split_artists("G.E.M.邓紫棋、周杰伦") == ["G.E.M.邓紫棋", "周杰伦"]


def test_placeholder():
    assert clean_placeholder("kuwo") == ""
    assert clean_placeholder("周杰伦") == "周杰伦"
    issues = guess_tag_issues({"title": "无名", "artist": "", "album": "kuwo", "year": "abc"})
    codes = {item["code"] for item in issues}
    assert "no_artist" in codes
    assert "placeholder_album" in codes
    assert "bad_year" in codes


def test_simplified_conversion_without_dependency():
    # 未安装 zhconv 时应原样返回，不应抛异常
    assert to_simplified("晴天") == "晴天"
