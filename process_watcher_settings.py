"""
Settings dialog for the process watcher (Phase H).

Provides ``show_process_watcher_settings_dialog(parent_window)``, modeled on
``igdb_ui.show_igdb_settings_dialog``. It edits the watcher_* and
notifications_* keys in ``config.json`` and applies live changes to the
running watcher / tray.
"""

from __future__ import annotations

from typing import Optional

import os
import subprocess
import sys

import PySimpleGUI as sg

from config import load_config, save_config
from utilities import calculate_popup_center_location
from watcher_log import get_log_dir, get_log_path, set_level as set_watcher_log_level


_QUIET_HOURS_HELP = (
    "Format: HH:MM-HH:MM (24h). Leave blank to disable. "
    "Example: 23:00-08:00 spans midnight."
)


def show_process_watcher_settings_dialog(
    parent_window=None,
    library_names: Optional[list] = None,
) -> Optional[dict]:
    """Modal settings dialog. Returns the new config dict on save, else None.

    `library_names` is an optional list of game titles from the user's
    library; when provided, the "Add mapping..." picker uses it as the
    game-name combobox source so the user can't typo the title. Falls
    back to free-text input when omitted (legacy callers).
    """
    config = load_config()

    qh = config.get('notifications_quiet_hours')
    qh_text = f"{qh[0]}-{qh[1]}" if qh and len(qh) == 2 else ''

    process_map = config.get('watcher_process_map', {}) or {}
    mapping_rows = [[exe, name] for exe, name in sorted(process_map.items())]
    installdir_map = config.get('watcher_installdir_map', {}) or {}
    user_roots = list(config.get('watcher_user_roots') or [])

    layout = [
        [sg.Text("Process Watcher Settings", font=('Arial', 14, 'bold'))],
        [sg.HorizontalSeparator()],

        [sg.Frame("General", [
            [sg.Checkbox("Enable process watcher (auto-track sessions)",
                         default=config.get('watcher_enabled', False),
                         key='-WATCHER-ENABLED-')],
            [sg.Checkbox("Strict mode: only watch known game-library folders",
                         default=config.get('watcher_strict_mode', True),
                         key='-WATCHER-STRICT-')],
            [sg.Checkbox("Only count time when the game window is in the foreground",
                         default=config.get('watcher_foreground_only', False),
                         key='-WATCHER-FOREGROUND-')],
            [sg.Text("    Pause after focus has been away for (seconds, 0 = instant):",
                     text_color='#555555'),
             sg.Spin([i for i in range(0, 301)],
                     initial_value=int(config.get(
                         'watcher_foreground_pause_grace_seconds', 30)),
                     key='-WATCHER-FG-GRACE-', size=(5, 1),
                     tooltip=("Quick alt-tabs shorter than this aren't\n"
                              "logged as a pause. Useful for replying to\n"
                              "messages or peeking at a wiki without\n"
                              "polluting the session log."))],
            [sg.Text("Pause after this many idle minutes (0 = never):"),
             sg.Spin([i for i in range(0, 121)],
                     initial_value=int(config.get('watcher_idle_pause_minutes', 10)),
                     key='-WATCHER-IDLE-', size=(5, 1))],
        ], expand_x=True)],

        [sg.Frame("Notifications", [
            [sg.Checkbox("Toast when a session starts",
                         default=config.get('notifications_on_start', True),
                         key='-NOTIF-START-')],
            [sg.Checkbox("Toast when a session ends",
                         default=config.get('notifications_on_end', True),
                         key='-NOTIF-END-')],
            [sg.Checkbox("Toast when a detected game needs to be matched",
                         default=config.get('notifications_on_match_needed', True),
                         key='-NOTIF-MATCH-')],
            [sg.Text("Quiet hours:"),
             sg.Input(default_text=qh_text, key='-NOTIF-QH-', size=(15, 1)),
             sg.Text("(HH:MM-HH:MM)", text_color='#555555')],
            [sg.Text(_QUIET_HOURS_HELP, font=('Arial', 8), text_color='#555555')],
            [sg.Checkbox("Show toasts even in fullscreen games (bypass Focus Assist)",
                         default=config.get('notifications_bypass_focus_assist', False),
                         key='-NOTIF-FA-BYPASS-',
                         tooltip=("Windows Focus Assist normally hides toasts while a game\n"
                                  "is in fullscreen. Enabling this escalates toasts to the\n"
                                  "Reminder scenario so they pop through. Trade-off:\n"
                                  "the toast stays on screen until you dismiss it."))],
        ], expand_x=True)],

        [sg.Frame("System tray", [
            [sg.Checkbox("Show tray icon",
                         default=config.get('tray_icon_enabled', True),
                         key='-TRAY-ENABLED-')],
        ], expand_x=True)],

        [sg.Frame("Logging (for debugging detection issues)", [
            [sg.Text("Log level:"),
             sg.Combo(['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                      default_value=str(config.get('watcher_log_level', 'INFO')).upper(),
                      key='-LOG-LEVEL-', size=(10, 1), readonly=True),
             sg.Button("Open log folder", key='-OPEN-LOG-DIR-'),
             sg.Button("Open log file", key='-OPEN-LOG-FILE-')],
            [sg.Text(
                "DEBUG includes per-tick decisions (resolver layers tried, "
                "fuzzy scores, idle seconds).",
                font=('Arial', 8), text_color='#555555')],
            [sg.Text(f"Log path: {get_log_path()}",
                     font=('Arial', 8), text_color='#555555')],
        ], expand_x=True)],

        [sg.Frame("Watch folders", [
            [sg.Text(
                "Strict mode also resolves processes whose .exe lives under\n"
                "any of these folders. Add e.g. D:/Games for an unmanaged\n"
                "library, or the install folder of an MMO / standalone game\n"
                "that isn't on Steam/Epic/GOG.",
                font=('Arial', 8), text_color='#555555')],
            [sg.Listbox(
                values=user_roots or ['(none added yet)'],
                key='-USER-ROOTS-',
                size=(80, 4),
                expand_x=True,
                enable_events=True,
                no_scrollbar=False,
                select_mode=sg.LISTBOX_SELECT_MODE_SINGLE)],
            [sg.Button("Add folder...", key='-ROOT-ADD-'),
             sg.Button("Remove selected", key='-ROOT-REMOVE-')],
        ], expand_x=True)],

        [sg.Frame("Learned mappings", [
            [sg.Text(
                "Per-executable game associations - the watcher's first\n"
                "lookup. Add manual links here for games you want tracked\n"
                "but that aren't installed via a recognized launcher.",
                font=('Arial', 8), text_color='#555555')],
            [sg.Table(
                values=mapping_rows or [['(none yet)', '']],
                headings=['Executable path', 'Mapped to game'],
                auto_size_columns=False,
                col_widths=[60, 25],
                num_rows=8,
                key='-MAPPINGS-',
                expand_x=True,
                enable_events=True,
            )],
            [sg.Button("Add mapping...", key='-MAPPING-ADD-'),
             sg.Button("Forget selected mapping", key='-FORGET-MAPPING-')],
        ], expand_x=True)],

        [sg.Push(), sg.Button("Save"), sg.Button("Cancel")],
    ]

    location = None
    if parent_window is not None:
        location = calculate_popup_center_location(
            parent_window, popup_width=700, popup_height=620)

    window = sg.Window("Process Watcher Settings", layout, modal=True,
                       icon='gameslisticon.ico', finalize=True, location=location)

    selected_exe: Optional[str] = None

    def _refresh_user_roots():
        """Repaint the user_roots listbox in place. Empty list shows a hint."""
        window['-USER-ROOTS-'].update(
            values=user_roots or ['(none added yet)'])

    while True:
        event, values = window.read()

        if event in (sg.WIN_CLOSED, 'Cancel'):
            window.close()
            return None

        if event == '-MAPPINGS-':
            sel = values.get('-MAPPINGS-') or []
            if sel and mapping_rows:
                selected_exe = mapping_rows[sel[0]][0]

        if event == '-FORGET-MAPPING-':
            if selected_exe and selected_exe in process_map:
                process_map.pop(selected_exe, None)
                mapping_rows = [[e, n] for e, n in sorted(process_map.items())]
                window['-MAPPINGS-'].update(values=mapping_rows or [['(none yet)', '']])
                selected_exe = None

        # ---- Watch folders ------------------------------------------------
        if event == '-ROOT-ADD-':
            picked = sg.popup_get_folder(
                "Pick a folder to add to the strict-mode whitelist",
                title="Add watch folder",
                keep_on_top=True,
                no_window=True)
            if picked:
                normalized = os.path.abspath(picked)
                # Case-insensitive dedupe so users can't add D:/Games and
                # d:\\Games\\ as separate entries.
                seen = {os.path.normcase(os.path.normpath(r))
                        for r in user_roots}
                if os.path.normcase(os.path.normpath(normalized)) in seen:
                    sg.popup_quick_message(
                        "That folder is already on the watch list.",
                        keep_on_top=True, background_color='#444',
                        text_color='white')
                else:
                    user_roots.append(normalized)
                    _refresh_user_roots()

        if event == '-ROOT-REMOVE-':
            sel = values.get('-USER-ROOTS-') or []
            if sel and sel[0] in user_roots:
                user_roots.remove(sel[0])
                _refresh_user_roots()

        # ---- Manually add a process_map entry -----------------------------
        if event == '-MAPPING-ADD-':
            new_entry = _prompt_for_new_mapping(window, library_names)
            if new_entry is not None:
                exe_norm, game_name = new_entry
                process_map[exe_norm] = game_name
                mapping_rows = [[e, n] for e, n in sorted(process_map.items())]
                window['-MAPPINGS-'].update(
                    values=mapping_rows or [['(none yet)', '']])
                # Auto-whitelist the parent folder so strict mode lets the
                # exe through. Same convenience as the per-game Link
                # dialog - users shouldn't have to do this in two steps.
                parent_folder = os.path.dirname(exe_norm)
                if parent_folder:
                    seen = {os.path.normcase(os.path.normpath(r))
                            for r in user_roots}
                    if os.path.normcase(
                            os.path.normpath(parent_folder)) not in seen:
                        user_roots.append(parent_folder)
                        _refresh_user_roots()

        if event == '-OPEN-LOG-DIR-':
            _open_path_in_explorer(get_log_dir())

        if event == '-OPEN-LOG-FILE-':
            log_file = get_log_path()
            if not os.path.exists(log_file):
                # The rotating handler uses delay=True so the file may not
                # yet exist if no log line has been written. Create an empty
                # one rather than failing the open.
                try:
                    open(log_file, 'a', encoding='utf-8').close()
                except Exception:
                    pass
            _open_path_in_explorer(log_file)

        if event == 'Save':
            qh_input = (values.get('-NOTIF-QH-') or '').strip()
            new_qh = None
            if qh_input:
                try:
                    a, b = qh_input.split('-', 1)
                    # Validate HH:MM
                    for token in (a, b):
                        h, m = token.split(':')
                        int(h); int(m)
                    new_qh = [a.strip(), b.strip()]
                except Exception:
                    sg.popup_error(
                        "Quiet hours must be in HH:MM-HH:MM format (e.g. 23:00-08:00).",
                        title="Invalid quiet hours")
                    continue

            new_log_level = (values.get('-LOG-LEVEL-') or 'INFO').upper()
            config.update({
                'watcher_enabled': bool(values['-WATCHER-ENABLED-']),
                'watcher_strict_mode': bool(values['-WATCHER-STRICT-']),
                'watcher_foreground_only': bool(values['-WATCHER-FOREGROUND-']),
                'watcher_foreground_pause_grace_seconds':
                    int(values['-WATCHER-FG-GRACE-']),
                'watcher_idle_pause_minutes': int(values['-WATCHER-IDLE-']),
                'notifications_on_start': bool(values['-NOTIF-START-']),
                'notifications_on_end': bool(values['-NOTIF-END-']),
                'notifications_on_match_needed': bool(values['-NOTIF-MATCH-']),
                'notifications_quiet_hours': new_qh,
                'notifications_bypass_focus_assist': bool(values['-NOTIF-FA-BYPASS-']),
                'tray_icon_enabled': bool(values['-TRAY-ENABLED-']),
                'watcher_process_map': process_map,
                'watcher_user_roots': user_roots,
                'watcher_log_level': new_log_level,
            })
            save_config(config)
            # Apply the log level immediately - no restart needed.
            try:
                set_watcher_log_level(new_log_level)
            except Exception:
                pass
            window.close()
            return config


def _prompt_for_new_mapping(parent_window,
                            library_names: Optional[list]
                            ) -> Optional[tuple]:
    """Small modal: pick an .exe + game name. Returns (normalized_exe, name).

    Used by the "Add mapping..." button on the Learned Mappings frame.
    When `library_names` is supplied the game-name field is a Combo
    populated from the user's library (with type-ahead); otherwise it
    falls back to a free-text Input so the dialog still works for
    legacy callers that don't have library data handy.
    """
    if library_names:
        name_field = sg.Combo(
            sorted(library_names, key=str.lower),
            key='-ADD-MAP-NAME-',
            size=(45, 1),
            enable_events=False,
        )
    else:
        name_field = sg.Input(key='-ADD-MAP-NAME-', size=(47, 1))

    layout = [
        [sg.Text("Add a manual exe -> game mapping",
                 font=('Arial', 11, 'bold'))],
        [sg.Text("Executable:", size=(11, 1)),
         sg.Input(key='-ADD-MAP-EXE-', size=(47, 1)),
         sg.FileBrowse(
             "Browse...",
             target='-ADD-MAP-EXE-',
             file_types=(('Executables', '*.exe'), ('All files', '*.*')))],
        [sg.Text("Game name:", size=(11, 1)),
         name_field],
        [sg.Text(
            "The parent folder will be added to the watch list\n"
            "automatically so strict mode lets the exe through.",
            font=('Arial', 8), text_color='#555555')],
        [sg.Push(),
         sg.Button("Add", key='-ADD-MAP-OK-'),
         sg.Button("Cancel", key='-ADD-MAP-CANCEL-')],
    ]

    location = calculate_popup_center_location(
        parent_window, 560, 220) if parent_window else (None, None)
    win = sg.Window(
        "Add Mapping",
        layout,
        modal=True,
        finalize=True,
        keep_on_top=True,
        icon='gameslisticon.ico',
        location=location,
    )

    result: Optional[tuple] = None
    while True:
        ev, vals = win.read()
        if ev in (sg.WIN_CLOSED, '-ADD-MAP-CANCEL-'):
            break
        if ev == '-ADD-MAP-OK-':
            exe_path = (vals.get('-ADD-MAP-EXE-') or '').strip()
            name = (vals.get('-ADD-MAP-NAME-') or '').strip()
            if not exe_path or not os.path.isfile(exe_path):
                sg.popup_error("Pick an existing .exe file.",
                               title="Add Mapping", keep_on_top=True)
                continue
            if not name:
                sg.popup_error("Game name is required.",
                               title="Add Mapping", keep_on_top=True)
                continue
            # Use os.path.normcase so the persisted key matches the
            # watcher's resolver lookup (which lower-cases on Windows).
            normalized = os.path.normcase(os.path.normpath(exe_path))
            result = (normalized, name)
            break
    win.close()
    return result


def _open_path_in_explorer(path: str) -> None:
    """Open `path` in Windows Explorer (or the platform equivalent)."""
    try:
        if sys.platform == 'win32':
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])
    except Exception as exc:
        sg.popup_error(f"Could not open {path}:\n{exc}", title="Open log")
