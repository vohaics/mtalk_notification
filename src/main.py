"""Command-line entry point for the MTalk notifier.

Usage::

    python -m src.main [--config path/to/config.json]

Or, to run silently in the background (no console window on Windows)::

    pythonw run_hidden.pyw

Shutdown paths (any one of these triggers the same ordered, idempotent
teardown; see :mod:`src.shutdown`):

- SIGINT / SIGTERM / SIGBREAK signals
- Alt+F4 or programmatic close of the hidden Tk root
  (``WM_DELETE_WINDOW``)
- ``stop.bat`` -> Windows named event ``MTalkNotifier_Stop``
- ``echo stop > <repo>/stop.request``
- Uncaught exception in the mainloop
- Watchdog force-exit after ``--shutdown-deadline`` seconds (default 10s)
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import sys
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "hide")

from .config import AppConfig
from .notifier import Notifier
from .shutdown import ShutdownCoordinator
from .stop_signal import StopSignalListener


DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.json"
PID_FILE_NAME = "mtalk_notifier.pid"
STOP_FILE_NAME = "stop.request"


def _setup_logging(log_file: Path, verbose: bool) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)

    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    rot = logging.handlers.RotatingFileHandler(
        str(log_file), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    rot.setFormatter(fmt)
    root.addHandler(rot)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)


def _install_signal_handlers(coord: ShutdownCoordinator) -> None:
    """Route OS signals into the coordinator.

    On Windows, ``signal.SIGBREAK`` is what a console gets for Ctrl+Break and
    is often the only signal we can catch under ``pythonw.exe``. ``SIGTERM``
    is what ``taskkill /pid <pid>`` (without ``/f``) delivers to a Python
    process. We attempt to install all of them and quietly skip any that the
    current platform / thread combination refuses.
    """
    log = logging.getLogger(__name__)

    def _handle(signum, _frame):  # noqa: ANN001
        try:
            name = signal.Signals(signum).name
        except (ValueError, AttributeError):
            name = str(signum)
        log.info("Received signal %s", name)
        coord.trigger(f"signal:{name}")

    sig_names = ["SIGINT", "SIGTERM"]
    if sys.platform == "win32":
        sig_names.append("SIGBREAK")  # Ctrl+Break on Windows

    for sn in sig_names:
        sig = getattr(signal, sn, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handle)
        except (ValueError, OSError):
            # Signals may not be installable off the main thread on some OSes
            continue


def _write_pid_file(pid_file: Path) -> None:
    """Write our PID so ``stop.bat`` can fall back to ``taskkill /pid``."""
    log = logging.getLogger(__name__)
    try:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
        log.info("Wrote PID file: %s (pid=%d)", pid_file, os.getpid())
    except OSError as exc:
        log.warning("Could not write PID file %s: %s", pid_file, exc)


def _remove_pid_file(pid_file: Path) -> None:
    log = logging.getLogger(__name__)
    try:
        if pid_file.exists():
            pid_file.unlink()
            log.info("Removed PID file: %s", pid_file)
    except OSError as exc:
        log.warning("Could not remove PID file %s: %s", pid_file, exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mtalk-notifier",
        description="Lightweight Windows 11 MTalk notifier.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Path to config.json (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging."
    )
    parser.add_argument(
        "--shutdown-deadline",
        type=float,
        default=10.0,
        help=(
            "Wall-clock deadline in seconds for graceful shutdown. If any "
            "teardown action is still running when this elapses, the process "
            "is forcibly terminated so no background process is left running."
        ),
    )
    args = parser.parse_args(argv)

    cfg = AppConfig.load(args.config)
    _setup_logging(cfg.log_file, args.verbose)
    log = logging.getLogger(__name__)
    log.info("MTalk notifier starting (config=%s, pid=%d)", args.config, os.getpid())

    coord = ShutdownCoordinator(deadline_s=args.shutdown_deadline)

    pid_file = cfg.root_dir / PID_FILE_NAME
    stop_file = cfg.root_dir / STOP_FILE_NAME

    # Everything the notifier acquires is registered with the coordinator so
    # a single ``coord.run()`` in the outermost ``finally`` cleans it all up
    # even if construction itself fails partway through.
    try:
        _write_pid_file(pid_file)
        coord.register("pid-file", lambda: _remove_pid_file(pid_file), timeout_s=1.0)

        stop_listener = StopSignalListener(
            coordinator=coord,
            stop_file=stop_file,
            poll_interval_s=cfg.poll_interval_seconds,
        )
        stop_listener.start()
        coord.register("stop-signal", stop_listener.stop, timeout_s=2.0)

        notifier = Notifier(cfg, coordinator=coord)
        _install_signal_handlers(coord)

        try:
            notifier.run()
        except KeyboardInterrupt:
            log.info("Interrupted by user")
            coord.trigger("KeyboardInterrupt")
    except BaseException:
        log.exception("Unhandled exception in main; requesting shutdown")
        coord.trigger("unhandled-exception")
        raise
    finally:
        coord.run()  # LIFO teardown; idempotent, watchdog-bounded
        log.info("MTalk notifier stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
