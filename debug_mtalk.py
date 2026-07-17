"""Diagnostic dumper for the MTalk UI Automation tree.

Use when the notifier is not firing for a message you expect it to catch:

    python debug_mtalk.py                            # print every named node
    python debug_mtalk.py --search "Vo Thanh Hai"    # highlight matches
    python debug_mtalk.py --all                      # include unnamed nodes too
    python debug_mtalk.py --output tree.txt          # write to file for inspection

The script prints each UI Automation node's control type, name, and
automation id so you can see exactly what MTalk exposes to UIA - which is
what mtalk_notifier.py searches. From the output you can answer:

  1. What is the sender's chat actually called in UIA?
     (spelling, spacing, language, extra prefix/suffix)
  2. Is there an unread badge (a small integer) next to it, and where -
     baked into the name, or in a sibling node?
  3. Does the DM appear as a top-level chat item at all when a message
     is unread?

Then either fix the target name in mtalk_notifier.py, or add a new target
by editing check_once() in that file (see the note at the end of the
script's output).
"""

from __future__ import annotations

import argparse
import sys

from mtalk_notifier import ACCOUNT, ROOM, find_mtalk_window


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dump MTalk's UI Automation tree for troubleshooting.",
    )
    parser.add_argument(
        "--search",
        default=None,
        help="Highlight nodes whose name contains this text (case-insensitive).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Print ALL nodes (default: only nodes with a non-empty name).",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=5000,
        help="Cap on total nodes visited (default: 5000).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write output to this file instead of stdout.",
    )
    args = parser.parse_args()

    try:
        import uiautomation as auto  # type: ignore
    except Exception as exc:
        print("uiautomation is not installed. Run:  pip install uiautomation")
        print(f"  ({exc})")
        return 2

    out = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout

    def w(msg: str = "") -> None:
        print(msg, file=out)

    window = find_mtalk_window()
    if window is None:
        w("MTalk window not found. Is MTalk running and visible?")
        w("Looking for a top-level window whose title contains: "
          "MTalk / mTalk / \uc5e0\ud1a1")
        w("")
        w("Top-level windows currently on the desktop:")
        try:
            for top in auto.GetRootControl().GetChildren():
                nm = (top.Name or "").strip()
                if nm:
                    w(f"  - {nm!r}")
        except Exception as exc:
            w(f"  (could not enumerate: {exc})")
        if args.output:
            out.close()
        return 3

    w(f"Found MTalk window: {window.Name!r}  (hwnd={window.NativeWindowHandle})")
    w("Current targets in mtalk_notifier.py:")
    w(f"  ROOM    = {ROOM!r}")
    w(f"  ACCOUNT = {ACCOUNT!r}")
    if args.search:
        w(f"Searching for: {args.search!r}")
    w("-" * 78)

    total = 0
    matches = 0
    search_lower = args.search.lower() if args.search else None
    room_lower = ROOM.lower()
    acc_lower = ACCOUNT.lower()

    def walk(node, depth: int) -> None:
        nonlocal total, matches
        if total >= args.max_nodes:
            return
        total += 1
        try:
            name = (node.Name or "").strip()
        except Exception:
            name = ""
        try:
            ctype = node.ControlTypeName
        except Exception:
            ctype = "?"
        try:
            aid = (node.AutomationId or "").strip()
        except Exception:
            aid = ""

        if name or args.all:
            marks = []
            low = name.lower()
            if search_lower and low and search_lower in low:
                marks.append("MATCH")
                matches += 1
            if low and room_lower in low:
                marks.append("<-- ROOM target")
            if low and acc_lower in low:
                marks.append("<-- ACCOUNT target")
            stripped = name.rstrip("+").strip()
            if stripped.isdigit() and 1 <= len(stripped) <= 4:
                marks.append("[BADGE?]")
            mark_str = ("   " + " ".join(marks)) if marks else ""
            indent = "  " * min(depth, 12)
            aid_bit = f" aid={aid!r}" if aid else ""
            w(f"{indent}[{ctype}] {name!r}{aid_bit}{mark_str}")

        try:
            children = node.GetChildren()
        except Exception:
            children = []
        for c in children:
            walk(c, depth + 1)

    walk(window, 0)

    w("-" * 78)
    w(f"Total nodes visited: {total}"
      f"{' (limit hit)' if total >= args.max_nodes else ''}")
    if args.search:
        w(f"Matches for {args.search!r}: {matches}")
    w("")
    w("Legend:  [ControlType] name  aid=<automation id>  <marks>")
    w("Marks:")
    w("  MATCH              node name contains --search text")
    w("  <-- ROOM/ACCOUNT   name contains a current target constant")
    w("  [BADGE?]           name is a small integer (likely an unread badge)")
    w("")
    w("Next steps once you have identified the correct chat name:")
    w("")
    w("  1. Open mtalk_notifier.py.")
    w("  2. To *rename* an existing target, change ROOM or ACCOUNT at the top.")
    w("  3. To *add* a third target, extend the tuple in check_once():")
    w("")
    w('        for target in (ROOM, ACCOUNT, \"Vo Thanh Hai\"):')
    w("")
    w("     That is the only change needed - the popup and mute logic")
    w("     already handle arbitrary target names.")

    if args.output:
        out.close()
        print(f"Wrote {args.output}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
