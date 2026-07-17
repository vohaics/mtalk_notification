"""Tkinter popup that shows a single 'new MTalk message' alert with Mute button.

Design (per spec):

    ---------------------------------------
    New MTalk Message

    Room:
    NOC Internal (Handover)

                [ Mute ]
    ---------------------------------------

Only one popup is visible at a time. If a new alert arrives while a popup
is already open, the popup content is refreshed instead of stacking.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from .config import PopupConfig, Target


log = logging.getLogger(__name__)


class AlertPopup:
    def __init__(
        self,
        root: tk.Tk,
        cfg: PopupConfig,
        on_mute: Callable[[], None],
    ) -> None:
        self._root = root
        self._cfg = cfg
        self._on_mute = on_mute
        self._window: Optional[tk.Toplevel] = None
        self._room_var: Optional[tk.StringVar] = None
        self._kind_var: Optional[tk.StringVar] = None

    # ------------------------------------------------------------------ public

    def show(self, target: Target) -> None:
        """Show the popup for `target`. Refreshes an already-open popup."""
        if self._window is not None and self._window.winfo_exists():
            self._update_content(target)
            self._raise_window()
            return
        self._build_window(target)

    def close(self) -> None:
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None

    # ---------------------------------------------------------------- internal

    def _build_window(self, target: Target) -> None:
        w = tk.Toplevel(self._root)
        w.title("MTalk Notifier")
        w.resizable(False, False)

        cw, ch = self._cfg.width, self._cfg.height
        w.geometry(f"{cw}x{ch}")
        self._center_bottom_right(w, cw, ch)

        if self._cfg.always_on_top:
            w.attributes("-topmost", True)

        try:
            w.attributes("-toolwindow", True)  # no minimize/maximize on Windows
        except tk.TclError:
            pass

        w.protocol("WM_DELETE_WINDOW", self._handle_mute)

        container = ttk.Frame(w, padding=(16, 14, 16, 14))
        container.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(
            container,
            text="\U0001F514  New MTalk Message",
            font=("Segoe UI", 12, "bold"),
        )
        title.pack(anchor="w")

        ttk.Separator(container, orient="horizontal").pack(fill=tk.X, pady=(6, 8))

        self._kind_var = tk.StringVar(value=self._kind_label(target))
        kind_lbl = ttk.Label(container, textvariable=self._kind_var, font=("Segoe UI", 9))
        kind_lbl.pack(anchor="w")

        self._room_var = tk.StringVar(value=target.display)
        room_lbl = ttk.Label(
            container,
            textvariable=self._room_var,
            font=("Segoe UI", 11, "bold"),
            foreground="#0b6",
            wraplength=cw - 40,
            justify="left",
        )
        room_lbl.pack(anchor="w", pady=(2, 12))

        btn_row = ttk.Frame(container)
        btn_row.pack(fill=tk.X, side=tk.BOTTOM)
        mute_btn = ttk.Button(btn_row, text="Mute", width=12, command=self._handle_mute)
        mute_btn.pack(side=tk.RIGHT)
        mute_btn.focus_set()
        w.bind("<Return>", lambda _e: self._handle_mute())
        w.bind("<Escape>", lambda _e: self._handle_mute())

        self._window = w
        self._raise_window()

    def _update_content(self, target: Target) -> None:
        if self._kind_var is not None:
            self._kind_var.set(self._kind_label(target))
        if self._room_var is not None:
            self._room_var.set(target.display)

    def _raise_window(self) -> None:
        if self._window is None:
            return
        try:
            self._window.deiconify()
            self._window.lift()
            self._window.focus_force()
        except tk.TclError:
            pass

    def _center_bottom_right(self, w: tk.Toplevel, cw: int, ch: int) -> None:
        """Anchor the popup near the Windows taskbar (bottom-right corner)."""
        try:
            w.update_idletasks()
            sw = w.winfo_screenwidth()
            sh = w.winfo_screenheight()
            margin = 24
            x = max(0, sw - cw - margin)
            y = max(0, sh - ch - margin - 40)  # leave room for taskbar
            w.geometry(f"{cw}x{ch}+{x}+{y}")
        except tk.TclError:
            pass

    def _handle_mute(self) -> None:
        log.info("Mute button pressed; stopping sound and closing popup")
        try:
            self._on_mute()
        finally:
            self.close()

    @staticmethod
    def _kind_label(target: Target) -> str:
        if target.kind == "account":
            return "Account:"
        return "Room:"
