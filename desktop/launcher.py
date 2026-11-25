"""拾音 · Windows 桌面启动器。

使用系统 Microsoft Edge 的 --app 应用窗口承载现有 Flask UI，
界面与网页版完全一致，不启动 Electron；后端只监听 127.0.0.1。
"""

from __future__ import annotations

import ctypes
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

APP_NAME = "拾音"
MUTEX_NAME = "Local\\ShiyinMusicOrganizerDesktop"
APP_VERSION = "1.3.40"
FIXED_PORT = 8765

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def executable_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def message_box(text: str, title: str = APP_NAME, error: bool = False) -> None:
    try:
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    except Exception:
        print(text)


def data_root() -> Path:
    override = os.environ.get("MORG_HOME", "").strip()
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "MusicOrganizer"


def log(message: str) -> None:
    try:
        path = data_root() / "logs" / "desktop.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass


def ensure_ffmpeg() -> None:
    for base in (resource_dir(), executable_dir()):
        candidate = base / "ffmpeg.exe"
        if candidate.is_file():
            os.environ["PATH"] = str(candidate.parent) + os.pathsep + os.environ.get("PATH", "")
            return


def single_instance() -> bool:
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return True
        if kernel32.GetLastError() == 183:
            return False
        return True
    except Exception:
        return True


def wait_for_server(url: str, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=0.7) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def find_edge() -> str:
    command = shutil.which("msedge")
    if command:
        return command
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


def find_edge_window(process_id: int):
    handles = []
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _lparam):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == process_id and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            handles.append(hwnd)
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return handles


def darken_titlebar(process_id: int, timeout: float = 12.0) -> bool:
    """把 Edge 应用窗口的 Windows 标题栏改成与软件背景一致的深色。"""
    deadline = time.time() + timeout
    applied = False
    while time.time() < deadline:
        for hwnd in find_edge_window(process_id):
            try:
                dwm = ctypes.windll.dwmapi
                enabled = ctypes.c_int(1)
                dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
                # Win11: 35 = caption color, 36 = text color, 34 = border color (COLORREF BGR)
                caption = ctypes.c_uint32(0x00140B07)   # #070b14
                text = ctypes.c_uint32(0x00F7F5FF)
                border = ctypes.c_uint32(0x00281A0B)
                dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
                dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))
                dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))
                applied = True
            except Exception:
                pass
        if applied:
            return True
        time.sleep(0.25)
    return False


def run() -> int:
    if not single_instance():
        message_box("拾音已经在运行，请切换到已打开的窗口。")
        return 0
    ensure_ffmpeg()
    log(f"launcher start version={APP_VERSION} frozen={getattr(sys, 'frozen', False)}")

    from waitress import create_server

    from music_organizer.config import Config
    from music_organizer.paths import get_paths
    from music_organizer.web.app import create_app

    paths = get_paths()
    paths.ensure()
    config = Config.load(paths)
    config.set("web.host", "127.0.0.1")
    app = create_app(config, paths, enable_automation=True)
    log("app created")

    port = FIXED_PORT
    server = create_server(
        app,
        host="127.0.0.1",
        port=port,
        threads=6,
        channel_timeout=300,
        cleanup_interval=30,
        connection_limit=80,
    )

    def serve() -> None:
        try:
            server.run()
        except Exception:
            log("server thread failed\n" + traceback.format_exc())

    threading.Thread(target=serve, name="shiyin-waitress", daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    if not wait_for_server(url):
        log("health check failed")
        message_box("拾音后端启动失败，详情：" + str(data_root() / "logs" / "desktop.log"), error=True)
        return 1
    log("backend ready")

    edge = find_edge()
    if not edge:
        message_box("没有找到 Microsoft Edge，请安装 Edge 或使用 Web 版。", error=True)
        server.close()
        return 1

    override_profile = os.environ.get("SHIYIN_EDGE_PROFILE", "").strip()
    if override_profile:
        profile = Path(override_profile)
    else:
        profile = Path(os.environ.get("LOCALAPPDATA") or data_root()) / "MusicOrganizer" / "edge-profile"
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError:
        log("profile mkdir failed\n" + traceback.format_exc())
        message_box("无法创建浏览器配置目录，请检查磁盘权限：\n" + str(profile), error=True)
        server.close()
        return 1
    args = [
        edge,
        f"--app={url}/?desktop=1",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
        "--disable-sync",
        "--force-dark-mode",
        "--kiosk",
        "--disable-features=Translate,MediaRouter,OptimizationHints,msEdgeIdentity,msEdgeSync,EdgeSync,Windows11CustomTitlebar,Windows10CustomTitlebar",
        "--no-service-autorun",
        "--window-size=1440,900",
        "--window-position=120,60",
    ]
    log("edge launch " + " ".join(args))
    try:
        proc = subprocess.Popen(args)
        app.config["MORG_DESKTOP_CLOSE"] = lambda: subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,
        )
        darken_titlebar(proc.pid)
    except Exception:
        log("edge launch failed\n" + traceback.format_exc())
        message_box("无法启动 Edge 应用窗口，详情：" + str(data_root() / "logs" / "desktop.log"), error=True)
        server.close()
        return 1

    # Edge 可能把窗口交给同 profile 的主进程，因此同时监听进程退出。
    try:
        proc.wait()
    except KeyboardInterrupt:
        pass
    log("edge closed")
    try:
        app.config["MORG_AUTOMATION"].stop()
    except Exception:
        pass
    try:
        server.close()
    except Exception:
        pass
    return 0


def main() -> int:
    log(f"desktop launcher start frozen={getattr(sys, 'frozen', False)} exe={sys.executable}")
    try:
        return run()
    except Exception:
        detail = traceback.format_exc()
        log(detail)
        message_box("拾音启动失败，详情已写入：\n" + str(data_root() / "logs" / "desktop.log") + "\n\n" + detail[-1200:], error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
