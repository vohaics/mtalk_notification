"""Tests for MTalkDetector's dedup/state-machine logic.

We drive ``_scan()`` with a small fake UI Automation tree so we don't need a
running MTalk instance to verify:

- The initial observation seeds state without firing (no phantom alerts on start).
- A count increase fires exactly one alert (dedup within the same message).
- A count decrease updates state silently.
- A subsequent increase fires again.
- Invisible target (element missing) does not fire and does not clobber state.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import List, Optional


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import AppConfig, PopupConfig, Target  # noqa: E402
from src.detector import DetectedMessage, MTalkDetector  # noqa: E402


class FakeElement:
    """Minimal stand-in for a uiautomation Control."""

    def __init__(self, name: str = "", children: Optional[List["FakeElement"]] = None):
        self.Name = name
        self._children = list(children or [])
        self._parent: Optional["FakeElement"] = None
        for c in self._children:
            c._parent = self

    def GetChildren(self):
        return list(self._children)

    def GetParentControl(self):
        return self._parent


def _make_cfg(targets):
    return AppConfig(
        targets=targets,
        window_title_hints=["MTalk"],
        poll_interval_seconds=1.0,
        sound_file=Path("/tmp/warning.mp3"),
        log_file=Path("/tmp/mtalk.log"),
        popup=PopupConfig(),
        prefer_events=False,
        root_dir=Path("/tmp"),
    )


def _make_detector(cfg, calls):
    def on_msg(msg: DetectedMessage) -> None:
        calls.append(msg)

    det = MTalkDetector(cfg, on_new_message=on_msg)
    # Bypass Windows-only UI Automation init.
    det._auto = object()
    det._auto_ok = True
    return det


class DedupStateMachineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.target = Target(kind="room", name="NOC Internal (Handover)",
                             display="NOC Internal (Handover)")
        self.cfg = _make_cfg([self.target])
        self.calls: List[DetectedMessage] = []
        self.det = _make_detector(self.cfg, self.calls)

    def _tree(self, room_label: str) -> FakeElement:
        # A tiny fake window with one list item whose name embeds the count.
        return FakeElement("MTalk Window", [
            FakeElement("Sidebar", [
                FakeElement(room_label),
            ])
        ])

    def test_initial_scan_does_not_fire(self) -> None:
        self.det._scan(self._tree("NOC Internal (Handover) (3)"))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.det._last_counts[self.target.key], 3)

    def test_increase_fires_once(self) -> None:
        self.det._scan(self._tree("NOC Internal (Handover) (0)"))  # seed as 0
        self.det._scan(self._tree("NOC Internal (Handover) (0)"))  # no change
        self.assertEqual(self.calls, [])

        self.det._scan(self._tree("NOC Internal (Handover) (2)"))  # 0 -> 2 : FIRE
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].count, 2)

        self.det._scan(self._tree("NOC Internal (Handover) (2)"))  # same : no fire
        self.assertEqual(len(self.calls), 1)

    def test_decrease_updates_silently_then_increase_fires(self) -> None:
        self.det._scan(self._tree("NOC Internal (Handover) (5)"))  # seed as 5
        self.det._scan(self._tree("NOC Internal (Handover) (0)"))  # user read them
        self.assertEqual(self.calls, [])
        self.assertEqual(self.det._last_counts[self.target.key], 0)

        self.det._scan(self._tree("NOC Internal (Handover) (1)"))  # new msg: FIRE
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].count, 1)

    def test_invisible_target_does_not_reset_state(self) -> None:
        self.det._scan(self._tree("NOC Internal (Handover) (2)"))  # seed as 2
        # Now the room is not visible (filter applied, minimised, etc.)
        empty_tree = FakeElement("MTalk Window", [FakeElement("Sidebar", [])])
        self.det._scan(empty_tree)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.det._last_counts[self.target.key], 2)


if __name__ == "__main__":
    unittest.main()
