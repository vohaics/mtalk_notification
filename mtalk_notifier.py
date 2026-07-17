"""Simplest possible MTalk notifier for Windows 11.

Watches for new unread messages in a target chat room and a target account
using Windows UI Automation, plays warning.mp3, and shows a small popup
with a single Mute button whenever a new message arrives.

Run:    python mtalk_notifier.py
Deps:   pip install uiautomation
Files:  put warning.mp3 next to this script.
"""

from __future__ import annotations

import ctypes
import re
import tkinter as tk
from pathlib import Path
from tkinter import ttk


# --- what to watch ----------------------------------------------------------
ROOM = "NOC Internal (Handover)"
ACCOUNT = "GNMC"

POLL_MS = 1000
SOUND_FILE = Path(__file__).with_name("warning.mp3")

# --- UI Automation (optional; app still runs without it) --------------------
try:
    import uiautomation as auto  # type: ignore
except Exception:
    auto = None

# --- sound: use MCI (built into Windows) so we need no extra pip deps -------
try:
    _MCI = ctypes.windll.winmm  # type: ignore[attr-defined]
except (AttributeError, OSError):
    _MCI = None


def play_sound() -> None:
    if _MCI is None or not SOUND_FILE.exists():
        return
    _MCI.mciSendStringW("close mtalk_snd", None, 0, 0)
    _MCI.mciSendStringW(
        f'open "{SOUND_FILE}" type mpegvideo alias mtalk_snd', None, 0, 0
    )
    _MCI.mciSendStringW("play mtalk_snd", None, 0, 0)


def stop_sound() -> None:
    if _MCI is None:
        return
    _MCI.mciSendStringW("close mtalk_snd", None, 0, 0)


# --- UI Automation helpers --------------------------------------------------
def find_mtalk_window():
    if auto is None:
        return None
    try:
        for w in auto.GetRootControl().GetChildren():
            name = w.Name or ""
            if "MTalk" in name or "mTalk" in name or "\uc5e0\ud1a1" in name:
                return w
    except Exception:
        pass
    return None


_COUNT_RE = re.compile(r"(?:\((\d+)\+?\)|\s(\d+)\+?)\s*$")


def unread_count(root, target: str) -> int:
    """Return current unread count for `target` under `root`. 0 if not found."""
    target_lower = target.lower()
    best = 0
    stack = [root]
    visited = 0
    while stack and visited < 2000:
        node = stack.pop()
        visited += 1
        try:
            name = node.Name or ""
        except Exception:
            name = ""
        if name and target_lower in name.lower():
            m = _COUNT_RE.search(name)
            if m:
                v = int(m.group(1) or m.group(2))
                if v > best:
                    best = v
            try:
                parent = node.GetParentControl()
                if parent is not None:
                    for sib in parent.GetChildren():
                        try:
                            s = (sib.Name or "").strip().rstrip("+")
                        except Exception:
                            s = ""
                        if s.isdigit() and len(s) <= 4:
                            v = int(s)
                            if v > best:
                                best = v
            except Exception:
                pass
        try:
            for c in node.GetChildren():
                stack.append(c)
        except Exception:
            pass
    return best


# --- app --------------------------------------------------------------------
class App:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("MTalk Notifier")
        self.root.geometry("320x170")
        self.root.resizable(False, False)

        self.monitoring = False
        self.popup: tk.Toplevel | None = None
        self.last_counts: dict[str, int] = {}

        ttk.Label(
            self.root, text="MTalk Notifier", font=("Segoe UI", 12, "bold")
        ).pack(pady=(16, 4))
        self.status = tk.StringVar(value="Stopped")
        ttk.Label(self.root, textvariable=self.status, foreground="#666").pack()

        row = ttk.Frame(self.root)
        row.pack(pady=16)
        self.start_btn = ttk.Button(row, text="Start Monitoring", command=self.start)
        self.stop_btn = ttk.Button(
            row, text="Stop Monitoring", command=self.stop, state="disabled"
        )
        self.exit_btn = ttk.Button(row, text="Exit", command=self.exit_app)
        for i, b in enumerate([self.start_btn, self.stop_btn, self.exit_btn]):
            b.grid(row=0, column=i, padx=4)

        self.root.protocol("WM_DELETE_WINDOW", self.exit_app)

    # ---------- main-window buttons ----------
    def start(self) -> None:
        if self.monitoring:
            return
        self.monitoring = True
        self.status.set("Monitoring...")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        # Reset baselines so pre-existing unreads never fire on Start.
        self.last_counts = {}
        self.root.after(POLL_MS, self.tick)

    def stop(self) -> None:
        if not self.monitoring:
            return
        self.monitoring = False
        self.status.set("Stopped")
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        # Also silence any in-flight alert.
        stop_sound()
        if self.popup is not None:
            try:
                self.popup.destroy()
            except tk.TclError:
                pass
            self.popup = None

    def exit_app(self) -> None:
        self.monitoring = False
        stop_sound()
        if self.popup is not None:
            try:
                self.popup.destroy()
            except tk.TclError:
                pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # ---------- polling loop ----------
    def tick(self) -> None:
        if not self.monitoring:
            return
        try:
            self.check_once()
        except Exception:
            pass  # never let a scan error break the loop
        if self.monitoring:
            self.root.after(POLL_MS, self.tick)

    def check_once(self) -> None:
        window = find_mtalk_window()
        if window is None:
            return
        for target in (ROOM, ACCOUNT):
            count = unread_count(window, target)
            last = self.last_counts.get(target)
            self.last_counts[target] = count
            # Alert only on strict increase. First observation seeds silently.
            # Since we always update last_counts to `count`, Mute naturally
            # "ignores the current unread message": the next alert can only
            # fire when a new message pushes the count above `count`.
            if last is not None and count > last:
                self.alert(target)

    # ---------- alert popup ----------
    def alert(self, target: str) -> None:
        play_sound()
        if self.popup is not None:
            try:
                self.popup.destroy()
            except tk.TclError:
                pass
        p = tk.Toplevel(self.root)
        self.popup = p
        p.title("MTalk")
        p.geometry("300x150")
        p.resizable(False, False)
        p.attributes("-topmost", True)
        ttk.Label(
            p, text="\U0001F514  New MTalk Message", font=("Segoe UI", 11, "bold")
        ).pack(pady=(16, 6))
        ttk.Label(
            p, text=target, font=("Segoe UI", 10, "bold"), foreground="#0b6"
        ).pack()
        ttk.Button(p, text="Mute", width=12, command=self.mute).pack(pady=14)
        p.protocol("WM_DELETE_WINDOW", self.mute)

    def mute(self) -> None:
        stop_sound()
        if self.popup is not None:
            try:
                self.popup.destroy()
            except tk.TclError:
                pass
            self.popup = None


if __name__ == "__main__":
    App().root.mainloop()
