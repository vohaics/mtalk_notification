"""Minimal MP3 player with an explicit stop() so the Mute button works.

Uses pygame.mixer because it supports mid-playback stop and MP3 decoding
without shelling out to another process. Initialisation is deferred until
the first play() call to keep startup cost low.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional


log = logging.getLogger(__name__)


class SoundPlayer:
    def __init__(self, sound_file: Path) -> None:
        self._sound_file = Path(sound_file)
        self._lock = threading.Lock()
        self._initialised = False
        self._sound = None  # type: Optional[object]
        self._channel = None  # type: Optional[object]

    def _ensure_initialised(self) -> bool:
        if self._initialised:
            return True
        try:
            import pygame  # local import to avoid hard dependency at import time
        except Exception as exc:  # pragma: no cover
            log.error("pygame is required for sound playback: %s", exc)
            return False

        try:
            # frequency + small buffer keeps latency low
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
        except Exception as exc:  # pragma: no cover
            log.error("Failed to initialise audio mixer: %s", exc)
            return False

        sound_path = self._sound_file
        if not sound_path.exists():
            # Fall back to a sibling warning.wav if the configured .mp3 is
            # missing. This keeps the notifier audible out of the box when
            # only the generated fallback tone is available.
            fallback = sound_path.with_suffix(".wav")
            if fallback.exists():
                log.warning(
                    "Sound file %s not found; falling back to %s",
                    sound_path,
                    fallback,
                )
                sound_path = fallback
            else:
                log.error("Sound file not found: %s (and no .wav fallback)", sound_path)
                return False

        try:
            self._sound = pygame.mixer.Sound(str(sound_path))
        except Exception as exc:  # pragma: no cover
            log.error("Failed to load sound %s: %s", sound_path, exc)
            return False

        self._initialised = True
        log.info("Audio mixer ready (file=%s)", sound_path)
        return True

    def play(self, loops: int = 0) -> None:
        """Play the warning sound. loops=-1 to loop forever, 0 for once."""
        with self._lock:
            if not self._ensure_initialised():
                return
            try:
                if self._channel is not None:
                    self._channel.stop()
                self._channel = self._sound.play(loops=loops)  # type: ignore[union-attr]
                log.debug("Playing warning sound")
            except Exception as exc:  # pragma: no cover
                log.error("Failed to play sound: %s", exc)

    def stop(self) -> None:
        with self._lock:
            if self._channel is not None:
                try:
                    self._channel.stop()
                    log.debug("Stopped warning sound")
                except Exception as exc:  # pragma: no cover
                    log.error("Failed to stop sound: %s", exc)
            self._channel = None

    def shutdown(self) -> None:
        self.stop()
        if self._initialised:
            try:
                import pygame

                pygame.mixer.quit()
            except Exception:  # pragma: no cover
                pass
            self._initialised = False
