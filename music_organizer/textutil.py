"""文本工具：标签清洗、文件名解析、LRC 解析与格式化。"""

from __future__ import annotations

import html
import math
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 基础清洗
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(
    r"[\s\-_·•―—–~`!@#$%^&*+=|\\/:;\"'“”‘’<>《》〈〉【】\[\]()（）{}"
    r".,，。、!?！？：；…]+"
)

_PLACEHOLDERS = {
    "",
    "unknown",
    "unknown artist",
    "unknown album",
    "unknown title",
    "未知",
    "未知歌手",
    "未知艺术家",
    "未知专辑",
    "未知歌曲",
    "无",
    "none",
    "null",
    "n/a",
    "na",
    "kuwo",
    "kugou",
    "qq",
    "qqmusic",
    "netease",
    "cloudmusic",
    "网易云音乐",
    "酷我",
    "酷狗",
    "qq音乐",
    "本地音乐",
    "佚名",
    "群星",
}

# 抓轨/下载工具留下的占位标题，例如 track 04、Audio 3、音轨 12
_GENERIC_TITLE_RE = re.compile(
    r"^(?:track|audio|song|untitled|音轨|曲目|歌曲)\s*[-_#]?\s*\d{1,3}$",
    re.I,
)

_TRACK_PREFIX_RE = re.compile(r"^\s*(?:[\[【(（]?\s*)?(\d{1,3})\s*[\]】)）]?\s*[-._、．]\s+?|^\s*(\d{1,3})\s+[-._]\s*")
_TRACK_PREFIX_LOOSE_RE = re.compile(r"^\s*(\d{1,3})\s*[-._、．]\s*")

_VERSION_WORDS = {
    "live": ("live", "现场", "演唱会", "concert"),
    "remix": ("remix", "混音", "dj版", "dj version"),
    "acoustic": ("acoustic", "不插电", "钢琴版", "piano version"),
    "instrumental": ("instrumental", "off vocal", "karaoke", "伴奏", "纯音乐", "无人声"),
    "demo": ("demo", "小样"),
    "cover": ("cover", "翻唱", "翻自"),
    "edit": ("radio edit", "short edit", "剪辑版", "片段"),
    "speed": ("sped up", "slowed", "加速版", "降速版"),
    "remaster": ("remaster", "重制版", "remastered"),
}

_FEAT_RE = re.compile(
    r"[\(（\[【][^)）\]】]*(?:feat|ft|featuring|with)[^)）\]】]*[\)）\]】]|\s+(?:feat|ft|featuring)\.?\s+.*$",
    re.IGNORECASE,
)

# 用于解析 "[00:12.34]" / "[1:02:03]"
_LRC_TIME_RE = re.compile(r"\[(\d{1,3}):([0-5]?\d)(?:[.:](\d{1,3}))?\]")
_LRC_META_RE = re.compile(r"^\[(ti|ar|al|by|offset|length|re|ve|au|tool):(.*)\]$", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def clean_spaces(text: Optional[str]) -> str:
    """折叠空白、去掉首尾空格，并修掉常见的零宽字符。"""
    if text is None:
        return ""
    text = str(text).replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", text).strip()


_CJK_SPACE_RE = re.compile(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])")


def collapse_cjk_spaces(text: Optional[str]) -> str:
    """去掉汉字之间被错误插入的空格，例如“娘 子” -> “娘子”。"""
    return _CJK_SPACE_RE.sub("", clean_spaces(text))


def normalize_key(text: Optional[str]) -> str:
    """用于比较的归一化：繁转简、去标点、去空白、统一大小写、全角转半角。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    if has_cjk(text):
        text = _simplify_for_key(text)
    text = text.lower()
    text = text.replace("&", "and").replace("＆", "and")
    text = _PUNCT_RE.sub("", text)
    return text.strip()


def is_placeholder(text: Optional[str]) -> bool:
    value = clean_spaces(text).lower()
    return value in _PLACEHOLDERS or bool(_GENERIC_TITLE_RE.match(value))


def clean_placeholder(text: Optional[str]) -> str:
    value = clean_spaces(text)
    return "" if is_placeholder(value) else value


def strip_feat(text: str) -> str:
    return clean_spaces(_FEAT_RE.sub(" ", text or ""))


def strip_track_prefix(text: str) -> str:
    """去掉文件名开头的音轨号：``01 - `` / ``01.`` / ``[01] ``。"""
    value = clean_spaces(text)
    value = re.sub(r"^\s*[\[【(（]\s*\d{1,3}\s*[\]】)）]\s*", "", value)
    for _ in range(2):
        new = _TRACK_PREFIX_LOOSE_RE.sub("", value, count=1)
        if new == value:
            break
        value = new
    return value.strip(" -_.")


def split_artists(text: str) -> List[str]:
    parts = re.split(r"\s*(?:&|,|，|、|/|；|;|\+|\bfeat\.?\b|\bft\.?\b)\s*", text or "", flags=re.I)
    out: List[str] = []
    for part in parts:
        part = clean_spaces(part)
        if part and part.lower() not in ("feat", "ft"):
            out.append(part)
    return out


def detect_versions(text: str) -> set:
    """识别 live / remix / 伴奏 等版本标记。"""
    low = unicodedata.normalize("NFKC", text or "").lower()
    found = set()
    for key, words in _VERSION_WORDS.items():
        for word in words:
            word_low = word.lower()
            if word_low.isascii():
                # 英文用单词边界，避免 "Deliver" 命中 "live"
                if re.search(r"(?<![a-z0-9])" + re.escape(word_low) + r"(?![a-z0-9])", low):
                    found.add(key)
                    break
            elif word_low in low:
                found.add(key)
                break
    return found


def core_title(text: str) -> str:
    """去掉 feat / live / remix 等附属信息，得到用于匹配的核心标题。"""
    value = strip_feat(text or "")
    value = re.sub(r"[\(（\[【][^)）\]】]{0,40}[\)）\]】]", " ", value)
    value = re.sub(r"[-_—–]\s*(?:live|remix|acoustic|instrumental|伴奏|纯音乐|现场|翻唱).*$", " ", value, flags=re.I)
    return clean_spaces(value)


def similarity(a: str, b: str) -> float:
    """0~1 的相似度（基于归一化后的字符串）。"""
    na, nb = normalize_key(a), normalize_key(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.9
    return SequenceMatcher(None, na, nb).ratio()


_GENERIC_FOLDERS = {
    "其他",
    "其它",
    "音乐",
    "music",
    "songs",
    "song",
    "audio",
    "downloads",
    "下载",
    "未分类",
    "新建文件夹",
    "ost",
    "原声",
    "华语",
    "欧美",
    "日韩",
    "粤语",
    "国语",
    "纯音乐",
    "轻音乐",
    "合集",
    "专辑",
    "album",
    "albums",
    "music library",
    "我的音乐",
    "本地音乐",
}


def looks_like_artist(text: str) -> bool:
    value = clean_spaces(text)
    if not value or len(value) > 80:
        return False
    if value.lower() in _PLACEHOLDERS:
        return False
    if value.lower() in _GENERIC_FOLDERS or value in _GENERIC_FOLDERS:
        return False
    if len(value) == 1 and not has_cjk(value):
        return False
    return not bool(re.search(r"[《》【】]|主题曲|插曲|片尾曲|OST", value, re.I))


# ---------------------------------------------------------------------------
# 文件名 / 标签 -> 查询变体
# ---------------------------------------------------------------------------

def parse_filename(filename: str, folder_name: str = "") -> Dict[str, Any]:
    """从文件名猜测标题与歌手。

    用户曲库常见命名是 ``歌名 - 歌手.flac``（与欧美的 ``歌手 - 歌名`` 相反），
    因此这里同时返回两种解释，交给匹配器按得分选择。

    返回::

        {
          "clean": "晴天 - 周杰倫",
          "title": "晴天",
          "artist": "周杰倫",
          "variants": [("晴天", "周杰倫"), ("周杰倫", "晴天")],
          "folder_artist": "周杰伦",
        }
    """
    base = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", filename or "")
    base = strip_track_prefix(base)
    base = clean_spaces(base)
    folder_artist = strip_track_prefix(folder_name)
    folder_artist = clean_spaces(folder_artist)
    if not looks_like_artist(folder_artist):
        folder_artist = ""

    variants: List[Tuple[str, str]] = []
    title = base
    artist = ""

    if " - " in base:
        left, right = base.rsplit(" - ", 1)
        left, right = clean_spaces(left), clean_spaces(right)
        if left and right:
            title, artist = left, right
            variants.append((left, right))
            if not (folder_artist and normalize_key(folder_artist) in normalize_key(right)):
                variants.append((right, left))
    elif "-" in base and folder_artist:
        left, right = base.rsplit("-", 1)
        left, right = clean_spaces(left), clean_spaces(right)
        if left and right:
            title, artist = left, right
            variants.append((left, right))
    else:
        title = base

    if folder_artist:
        variants.append((title, folder_artist))
        if not artist:
            artist = folder_artist

    # 去重但保持顺序
    seen = set()
    uniq: List[Tuple[str, str]] = []
    for t, a in variants:
        key = (normalize_key(t), normalize_key(a))
        if key not in seen and t:
            seen.add(key)
            uniq.append((collapse_cjk_spaces(t), collapse_cjk_spaces(a)))

    return {
        "clean": base,
        "title": collapse_cjk_spaces(title),
        "artist": collapse_cjk_spaces(artist),
        "variants": uniq,
        "folder_artist": folder_artist,
    }


# ---------------------------------------------------------------------------
# LRC
# ---------------------------------------------------------------------------

def _parse_timestamp(minutes: str, seconds: str, frac: Optional[str]) -> Optional[float]:
    try:
        m = int(minutes)
        s = int(seconds)
        if s >= 60:
            return None
        value = m * 60 + s
        if frac:
            frac = frac[:3]
            value += int(frac) / (10 ** len(frac))
        return round(value, 3)
    except ValueError:
        return None


def parse_lrc(text: str) -> Dict[str, Any]:
    """解析 LRC。

    返回 ``{"lines": [{"time": float, "text": str, "translation": str|None}],
    "meta": {...}, "synced": bool, "plain": bool}``。

    同一时间戳出现两次时（常见于原文 + 翻译），第二次作为 translation。
    """
    raw_lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    meta: Dict[str, str] = {}
    entries: List[Tuple[float, str]] = []
    plain_lines: List[str] = []
    has_time = False

    for raw in raw_lines:
        line = raw.strip()
        if not line:
            continue
        m = _LRC_META_RE.match(line)
        if m:
            meta[m.group(1).lower()] = m.group(2).strip()
            continue
        stamps = list(_LRC_TIME_RE.finditer(line))
        body = _LRC_TIME_RE.sub("", line).strip()
        if stamps:
            has_time = True
            for stamp in stamps:
                t = _parse_timestamp(stamp.group(1), stamp.group(2), stamp.group(3))
                if t is not None and body:
                    entries.append((t, body))
        elif not line.startswith("["):
            plain_lines.append(line)

    entries.sort(key=lambda item: item[0])
    lines: List[Dict[str, Any]] = []
    for t, body in entries:
        if lines and abs(lines[-1]["time"] - t) < 0.001 and not lines[-1].get("translation"):
            lines[-1]["translation"] = body
        else:
            lines.append({"time": t, "text": body, "translation": None})

    # 带时间戳但正文极少时，按纯文本处理
    synced = has_time and len(lines) >= 2
    plain = not has_time or len(lines) < 2
    if plain and not plain_lines:
        plain_lines = [line for _, body in entries for line in [body]]

    return {
        "lines": lines,
        "meta": meta,
        "synced": synced,
        "plain": plain,
        "plain_text": "\n".join(plain_lines).strip(),
    }


def lrc_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0))
    minutes = int(seconds // 60)
    sec = seconds - minutes * 60
    return f"[{minutes:02d}:{sec:05.2f}]"


def build_lrc(lines: List[Dict[str, Any]], meta: Optional[Dict[str, str]] = None) -> str:
    """把行结构写回 LRC 文本。"""
    out: List[str] = []
    for key, value in (meta or {}).items():
        if key in ("ti", "ar", "al", "by", "offset"):
            out.append(f"[{key}:{value}]")
    for line in sorted(lines, key=lambda x: float(x.get("time") or 0)):
        if not (line.get("text") or "").strip():
            continue
        stamp = lrc_timestamp(float(line.get("time") or 0))
        out.append(f"{stamp}{line['text'].strip()}")
        if line.get("translation"):
            out.append(f"{stamp}{line['translation'].strip()}")
    return "\n".join(out).strip() + "\n"


def lrc_plain_text(text: str) -> str:
    parsed = parse_lrc(text)
    if parsed["lines"]:
        return "\n".join(line["text"] for line in parsed["lines"]).strip()
    return parsed.get("plain_text", "").strip()


def lrc_line_count(text: str) -> int:
    return len(parse_lrc(text)["lines"])


def count_timestamps(text: str) -> int:
    return len(_LRC_TIME_RE.findall(text or ""))


def clean_lyrics_text(text: str, simplified: bool = False) -> str:
    """统一换行、去 BOM、去 HTML 实体、按需繁转简。"""
    if not text:
        return ""
    value = html.unescape(str(text))
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\ufeff", "").replace("\u200b", "")
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = _HTML_TAG_RE.sub("", value)
    if simplified:
        value = to_simplified(value)
    return value.strip() + ("\n" if value.strip() else "")


_zhconv = None
_zhconv_checked = False


def to_simplified(text: str) -> str:
    """繁转简；未安装 zhconv 时原样返回。"""
    global _zhconv, _zhconv_checked
    if not text:
        return text
    if not _zhconv_checked:
        _zhconv_checked = True
        try:  # pragma: no cover - 取决于环境
            import zhconv  # type: ignore

            _zhconv = zhconv
        except Exception:
            _zhconv = None
    if _zhconv is None:
        return text
    try:
        return _zhconv.convert(text, "zh-cn")
    except Exception:
        return text


@lru_cache(maxsize=4096)
def _simplify_for_key(text: str) -> str:
    """比较用的繁转简缓存，避免每首歌重复转换同一个歌手名。"""
    return to_simplified(text)


# ---------------------------------------------------------------------------
# 展示格式
# ---------------------------------------------------------------------------

def format_duration(seconds: Optional[float]) -> str:
    try:
        total = int(round(float(seconds or 0)))
    except (TypeError, ValueError):
        return "00:00"
    if total < 0:
        total = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_size(num_bytes: Optional[float]) -> str:
    try:
        value = float(num_bytes or 0)
    except (TypeError, ValueError):
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(value)} {units[idx]}"
    return f"{value:.1f} {units[idx]}"


def format_bitrate(bps: Optional[float]) -> str:
    try:
        value = int(bps or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.0f} Mbps"
    return f"{round(value / 1000)} kbps"


def format_quality(ext: str, sample_rate: Optional[int], bits: Optional[int], channels: Optional[int], bitrate: Optional[int]) -> str:
    parts: List[str] = []
    ext = (ext or "").lstrip(".").upper()
    if ext:
        parts.append(ext)
    if sample_rate:
        parts.append(f"{sample_rate / 1000:g}kHz")
    if bits:
        parts.append(f"{bits}bit")
    if channels:
        parts.append({1: "单声道", 2: "立体声"}.get(int(channels), f"{channels}声道"))
    if not parts and bitrate:
        parts.append(format_bitrate(bitrate))
    return " · ".join(parts)


def safe_filename(name: str, max_len: int = 120) -> str:
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", clean_spaces(name))
    value = value.strip(" .")
    if len(value) > max_len:
        value = value[:max_len].rstrip()
    return value or "untitled"


def truncate(text: str, length: int = 40) -> str:
    text = clean_spaces(text)
    return text if len(text) <= length else text[: length - 1] + "…"


def guess_tag_issues(meta: Dict[str, Any]) -> List[Dict[str, str]]:
    """检测标签问题，返回 ``[{"code": ..., "label": ...}]``。"""
    issues: List[Dict[str, str]] = []
    title = clean_spaces(meta.get("title"))
    artist = clean_spaces(meta.get("artist"))
    album = clean_spaces(meta.get("album"))

    if not title:
        issues.append({"code": "no_title", "label": "缺少标题"})
    if not artist:
        issues.append({"code": "no_artist", "label": "缺少歌手"})
    if not album:
        issues.append({"code": "no_album", "label": "缺少专辑"})

    for field, value, label in (("title", title, "标题"), ("artist", artist, "歌手"), ("album", album, "专辑")):
        if value and is_placeholder(value):
            issues.append({"code": f"placeholder_{field}", "label": f"{label}是占位值“{value}”"})

    if album and re.search(r"https?://|www\.|\.(com|cn|net)\b", album, re.I):
        issues.append({"code": "album_url", "label": "专辑名疑似网址"})

    if title and re.search(r"\.(flac|mp3|m4a|wav)$", title, re.I):
        issues.append({"code": "title_has_ext", "label": "标题含文件扩展名"})

    if title and artist and normalize_key(artist) and normalize_key(artist) in normalize_key(title) and len(normalize_key(title)) > len(normalize_key(artist)) + 1:
        issues.append({"code": "title_has_artist", "label": "标题里重复包含歌手名"})

    year = clean_spaces(str(meta.get("year") or ""))
    if year:
        m = re.search(r"(19|20)\d{2}", year)
        if not m:
            issues.append({"code": "bad_year", "label": "年份格式异常"})

    return issues
