"""Silent Windows launcher for the MTalk notifier.

Double-click this file (or run it via ``pythonw.exe``) to start the notifier
without a visible console window. All output still goes to
``logs/mtalk_notifier.log``.
"""

from src.main import main

if __name__ == "__main__":
    main([])
