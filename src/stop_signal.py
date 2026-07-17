"""External stop signals: Windows named event + stop-request file.

Two independent triggers keep working even if the app is running as a
console-less ``pythonw.exe`` background process:

1. **Windows named event.** On Windows we call ``CreateEventW`` for a
   session-local event named ``MTalkNotifier_Stop`` and block on
   ``WaitForSingleObject`` in a background thread. ``stop.bat`` opens the
   same event via PowerShell and calls ``.Set()`` on it, which wakes the
   listener immediately (no polling, no CPU).

2. **Stop-request file.** As a portable, no-PowerShell fallback we also
   poll for a ``stop.request`` file next to ``config.json``. Any user or
   script (e.g. ``echo stop > stop.request``) can create the file to ask
   the notifier to quit. We remove the file after observing it so the app
   can be restarted cleanly.

When either signal fires the listener calls
``coordinator.trigger("<source>")`` — the coordinator handles idempotency,
LIFO teardown, and the exit watchdog.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .shutdown import ShutdownCoordinator


log = logging.getLogger(__name__)


# Session-local name. Session-local names don't require any elevation and are
# scoped to the current user session, which is exactly right for a per-user
# background helper.
NAMED_EVENT = "MTalkNotifier_Stop"


class StopSignalListener:
    def __init__(
        self,
        coordinator: ShutdownCoordinator,
        stop_file: Path,
        poll_interval_s: float = 1.0,
        event_name: str = NAMED_EVENT,
    ) -> None:
        self._coord = coordinator
        self._stop_file = Path(stop_file)
        self._poll_interval_s = float(poll_interval_s)
        self._event_name = event_name

        self._stop_local = threading.Event()  # tells our own threads to exit
        self._threads: list[threading.Thread] = []
        self._named_event_handle: Optional[int] = None

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        # Always run the file watcher (cheap, cross-platform).
        t_file = threading.Thread(
            target=self._watch_stop_file,
            name="stop-signal-file",
            daemon=True,
        )
        t_file.start()
        self._threads.append(t_file)

        # Named-event watcher only on Windows.
        if sys.platform == "win32":
            t_event = threading.Thread(
                target=self._watch_named_event,
                name="stop-signal-event",
                daemon=True,
            )
            t_event.start()
            self._threads.append(t_event)

        log.info(
            "Stop-signal listener started (file=%s, event=%s)",
            self._stop_file,
            self._event_name if sys.platform == "win32" else "n/a",
        )

    def stop(self) -> None:
        """Idempotent teardown, invoked by the shutdown coordinator."""
        self._stop_local.set()
        self._signal_named_event_self()
        for t in list(self._threads):
            try:
                t.join(timeout=1.0)
            except Exception:
                pass
        self._threads.clear()
        self._close_named_event()

    # ---------------------------------------------------------------- watchers

    def _watch_stop_file(self) -> None:
        interval = max(0.2, self._poll_interval_s)
        while not self._stop_local.is_set():
            try:
                if self._stop_file.exists():
                    log.info("Stop-request file observed: %s", self._stop_file)
                    try:
                        self._stop_file.unlink()
                    except OSError as exc:
                        log.warning("Could not remove %s: %s", self._stop_file, exc)
                    self._coord.trigger(f"stop-file:{self._stop_file.name}")
                    return
            except Exception:
                log.exception("stop-file watcher error")
            self._stop_local.wait(timeout=interval)

    def _watch_named_event(self) -> None:  # pragma: no cover - Windows only
        try:
            import ctypes
            from ctypes import wintypes
        except Exception as exc:
            log.debug("ctypes not available for named event: %s", exc)
            return

        kernel32 = ctypes.windll.kernel32
        # CreateEventW(lpSecurity, manualReset, initialState, name)
        CreateEventW = kernel32.CreateEventW
        CreateEventW.restype = wintypes.HANDLE
        CreateEventW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        WaitForSingleObject = kernel32.WaitForSingleObject
        WaitForSingleObject.restype = wintypes.DWORD
        WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        CloseHandle = kernel32.CloseHandle
        CloseHandle.restype = wintypes.BOOL
        CloseHandle.argtypes = [wintypes.HANDLE]

        WAIT_OBJECT_0 = 0x00000000
        WAIT_TIMEOUT = 0x00000102

        handle = CreateEventW(None, True, False, self._event_name)
        if not handle:
            err = ctypes.get_last_error()
            log.warning("CreateEventW failed (err=%s); named-event stop unavailable", err)
            return
        self._named_event_handle = handle
        log.debug("Named event %r created (handle=%s)", self._event_name, handle)

        try:
            # Wait in short chunks so `stop()` can wake us via _signal_named_event_self.
            while not self._stop_local.is_set():
                result = WaitForSingleObject(handle, 500)  # ms
                if result == WAIT_OBJECT_0:
                    if self._stop_local.is_set():
                        return
                    log.info("Windows named event %r signalled", self._event_name)
                    self._coord.trigger(f"named-event:{self._event_name}")
                    return
                if result != WAIT_TIMEOUT:
                    err = ctypes.get_last_error()
                    log.warning(
                        "WaitForSingleObject returned unexpected 0x%x (err=%s)",
                        result,
                        err,
                    )
                    return
        finally:
            pass  # handle closed in _close_named_event

    def _signal_named_event_self(self) -> None:  # pragma: no cover - Windows only
        """Set the event ourselves so the waiter thread wakes up and exits."""
        if sys.platform != "win32" or self._named_event_handle is None:
            return
        try:
            import ctypes
            from ctypes import wintypes

            SetEvent = ctypes.windll.kernel32.SetEvent
            SetEvent.restype = wintypes.BOOL
            SetEvent.argtypes = [wintypes.HANDLE]
            SetEvent(self._named_event_handle)
        except Exception:
            log.debug("SetEvent on self-wake failed", exc_info=True)

    def _close_named_event(self) -> None:  # pragma: no cover - Windows only
        if sys.platform != "win32" or self._named_event_handle is None:
            return
        try:
            import ctypes
            from ctypes import wintypes

            CloseHandle = ctypes.windll.kernel32.CloseHandle
            CloseHandle.restype = wintypes.BOOL
            CloseHandle.argtypes = [wintypes.HANDLE]
            CloseHandle(self._named_event_handle)
        except Exception:
            log.debug("CloseHandle on named event failed", exc_info=True)
        self._named_event_handle = None
