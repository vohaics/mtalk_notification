"""Command-line entry point for the MTalk notifier.

Usage::

    python -m src.main [--config path/to/config.json]

Or, to run silently in the background (no console window on Windows)::

    pythonw run_hidden.pyw
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


DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.json"


def _setup_logging(log_file: Path, verbose: bool) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)

    # Remove any prior handlers (fresh install in unit tests)
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

    # Console handler is useful when running from a terminal.
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)


def _install_signal_handlers(notifier: Notifier) -> None:
    def _handle(signum, _frame):  # noqa: ANN001
        logging.getLogger(__name__).info("Received signal %s", signum)
        try:
            notifier._shutdown()  # noqa: SLF001 - intentional
        except Exception:
            pass

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle)
        except (ValueError, OSError):
            # Signals may not be installable off the main thread or on Windows
            pass


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
    args = parser.parse_args(argv)

    cfg = AppConfig.load(args.config)
    _setup_logging(cfg.log_file, args.verbose)
    log = logging.getLogger(__name__)
    log.info("MTalk notifier starting (config=%s)", args.config)

    notifier = Notifier(cfg)
    _install_signal_handlers(notifier)
    try:
        notifier.run()
    except KeyboardInterrupt:
        log.info("Interrupted by user")
    finally:
        log.info("MTalk notifier stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
