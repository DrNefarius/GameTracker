"""Version2 entry point - launches the Flet UI.

Run in development with:   flet run app_flet.py
Or directly with Python:   python app_flet.py

The legacy PySimpleGUI entry point (main.py) is left untouched during the
migration so both UIs remain runnable.
"""

import flet as ft

from ui_flet.app import main


if __name__ == "__main__":
    ft.run(main)
