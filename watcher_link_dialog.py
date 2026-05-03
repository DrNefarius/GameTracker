"""
Dialog for linking an executable (or its install folder) to a library game.

The watcher's auto-detection covers Steam / Epic / GOG installs out of the
box, but plenty of titles fall outside that net: standalone old games
shipped on a CD twenty years ago, MMOs whose installer drops a folder
straight onto your D: drive (Guild Wars, EVE Online, classic WoW
installations), and any indie game you got off itch.io that doesn't go
through a launcher. Strict mode (on by default) explicitly *skips* those
binaries to avoid mistaking a random Windows utility for a game, so the
auto-learn flow never gets a chance to fire on them.

This dialog is the user-facing fix:

  1. Pick an executable for the game in question.
  2. Choose whether to map just that one .exe (single-binary games) or
     the whole install folder (launcher + renderer pairs, MMOs that
     update via a separate updater process, etc).
  3. We persist the mapping AND auto-add the parent directory to
     `watcher_user_roots` so strict mode lets the path through.

Returns True when the user committed a mapping, False otherwise. Caller
is expected to handle UI refresh (the watcher picks up the new mapping
from config on its next tick - no restart needed).
"""

from __future__ import annotations

import os
from typing import Any, Optional

import PySimpleGUI as sg

from utilities import calculate_popup_center_location
from watcher_log import bridge_logger

_log = bridge_logger()


def _is_console_platform(platform: Optional[str]) -> bool:
    """Best-effort 'is this a console game?' check.

    We surface a friendly warning when the user tries to link an .exe to
    a game whose stored platform is a console - chances are they meant
    to use the tray's "Start Console Session" instead, since linking an
    emulator's .exe to e.g. "Super Mario 64" would mistakenly track
    every emulator launch as that one game.
    """
    if not platform:
        return False
    pl = platform.lower()
    keywords = (
        'switch', 'playstation', 'ps1', 'ps2', 'ps3', 'ps4', 'ps5',
        'xbox', 'wii', 'gamecube', 'n64', 'nintendo', 'sega', 'genesis',
        'dreamcast', 'saturn', 'gameboy', 'game boy', 'gba', 'ds ',
        '3ds', 'snes', 'nes ', 'atari', 'commodore', 'arcade',
    )
    return any(k in pl for k in keywords)


def show_link_executable_dialog(
    game_name: str,
    game_platform: Optional[str] = None,
    parent_window: Any = None,
) -> bool:
    """Modal dialog. Returns True if a mapping was created, False otherwise."""
    try:
        from process_watcher import get_watcher
    except Exception as exc:  # noqa: BLE001
        sg.popup_error(f"Watcher unavailable:\n{exc}",
                       title="Link Executable", keep_on_top=True)
        return False
    watcher = get_watcher()
    if watcher is None:
        sg.popup_error(
            "The process watcher hasn't been initialized in this session.\n"
            "Enable it in Process Watcher Settings first.",
            title="Link Executable", keep_on_top=True)
        return False

    layout = [
        [sg.Text(f"Link an executable to '{game_name}'",
                 font=('Arial', 12, 'bold'))],
        [sg.Text(
            "Pick the .exe that launches this game. The watcher will\n"
            "track sessions automatically the next time it runs.",
            text_color='#555555')],
        [sg.HorizontalSeparator()],

        [sg.Text("Executable:", size=(11, 1)),
         sg.Input(key='-LINK-EXE-', size=(48, 1), enable_events=True),
         sg.FileBrowse(
             "Browse...",
             target='-LINK-EXE-',
             file_types=(('Executables', '*.exe'), ('All files', '*.*')))],

        [sg.Frame("Match scope", [
            [sg.Radio(
                "Just this executable",
                group_id='-LINK-SCOPE-', default=True,
                key='-LINK-SCOPE-EXE-',
                tooltip=("Most games. Only sessions started by this\n"
                         "specific .exe will be tracked."))],
            [sg.Radio(
                "Any executable in this folder",
                group_id='-LINK-SCOPE-', default=False,
                key='-LINK-SCOPE-DIR-',
                tooltip=("Use this for MMOs and games whose launcher .exe\n"
                         "spawns a separate renderer .exe (Guild Wars,\n"
                         "Guild Wars 2's two binaries, games that update\n"
                         "via a dedicated updater process)."))],
        ], expand_x=True)],

        [sg.Text(
            "We'll also add the parent folder to the strict-mode\n"
            "whitelist so this exe isn't filtered out.",
            font=('Arial', 8), text_color='#555555')],

        [sg.HorizontalSeparator()],
        [sg.Push(),
         sg.Button("Link", key='-LINK-OK-', disabled=True),
         sg.Button("Cancel", key='-LINK-CANCEL-')],
    ]

    location = calculate_popup_center_location(
        parent_window, 580, 320) if parent_window else (None, None)
    win = sg.Window(
        "Link Executable",
        layout,
        modal=True,
        finalize=True,
        keep_on_top=True,
        icon='gameslisticon.ico',
        location=location,
    )

    linked = False
    while True:
        ev, vals = win.read()
        if ev in (sg.WIN_CLOSED, '-LINK-CANCEL-'):
            break

        if ev == '-LINK-EXE-':
            # Enable Link only when an existing .exe is selected.
            path = (vals.get('-LINK-EXE-') or '').strip()
            ok = bool(path) and os.path.isfile(path) \
                and path.lower().endswith('.exe')
            win['-LINK-OK-'].update(disabled=not ok)
            continue

        if ev == '-LINK-OK-':
            exe_path = (vals.get('-LINK-EXE-') or '').strip()
            if not exe_path or not os.path.isfile(exe_path):
                sg.popup_error("That file doesn't exist.",
                               title="Link Executable", keep_on_top=True)
                continue
            scope_dir = bool(vals.get('-LINK-SCOPE-DIR-'))

            # Console-platform sanity check: linking an emulator binary
            # to one specific console title is almost always a mistake -
            # the same emulator launches every game on the platform, so
            # the mapping would mis-attribute every future emulator
            # session to whichever title the user happened to link.
            if _is_console_platform(game_platform):
                proceed = sg.popup_yes_no(
                    f"'{game_name}' is stored as a console game "
                    f"({game_platform}).\n\n"
                    "Linking an emulator .exe here will cause every future\n"
                    "launch of that emulator to be tracked as this one\n"
                    "game, even when you're playing other titles on the\n"
                    "same emulator.\n\n"
                    "Use the tray's 'Start Console Session' for console\n"
                    "titles instead.\n\n"
                    "Link anyway?",
                    title="Probably not what you want",
                    keep_on_top=True)
                if proceed != 'Yes':
                    continue

            try:
                # 1. Persist the mapping (exe-only or install-dir prefix).
                if scope_dir:
                    install_dir = os.path.dirname(exe_path)
                    watcher.remember_installdir_mapping(install_dir, game_name)
                    _log.info("link dialog: installdir mapping %s -> %r",
                              install_dir, game_name)
                else:
                    watcher.remember_mapping(exe_path, game_name)
                    _log.info("link dialog: exe mapping %s -> %r",
                              exe_path, game_name)

                # 2. Whitelist the parent folder for strict-mode resolution.
                added = watcher.add_user_root(os.path.dirname(exe_path))
                root_msg = (
                    "added the parent folder to watch roots."
                    if added else "(parent folder already on watch list)")

                # 3. Force the resolver to reconsider any process for
                #    this exe / folder that's already running. Without
                #    this the user would have to relaunch the game to
                #    see the new mapping take effect.
                try:
                    if scope_dir:
                        watcher.recheck_install_dir(
                            os.path.dirname(exe_path))
                    else:
                        watcher.recheck_exe(exe_path)
                except Exception as exc:  # noqa: BLE001
                    _log.debug("link dialog: recheck after link failed: %s",
                               exc)
            except Exception as exc:  # noqa: BLE001
                sg.popup_error(
                    f"Saving the link failed:\n{exc}",
                    title="Link Executable", keep_on_top=True)
                continue

            linked = True
            scope_label = ("Any .exe in the install folder"
                           if scope_dir else os.path.basename(exe_path))
            sg.popup_quick_message(
                f"Linked.\nMatch scope: {scope_label}\n{root_msg}",
                keep_on_top=True, background_color='#2d6a4f',
                text_color='white')
            break

    win.close()
    return linked
