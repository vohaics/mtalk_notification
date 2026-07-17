"""Tests for MTalkDetector.stop / release_resources idempotency."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import AppConfig, PopupConfig, Target  # noqa: E402
from src.detector import MTalkDetector  # noqa: E402


def _make_cfg():
    return AppConfig(
        targets=[Target(kind="room", name="X", display="X")],
        window_title_hints=["MTalk"],
        poll_interval_seconds=0.05,
        sound_file=Path("/tmp/warn.mp3"),
        log_file=Path("/tmp/mtalk.log"),
        popup=PopupConfig(),
        prefer_events=False,
        root_dir=Path("/tmp"),
    )


class StopIdempotencyTest(unittest.TestCase):
    def test_stop_can_be_called_multiple_times(self) -> None:
        det = MTalkDetector(_make_cfg(), on_new_message=lambda m: None)
        # Never called start(), so no thread. stop() must still be safe.
        det.stop()
        det.stop()
        det.release_resources()
        det.release_resources()

    def test_stop_ends_thread(self) -> None:
        det = MTalkDetector(_make_cfg(), on_new_message=lambda m: None)

        # Bypass uiautomation init (would fail on non-Windows).
        det._init_uiautomation = lambda: False  # type: ignore[assignment]

        det.start()
        # Give the thread a moment to enter its loop / bail out on _init_uiautomation.
        t = det._thread
        det.stop(join_timeout_s=1.0)
        self.assertIsNone(det._thread)
        if t is not None:
            self.assertFalse(t.is_alive())

    def test_release_resources_clears_auto(self) -> None:
        det = MTalkDetector(_make_cfg(), on_new_message=lambda m: None)
        det._auto = object()
        det._auto_ok = True
        det.release_resources()
        self.assertIsNone(det._auto)
        self.assertFalse(det._auto_ok)
        # Second call still fine.
        det.release_resources()


if __name__ == "__main__":
    unittest.main()
