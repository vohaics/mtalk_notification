"""Central shutdown coordinator.

Every long-lived resource in the app (detector thread, sound mixer, popup,
UI Automation event hooks, tray icon, stop-signal listener, PID file, Tk root)
registers a teardown callable with a single :class:`ShutdownCoordinator`.

Guarantees
----------
- **Idempotent.** :meth:`ShutdownCoordinator.trigger` can be called any number
  of times from any thread; the registered actions run exactly once.
- **Ordered (LIFO).** Actions run in reverse registration order, mirroring
  ``contextlib.ExitStack`` / ``atexit``: the most recently created resource is
  torn down first. This matches typical construction dependencies (detector
  built after sound built after popup built after Tk root).
- **Bounded.** Each action has an individual timeout, so a single stuck
  subsystem never blocks the whole shutdown. A wall-clock watchdog fires after
  ``deadline_s`` and force-terminates the process via ``os._exit`` as the
  ultimate escape hatch, so the app never leaves a zombie background process.
- **Thread-safe.** ``trigger()`` may be called from signal handlers, Tk
  callbacks, background threads, or the tray icon. Only the first call wins;
  subsequent calls are logged and ignored.
- **Reason-tagged.** Every trigger records who caused the shutdown, which
  lands in the log for post-mortems (Ctrl+C vs. Alt+F4 vs. stop.bat).

Typical use::

    coord = ShutdownCoordinator(deadline_s=10.0)
    coord.register("popup", popup.close, timeout_s=1.0)
    coord.register("sound", sound.shutdown, timeout_s=2.0)
    coord.register("detector", detector.stop, timeout_s=3.0)

    # ... run the app ...
    # Trigger from anywhere:
    coord.trigger("SIGINT")

    # After the main thread's Tk mainloop returns:
    coord.run()   # LIFO teardown
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional


log = logging.getLogger(__name__)


ShutdownAction = Callable[[], None]
ForceExit = Callable[[int], None]


def _default_force_exit(code: int) -> None:  # pragma: no cover - process exit
    os._exit(code)


@dataclass
class _RegisteredAction:
    name: str
    action: ShutdownAction
    timeout_s: float


class ShutdownCoordinator:
    """Owns the app's shutdown lifecycle."""

    def __init__(
        self,
        deadline_s: float = 10.0,
        force_exit: ForceExit = _default_force_exit,
    ) -> None:
        self._lock = threading.Lock()
        self._actions: List[_RegisteredAction] = []
        self._triggered = threading.Event()
        self._completed = threading.Event()
        self._deadline_s = float(deadline_s)
        self._reason: Optional[str] = None
        self._force_exit = force_exit
        self._watchdog_thread: Optional[threading.Thread] = None

    # ---------------------------------------------------------------- register

    def register(
        self,
        name: str,
        action: ShutdownAction,
        timeout_s: float = 3.0,
    ) -> None:
        """Register a teardown action. Actions run in **reverse** registration
        order at shutdown time."""
        with self._lock:
            if self._triggered.is_set():
                # Late registrations are refused so we don't add work after the
                # deadline watchdog is already running.
                log.warning(
                    "Refusing late shutdown action %r; shutdown already triggered.",
                    name,
                )
                return
            self._actions.append(
                _RegisteredAction(name=name, action=action, timeout_s=float(timeout_s))
            )

    # ---------------------------------------------------------------- triggers

    @property
    def triggered(self) -> bool:
        return self._triggered.is_set()

    @property
    def completed(self) -> bool:
        return self._completed.is_set()

    @property
    def reason(self) -> Optional[str]:
        return self._reason

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until shutdown is triggered. Returns True if triggered."""
        return self._triggered.wait(timeout=timeout)

    def trigger(self, reason: str) -> None:
        """Request shutdown. Safe to call from any thread; idempotent."""
        first = False
        with self._lock:
            if not self._triggered.is_set():
                self._reason = reason
                self._triggered.set()
                first = True
        if first:
            log.info("Shutdown requested: %s", reason)
            self._start_watchdog()
        else:
            log.debug("Shutdown re-trigger ignored (reason=%s)", reason)

    # ------------------------------------------------------------------- run

    def run(self) -> None:
        """Execute all registered actions in LIFO order.

        Should be called exactly once, typically from the main thread after
        the Tk mainloop returns. Safe to call before or after :meth:`trigger`;
        if trigger has not been called yet this sets the trigger with reason
        ``'explicit run()'`` so the watchdog still guards the deadline.
        """
        if self._completed.is_set():
            log.debug("Shutdown.run() called twice; ignoring")
            return
        if not self._triggered.is_set():
            self.trigger("explicit run()")

        with self._lock:
            actions = list(reversed(self._actions))

        log.info(
            "Running %d shutdown actions (reason=%s, deadline=%.1fs)",
            len(actions),
            self._reason,
            self._deadline_s,
        )
        for entry in actions:
            self._run_one(entry)
        self._completed.set()
        log.info("Shutdown complete")

    def _run_one(self, entry: _RegisteredAction) -> None:
        result: dict = {"done": False, "err": None}

        def runner() -> None:
            try:
                entry.action()
            except BaseException as exc:  # pragma: no cover - defensive
                result["err"] = exc
            finally:
                result["done"] = True

        t = threading.Thread(
            target=runner, name=f"shutdown[{entry.name}]", daemon=True
        )
        t.start()
        t.join(timeout=entry.timeout_s)
        if not result["done"]:
            log.warning(
                "Shutdown action %r timed out after %.1fs (thread still alive; "
                "will be reaped by watchdog if it holds the process open)",
                entry.name,
                entry.timeout_s,
            )
        elif result["err"] is not None:
            log.warning(
                "Shutdown action %r raised %s: %s",
                entry.name,
                type(result["err"]).__name__,
                result["err"],
            )
        else:
            log.info("Shutdown action %r finished", entry.name)

    # ----------------------------------------------------------------- watchdog

    def _start_watchdog(self) -> None:
        if self._watchdog_thread is not None:
            return

        def watchdog() -> None:
            deadline = time.monotonic() + self._deadline_s
            while time.monotonic() < deadline:
                if self._completed.wait(timeout=0.2):
                    log.debug("Shutdown watchdog: clean exit observed")
                    return
            if not self._completed.is_set():
                log.error(
                    "Shutdown watchdog fired after %.1fs; forcing process exit "
                    "to guarantee no background processes are left.",
                    self._deadline_s,
                )
                try:
                    self._force_exit(1)
                except BaseException:  # pragma: no cover
                    pass

        self._watchdog_thread = threading.Thread(
            target=watchdog, name="shutdown-watchdog", daemon=True
        )
        self._watchdog_thread.start()
