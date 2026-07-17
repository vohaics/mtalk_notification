"""Tests for ShutdownCoordinator.

Covers:
- Idempotency of trigger() across many concurrent threads.
- LIFO execution order at run() time.
- Per-action timeout handling: a slow action doesn't block other actions.
- Watchdog force_exit fires when run() hangs past the deadline.
- run() without a prior trigger() still runs actions (and sets reason).
- register() after trigger() is refused.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.shutdown import ShutdownCoordinator  # noqa: E402


class TriggerIdempotencyTest(unittest.TestCase):
    def test_first_trigger_wins_reason(self) -> None:
        c = ShutdownCoordinator(deadline_s=5.0, force_exit=lambda code: None)
        c.trigger("first")
        c.trigger("second")
        c.trigger("third")
        self.assertEqual(c.reason, "first")
        self.assertTrue(c.triggered)

    def test_concurrent_triggers_are_thread_safe(self) -> None:
        c = ShutdownCoordinator(deadline_s=5.0, force_exit=lambda code: None)
        threads = [
            threading.Thread(target=lambda i=i: c.trigger(f"t{i}"))
            for i in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(c.triggered)
        self.assertIsNotNone(c.reason)
        self.assertTrue(c.reason.startswith("t"))


class LifoOrderTest(unittest.TestCase):
    def test_actions_run_in_reverse_registration_order(self) -> None:
        c = ShutdownCoordinator(deadline_s=5.0, force_exit=lambda code: None)
        order = []
        c.register("a", lambda: order.append("a"))
        c.register("b", lambda: order.append("b"))
        c.register("c", lambda: order.append("c"))
        c.trigger("test")
        c.run()
        self.assertEqual(order, ["c", "b", "a"])
        self.assertTrue(c.completed)

    def test_run_without_prior_trigger_still_runs(self) -> None:
        c = ShutdownCoordinator(deadline_s=5.0, force_exit=lambda code: None)
        marker = []
        c.register("x", lambda: marker.append("x"))
        c.run()
        self.assertEqual(marker, ["x"])
        self.assertTrue(c.triggered)
        self.assertEqual(c.reason, "explicit run()")


class TimeoutTest(unittest.TestCase):
    def test_slow_action_does_not_block_others(self) -> None:
        c = ShutdownCoordinator(deadline_s=5.0, force_exit=lambda code: None)
        events = []

        def slow():
            time.sleep(2.0)
            events.append("slow-done")

        def fast_a():
            events.append("fast-a")

        def fast_b():
            events.append("fast-b")

        c.register("fast_a", fast_a, timeout_s=1.0)
        c.register("slow", slow, timeout_s=0.2)
        c.register("fast_b", fast_b, timeout_s=1.0)

        start = time.monotonic()
        c.trigger("test")
        c.run()
        elapsed = time.monotonic() - start

        # fast_b registered last, so it runs first; then slow (times out at
        # 0.2s); then fast_a. Total should be well under 1s, not 2s.
        self.assertLess(elapsed, 1.5, f"run() took {elapsed:.2f}s; slow action was not bounded")
        self.assertIn("fast-b", events)
        self.assertIn("fast-a", events)


class RegisterAfterTriggerTest(unittest.TestCase):
    def test_late_registration_is_refused(self) -> None:
        c = ShutdownCoordinator(deadline_s=5.0, force_exit=lambda code: None)
        ran = []
        c.trigger("early")
        c.register("late", lambda: ran.append("late"))
        c.run()
        self.assertEqual(ran, [])


class WatchdogTest(unittest.TestCase):
    def test_watchdog_calls_force_exit_when_run_hangs(self) -> None:
        force_exit_calls = []
        force_exit_event = threading.Event()

        def fake_force_exit(code: int) -> None:
            force_exit_calls.append(code)
            force_exit_event.set()

        c = ShutdownCoordinator(deadline_s=0.4, force_exit=fake_force_exit)

        # An action that hangs forever - simulate a stuck subsystem.
        hang = threading.Event()
        c.register("hang", lambda: hang.wait(timeout=10.0), timeout_s=10.0)

        c.trigger("test-hang")
        # Do NOT call c.run() here. We only care that the watchdog fires
        # when the process is triggered but never completes.
        self.assertTrue(force_exit_event.wait(timeout=2.0), "watchdog never fired")
        self.assertEqual(force_exit_calls, [1])
        hang.set()

    def test_watchdog_does_not_fire_on_clean_completion(self) -> None:
        force_exit_calls = []

        def fake_force_exit(code: int) -> None:
            force_exit_calls.append(code)

        c = ShutdownCoordinator(deadline_s=0.6, force_exit=fake_force_exit)
        c.register("quick", lambda: None, timeout_s=1.0)
        c.trigger("test")
        c.run()
        # Give the watchdog time to notice completion and exit.
        time.sleep(0.9)
        self.assertEqual(force_exit_calls, [])


class RunTwiceIsSafeTest(unittest.TestCase):
    def test_run_is_idempotent(self) -> None:
        c = ShutdownCoordinator(deadline_s=5.0, force_exit=lambda code: None)
        calls = []
        c.register("once", lambda: calls.append(1))
        c.run()
        c.run()
        self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()
