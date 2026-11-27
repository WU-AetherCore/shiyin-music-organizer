"""拾音 · Windows 桌面启动器。

使用系统 Microsoft Edge 的 --app 应用窗口承载现有 Flask UI，
界面与网页版完全一致，不启动 Electron；后端只监听 127.0.0.1。
"""

from __future__ import annotations

import ctypes
import json
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
APP_VERSION = "1.3.41"
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
        if pid.value != process_id or not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            text = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, text, length + 1)
            if is_app_window_title(text.value):
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


def is_app_window_title(text: str) -> bool:
    if "本地音乐整理小插件" not in text:
        return False
    lower = text.lower()
    # 普通 Edge / Chrome 浏览器标签页也可能包含这个标题，必须排除。
    return (
        "microsoft edge" not in lower
        and "google chrome" not in lower
        and "mozilla firefox" not in lower
        and " - 个人" not in text
        and "和另外" not in text
    )


def find_app_window(process_id: int):
    """优先按 Edge 主进程找，找不到时按拾音应用窗口标题兜底。"""
    handles = find_edge_window(process_id)
    if handles:
        return handles
    found = []
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        text = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, text, length + 1)
        if is_app_window_title(text.value) or text.value.strip() == "拾音":
            found.append(hwnd)
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return found


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("showCmd", ctypes.c_uint),
        ("ptMinPosition", ctypes.c_long * 2),
        ("ptMaxPosition", ctypes.c_long * 2),
        ("rcNormalPosition", ctypes.c_long * 4),
    ]


def window_state_path() -> Path:
    return data_root() / "window-state.json"


def load_window_state() -> dict:
    try:
        raw = json.loads(window_state_path().read_text(encoding="utf-8"))
        width = max(900, min(6000, int(raw.get("width", 1440))))
        height = max(650, min(4000, int(raw.get("height", 900))))
        return {
            "x": int(raw.get("x", 120)),
            "y": int(raw.get("y", 60)),
            "width": width,
            "height": height,
            "maximized": bool(raw.get("maximized", False)),
        }
    except Exception:
        return {"x": 120, "y": 60, "width": 1440, "height": 900, "maximized": False}


def read_window_snapshot(process_id: int):
    handles = find_app_window(process_id)
    if not handles:
        return None
    hwnd = handles[0]
    placement = WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(WINDOWPLACEMENT)
    if not ctypes.windll.user32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
        return None
    left, top, right, bottom = placement.rcNormalPosition
    width, height = right - left, bottom - top
    if width < 500 or height < 400:
        return None
    return {
        "x": left,
        "y": top,
        "width": width,
        "height": height,
        "maximized": placement.showCmd == 3,
    }


def save_window_state(state: dict) -> None:
    try:
        path = window_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    except Exception:
        log("save window state failed\n" + traceback.format_exc())


def stop_app_edge(profile: Path) -> None:
    """只结束使用拾音专用配置目录的 Edge 进程，不影响普通浏览器。"""
    try:
        escaped = str(profile).replace("'", "''")
        script = (
            "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{escaped}*' }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,
            timeout=10,
        )
    except Exception:
        log("stop app edge failed\n" + traceback.format_exc())


def focus_existing_window() -> bool:
    handles = find_app_window(0)
    if not handles:
        return False
    user32 = ctypes.windll.user32
    hwnd = handles[0]
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    return True


def kill_other_instances() -> None:
    """清理没有可见窗口、但仍占着后端/单实例锁的旧进程。"""
    try:
        script = (
            "$self = " + str(os.getpid()) + "; "
            "Get-Process -Name ShiyinMusicOrganizer -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Id -ne $self } | "
            "Stop-Process -Force -ErrorAction SilentlyContinue"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,
            timeout=10,
        )
    except Exception:
        log("kill stale instances failed\n" + traceback.format_exc())


def close_stale_windows() -> None:
    """关闭旧版本 window.open 留下的桌面歌词浏览器窗口。"""
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        text = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, text, length + 1)
        title = text.value.strip()
        if title == "127.0.0.1:8765" or title.startswith("127.0.0.1:8765 "):
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        return True

    user32.EnumWindows(callback_type(callback), 0)


def start_desktop_lyrics_helper() -> None:
    """启动独立置顶歌词窗口进程。"""
    try:
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--desktop-lyrics"]
        else:
            cmd = [sys.executable, str(Path(__file__).resolve()), "--desktop-lyrics"]
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,
        )
        log("desktop lyrics helper started")
    except Exception:
        log("desktop lyrics helper failed\n" + traceback.format_exc())


def run() -> int:
    close_stale_windows()
    if not single_instance():
        if focus_existing_window():
            log("existing window focused")
            return 0
        log("stale instance without window; terminating it")
        kill_other_instances()
        time.sleep(1.0)
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
    start_desktop_lyrics_helper()

    edge = find_edge()
    if not edge:
        message_box("没有找到 Microsoft Edge，请安装 Edge 或使用 Web 版。", error=True)
        server.close()
        return 1

    override_profile = os.environ.get("SHIYIN_EDGE_PROFILE", "").strip()
    if override_profile:
        profile = Path(override_profile)
    else:
        profile = Path(os.environ.get("LOCALAPPDATA") or data_root()) / "MusicOrganizer" / "edge-profile-v2"
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError:
        log("profile mkdir failed\n" + traceback.format_exc())
        message_box("无法创建浏览器配置目录，请检查磁盘权限：\n" + str(profile), error=True)
        server.close()
        return 1
    window_state = load_window_state()
    log("window state " + json.dumps(window_state, ensure_ascii=False))
    args = [
        edge,
        f"--app={url}/?desktop={APP_VERSION}-{int(time.time())}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
        "--disable-sync",
        "--force-dark-mode",
        "--no-service-autorun",
        "--disable-features=Translate,MediaRouter,OptimizationHints,msEdgeIdentity,msEdgeSync,EdgeSync",
        f"--window-size={window_state['width']},{window_state['height']}",
        f"--window-position={window_state['x']},{window_state['y']}",
    ]
    if window_state.get("maximized"):
        args.append("--start-maximized")
    log("edge launch " + " ".join(args))
    try:
        proc = subprocess.Popen(args)
        app.config["MORG_DESKTOP_CLOSE"] = lambda: subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,
        )
        last_window = {"state": None}
        stop_watch = threading.Event()

        def watch_window() -> None:
            while not stop_watch.is_set():
                snapshot = read_window_snapshot(proc.pid)
                if snapshot:
                    last_window["state"] = snapshot
                time.sleep(0.5)

        threading.Thread(target=watch_window, name="shiyin-window", daemon=True).start()
        darken_titlebar(proc.pid)
    except Exception:
        log("edge launch failed\n" + traceback.format_exc())
        message_box("无法启动 Edge 应用窗口，详情：" + str(data_root() / "logs" / "desktop.log"), error=True)
        server.close()
        return 1

    # Edge 可能把窗口交给同 profile 的主进程后自身退出。
    # 不能只等 Popen 进程，否则启动约十几秒后会把窗口和后端一起关掉。
    started = time.time()
    seen_window = False
    missing_since = None
    while True:
        handles = find_app_window(proc.pid)
        if handles:
            seen_window = True
            missing_since = None
        else:
            if seen_window:
                if missing_since is None:
                    missing_since = time.time()
                elif time.time() - missing_since > 3.0:
                    break
            elif proc.poll() is not None and time.time() - started > 20.0:
                break
            elif time.time() - started > 45.0 and not seen_window:
                break
        time.sleep(0.4)

    stop_watch.set()
    stop_app_edge(profile)
    final_state = last_window.get("state")
    if final_state:
        save_window_state(final_state)
        log("window state saved " + json.dumps(final_state, ensure_ascii=False))
    log("edge window closed")
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
    if "--desktop-lyrics" in sys.argv:
        try:
            from lyrics_window import main as lyrics_main

            return lyrics_main()
        except Exception:
            log("desktop lyrics window crash\n" + traceback.format_exc())
            return 1
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
