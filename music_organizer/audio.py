"""音频标签读写：歌词、封面、基础标签与备份。

支持格式
--------
- 可读写：FLAC / MP3 / M4A(MP4) / OGG / Opus / WAV / AIFF / APE / WavPack
- 只读  ：AAC(ADTS) 以及 mutagen 能识别但没有写入实现的格式

设计原则
--------
1. 所有写操作前都可以备份（JSON，包含标签、歌词和封面），支持撤销。
2. 歌词优先写入内嵌标签，可选同时写同名 ``.lrc``。
3. 封面统一转成 JPEG（兼容性最好），保留原始长宽比；可选同名封面文件。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:  # Pillow 是可选依赖，缺失时降级为原始字节写入
    from PIL import Image, ImageOps  # type: ignore
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    ImageOps = None  # type: ignore

from .textutil import clean_spaces, count_timestamps, guess_tag_issues

READABLE_EXTS = {
    ".flac",
    ".mp3",
    ".m4a",
    ".mp4",
    ".aac",
    ".ogg",
    ".opus",
    ".wav",
    ".aiff",
    ".aif",
    ".ape",
    ".wv",
}

WRITABLE_EXTS = {
    ".flac",
    ".mp3",
    ".m4a",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".aiff",
    ".aif",
    ".ape",
    ".wv",
}

SIDECAR_COVER_NAMES = (
    "cover.jpg",
    "cover.jpeg",
    "cover.png",
    "folder.jpg",
    "folder.jpeg",
    "folder.png",
    "AlbumArt.jpg",
    "AlbumArtLarge.jpg",
    "front.jpg",
    "front.png",
)

MAX_COVER_BYTES = 12 * 1024 * 1024


class AudioError(Exception):
    """音频读写失败。"""


class UnsupportedFormat(AudioError):
    """该格式不支持写入标签。"""


@dataclass
class CoverInfo:
    data: bytes
    mime: str
    source: str = "embed"
    width: int = 0
    height: int = 0


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def ext_of(path: str | Path) -> str:
    return Path(path).suffix.lower()


def is_writable(path: str | Path) -> bool:
    return ext_of(path) in WRITABLE_EXTS


def _first(tags: Any, keys: Tuple[str, ...], default: str = "") -> str:
    if tags is None:
        return default
    for key in keys:
        try:
            value = tags.get(key)
        except Exception:
            value = None
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        text = str(value).strip()
        if text:
            return text
    return default


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.match(r"^(\d+)", text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _sniff_image(data: bytes) -> Tuple[str, str]:
    """返回 (mime, ext)，识别 jpeg/png/webp/gif/bmp。"""
    if not data:
        return "image/jpeg", ".jpg"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", ".gif"
    if data[:2] == b"BM":
        return "image/bmp", ".bmp"
    return "image/jpeg", ".jpg"


def image_dimensions(data: bytes) -> Tuple[int, int]:
    """不依赖 Pillow 读取 JPEG/PNG 尺寸，失败返回 (0, 0)。"""
    if not data:
        return 0, 0
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if data[:3] == b"\xff\xd8\xff":
            idx = 2
            size = len(data)
            while idx < size - 9:
                if data[idx] != 0xFF:
                    idx += 1
                    continue
                marker = data[idx + 1]
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    idx += 2
                    continue
                seg_len = int.from_bytes(data[idx + 2 : idx + 4], "big")
                if marker in (
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                ):
                    height = int.from_bytes(data[idx + 5 : idx + 7], "big")
                    width = int.from_bytes(data[idx + 7 : idx + 9], "big")
                    return width, height
                idx += 2 + seg_len
    except Exception:
        pass
    return 0, 0


def prepare_cover(
    data: bytes,
    min_size: int = 300,
    max_size: int = 1280,
    quality: int = 90,
) -> Dict[str, Any]:
    """校验并规范化封面：转 RGB、限制尺寸、输出 JPEG。

    返回 ``{"data", "mime", "width", "height", "original_width", "original_height",
    "low_res": bool}``；数据不合法时抛出 :class:`AudioError`。
    """
    if not data:
        raise AudioError("封面数据为空")
    if len(data) > MAX_COVER_BYTES:
        raise AudioError("封面文件过大")
    mime, _ = _sniff_image(data)
    width, height = image_dimensions(data)

    if Image is None:
        if width and height and width < min_size and height < min_size:
            raise AudioError(f"封面尺寸过小（{width}x{height}）")
        return {
            "data": data,
            "mime": mime,
            "width": width,
            "height": height,
            "original_width": width,
            "original_height": height,
            "bytes": len(data),
            "low_res": False,
        }

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise AudioError(f"无法识别的图片：{exc}") from exc

    original_width, original_height = img.size
    if max(original_width, original_height) < min_size:
        raise AudioError(f"封面尺寸过小（{original_width}x{original_height}）")

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    longest = max(img.size)
    if longest > max_size:
        ratio = max_size / float(longest)
        new_size = (max(1, round(img.width * ratio)), max(1, round(img.height * ratio)))
        img = img.resize(new_size, Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=int(quality), optimize=True, progressive=True)
    payload = out.getvalue()
    if len(payload) > len(data) and mime == "image/jpeg" and longest <= max_size:
        # 重新压缩反而更大，保留原图
        return {
            "data": data,
            "mime": mime,
            "width": original_width,
            "height": original_height,
            "original_width": original_width,
            "original_height": original_height,
            "bytes": len(data),
            "low_res": False,
        }
    return {
        "data": payload,
        "mime": "image/jpeg",
        "width": img.width,
        "height": img.height,
        "original_width": original_width,
        "original_height": original_height,
        "bytes": len(payload),
        "low_res": False,
    }


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def _load_mutagen(path: str | Path):
    try:
        import mutagen  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise AudioError("缺少 mutagen，请先安装依赖") from exc
    try:
        return mutagen.File(str(path))
    except Exception as exc:
        raise AudioError(str(exc)) from exc


def _read_sidecar_lyrics(path: Path) -> Tuple[str, str]:
    for suffix in (".lrc", ".LRC"):
        candidate = path.with_suffix(suffix)
        if candidate.exists() and candidate.is_file():
            try:
                text = candidate.read_text(encoding="utf-8", errors="ignore")
                if text.strip():
                    return text, "lrc"
            except OSError:
                continue
    return "", ""


def _read_sidecar_cover(path: Path) -> Optional[CoverInfo]:
    candidates = [path.with_suffix(".jpg"), path.with_suffix(".jpeg"), path.with_suffix(".png")]
    candidates += [path.parent / name for name in SIDECAR_COVER_NAMES]
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                data = candidate.read_bytes()
                if len(data) < 100:
                    continue
                mime, _ = _sniff_image(data)
                width, height = image_dimensions(data)
                return CoverInfo(data=data, mime=mime, source="file", width=width, height=height)
        except OSError:
            continue
    return None


def read_cover(path: str | Path, include_sidecar: bool = True) -> Optional[CoverInfo]:
    """读取内嵌封面；没有时回退到同目录封面文件。"""
    p = Path(path)
    ext = ext_of(p)
    try:
        if ext == ".flac":
            from mutagen.flac import FLAC

            audio = FLAC(str(p))
            if audio.pictures:
                pic = audio.pictures[0]
                return CoverInfo(data=pic.data, mime=pic.mime or _sniff_image(pic.data)[0], width=pic.width, height=pic.height)
        elif ext == ".mp3":
            from mutagen.id3 import ID3

            try:
                tags = ID3(str(p))
            except Exception:
                tags = None
            if tags is not None:
                apics = tags.getall("APIC")
                if apics:
                    apic = apics[0]
                    width, height = image_dimensions(apic.data)
                    return CoverInfo(data=apic.data, mime=apic.mime or _sniff_image(apic.data)[0], width=width, height=height)
        elif ext in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4

            audio = MP4(str(p))
            covers = (audio.tags or {}).get("covr") or []
            if covers:
                cover = covers[0]
                data = bytes(cover)
                mime = "image/png" if getattr(cover, "imageformat", 0) == 13 else "image/jpeg"
                width, height = image_dimensions(data)
                return CoverInfo(data=data, mime=mime, width=width, height=height)
        elif ext in (".ogg", ".opus"):
            from mutagen.flac import Picture
            from mutagen.oggopus import OggOpus
            from mutagen.oggvorbis import OggVorbis

            audio = OggOpus(str(p)) if ext == ".opus" else OggVorbis(str(p))
            values = audio.get("metadata_block_picture") or []
            if values:
                pic = Picture(base64.b64decode(values[0]))
                return CoverInfo(data=pic.data, mime=pic.mime or "image/jpeg", width=pic.width, height=pic.height)
        elif ext in (".ape", ".wv"):
            from mutagen.apev2 import APEv2

            tags = APEv2(str(p))
            value = tags.get("Cover Art (Front)")
            if value is not None:
                raw = value.value if hasattr(value, "value") else bytes(value)
                nul = raw.find(b"\x00")
                data = raw[nul + 1 :] if nul >= 0 else raw
                mime, _ = _sniff_image(data)
                width, height = image_dimensions(data)
                return CoverInfo(data=data, mime=mime, width=width, height=height)
        elif ext in (".wav", ".aiff", ".aif"):
            audio = _load_mutagen(p)
            tags = getattr(audio, "tags", None)
            if tags is not None:
                apics = tags.getall("APIC") if hasattr(tags, "getall") else []
                if apics:
                    data = apics[0].data
                    return CoverInfo(data=data, mime=apics[0].mime or "image/jpeg")
    except Exception:
        pass

    if include_sidecar:
        return _read_sidecar_cover(p)
    return None


def read_embedded_lyrics(path: str | Path) -> str:
    """只读取音频内嵌歌词，不回退到同名 .lrc。"""
    p = Path(path)
    ext = ext_of(p)
    text = ""
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3

            try:
                tags = ID3(str(p))
            except Exception:
                tags = None
            if tags is not None:
                frames = tags.getall("USLT")
                if frames:
                    text = str(frames[0])
                if not text and "SYLT" in tags:
                    # 同步歌词帧较少见，退化为纯文本行
                    sylt = tags["SYLT"]
                    text = "\n".join(str(item[1]) for item in getattr(sylt, "text", []) if item)
        elif ext == ".flac":
            from mutagen.flac import FLAC

            audio = FLAC(str(p))
            text = _first(audio, ("LYRICS", "UNSYNCEDLYRICS", "LYRIC"))
        elif ext in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4

            audio = MP4(str(p))
            text = _first(audio.tags, ("\xa9lyr", "lyr"))
        elif ext in (".ogg", ".opus"):
            from mutagen.oggopus import OggOpus
            from mutagen.oggvorbis import OggVorbis

            audio = OggOpus(str(p)) if ext == ".opus" else OggVorbis(str(p))
            text = _first(audio, ("LYRICS", "UNSYNCEDLYRICS", "LYRIC"))
        elif ext in (".ape", ".wv"):
            from mutagen.apev2 import APEv2

            tags = APEv2(str(p))
            text = _first(tags, ("LYRICS", "UNSYNCEDLYRICS"))
        elif ext in (".wav", ".aiff", ".aif"):
            audio = _load_mutagen(p)
            tags = getattr(audio, "tags", None)
            if tags is not None and hasattr(tags, "getall"):
                frames = tags.getall("USLT")
                if frames:
                    text = str(frames[0])
    except Exception:
        text = ""
    return text.strip()


def read_lyrics(path: str | Path) -> Tuple[str, str]:
    """读取歌词，返回 ``(text, source)``；source 为 embed / lrc / ''。"""
    p = Path(path)
    text = read_embedded_lyrics(p)
    if text:
        return text, "embed"
    sidecar, source = _read_sidecar_lyrics(p)
    if sidecar:
        return sidecar, source
    return "", ""


def _read_tags(path: Path) -> Dict[str, str]:
    ext = ext_of(path)
    title = artist = album = album_artist = year = genre = ""
    track_no = disc_no = None
    try:
        if ext == ".flac":
            from mutagen.flac import FLAC

            audio = FLAC(str(path))
            title = _first(audio, ("TITLE",))
            artist = _first(audio, ("ARTIST",))
            album = _first(audio, ("ALBUM",))
            album_artist = _first(audio, ("ALBUMARTIST", "ALBUM ARTIST"))
            year = _first(audio, ("DATE", "YEAR"))
            genre = _first(audio, ("GENRE",))
            track_no = _to_int(_first(audio, ("TRACKNUMBER", "TRACK")))
            disc_no = _to_int(_first(audio, ("DISCNUMBER", "DISC")))
        elif ext == ".mp3":
            from mutagen.id3 import ID3

            try:
                tags = ID3(str(path))
            except Exception:
                tags = None
            if tags is not None:
                title = _first(tags, ("TIT2",))
                artist = _first(tags, ("TPE1",))
                album = _first(tags, ("TALB",))
                album_artist = _first(tags, ("TPE2",))
                year = _first(tags, ("TDRC", "TYER"))
                genre = _first(tags, ("TCON",))
                track_no = _to_int(_first(tags, ("TRCK",)))
                disc_no = _to_int(_first(tags, ("TPOS",)))
        elif ext in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4

            tags = MP4(str(path)).tags or {}
            title = _first(tags, ("\xa9nam",))
            artist = _first(tags, ("\xa9ART",))
            album = _first(tags, ("\xa9alb",))
            album_artist = _first(tags, ("aART",))
            year = _first(tags, ("\xa9day",))
            genre = _first(tags, ("\xa9gen",))
            trkn = tags.get("trkn") or []
            if trkn:
                try:
                    track_no = int(trkn[0][0])
                except Exception:
                    track_no = None
            disk = tags.get("disk") or []
            if disk:
                try:
                    disc_no = int(disk[0][0])
                except Exception:
                    disc_no = None
        elif ext in (".ogg", ".opus"):
            from mutagen.oggopus import OggOpus
            from mutagen.oggvorbis import OggVorbis

            audio = OggOpus(str(path)) if ext == ".opus" else OggVorbis(str(path))
            title = _first(audio, ("TITLE",))
            artist = _first(audio, ("ARTIST",))
            album = _first(audio, ("ALBUM",))
            album_artist = _first(audio, ("ALBUMARTIST", "ALBUM ARTIST"))
            year = _first(audio, ("DATE", "YEAR"))
            genre = _first(audio, ("GENRE",))
            track_no = _to_int(_first(audio, ("TRACKNUMBER", "TRACK")))
            disc_no = _to_int(_first(audio, ("DISCNUMBER", "DISC")))
        elif ext in (".ape", ".wv"):
            from mutagen.apev2 import APEv2

            tags = APEv2(str(path))
            title = _first(tags, ("Title", "TITLE"))
            artist = _first(tags, ("Artist", "ARTIST"))
            album = _first(tags, ("Album", "ALBUM"))
            album_artist = _first(tags, ("Album Artist", "ALBUMARTIST"))
            year = _first(tags, ("Year", "YEAR"))
            genre = _first(tags, ("Genre", "GENRE"))
            track_no = _to_int(_first(tags, ("Track", "TRACKNUMBER")))
            disc_no = _to_int(_first(tags, ("Disc", "DISCNUMBER")))
        else:
            audio = _load_mutagen(path)
            tags = getattr(audio, "tags", None)
            if tags is not None:
                title = _first(tags, ("TIT2", "title", "TITLE", "\xa9nam"))
                artist = _first(tags, ("TPE1", "artist", "ARTIST", "\xa9ART"))
                album = _first(tags, ("TALB", "album", "ALBUM", "\xa9alb"))
                year = _first(tags, ("TDRC", "date", "DATE", "\xa9day"))
                genre = _first(tags, ("TCON", "genre", "GENRE", "\xa9gen"))
                if hasattr(tags, "getall"):
                    try:
                        track_no = _to_int(_first(tags, ("TRCK",)))
                    except Exception:
                        pass
    except Exception:
        pass

    if isinstance(track_no, tuple):
        track_no = track_no[0] if track_no else None
    if isinstance(disc_no, tuple):
        disc_no = disc_no[0] if disc_no else None

    return {
        "title": clean_spaces(title),
        "artist": clean_spaces(artist),
        "album": clean_spaces(album),
        "album_artist": clean_spaces(album_artist),
        "year": clean_spaces(str(year))[:16],
        "genre": clean_spaces(genre),
        "track_no": track_no,
        "disc_no": disc_no,
    }


def read_meta(path: str | Path) -> Dict[str, Any]:
    """读取一首歌的全部索引信息（不返回封面二进制）。"""
    p = Path(path)
    info: Dict[str, Any] = {
        "path": str(p),
        "filename": p.name,
        "dir": str(p.parent),
        "ext": p.suffix.lower(),
        "size": 0,
        "mtime": 0.0,
        "duration": 0.0,
        "bitrate": 0,
        "sample_rate": 0,
        "bits": 0,
        "channels": 0,
        "title": "",
        "artist": "",
        "album": "",
        "album_artist": "",
        "year": "",
        "genre": "",
        "track_no": None,
        "disc_no": None,
        "lyrics_text": "",
        "lyrics_source": "",
        "lyrics_synced": False,
        "has_cover": False,
        "cover_mime": "",
        "cover_size": 0,
        "audio_ok": False,
        "error": "",
    }
    try:
        stat = p.stat()
        info["size"] = stat.st_size
        info["mtime"] = stat.st_mtime
    except OSError as exc:
        info["error"] = str(exc)
        return info

    try:
        audio = _load_mutagen(p)
        if audio is not None and audio.info is not None:
            audio_info = audio.info
            info["duration"] = round(float(getattr(audio_info, "length", 0) or 0), 3)
            info["bitrate"] = int(getattr(audio_info, "bitrate", 0) or 0)
            info["sample_rate"] = int(getattr(audio_info, "sample_rate", 0) or 0)
            info["bits"] = int(getattr(audio_info, "bits_per_sample", 0) or 0)
            info["channels"] = int(getattr(audio_info, "channels", 0) or 0)
            info["audio_ok"] = True
    except Exception as exc:
        info["error"] = str(exc)

    info.update(_read_tags(p))

    lyrics, source = read_lyrics(p)
    info["lyrics_text"] = lyrics
    info["lyrics_source"] = source
    info["lyrics_synced"] = count_timestamps(lyrics) >= 3
    info["has_lyrics"] = bool(lyrics.strip())

    cover = read_cover(p)
    if cover:
        info["has_cover"] = True
        info["cover_mime"] = cover.mime
        info["cover_size"] = len(cover.data)
        info["cover_width"] = cover.width
        info["cover_height"] = cover.height
        info["cover_source"] = cover.source

    info["tag_issues"] = guess_tag_issues(info)
    info["quick_hash"] = quick_hash(p, info["size"])
    return info


def quick_hash(path: str | Path, size: Optional[int] = None) -> str:
    """体积 + 头尾片段的哈希，用于重复文件检测（不读整个文件）。"""
    p = Path(path)
    digest = hashlib.sha1()
    try:
        total = size if size is not None else p.stat().st_size
        digest.update(str(total).encode())
        with p.open("rb") as fh:
            digest.update(fh.read(131072))
            if total > 262144:
                fh.seek(-65536, os.SEEK_END)
                digest.update(fh.read(65536))
    except OSError:
        return ""
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------

def _flac_picture(data: bytes, mime: str, width: int, height: int):
    from mutagen.flac import Picture

    pic = Picture()
    pic.type = 3  # front cover
    pic.mime = mime
    pic.desc = "Cover"
    pic.width = width
    pic.height = height
    pic.depth = 24
    pic.data = data
    return pic


def _set_vorbis_tag(audio, key: str, value: Any) -> None:
    """Vorbis comment 使用小写键，兼容 Jellyfin / TagLib 等读取器。"""
    for existing in list(audio.keys()):
        if existing.upper() == key.upper():
            del audio[existing]
    if value not in (None, "", []):
        audio[key.lower()] = str(value)


def _apply_flac(audio, lyrics, cover, tags) -> None:
    from mutagen.flac import Picture

    if lyrics is not None:
        for key in list(audio.keys()):
            if key.upper() in ("LYRICS", "UNSYNCEDLYRICS", "LYRIC"):
                del audio[key]
        if lyrics.strip():
            audio["lyrics"] = lyrics
            audio["unsyncedlyrics"] = lyrics
    if cover is not None:
        data, mime = cover
        width, height = image_dimensions(data)
        keep = [pic for pic in audio.pictures if pic.type not in (3, 0)]
        audio.clear_pictures()
        for pic in keep:
            audio.add_picture(pic)
        audio.add_picture(_flac_picture(data, mime, width, height))
    if tags:
        mapping = {
            "title": "title",
            "artist": "artist",
            "album": "album",
            "album_artist": "albumartist",
            "year": "date",
            "genre": "genre",
            "track_no": "tracknumber",
            "disc_no": "discnumber",
        }
        for key, tag_key in mapping.items():
            if key in tags:
                _set_vorbis_tag(audio, tag_key, tags[key])
    audio.save()


def _apply_mp3(path: Path, lyrics, cover, tags) -> None:
    from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, USLT

    try:
        id3 = ID3(str(path))
    except Exception:
        id3 = ID3()

    if lyrics is not None:
        id3.delall("USLT")
        if lyrics.strip():
            id3.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
    if cover is not None:
        data, mime = cover
        id3.delall("APIC")
        id3.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
    if tags:
        mapping = {
            "title": (TIT2, "TIT2"),
            "artist": (TPE1, "TPE1"),
            "album": (TALB, "TALB"),
            "album_artist": (TPE2, "TPE2"),
            "year": (TDRC, "TDRC"),
            "genre": (TCON, "TCON"),
            "track_no": (TRCK, "TRCK"),
            "disc_no": (TPOS, "TPOS"),
        }
        for key, (frame_cls, frame_id) in mapping.items():
            if key not in tags:
                continue
            value = tags[key]
            if value in (None, "", []):
                if frame_id in id3:
                    del id3[frame_id]
            else:
                id3.delall(frame_id)
                id3.add(frame_cls(encoding=3, text=str(value)))
    id3.save(str(path), v2_version=3)


def _apply_mp4(path: Path, lyrics, cover, tags) -> None:
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(str(path))
    if audio.tags is None:
        audio.add_tags()
    if lyrics is not None:
        if lyrics.strip():
            audio["\xa9lyr"] = lyrics
        elif "\xa9lyr" in audio:
            del audio["\xa9lyr"]
    if cover is not None:
        data, mime = cover
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(data, imageformat=fmt)]
    if tags:
        mapping = {
            "title": "\xa9nam",
            "artist": "\xa9ART",
            "album": "\xa9alb",
            "album_artist": "aART",
            "year": "\xa9day",
            "genre": "\xa9gen",
        }
        for key, atom in mapping.items():
            if key not in tags:
                continue
            value = tags[key]
            if value in (None, "", []):
                if atom in audio:
                    del audio[atom]
            else:
                audio[atom] = [str(value)]
        if "track_no" in tags:
            track = _to_int(tags.get("track_no")) or 0
            audio["trkn"] = [(track, 0)]
        if "disc_no" in tags:
            disc = _to_int(tags.get("disc_no")) or 0
            audio["disk"] = [(disc, 0)]
    audio.save()


def _apply_ogg(path: Path, lyrics, cover, tags) -> None:
    from mutagen.flac import Picture
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis

    audio = OggOpus(str(path)) if ext_of(path) == ".opus" else OggVorbis(str(path))
    if lyrics is not None:
        for key in list(audio.keys()):
            if key.upper() in ("LYRICS", "UNSYNCEDLYRICS", "LYRIC"):
                del audio[key]
        if lyrics.strip():
            audio["lyrics"] = lyrics
            audio["unsyncedlyrics"] = lyrics
    if cover is not None:
        data, mime = cover
        picture = _flac_picture(data, mime, *image_dimensions(data))
        audio["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]
    if tags:
        mapping = {
            "title": "title",
            "artist": "artist",
            "album": "album",
            "album_artist": "albumartist",
            "year": "date",
            "genre": "genre",
            "track_no": "tracknumber",
            "disc_no": "discnumber",
        }
        for key, tag_key in mapping.items():
            if key in tags:
                _set_vorbis_tag(audio, tag_key, tags[key])
    audio.save()


def _apply_ape(path: Path, lyrics, cover, tags) -> None:
    from mutagen.apev2 import APEBinaryValue, APEv2

    try:
        ape = APEv2(str(path))
    except Exception:
        ape = APEv2()
    if lyrics is not None:
        if lyrics.strip():
            ape["LYRICS"] = lyrics
        elif "LYRICS" in ape:
            del ape["LYRICS"]
    if cover is not None:
        data, mime = cover
        ext = ".png" if mime == "image/png" else ".jpg"
        ape["Cover Art (Front)"] = APEBinaryValue(f"cover{ext}".encode("utf-8") + b"\x00" + data)
    if tags:
        mapping = {
            "title": "Title",
            "artist": "Artist",
            "album": "Album",
            "album_artist": "Album Artist",
            "year": "Year",
            "genre": "Genre",
            "track_no": "Track",
            "disc_no": "Disc",
        }
        for key, tag_key in mapping.items():
            if key not in tags:
                continue
            value = tags[key]
            if value in (None, "", []):
                if tag_key in ape:
                    del ape[tag_key]
            else:
                ape[tag_key] = str(value)
    ape.save(str(path))


def _apply_id3_container(path: Path, lyrics, cover, tags) -> None:
    """WAV / AIFF：容器自带 ID3 标签。"""
    from mutagen.id3 import APIC, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, USLT

    audio = _load_mutagen(path)
    if audio is None:
        raise UnsupportedFormat(f"无法解析 {path.name}")
    if getattr(audio, "tags", None) is None:
        audio.add_tags()
    id3 = audio.tags
    if lyrics is not None:
        id3.delall("USLT")
        if lyrics.strip():
            id3.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
    if cover is not None:
        data, mime = cover
        id3.delall("APIC")
        id3.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
    if tags:
        mapping = {
            "title": (TIT2, "TIT2"),
            "artist": (TPE1, "TPE1"),
            "album": (TALB, "TALB"),
            "album_artist": (TPE2, "TPE2"),
            "year": (TDRC, "TDRC"),
            "genre": (TCON, "TCON"),
            "track_no": (TRCK, "TRCK"),
            "disc_no": (TPOS, "TPOS"),
        }
        for key, (frame_cls, frame_id) in mapping.items():
            if key not in tags:
                continue
            value = tags[key]
            if value in (None, "", []):
                if frame_id in id3:
                    del id3[frame_id]
            else:
                id3.delall(frame_id)
                id3.add(frame_cls(encoding=3, text=str(value)))
    audio.save()


def update_media(
    path: str | Path,
    lyrics: Optional[str] = None,
    cover: Optional[Tuple[bytes, str]] = None,
    tags: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """一次性写入歌词 / 封面 / 标签。

    - ``lyrics=None`` 表示不改歌词；空字符串表示删除歌词。
    - ``cover=None`` 表示不改封面；空元组不可用，删除封面请用 :func:`clear_cover`。
    """
    p = Path(path)
    if not p.exists():
        raise AudioError(f"文件不存在：{p}")
    ext = ext_of(p)
    if ext not in WRITABLE_EXTS:
        raise UnsupportedFormat(f"暂不支持写入 .{ext.lstrip('.')} 格式")
    if not os.access(p, os.W_OK):
        raise AudioError(f"没有写入权限：{p}")
    if lyrics is None and cover is None and not tags:
        return {"changed": False}

    try:
        if ext == ".flac":
            from mutagen.flac import FLAC

            _apply_flac(FLAC(str(p)), lyrics, cover, tags)
        elif ext == ".mp3":
            _apply_mp3(p, lyrics, cover, tags)
        elif ext in (".m4a", ".mp4"):
            _apply_mp4(p, lyrics, cover, tags)
        elif ext in (".ogg", ".opus"):
            _apply_ogg(p, lyrics, cover, tags)
        elif ext in (".ape", ".wv"):
            _apply_ape(p, lyrics, cover, tags)
        else:
            _apply_id3_container(p, lyrics, cover, tags)
    except AudioError:
        raise
    except Exception as exc:
        raise AudioError(f"写入失败：{exc}") from exc

    return {"changed": True, "lyrics": lyrics is not None, "cover": cover is not None, "tags": bool(tags)}


def clear_cover(path: str | Path) -> None:
    """删除内嵌封面。"""
    p = Path(path)
    ext = ext_of(p)
    if ext == ".flac":
        from mutagen.flac import FLAC

        audio = FLAC(str(p))
        audio.clear_pictures()
        audio.save()
    elif ext == ".mp3":
        from mutagen.id3 import ID3

        tags = ID3(str(p))
        tags.delall("APIC")
        tags.save(str(p), v2_version=3)
    elif ext in (".m4a", ".mp4"):
        from mutagen.mp4 import MP4

        audio = MP4(str(p))
        if audio.tags and "covr" in audio.tags:
            del audio.tags["covr"]
            audio.save()
    elif ext in (".ogg", ".opus"):
        from mutagen.oggopus import OggOpus
        from mutagen.oggvorbis import OggVorbis

        audio = OggOpus(str(p)) if ext == ".opus" else OggVorbis(str(p))
        if "metadata_block_picture" in audio:
            del audio["metadata_block_picture"]
            audio.save()
    elif ext in (".ape", ".wv"):
        from mutagen.apev2 import APEv2

        tags = APEv2(str(p))
        if "Cover Art (Front)" in tags:
            del tags["Cover Art (Front)"]
            tags.save(str(p))
    else:
        audio = _load_mutagen(p)
        tags = getattr(audio, "tags", None)
        if tags is not None and hasattr(tags, "delall"):
            tags.delall("APIC")
            audio.save()


# ---------------------------------------------------------------------------
# 备份 / 撤销
# ---------------------------------------------------------------------------

def _backup_key(path: str | Path) -> str:
    return hashlib.sha1(str(Path(path).resolve()).encode("utf-8")).hexdigest()[:16]


def backup_track(path: str | Path, backup_root: str | Path, reason: str = "") -> Optional[str]:
    """写入前备份标签、歌词和封面，返回备份文件路径。"""
    p = Path(path)
    try:
        tags = _read_tags(p)
        lyrics, lyrics_source = read_lyrics(p)
        cover = read_cover(p, include_sidecar=False)
        payload = {
            "path": str(p.resolve()),
            "reason": reason,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "stat": {"size": p.stat().st_size, "mtime": p.stat().st_mtime},
            "tags": tags,
            "lyrics": lyrics,
            "lyrics_source": lyrics_source,
            "cover_mime": cover.mime if cover else "",
            "cover_b64": base64.b64encode(cover.data).decode("ascii") if cover and len(cover.data) <= 4 * 1024 * 1024 else "",
            "had_cover": bool(cover),
        }
        folder = Path(backup_root) / _backup_key(p)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        target = folder / f"{stamp}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return str(target)
    except Exception:
        return None


def restore_backup(backup_file: str | Path) -> Dict[str, Any]:
    """按备份恢复一首歌的标签 / 歌词 / 封面。"""
    data = json.loads(Path(backup_file).read_text(encoding="utf-8"))
    path = data.get("path")
    if not path or not Path(path).exists():
        raise AudioError("备份对应的音频文件不存在")
    cover = None
    if data.get("cover_b64"):
        cover = (base64.b64decode(data["cover_b64"]), data.get("cover_mime") or "image/jpeg")
    tags = data.get("tags") or {}
    update_media(path, lyrics=data.get("lyrics", ""), cover=cover, tags=tags)
    if cover is None and not data.get("had_cover"):
        # 备份时没有封面，撤销时也应移除后来写入的封面
        try:
            clear_cover(path)
        except Exception:
            pass
    return {"path": path, "restored_at": datetime.now().isoformat(timespec="seconds")}


def latest_backup(path: str | Path, backup_root: str | Path) -> Optional[str]:
    folder = Path(backup_root) / _backup_key(path)
    if not folder.is_dir():
        return None
    files = sorted(folder.glob("*.json"))
    return str(files[-1]) if files else None


def prune_backups(backup_root: str | Path, max_backups: int = 300) -> int:
    """按数量裁剪备份，返回删除数量。"""
    root = Path(backup_root)
    if not root.is_dir() or max_backups <= 0:
        return 0
    files = sorted(root.glob("*/*.json"), key=lambda p: p.stat().st_mtime)
    removed = 0
    while len(files) > max_backups:
        victim = files.pop(0)
        try:
            victim.unlink()
            removed += 1
        except OSError:
            break
    # 清空目录
    for folder in root.iterdir():
        if folder.is_dir() and not any(folder.iterdir()):
            try:
                folder.rmdir()
            except OSError:
                pass
    return removed


def write_lrc_sidecar(path: str | Path, lyrics: str) -> str:
    p = Path(path)
    target = p.with_suffix(".lrc")
    target.write_text(lyrics, encoding="utf-8")
    return str(target)


def write_cover_sidecar(path: str | Path, data: bytes) -> str:
    """写入与歌曲同名的封面文件（``歌曲.jpg``），避免覆盖目录里其他专辑的封面。"""
    p = Path(path)
    _, ext = _sniff_image(data)
    target = p.with_suffix(ext)
    target.write_bytes(data)
    return str(target)


def extract_cover_to_file(path: str | Path, target: str | Path) -> str:
    cover = read_cover(path)
    if not cover:
        raise AudioError("没有可导出的封面")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(cover.data)
    return str(target)


def copy_file(src: str | Path, dst: str | Path) -> str:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dst))
    return str(dst)


def guess_mime(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"
