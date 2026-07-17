"""Tests for the AppConfig loader."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import AppConfig  # noqa: E402


class AppConfigTest(unittest.TestCase):
    def test_load_default_config(self) -> None:
        cfg = AppConfig.load(ROOT / "config.json")
        keys = {t.key for t in cfg.targets}
        self.assertIn("room:NOC Internal (Handover)", keys)
        self.assertIn("account:GNMC", keys)
        self.assertGreater(cfg.poll_interval_seconds, 0)
        self.assertTrue(cfg.sound_file.is_absolute())
        self.assertTrue(cfg.log_file.is_absolute())
        self.assertTrue(cfg.popup.always_on_top)
        # Sound file may be .mp3 (not shipped) but sibling .wav is generated.
        wav = cfg.sound_file.with_suffix(".wav")
        self.assertTrue(cfg.sound_file.exists() or wav.exists())

    def test_load_minimal_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            p.write_text(
                json.dumps(
                    {
                        "targets": [{"kind": "room", "name": "X"}],
                        "sound_file": "s.mp3",
                        "log_file": "l.log",
                    }
                ),
                encoding="utf-8",
            )
            cfg = AppConfig.load(p)
            self.assertEqual(len(cfg.targets), 1)
            self.assertEqual(cfg.targets[0].kind, "room")
            self.assertEqual(cfg.targets[0].name, "X")
            self.assertEqual(cfg.targets[0].display, "X")
            self.assertEqual(cfg.window_title_hints, ["MTalk"])
            self.assertEqual(cfg.poll_interval_seconds, 1.0)
            self.assertEqual(cfg.sound_file, (Path(td) / "s.mp3").resolve())
            self.assertEqual(cfg.log_file, (Path(td) / "l.log").resolve())


if __name__ == "__main__":
    unittest.main()
