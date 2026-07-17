"""Coordinator: wires detector, sound and popup together on the Tk main thread.

Shutdown is delegated to a :class:`ShutdownCoordinator` passed in from
``main.py``. Each subsystem registers its own teardown callable, and the
coordinator invokes them in LIFO order with per-action timeouts and a
wall-clock watchdog. See :mod:`src.shutdown` for details.
"""

from __future__ import annotations

import logging
import queue
import tkinter as tk
from typing import Optional

from .config import AppConfig
from .detector import DetectedMessage, MTalkDetector
from .popup import AlertPopup
from .shutdown import ShutdownCoordinator
from .sound_player import SoundPlayer


log = logging.getLogger(__name__)


class Notifier:
    """Owns the Tk root, detector, sound player, and popup lifecycle."""

    def __init__(self, cfg: AppConfig, coordinator: ShutdownCoordinator) -> None:
        self._cfg = cfg
        self._coord = coordinator

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

        # Register subsystems in construction order. The coordinator runs them
        # in LIFO order (most recently created is torn down first):
        #   1. detector.stop         (signals thread, joins, releases UIA/COM)
        #   2. popup.close           (destroys Toplevel if open)
        #   3. sound.shutdown        (stops playback + quits pygame.mixer)
        #   4. self._destroy_tk_root (breaks mainloop and destroys Tk root)
        # main.py registers the stop-signal listener and PID file separately.
        self._coord.register("tk-root", self._destroy_tk_root, timeout_s=2.0)
        self._coord.register("sound", self._sound.shutdown, timeout_s=2.0)
        self._coord.register("popup", self._popup.close, timeout_s=1.0)
        self._coord.register("detector", self._detector.stop, timeout_s=4.0)

        # Alt+F4 / programmatic close of hidden root -> request shutdown.
        # We do NOT tear down here directly; we route through the coordinator
        # so every trigger path takes the same, ordered, idempotent path.
        self._root.protocol(
            "WM_DELETE_WINDOW",
            lambda: self._coord.trigger("WM_DELETE_WINDOW"),
        )

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
        self._root.after(200, self._check_shutdown_flag)
        try:
            self._root.mainloop()
        except KeyboardInterrupt:
            self._coord.trigger("KeyboardInterrupt in mainloop")
        finally:
            log.info("Tk mainloop exited")

    def _destroy_tk_root(self) -> None:
        """Break out of mainloop and destroy the Tk root. Idempotent."""
        root = getattr(self, "_root", None)
        if root is None:
            return
        try:
            # Close popup first if still open (belt-and-braces; popup.close is
            # already registered as its own coordinator action).
            self._popup.close()
        except Exception:
            pass
        try:
            root.quit()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass
        self._root = None  # type: ignore[assignment]

    # -------------------------------------------------- shutdown-flag polling

    def _check_shutdown_flag(self) -> None:
        """Poll the coordinator from the Tk main thread.

        Signal handlers and other threads only *request* shutdown by calling
        ``coordinator.trigger()``. The actual teardown must run on the Tk main
        thread so Tk resources are freed by their owning thread. This tiny
        200ms poll turns the async request into a synchronous main-thread
        exit of ``mainloop()``.
        """
        if self._coord.triggered:
            log.info(
                "Shutdown flag observed on Tk thread (reason=%s); exiting mainloop.",
                self._coord.reason,
            )
            root = getattr(self, "_root", None)
            if root is not None:
                try:
                    root.quit()
                except tk.TclError:
                    pass
            return
        root = getattr(self, "_root", None)
        if root is not None:
            try:
                root.after(200, self._check_shutdown_flag)
            except tk.TclError:
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
            root = getattr(self, "_root", None)
            if root is not None and not self._coord.triggered:
                try:
                    root.after(100, self._drain_queue)
                except tk.TclError:
                    pass

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
