# -*- mode: python ; coding: utf-8 -*-
import shutil
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
FFMPEG = Path(r"C:\Users\17076\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe")

datas = [
    (str(ROOT / "music_organizer" / "web" / "templates"), "music_organizer/web/templates"),
    (str(ROOT / "music_organizer" / "web" / "static"), "music_organizer/web/static"),
]
binaries = []
hiddenimports = [
    "waitress",
    "mutagen",
    "mutagen.flac",
    "mutagen.mp3",
    "mutagen.mp4",
    "mutagen.ogg",
    "mutagen.wave",
    "PIL.Image",
    "PIL.ImageDraw",
]
for package in ("zhconv",):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

if FFMPEG.is_file():
    binaries.append((str(FFMPEG), "."))
else:
    print("WARNING: ffmpeg.exe not found; conversion/transcoding disabled")

a = Analysis(
    [str(ROOT / "desktop" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ShiyinMusicOrganizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "desktop" / "app.ico"),
    version=str(ROOT / "desktop" / "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ShiyinMusicOrganizer",
)
