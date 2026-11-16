"""查询构建：把标签、文件名、文件夹名组合成多个 (标题, 歌手) 假设。

``providers`` 导入本模块会形成循环，因此打分逻辑放在 :mod:`scoring`。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .providers.base import TrackQuery
from .textutil import (
    clean_placeholder,
    clean_spaces,
    collapse_cjk_spaces,
    detect_versions,
    is_placeholder,
    normalize_key,
    parse_filename,
)

# 兼容旧调用方
from .scoring import (  # noqa: E402,F401
    choose_best_cover,
    choose_best_lyrics,
    confidence,
    score_cover,
    score_lyrics,
)

_TITLE_JUNK_RE = re.compile(r"(?:https?://|www\.)\S+|\.(?:flac|mp3|m4a|wav)\b", re.IGNORECASE)


def build_query(meta: Dict[str, Any]) -> TrackQuery:
    """从扫描结果构建检索对象。"""
    path = str(meta.get("path") or "")
    filename = str(meta.get("filename") or Path(path).name)
    folder = Path(path).parent.name if path else ""

    tag_title = clean_spaces(meta.get("title"))
    tag_artist = clean_spaces(meta.get("artist"))
    if is_placeholder(tag_title):
        tag_title = ""
    if is_placeholder(tag_artist):
        tag_artist = ""
    album = clean_placeholder(meta.get("album"))
    duration = float(meta.get("duration") or 0)

    parsed = parse_filename(filename, folder)
    pairs: List[Tuple[str, str]] = []
    seen: set = set()

    def add(title: str, artist: str) -> None:
        title = collapse_cjk_spaces(title)
        artist = collapse_cjk_spaces(artist)
        if not title or len(title) > 120:
            return
        key = (normalize_key(title), normalize_key(artist))
        if key in seen:
            return
        seen.add(key)
        pairs.append((title, artist))

    if tag_title:
        add(tag_title, tag_artist)
        add(tag_title, parsed.get("folder_artist") or "")
        if tag_artist and tag_artist != tag_title:
            add(tag_artist, tag_title)
    for title, artist in parsed.get("variants") or []:
        add(title, artist)
    if not tag_title:
        add(parsed.get("title") or "", parsed.get("artist") or "")

    # 文件名里的“标题”可能包含歌手后缀，补充一个去掉后缀的假设
    for title, artist in list(pairs):
        if artist and title.endswith(" " + artist):
            add(title[: -len(artist) - 1], artist)

    if not pairs:
        add(parsed.get("clean") or Path(filename).stem, "")

    primary_title = tag_title or (pairs[0][0] if pairs else "")
    primary_artist = tag_artist or (pairs[0][1] if pairs else "")
    context = " ".join(filter(None, [tag_title, filename, album, folder]))
    versions = detect_versions(context)
    # 选秀/现场专辑里的音频通常是 Live 版，避免把 (Live) 当成错误版本扣 45 分
    if re.search(r"中国好声音|中国新歌声|我是歌手|歌手第|歌手·|live|现场|演唱会|音乐节", context, re.I):
        versions.add("live")
    return TrackQuery(
        path=path,
        title=primary_title,
        artist=primary_artist,
        album=album,
        duration=duration,
        pairs=pairs[:8],
        versions=versions,
        filename=filename,
    )


def sanitize_query_text(text: str) -> str:
    return _TITLE_JUNK_RE.sub(" ", text or "").strip()
