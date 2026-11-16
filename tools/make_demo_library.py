#!/usr/bin/env python3
"""生成一套用于开发 / 截图 / 演示的本地音乐库。

用法::

    python tools/make_demo_library.py E:\\tmp\\morg-demo
    python tools/make_demo_library.py --force ./demo

会调用系统 ffmpeg 合成 10 秒左右的音频，再用 mutagen 写入标签、封面与歌词，
其中故意保留若干“缺歌词 / 缺封面 / 标签异常”的曲目，用来展示体检与修复界面。
仅供开发测试，不会打包进安装包。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import math
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    print("需要 Pillow：pip install pillow", file=sys.stderr)
    raise

try:
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import APIC, ID3, TIT2, TPE1, TALB, USLT
    from mutagen.mp3 import MP3
except ImportError:  # pragma: no cover
    print("需要 mutagen：pip install mutagen", file=sys.stderr)
    raise

TRACKS = [
    # (folder, title, artist, album, seconds, tone, fmt, cover, lyrics, note)
    ("1-周杰伦", "晴天", "周杰伦", "叶惠美", 269, 440, "flac", True, True, ""),
    ("1-周杰伦", "七里香", "周杰伦", "七里香", 299, 494, "flac", True, False, ""),
    ("1-周杰伦", "夜曲", "周杰伦", "十一月的萧邦", 226, 523, "flac", False, True, ""),
    ("1-周杰伦", "不能說的秘密", "周杰倫", "魔天倫世界巡迴演唱會", 278, 392, "flac", True, True, "tag"),
    ("2-邓紫棋", "泡沫", "G.E.M. 邓紫棋", "Xposed", 258, 587, "flac", True, True, ""),
    ("2-邓紫棋", "光年之外", "G.E.M. 邓紫棋", "光年之外", 235, 659, "flac", False, False, ""),
    ("3-薛之谦", "演员", "薛之谦", "绅士", 261, 349, "flac", True, True, ""),
    ("3-薛之谦", "刚刚好", "薛之谦", "初学者", 253, 330, "flac", True, False, ""),
    ("4-林俊杰", "江南", "林俊杰", "第二天堂", 268, 294, "flac", True, True, ""),
    ("5-李荣浩", "年少有为", "李荣浩", "耳朵", 279, 415, "flac", True, True, ""),
    ("6-毛不易", "消愁", "毛不易", "平凡的一天", 274, 466, "flac", True, False, ""),
    ("6-毛不易", "一荤一素", "毛不易", "平凡的一天", 280, 220, "flac", False, False, ""),
    ("9- 陈奕迅", "十年", "陈奕迅", "黑白灰", 205, 262, "flac", True, True, ""),
    ("10-赵雷", "成都", "赵雷", "无法长大", 328, 196, "flac", True, True, ""),
    ("其他", "STAY", "The Kid LAROI & Justin Bieber", "F*CK LOVE", 141, 698, "flac", True, True, ""),
    ("其他", "Someone You Loved", "Lewis Capaldi", "Divinely Uninspired to a Hellish Extent", 182, 311, "mp3", False, True, ""),
    ("其他", "Sugar", "Maroon 5", "V", 235, 784, "flac", True, True, ""),
    ("其他", "无名测试曲", "", "kuwo", 120, 150, "mp3", False, False, "bad"),
]

LYRICS = """[ti:{title}]
[ar:{artist}]
[al:{album}]
[by:MusicOrganizer Demo]
[00:00.00]（演示歌词 · 非官方文本）
[00:06.20]{title}
[00:11.50]演唱：{artist}
[00:17.80]夜色落在安静的窗台
[00:24.10]旧唱片转着当年的对白
[00:30.40]我把心事写成一行行
[00:36.70]等一阵风把它吹向远方
[00:43.00]如果时间可以慢一点
[00:49.30]让我再听一遍那年的夏天
[00:55.60]灯光熄灭之前
[01:01.90]请把这首歌留在耳边
[01:08.20]La la la la…
[01:14.50]（间奏）
[01:20.80]夜色落在安静的窗台
[01:27.10]旧唱片转着当年的对白
[01:33.40]我把心事写成一行行
[01:39.70]等一阵风把它吹向远方
[01:46.00]如果时间可以慢一点
[01:52.30]让我再听一遍那年的夏天
[01:58.60]灯光熄灭之前
[02:04.90]请把这首歌留在耳边
[02:11.20]— 完 —
"""

PALETTES = [
    ((28, 32, 68), (124, 77, 255), (0, 229, 255)),
    ((36, 20, 52), (233, 30, 99), (255, 152, 0)),
    ((12, 40, 44), (0, 150, 136), (255, 235, 59)),
    ((52, 24, 20), (255, 87, 34), (255, 193, 7)),
    ((18, 28, 56), (63, 81, 181), (139, 195, 74)),
    ((40, 16, 40), (156, 39, 176), (255, 64, 129)),
]


def make_cover(title: str, artist: str, size: int = 900) -> bytes:
    seed = int(hashlib.sha1(f"{title}{artist}".encode("utf-8")).hexdigest()[:8], 16)
    palette = PALETTES[seed % len(PALETTES)]
    img = Image.new("RGB", (size, size), palette[0])
    draw = ImageDraw.Draw(img)
    # 渐变
    for y in range(size):
        t = y / size
        color = tuple(int(palette[0][i] * (1 - t) + palette[1][i] * t) for i in range(3))
        draw.line([(0, y), (size, y)], fill=color)
    # 抽象圆环
    for index in range(5):
        r = int(size * (0.18 + index * 0.11))
        cx = int(size * (0.5 + 0.28 * math.sin(seed * 0.01 + index)))
        cy = int(size * (0.5 + 0.28 * math.cos(seed * 0.013 + index)))
        box = [cx - r, cy - r, cx + r, cy + r]
        draw.ellipse(box, outline=palette[2], width=max(2, int(size * 0.006)))
    # 大号首字母
    letters = "".join(ch for ch in (title or "?") if ch.isascii())[:2].upper() or "♪"
    try:
        from PIL import ImageFont

        font = None
        for name in ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"):
            try:
                font = ImageFont.truetype(name, int(size * 0.22))
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), letters, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]), letters, font=font, fill=(255, 255, 255))
    except Exception:
        pass
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()


def make_audio(target: Path, seconds: int, tone: int, fmt: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    codec = ["-c:a", "flac", "-sample_fmt", "s16"] if fmt == "flac" else ["-c:a", "libmp3lame", "-b:a", "320k"]
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={tone}:duration={min(seconds, 30)}",
        *codec,
        str(target),
    ]
    subprocess.run(cmd, check=True)


def write_tags(target: Path, title: str, artist: str, album: str, cover: bytes | None, lyrics: str | None, bad: bool) -> None:
    if target.suffix.lower() == ".flac":
        audio = FLAC(str(target))
        audio["TITLE"] = title
        if artist:
            audio["ARTIST"] = artist
        if album and album != "kuwo":
            audio["ALBUM"] = album
        if bad:
            audio["ALBUM"] = "kuwo"
        if lyrics:
            audio["LYRICS"] = lyrics
            audio["UNSYNCEDLYRICS"] = lyrics
        if cover:
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.desc = "Cover"
            pic.width = 900
            pic.height = 900
            pic.depth = 24
            pic.data = cover
            audio.add_picture(pic)
        audio.save()
    else:
        try:
            tags = ID3(str(target))
        except Exception:
            tags = ID3()
        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text=artist or ""))
        tags.add(TALB(encoding=3, text="kuwo" if bad else album))
        if cover:
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
        if lyrics:
            tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
        tags.save(str(target), v2_version=3)


def build(root: Path, force: bool = False) -> None:
    music = root / "music"
    if music.exists() and any(music.iterdir()):
        if not force:
            print(f"目录已存在：{music}（用 --force 覆盖）")
            return
        shutil.rmtree(music)
    music.mkdir(parents=True, exist_ok=True)
    for index, (folder, title, artist, album, seconds, tone, fmt, with_cover, with_lyrics, note) in enumerate(TRACKS, start=1):
        safe_title = title.replace("/", "_")
        filename = f"{safe_title} - {artist}.{fmt}" if artist else f"{safe_title}.{fmt}"
        target = music / folder / filename
        if target.exists():
            continue
        make_audio(target, seconds, tone, fmt)
        cover = make_cover(title, artist) if with_cover else None
        lyrics = LYRICS.format(title=title, artist=artist or "佚名", album=album) if with_lyrics else None
        write_tags(target, title, artist, album, cover, lyrics, bad=(note == "bad"))
        print(f"  [{index:02d}] {target.relative_to(root)}  cover={'Y' if cover else '-'} lyrics={'Y' if lyrics else '-'}")
    # 一首外挂歌词 + 一首外挂封面，覆盖“文件级”场景
    sidecar = music / "其他" / "STAY - The Kid LAROI & Justin Bieber.lrc"
    sidecar.write_text(
        LYRICS.format(title="STAY", artist="The Kid LAROI & Justin Bieber", album="F*CK LOVE"),
        encoding="utf-8",
    )
    print(f"\n完成：{music}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成本地音乐整理插件的演示曲库")
    parser.add_argument("target", nargs="?", default="demo", help="输出目录（会创建 music 子目录）")
    parser.add_argument("--force", action="store_true", help="覆盖已有目录")
    args = parser.parse_args(argv)
    if not shutil.which("ffmpeg"):
        print("未找到 ffmpeg，请先安装（apt install ffmpeg / winget install ffmpeg）", file=sys.stderr)
        return 1
    root = Path(args.target).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    build(root, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
