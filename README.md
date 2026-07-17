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
- `debug_mtalk.py` — optional troubleshooting script (see below).
- `requirements.txt` — one dependency: `uiautomation`.
- `warning.mp3` — you provide.

Sound playback uses Windows' built-in Media Control Interface (MCI, via
`ctypes` + `winmm.dll`), so no audio-library dependency is required.

## Troubleshooting: "it doesn't alert when X sends a message"

The notifier only watches the two targets in `mtalk_notifier.py`:

```
ROOM    = "NOC Internal (Handover)"
ACCOUNT = "GNMC"
```

So if a message comes from a **different** chat / DM (say a direct
message from a person like `Vo Thanh Hai`), the app has nothing to alert
on. Two ways to diagnose and fix:

### 1. Dump what UI Automation actually sees

```
python debug_mtalk.py --search "Vo Thanh Hai"
```

The script walks the MTalk window with UI Automation and prints every
named node it finds. Nodes matching your `--search` are marked `MATCH`,
nodes matching the current `ROOM`/`ACCOUNT` constants are marked, and
anything that looks like an unread badge (a small integer next to a chat
row) is marked `[BADGE?]`. Useful things to look for:

- Does the chat with that person appear at all? (If not, the chat needs
  to be visible in MTalk's chat list for UIA to expose it.)
- What is its exact name? (Spelling, punctuation, extra prefix.)
- Is there a small integer next to it, or an obvious `(N)` suffix?

Save the output for reference:

```
python debug_mtalk.py --search "Vo Thanh Hai" --output tree.txt
```

### 2. Add a third target

Once you know the exact name UIA uses, add it to the polling loop.
Edit two lines in `mtalk_notifier.py`:

```python
# top of the file
EXTRA = "Vo Thanh Hai"

# inside App.check_once():
for target in (ROOM, ACCOUNT, EXTRA):
    ...
```

That's it — the popup, mute, and dedup logic already work with any
target name.

If the debug script shows that MTalk exposes the unread count in a way
that the current extractor doesn't recognise (e.g. a colored dot rather
than a number, or a badge in a sibling node with a specific
AutomationId), share the relevant lines from `tree.txt` and the
extractor can be adjusted to match.
