# MTalk Notifier

Simplest possible MTalk notifier for Windows 11. Watches for new unread
messages in one chat room and one account, plays `warning.mp3`, and shows
a small popup with a single **Mute** button.

Monitored targets (edit the two constants at the top of
`mtalk_notifier.py` to change):

- Room: `NOC Internal (Handover)`
- Account: `GNMC`

## Setup

Requires Python 3.10+ on Windows 11.

```
pip install -r requirements.txt
```

Drop your alarm sound at `warning.mp3` next to `mtalk_notifier.py`.

## Run

```
python mtalk_notifier.py
```

The main window has three buttons:

- **Start Monitoring** — begins checking MTalk every second.
- **Stop Monitoring** — completely stops checking until Start is pressed
  again. Also silences any in-flight alert.
- **Exit** — quits the app.

## Behaviour

- While monitoring, the app checks MTalk every 1 second via Windows UI
  Automation.
- When a target's unread count strictly increases (a genuinely new
  message), the app plays `warning.mp3` and shows a popup.
- Pressing **Mute** on the popup stops the sound, closes the popup, and
  ignores the current unread state. The next alert only fires when a
  brand new message arrives.
- The first observation after pressing Start is silent, so pre-existing
  unread messages never phantom-alert.

## Files

- `mtalk_notifier.py` — the entire app (single file, ~180 lines).
- `requirements.txt` — one dependency: `uiautomation`.
- `warning.mp3` — you provide.

Sound playback uses Windows' built-in Media Control Interface (MCI, via
`ctypes` + `winmm.dll`), so no audio-library dependency is required.
