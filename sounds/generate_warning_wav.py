"""Generate a simple two-tone warning.wav (used as a fallback when the user
hasn't provided sounds/warning.mp3 yet).

Uses only the Python standard library. Run once:

    python sounds/generate_warning_wav.py

Output: sounds/warning.wav (~1.5 seconds, 44.1 kHz, mono).
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path


SAMPLE_RATE = 44100
DURATION_S = 1.6
AMPLITUDE = 0.35
TONE_A_HZ = 880.0
TONE_B_HZ = 660.0
SWITCH_S = 0.18  # switch tone every 0.18s -> "beep-boop" pattern
FADE_S = 0.008


def _generate_samples() -> bytes:
    total = int(SAMPLE_RATE * DURATION_S)
    fade = max(1, int(SAMPLE_RATE * FADE_S))
    switch_samples = max(1, int(SAMPLE_RATE * SWITCH_S))
    buf = bytearray()

    for i in range(total):
        band = (i // switch_samples) % 2
        freq = TONE_A_HZ if band == 0 else TONE_B_HZ
        # simple linear fade in/out to avoid clicks
        env = 1.0
        if i < fade:
            env = i / fade
        elif i > total - fade:
            env = max(0.0, (total - i) / fade)
        sample = AMPLITUDE * env * math.sin(2 * math.pi * freq * (i / SAMPLE_RATE))
        buf += struct.pack("<h", int(sample * 32767))
    return bytes(buf)


def main() -> None:
    out = Path(__file__).with_name("warning.wav")
    frames = _generate_samples()
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(frames)
    print(f"Wrote {out} ({len(frames)} bytes)")


if __name__ == "__main__":
    main()
