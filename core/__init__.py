"""UI-agnostic service layer for the GameTracker app.

This package wraps the existing backend (data_management, session_data,
process_watcher, etc.) behind a thin facade so the UI - currently the new Flet
UI in ``ui_flet/`` - never imports a specific GUI toolkit's modules and the
backend never imports the UI. Nothing in here may import ``flet`` or
``PySimpleGUI``.
"""
