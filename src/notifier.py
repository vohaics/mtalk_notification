"""Coordinator: wires detector, sound and popup together on the Tk main thread."""

from __future__ import annotations

import logging
import queue
import tkinter as tk
from typing import Optional

from .config import AppConfig
from .detector import DetectedMessage, MTalkDetector
from .popup import AlertPopup
from .sound_player import SoundPlayer


log = logging.getLogger(__name__)


class Notifier:
    """Owns the Tk root, detector, sound player, and popup lifecycle."""

    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self._root = tk.Tk()
        self._root.withdraw()  # no main window; only the popup is visible

        try:
            self._root.iconify()
        except tk.TclError:
            pass

        self._sound = SoundPlayer(cfg.sound_file)
        self._popup = AlertPopup(self._root, cfg.popup, on_mute=self._on_mute)
        self._queue: "queue.Queue[DetectedMessage]" = queue.Queue()

        self._detector = MTalkDetector(cfg, on_new_message=self._enqueue)

        # Ensure clean shutdown from Alt+F4 or system signal
        self._root.protocol("WM_DELETE_WINDOW", self._shutdown)

    # -------------------------------------------------------------- lifecycle

    def run(self) -> None:
        log.info(
            "Starting notifier: targets=%s, poll=%.2fs, sound=%s",
            [t.key for t in self._cfg.targets],
            self._cfg.poll_interval_seconds,
            self._cfg.sound_file,
        )
        self._detector.start()
        self._root.after(100, self._drain_queue)
        try:
            self._root.mainloop()
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        log.info("Shutting down notifier")
        try:
            self._detector.stop()
        except Exception:
            log.exception("Error stopping detector")
        try:
            self._sound.shutdown()
        except Exception:
            log.exception("Error shutting down sound player")
        try:
            self._popup.close()
        except Exception:
            pass
        try:
            self._root.quit()
            self._root.destroy()
        except Exception:
            pass

    # ------------------------------------------------------ event marshalling

    def _enqueue(self, msg: DetectedMessage) -> None:
        """Called from the detector thread; forwards to the Tk main thread."""
        self._queue.put(msg)

    def _drain_queue(self) -> None:
        try:
            while True:
                msg = self._queue.get_nowait()
                self._handle_new_message(msg)
        except queue.Empty:
            pass
        finally:
            self._root.after(100, self._drain_queue)

    # ---------------------------------------------------------------- actions

    def _handle_new_message(self, msg: DetectedMessage) -> None:
        log.info(
            "NEW MESSAGE detected -> %s '%s' (unread=%d) - playing sound + popup",
            msg.target.kind,
            msg.target.display,
            msg.count,
        )
        # Sound: play once (spec says play immediately, mute stops it).
        # We use a single-shot play so audio ends on its own if the user
        # never touches the popup, but Mute can still cut it short.
        self._sound.play(loops=0)
        self._popup.show(msg.target)

    def _on_mute(self) -> None:
        log.info("User pressed Mute")
        self._sound.stop()
