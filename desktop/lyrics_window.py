"""独立桌面歌词窗口：无边框、置顶、可拖动，独立于主程序。"""

from __future__ import annotations

import json
import os
import threading
import time
import tkinter as tk
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8765"
STATE_URL = BASE + "/api/desktop/lyrics-state"
TOGGLE_URL = BASE + "/api/desktop/lyrics-toggle"
COMMAND_URL = BASE + "/api/desktop/lyrics-command"


def _config_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MusicOrganizer"
    root.mkdir(parents=True, exist_ok=True)
    return root / "desktop-lyrics.json"


def _post(url: str, body: dict | None = None) -> dict | None:
    try:
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=1.2) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def _get(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=1.2) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


class LyricsWindow:
    def __init__(self) -> None:
        self.config = self._load_config()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", 0.94)
        except tk.TclError:
            pass
        self.root.configure(bg="#0d1220")
        width = 560
        height = 168
        x = int(self.config.get("x", 120))
        y = int(self.config.get("y", 120))
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.locked = bool(self.config.get("locked", False))
        self.font_size = int(self.config.get("font_size", 26))
        self.visible = False
        self.health_failures = 0
        self.fade_timer: str | None = None
        self.drag = None
        self._build()
        self._poll()
        self.root.after(800, self._keep_topmost)

    def _load_config(self) -> dict:
        try:
            return json.loads(_config_path().read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_config(self) -> None:
        try:
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            _config_path().write_text(
                json.dumps(
                    {
                        "x": x,
                        "y": y,
                        "locked": self.locked,
                        "font_size": self.font_size,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _build(self) -> None:
        self.panel = tk.Frame(self.root, bg="#111827", highlightthickness=1, highlightbackground="#2d3b5c")
        self.panel.pack(fill="both", expand=True, padx=3, pady=3)

        self.close_btn = tk.Button(
            self.panel, text="×", command=self._close, bg="#1c2740", fg="#e8edff",
            activebackground="#334155", activeforeground="#ffffff", relief="flat",
            font=("Microsoft YaHei UI", 12, "bold"), width=2, bd=0, cursor="hand2",
        )
        self.close_btn.place(relx=1.0, y=6, x=-8, anchor="ne")
        self.close_btn.lift()

        self.controls = tk.Frame(self.panel, bg="#111827")
        self.controls.pack(fill="x", padx=14, pady=(8, 0))
        self._button("⏮", "prev", 3)
        self.play_btn = self._button("▶", "toggle", 3)
        self._button("⏭", "next", 3)
        tk.Label(self.controls, text="", bg="#111827", width=1).pack(side="left")
        self.lock_btn = tk.Button(
            self.controls, text="🔒" if self.locked else "🔓", command=self._toggle_lock,
            bg="#1c2740", fg="#e8edff", activebackground="#334155", relief="flat",
            font=("Microsoft YaHei UI", 10, "bold"), width=3, height=1, bd=0, cursor="hand2",
        )
        self.lock_btn.pack(side="left", padx=3)
        self._button("A-", "font_down", 3)
        self._button("A+", "font_up", 3)

        self.next_label = tk.Label(
            self.panel, text="", bg="#111827", fg="#9aa7c7",
            font=("Microsoft YaHei UI", max(11, int(self.font_size * 0.52))), anchor="w",
        )
        self.next_label.pack(fill="x", padx=18, pady=(5, 0))
        self.current_label = tk.Label(
            self.panel, text="♪ 等待播放", bg="#111827", fg="#fff6cf",
            font=("Microsoft YaHei UI", self.font_size, "bold"), anchor="w",
        )
        self.current_label.pack(fill="x", padx=18, pady=(2, 6))
        self.close_btn.place(relx=1.0, y=6, x=-8, anchor="ne")
        self.close_btn.lift()

        for widget in (self.panel, self.current_label, self.next_label):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)
        self.panel.bind("<Enter>", lambda _e: self._wake())
        self.panel.bind("<Leave>", lambda _e: self._arm_fade())

    def _button(self, text: str, action: str, width: int) -> tk.Button:
        button = tk.Button(
            self.controls, text=text, command=lambda a=action: self._action(a),
            bg="#1c2740", fg="#e8edff", activebackground="#334155", activeforeground="#fff",
            relief="flat", font=("Microsoft YaHei UI", 11, "bold"), width=width,
            height=1, bd=0, cursor="hand2",
        )
        button.pack(side="left", padx=3)
        return button

    def _drag_start(self, event) -> None:
        if self.locked:
            return
        self.drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_move(self, event) -> None:
        if self.locked or not self.drag:
            return
        self.root.geometry(f"+{event.x_root - self.drag[0]}+{event.y_root - self.drag[1]}")

    def _drag_end(self, _event) -> None:
        self.drag = None
        self._save_config()

    def _close(self) -> None:
        _post(TOGGLE_URL, {"visible": False})
        self.visible = False
        self.root.withdraw()

    def _toggle_lock(self) -> None:
        self.locked = not self.locked
        self.lock_btn.configure(text="🔒" if self.locked else "🔓")
        self._save_config()

    def _action(self, action: str) -> None:
        if action == "font_down":
            self.font_size = max(16, self.font_size - 2)
            self.current_label.configure(font=("Microsoft YaHei UI", self.font_size, "bold"))
            self.next_label.configure(font=("Microsoft YaHei UI", max(11, int(self.font_size * 0.52))))
            self._save_config()
            return
        if action == "font_up":
            self.font_size = min(48, self.font_size + 2)
            self.current_label.configure(font=("Microsoft YaHei UI", self.font_size, "bold"))
            self.next_label.configure(font=("Microsoft YaHei UI", max(11, int(self.font_size * 0.52))))
            self._save_config()
            return
        _post(COMMAND_URL, {"action": action})

    def _wake(self) -> None:
        if self.fade_timer:
            try:
                self.root.after_cancel(self.fade_timer)
            except Exception:
                pass
            self.fade_timer = None
        if not self.controls.winfo_ismapped():
            self.controls.pack(fill="x", padx=14, pady=(8, 0), before=self.next_label)
        self.close_btn.place(relx=1.0, y=6, x=-8, anchor="ne")
        self.close_btn.lift()

    def _arm_fade(self) -> None:
        if self.locked:
            return
        if self.fade_timer:
            try:
                self.root.after_cancel(self.fade_timer)
            except Exception:
                pass
        self.fade_timer = self.root.after(3000, self._fade)

    def _fade(self) -> None:
        self.fade_timer = None
        if self.locked:
            return
        self.controls.pack_forget()
        self.close_btn.lift()

    def _keep_topmost(self) -> None:
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.root.after(1200, self._keep_topmost)

    def _poll(self) -> None:
        state = _get(STATE_URL)
        if state is None:
            self.health_failures += 1
            if self.health_failures > 12:
                self.root.destroy()
                return
        else:
            self.health_failures = 0
            visible = bool(state.get("visible"))
            if visible and not self.visible:
                self.visible = True
                self.root.deiconify()
                self.root.attributes("-topmost", True)
            elif not visible and self.visible:
                self.visible = False
                self.root.withdraw()
            current = str(state.get("current") or ("♪ 欣赏纯音乐" if not state.get("playing") else "♪ 等待播放"))
            following = str(state.get("next") or "")
            if self.current_label.cget("text") != current:
                self.current_label.configure(text=current)
            if self.next_label.cget("text") != following:
                self.next_label.configure(text=following)
            self.play_btn.configure(text="Ⅱ" if state.get("playing") else "▶")
        self.root.after(280, self._poll)

    def run(self) -> int:
        self.root.mainloop()
        return 0


def main() -> int:
    return LyricsWindow().run()


if __name__ == "__main__":
    raise SystemExit(main())
