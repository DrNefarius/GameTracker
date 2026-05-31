"""Version2 entry point - launches the Flet UI.

Run in development with:   flet run app_flet.py
Or directly with Python:   python app_flet.py

The legacy PySimpleGUI entry point (main.py) is left untouched during the
migration so both UIs remain runnable.
"""

import os
import sys

import flet as ft

from single_instance import try_acquire
from ui_flet.app import main


if __name__ == "__main__":
    # Refuse to start a second copy. If one is already running, it was pinged to
    # bring its window to the foreground (it may be hidden in the system tray),
    # so we just exit. The returned guard is kept alive by the single_instance
    # module global for this process's lifetime.
    if try_acquire() is None:
        print("GameTracker is already running - "
              "bringing the existing window to the front.")
        # Flush first: os._exit() skips buffer flushing, but we need it so the
        # message still reaches a piped/redirected stdout. Use os._exit (not
        # sys.exit) so we terminate immediately instead of waiting on non-daemon
        # threads the launcher may have started - e.g. `flet run`'s hot-reload
        # file watcher, which otherwise keeps the process alive until Ctrl+C.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    ft.run(main)
