"""Windows UI Automation-based detector for unread MTalk messages.

Strategy
--------
- Attach to any window whose title matches one of ``window_title_hints``.
- For each configured Target (room name or account name), walk the UI Automation
  tree beneath the MTalk window and try to locate an element whose Name
  contains that target text (case-insensitive, partial match).
- From that element (and its immediate parent/siblings) extract the *unread
  count*. We look for:
    1. ``(NN)`` or ``NN`` suffix embedded in the element's own Name.
    2. A neighbouring TextControl whose Name is a pure integer.
    3. A neighbouring element whose Name contains "unread" / "new" / "새".
  If nothing matches we assume ``0`` unread.
- A "new unread message" is any transition where the current count is
  **strictly greater** than the last observed count for that target.
  This naturally prevents duplicate alerts for the same message.

Detection channels
------------------
- **Events (primary):** subscribes to StructureChanged and
  PropertyChanged (Name) on the MTalk window. Every event triggers a rescan.
- **Polling (fallback / safety net):** always runs on
  ``poll_interval_seconds`` (default 1s) so we never miss a change if the
  event pipe drops.

Everything happens on a background thread. When a new unread message is
detected the ``on_new_message(target, count)`` callback fires from that
thread; the coordinator is expected to marshal it onto the Tk main thread.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .config import AppConfig, Target


def _is_windows() -> bool:
    return sys.platform == "win32"


log = logging.getLogger(__name__)

# Matches a trailing "(N)" or " N" unread-count suffix in an item name.
# Examples matched: "NOC Internal (Handover) (3)", "GNMC  12", "GNMC 5+".
_TRAILING_COUNT_RE = re.compile(
    r"(?:\((\d+)\+?\)|\s(\d+)\+?)\s*$"
)
_UNREAD_HINT_WORDS = ("unread", "new", "새", "안읽", "미읽")


@dataclass
class DetectedMessage:
    target: Target
    count: int


NewMessageCallback = Callable[[DetectedMessage], None]


class MTalkDetector:
    """Background monitor for the MTalk window."""

    def __init__(self, cfg: AppConfig, on_new_message: NewMessageCallback) -> None:
        self._cfg = cfg
        self._on_new_message = on_new_message

        self._stop = threading.Event()
        self._wake = threading.Event()  # can be set by events to trigger immediate scan
        self._thread: Optional[threading.Thread] = None

        # Last observed unread count per target.key. ``-1`` means "never seen".
        self._last_counts: Dict[str, int] = {t.key: -1 for t in cfg.targets}

        # UI Automation handles retained so we can unsubscribe cleanly.
        self._auto = None  # imported lazily on Windows
        self._auto_ok = False
        self._event_hooks: list = []
        self._com_initialised_on_thread = False

        # Idempotency guard for stop()/release_resources().
        self._stopped = threading.Event()
        self._released = threading.Event()

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._stopped.clear()
        self._released.clear()
        self._thread = threading.Thread(
            target=self._run, name="mtalk-detector", daemon=True
        )
        self._thread.start()

    def stop(self, join_timeout_s: float = 3.0) -> None:
        """Signal the detector thread to stop and join it.

        Idempotent: safe to call multiple times from any thread.

        Steps:
        1. Set the stop event so the run loop exits at its next wait boundary.
        2. Set the wake event so the loop doesn't have to wait a full
           ``poll_interval_seconds`` before noticing.
        3. Join the thread with a bounded timeout (the shutdown coordinator
           has its own overall deadline as a backstop).
        4. Release Windows UI Automation resources (event hooks + auto handle
           + COM uninit if we initialised it on this thread).
        """
        if self._stopped.is_set():
            return
        self._stopped.set()

        self._stop.set()
        self._wake.set()

        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=join_timeout_s)
            if thread.is_alive():
                log.warning(
                    "Detector thread did not exit within %.1fs; leaving as daemon.",
                    join_timeout_s,
                )
        self._thread = None

        self.release_resources()

    def release_resources(self) -> None:
        """Explicitly release Windows UI Automation resources.

        - Removes any StructureChanged / PropertyChanged event hooks so the OS
          doesn't keep callbacks pointing at freed Python objects.
        - Drops the ``uiautomation`` module reference (any per-thread COM
          state it created will be released when the detector thread's
          COM apartment is uninitialised).
        - Uninitialises COM on the detector thread if we initialised it.

        Idempotent.
        """
        if self._released.is_set():
            return
        self._released.set()

        try:
            self._remove_event_hooks()
        except Exception:
            log.exception("Failed to remove UI Automation event hooks")

        self._auto = None
        self._auto_ok = False
        log.info("UI Automation resources released")

    # ---------------------------------------------------------------- run loop

    def _run(self) -> None:
        log.info("Detector thread starting")
        self._com_init_on_thread()
        try:
            self._auto_ok = self._init_uiautomation()
            if not self._auto_ok:
                log.error(
                    "UI Automation is not available; detector cannot function on "
                    "this platform. This tool must run on Windows."
                )
                return

            events_subscribed = False
            last_window_hwnd: Optional[int] = None
            interval = max(0.1, float(self._cfg.poll_interval_seconds))

            while not self._stop.is_set():
                try:
                    window = self._find_mtalk_window()

                    if window is None:
                        # Window not found - drop any event subs and wait a bit.
                        if events_subscribed:
                            self._remove_event_hooks()
                            events_subscribed = False
                            last_window_hwnd = None
                        log.debug("MTalk window not found; will retry.")
                    else:
                        hwnd = self._safe_hwnd(window)
                        if hwnd != last_window_hwnd:
                            self._remove_event_hooks()
                            events_subscribed = False
                            last_window_hwnd = hwnd

                        if self._cfg.prefer_events and not events_subscribed:
                            events_subscribed = self._try_subscribe_events(window)
                            if events_subscribed:
                                log.info(
                                    "Subscribed to UI Automation events on MTalk window."
                                )
                            else:
                                log.info(
                                    "UI Automation events unavailable; falling back to polling."
                                )

                        self._scan(window)
                except Exception:  # keep the thread alive across transient failures
                    log.exception("Detector scan failed")

                # Wait until poll interval or until an event nudges us.
                self._wake.wait(timeout=interval)
                self._wake.clear()
        finally:
            # Always remove event hooks and uninit COM before the thread ends,
            # even if we're exiting because of an unhandled exception. This is
            # the safest place to unhook, because UI Automation event handlers
            # are per-thread and the OS callbacks land on this thread.
            try:
                self._remove_event_hooks()
            except Exception:
                log.exception("Error removing event hooks on thread exit")
            self._com_uninit_on_thread()
            log.info("Detector thread stopped")

    # -------------------------------------------------------------------- COM

    def _com_init_on_thread(self) -> None:
        """Initialise COM (STA) on this thread so UI Automation event
        callbacks are delivered here. No-op on non-Windows."""
        if not _is_windows():
            return
        try:
            import ctypes

            hr = ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # STA
            # S_OK (0) means we initialised; S_FALSE (1) means already inited
            # on this thread; RPC_E_CHANGED_MODE (0x80010106) means a different
            # apartment mode was already active - all are OK to proceed.
            self._com_initialised_on_thread = hr == 0
            log.debug("CoInitializeEx returned 0x%x", hr & 0xFFFFFFFF)
        except Exception:
            log.exception("CoInitializeEx failed; continuing anyway")

    def _com_uninit_on_thread(self) -> None:
        if not _is_windows():
            return
        if not self._com_initialised_on_thread:
            return
        try:
            import ctypes

            ctypes.windll.ole32.CoUninitialize()
            log.debug("CoUninitialize called on detector thread")
        except Exception:
            log.exception("CoUninitialize failed")
        finally:
            self._com_initialised_on_thread = False

    # -------------------------------------------------------- UI Automation IO

    def _init_uiautomation(self) -> bool:
        try:
            import uiautomation as auto  # type: ignore
        except Exception as exc:
            log.error("Could not import uiautomation: %s", exc)
            return False

        # These flags keep uiautomation quiet and responsive.
        try:
            auto.SetGlobalSearchTimeout(2.0)
        except Exception:
            pass
        try:
            auto.uiautomation.DEBUG_SEARCH_TIME = False  # type: ignore[attr-defined]
        except Exception:
            pass

        self._auto = auto
        return True

    def _find_mtalk_window(self):
        auto = self._auto
        assert auto is not None
        try:
            desktop = auto.GetRootControl()
        except Exception:
            return None

        hints_lower = [h.lower() for h in self._cfg.window_title_hints]
        try:
            for win in desktop.GetChildren():
                name = (win.Name or "").lower()
                if not name:
                    continue
                if any(h in name for h in hints_lower):
                    return win
        except Exception:
            log.exception("Failed to enumerate desktop windows")
        return None

    def _safe_hwnd(self, element) -> Optional[int]:
        try:
            return int(element.NativeWindowHandle)
        except Exception:
            return None

    # -------------------------------------------------------------- event subs

    def _try_subscribe_events(self, window) -> bool:
        """Subscribe to structure and property changes to trigger fast rescans."""
        auto = self._auto
        assert auto is not None

        def _wake(*_args, **_kwargs) -> None:
            self._wake.set()

        try:
            # Structure changes cover new list items / new chat bubbles.
            hook1 = auto.GetStructureChangedEventHandler(_wake)  # type: ignore[attr-defined]
            window.AddStructureChangedEventHandler(  # type: ignore[attr-defined]
                auto.TreeScope_Subtree, hook1
            )
            self._event_hooks.append(("structure", window, hook1))

            # Name changes cover unread badge count updates.
            hook2 = auto.GetPropertyChangedEventHandler(_wake)  # type: ignore[attr-defined]
            window.AddPropertyChangedEventHandler(  # type: ignore[attr-defined]
                auto.TreeScope_Subtree, hook2, [auto.PropertyId.NameProperty]
            )
            self._event_hooks.append(("property", window, hook2))
            return True
        except Exception as exc:
            log.debug("Event subscription failed: %s", exc)
            self._remove_event_hooks()
            return False

    def _remove_event_hooks(self) -> None:
        for kind, element, hook in self._event_hooks:
            try:
                if kind == "structure":
                    element.RemoveStructureChangedEventHandler(hook)  # type: ignore[attr-defined]
                elif kind == "property":
                    element.RemovePropertyChangedEventHandler(hook)  # type: ignore[attr-defined]
            except Exception:
                pass
        self._event_hooks.clear()

    # ------------------------------------------------------------------ scan

    def _scan(self, window) -> None:
        for target in self._cfg.targets:
            try:
                count = self._unread_count_for_target(window, target)
            except Exception:
                log.exception("Failed to inspect target %s", target.key)
                continue

            last = self._last_counts.get(target.key, -1)
            if count is None:
                # Target element not visible right now. Don't update state so
                # transient invisibility (e.g. filter, minimised) does not
                # cause spurious 0->N transitions later.
                log.debug("Target %s not visible", target.key)
                continue

            if last < 0:
                # First observation - seed state without firing to avoid a
                # phantom alert for messages that were already unread when
                # the notifier started.
                log.info(
                    "Initial unread count for %s = %d (no alert on startup)",
                    target.key,
                    count,
                )
                self._last_counts[target.key] = count
                continue

            if count > last:
                delta = count - last
                log.info(
                    "New unread message(s) for %s: %d -> %d (+%d)",
                    target.key,
                    last,
                    count,
                    delta,
                )
                self._last_counts[target.key] = count
                self._on_new_message(DetectedMessage(target=target, count=count))
            elif count != last:
                log.debug(
                    "Unread count for %s changed %d -> %d (no alert)",
                    target.key,
                    last,
                    count,
                )
                self._last_counts[target.key] = count

    def _unread_count_for_target(self, window, target: Target) -> Optional[int]:
        """Return current unread count for target, or None if not present."""
        matches = self._find_named_elements(window, target.name)
        if not matches:
            return None

        best: Optional[int] = None
        for element in matches:
            count = self._extract_unread_count(element)
            if count is not None and (best is None or count > best):
                best = count
        return best if best is not None else 0

    # ---------------------------------------------------------- tree searching

    def _find_named_elements(self, root, needle: str, limit: int = 8) -> list:
        """Depth-first walk that collects up to `limit` elements whose Name
        contains `needle` (case-insensitive)."""
        auto = self._auto
        assert auto is not None
        needle_lower = needle.lower()
        found: list = []
        stack = [root]
        visited = 0
        # Cap the walk to keep CPU predictable even on very large trees.
        visit_cap = 4000
        while stack and len(found) < limit and visited < visit_cap:
            node = stack.pop()
            visited += 1
            try:
                name = node.Name or ""
            except Exception:
                name = ""
            if name and needle_lower in name.lower():
                found.append(node)
            try:
                children = node.GetChildren()
            except Exception:
                children = []
            # push in reverse so DFS visits in original order
            for child in reversed(children):
                stack.append(child)
        return found

    # ---------------------------------------------------- unread-count extract

    def _extract_unread_count(self, element) -> Optional[int]:
        """Best-effort extraction of an unread count from `element` and neighbours."""
        # 1. Inline suffix in the target element's own Name.
        try:
            name = element.Name or ""
        except Exception:
            name = ""
        inline = _extract_count_from_text(name)
        if inline is not None:
            return inline

        # 2. Look at neighbouring elements: parent, siblings, descendants.
        candidates: list = []
        try:
            parent = element.GetParentControl()
            if parent is not None:
                candidates.append(parent)
                candidates.extend(parent.GetChildren())
        except Exception:
            pass
        try:
            candidates.extend(element.GetChildren())
        except Exception:
            pass

        best_numeric: Optional[int] = None
        hint_seen = False
        for cand in candidates:
            try:
                text = cand.Name or ""
            except Exception:
                text = ""
            if not text:
                continue
            low = text.lower()
            if any(hint in low for hint in _UNREAD_HINT_WORDS):
                hint_seen = True
                num = _extract_count_from_text(text)
                if num is not None:
                    return num
            # Standalone integer (badge)
            if _is_small_integer(text):
                value = int(text)
                if best_numeric is None or value > best_numeric:
                    best_numeric = value

        if best_numeric is not None:
            return best_numeric
        if hint_seen:
            # There was an "unread" element without a number: treat as 1.
            return 1
        # Element found but no unread indicator - definitely 0.
        return 0


# --------------------------------------------------------------------- helpers

def _extract_count_from_text(text: str) -> Optional[int]:
    m = _TRAILING_COUNT_RE.search(text)
    if not m:
        return None
    for g in m.groups():
        if g:
            try:
                return int(g)
            except ValueError:
                return None
    return None


def _is_small_integer(text: str) -> bool:
    stripped = text.strip().rstrip("+")
    if not stripped or not stripped.isdigit():
        return False
    return len(stripped) <= 4
