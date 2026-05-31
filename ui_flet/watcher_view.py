"""Process-watcher settings dialog for the Flet UI.

A dependency-free (no PySimpleGUI) port of the watcher settings dialog from
``process_watcher_settings.show_process_watcher_settings_dialog``. It edits the
``watcher_*`` / ``notifications_*`` / ``tray_icon_enabled`` config keys through
``service.config`` and persists them with ``config.save_config(service.config)``.

UX mirrors the legacy PySimpleGUI dialog's grouping:
  - General: enable watcher, strict mode, foreground-only + grace seconds,
    idle-pause minutes.
  - Notifications: on-start / on-end / on-match-needed toggles, quiet hours,
    bypass Focus Assist.
  - System tray: show tray icon.
  - Logging: log-level dropdown (DEBUG/INFO/WARNING/ERROR) + Open log folder /
    Open log file buttons + the resolved log path.
  - Extra watched folders (``watcher_user_roots``) and ignored process names
    (``watcher_ignore_list``): each a bounded, scrollable list with per-row
    delete + an "Add" field.
  - Learned mappings (``watcher_process_map``): the watcher's per-executable
    exe->game associations (its first-layer lookup), shown as a bounded,
    scrollable list with per-row "forget" plus an "Add mapping..." picker
    (browse to an .exe + pick a library game; the parent folder is
    auto-whitelisted, mirroring the legacy dialog).

Every variable-length list is wrapped in a fixed-height scrolling Container so a
large library / many learned mappings can't make the dialog explode vertically.

Flet 0.85.2 has no Spin control, so the two integer fields (idle minutes,
foreground grace seconds) are plain ``TextField`` widgets coerced to ``int`` in
``get_values`` (non-numeric input falls back to the existing/default value).

``_build_watcher_content`` is split out so the body can be constructed and
inspected without a live Flet page (used by the headless tests); it returns
``(content, get_values, wiring)`` where ``wiring`` exposes the hooks the live
dialog attaches a page-dependent FilePicker flow to.
"""

import os
import subprocess
import sys

import flet as ft

import config as config_module
from watcher_log import get_log_dir, get_log_path, set_level as set_watcher_log_level
from ui_flet.watcher_dialogs import _build_game_picker

_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


def _coerce_int(value, fallback, minimum=0, maximum=None):
    """Best-effort int coercion for a TextField value.

    Returns ``fallback`` (already an int) when ``value`` is blank or not a
    valid integer. The result is clamped to ``[minimum, maximum]``.
    """
    try:
        result = int(str(value).strip())
    except (TypeError, ValueError):
        result = int(fallback)
    if minimum is not None and result < minimum:
        result = minimum
    if maximum is not None and result > maximum:
        result = maximum
    return result


def _parse_quiet_hours(text):
    """Parse ``HH:MM-HH:MM`` into ``["HH:MM", "HH:MM"]`` or ``None``.

    Blank input yields ``None`` (disabled). Raises ``ValueError`` on a
    malformed (non-blank) string so the caller can show an error.
    """
    text = (text or "").strip()
    if not text:
        return None
    start, end = text.split("-", 1)
    for token in (start, end):
        hours, minutes = token.strip().split(":")
        int(hours)
        int(minutes)
    return [start.strip(), end.strip()]


def _safe_update(control):
    """Call ``control.update()`` only if it is mounted (it raises pre-mount)."""
    try:
        control.update()
    except Exception:  # noqa: BLE001
        pass


def _open_path(path):
    """Open ``path`` in the platform file manager. Returns True on success."""
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:  # noqa: BLE001
        return False


def _scroll_box(rows_column, height):
    """Wrap a (scrollable) Column in a fixed-height tinted container so a long
    list scrolls internally instead of growing the dialog without bound."""
    return ft.Container(
        content=rows_column,
        height=height,
        border_radius=8,
        padding=ft.Padding(4, 4, 4, 4),
        bgcolor=ft.Colors.with_opacity(0.03, ft.Colors.ON_SURFACE),
    )


def _build_editable_list(values, hint_text, empty_text, list_height=140):
    """Build an editable string-list editor with a bounded, scrollable body.

    Returns ``(control, get_items_fn, add_item_fn)``:
      - ``control`` is a Flet Column: a fixed-height scrolling list of rows
        (each a delete IconButton + label) followed by an "Add" TextField +
        button.
      - ``get_items_fn()`` returns the current list of strings (order
        preserved, blanks dropped).
      - ``add_item_fn(value)`` appends ``value`` (case-insensitive dedupe) and
        repaints - used by the Add-mapping flow to auto-whitelist parent folders.

    The list lives in a plain Python list captured in the closure so it works
    without a live page; updates are no-ops until the controls are mounted.
    """
    items = [str(v) for v in (values or []) if str(v).strip()]

    # Virtualized list (renders only visible rows) so a long folder / ignore
    # list stays smooth and the dialog can't grow without bound.
    rows_column = ft.ListView(spacing=4)
    add_field = ft.TextField(hint_text=hint_text, expand=True, dense=True)
    empty_label = ft.Text(empty_text, italic=True, size=12, color=ft.Colors.GREY)

    def _rebuild():
        rows = []
        if not items:
            rows.append(empty_label)
        for item in items:
            rows.append(_make_row(item))
        rows_column.controls = rows
        _safe_update(rows_column)

    def _make_row(item):
        def _delete(_):
            if item in items:
                items.remove(item)
            _rebuild()

        return ft.Row(
            [
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    icon_color=ft.Colors.RED,
                    tooltip="Remove",
                    on_click=_delete,
                ),
                ft.Text(item, expand=True, selectable=True),
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def add_item(value):
        new_value = (value or "").strip()
        if not new_value:
            return
        # Case-insensitive dedupe, matching the legacy dialog's behaviour.
        if new_value.lower() not in {i.lower() for i in items}:
            items.append(new_value)
        _rebuild()

    def _add(_):
        add_item(add_field.value)
        add_field.value = ""
        _safe_update(add_field)

    add_button = ft.Button("Add", icon=ft.Icons.ADD, on_click=_add)
    add_field.on_submit = _add

    _rebuild()

    control = ft.Column(
        [
            _scroll_box(rows_column, list_height),
            ft.Row([add_field, add_button], vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ],
        spacing=8,
        tight=True,
    )

    def get_items():
        return [str(i) for i in items]

    return control, get_items, add_item


def _build_mappings_editor(process_map, list_height=200):
    """Editor for the watcher's learned exe->game mappings.

    Returns ``(control, get_map_fn, add_mapping_fn)``:
      - ``control`` is a fixed-height scrolling list, one row per mapping
        (game name + exe path + a per-row "forget" button).
      - ``get_map_fn()`` returns the current ``{exe: game}`` dict.
      - ``add_mapping_fn(exe_norm, game)`` inserts / overwrites a mapping and
        repaints (used by the Add-mapping picker).
    """
    mapping = dict(process_map or {})

    # Virtualized so a library with many auto-learned mappings stays smooth.
    rows_column = ft.ListView(spacing=4)
    empty_label = ft.Text("(no learned mappings yet)", italic=True, size=12,
                          color=ft.Colors.GREY)

    def _rebuild():
        rows = []
        if not mapping:
            rows.append(empty_label)
        for exe, game in sorted(mapping.items(), key=lambda kv: kv[0].lower()):
            rows.append(_make_row(exe, game))
        rows_column.controls = rows
        _safe_update(rows_column)

    def _make_row(exe, game):
        def _forget(_):
            mapping.pop(exe, None)
            _rebuild()

        return ft.Row(
            [
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    icon_color=ft.Colors.RED,
                    tooltip="Forget this mapping",
                    on_click=_forget,
                ),
                ft.Column(
                    [
                        ft.Text(game, weight=ft.FontWeight.W_600, size=13, selectable=True),
                        ft.Text(exe, size=11, color=ft.Colors.ON_SURFACE_VARIANT,
                                selectable=True),
                    ],
                    spacing=0, tight=True, expand=True,
                ),
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def add_mapping(exe_norm, game):
        if exe_norm and game:
            mapping[exe_norm] = game
            _rebuild()

    _rebuild()

    control = _scroll_box(rows_column, list_height)

    def get_map():
        return dict(mapping)

    return control, get_map, add_mapping


def _section_title(text):
    return ft.Text(text, weight=ft.FontWeight.BOLD, size=14)


def _hint(text):
    return ft.Text(text, italic=True, size=11, color=ft.Colors.GREY)


def _build_watcher_content(service):
    """Build the watcher settings form.

    Returns ``(control, get_values_fn, wiring)`` where:
      - ``control`` is the Flet Control to drop into a dialog's ``content``.
      - ``get_values_fn()`` returns a dict mapping the watcher config keys to
        their new values (ints for numerics, bools for switches, lists for the
        editable lists, the ``{exe: game}`` learned-mapping dict, the log-level
        string, and ``notifications_quiet_hours`` as a 2-element list or None).
      - ``wiring`` is a dict exposing the page-dependent hooks the live dialog
        attaches to: ``mapping_add_button`` (the "Add mapping..." ft.Button),
        ``add_mapping(exe_norm, game)`` and ``add_root(folder)``.

    No live page is required to call this, so it is safe to use in tests (the
    Add-mapping button simply has no handler until the live dialog wires it).
    """
    cfg = getattr(service, "config", {}) or {}

    # ---- General --------------------------------------------------------
    enabled = ft.Switch(
        label="Enable process watcher (auto-track sessions)",
        value=bool(cfg.get("watcher_enabled", False)),
    )
    strict_mode = ft.Switch(
        label="Strict mode: only watch known game-library folders",
        value=bool(cfg.get("watcher_strict_mode", True)),
    )
    foreground_only = ft.Switch(
        label="Only count time when the game window is in the foreground",
        value=bool(cfg.get("watcher_foreground_only", False)),
    )
    fg_grace = ft.TextField(
        label="Pause after focus away for (seconds, 0 = instant)",
        value=str(_coerce_int(cfg.get("watcher_foreground_pause_grace_seconds", 30), 30)),
        width=320,
        keyboard_type=ft.KeyboardType.NUMBER,
        tooltip=(
            "Quick alt-tabs shorter than this aren't logged as a pause. "
            "Useful for replying to messages or peeking at a wiki without "
            "polluting the session log."
        ),
    )
    idle_minutes = ft.TextField(
        label="Pause after this many idle minutes (0 = never)",
        value=str(_coerce_int(cfg.get("watcher_idle_pause_minutes", 10), 10)),
        width=320,
        keyboard_type=ft.KeyboardType.NUMBER,
    )

    # ---- Notifications --------------------------------------------------
    notif_start = ft.Switch(
        label="Toast when a session starts",
        value=bool(cfg.get("notifications_on_start", True)),
    )
    notif_end = ft.Switch(
        label="Toast when a session ends",
        value=bool(cfg.get("notifications_on_end", True)),
    )
    notif_match = ft.Switch(
        label="Toast when a detected game needs to be matched",
        value=bool(cfg.get("notifications_on_match_needed", True)),
    )
    notif_bypass = ft.Switch(
        label="Show toasts even in fullscreen games (bypass Focus Assist)",
        value=bool(cfg.get("notifications_bypass_focus_assist", False)),
        tooltip=(
            "Windows Focus Assist normally hides toasts while a game is in "
            "fullscreen. Enabling this escalates toasts to the Reminder "
            "scenario so they pop through (they stay until dismissed)."
        ),
    )
    qh = cfg.get("notifications_quiet_hours")
    qh_text = f"{qh[0]}-{qh[1]}" if qh and len(qh) == 2 else ""
    quiet_hours = ft.TextField(
        label="Quiet hours (HH:MM-HH:MM, blank = off)",
        value=qh_text,
        hint_text="23:00-08:00",
        width=320,
    )

    # ---- System tray ----------------------------------------------------
    tray_enabled = ft.Switch(
        label="Show tray icon",
        value=bool(cfg.get("tray_icon_enabled", True)),
    )

    # ---- Logging --------------------------------------------------------
    current_level = str(cfg.get("watcher_log_level", "INFO")).upper()
    if current_level not in _LOG_LEVELS:
        current_level = "INFO"
    log_level = ft.Dropdown(
        label="Log level",
        value=current_level,
        options=[ft.dropdown.Option(key=lvl, text=lvl) for lvl in _LOG_LEVELS],
        width=200,
    )

    def _open_log_dir(_):
        _open_path(get_log_dir())

    def _open_log_file(_):
        log_file = get_log_path()
        if not os.path.exists(log_file):
            # The rotating handler uses delay=True so the file may not exist
            # yet; create an empty one rather than failing the open.
            try:
                open(log_file, "a", encoding="utf-8").close()
            except Exception:  # noqa: BLE001
                pass
        _open_path(log_file)

    log_buttons = ft.Row(
        [
            ft.OutlinedButton("Open log folder", icon=ft.Icons.FOLDER_OPEN,
                              on_click=_open_log_dir),
            ft.OutlinedButton("Open log file", icon=ft.Icons.DESCRIPTION,
                              on_click=_open_log_file),
        ],
        spacing=8, wrap=True,
    )

    # ---- Editable lists -------------------------------------------------
    roots_editor, get_roots, add_root = _build_editable_list(
        cfg.get("watcher_user_roots"),
        hint_text="e.g. D:\\Games",
        empty_text="(no extra folders added yet)",
    )
    ignore_editor, get_ignore, _add_ignore = _build_editable_list(
        cfg.get("watcher_ignore_list"),
        hint_text="e.g. launcher.exe",
        empty_text="(no ignored process names yet)",
    )

    # ---- Learned mappings ----------------------------------------------
    mappings_editor, get_map, add_mapping = _build_mappings_editor(
        cfg.get("watcher_process_map"),
    )
    mapping_add_button = ft.Button("Add mapping...", icon=ft.Icons.ADD_LINK)

    content = ft.Container(
        width=580,
        height=540,
        content=ft.Column(
            [
                _section_title("General"),
                enabled,
                strict_mode,
                foreground_only,
                fg_grace,
                idle_minutes,
                ft.Divider(),

                _section_title("Notifications"),
                notif_start,
                notif_end,
                notif_match,
                notif_bypass,
                quiet_hours,
                _hint("Quiet hours use 24h time; e.g. 23:00-08:00 spans midnight."),
                ft.Divider(),

                _section_title("System tray"),
                tray_enabled,
                ft.Divider(),

                _section_title("Logging (for debugging detection issues)"),
                ft.Row([log_level], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                log_buttons,
                _hint(
                    "DEBUG includes per-tick decisions (resolver layers tried, "
                    "fuzzy scores, idle seconds)."
                ),
                _hint(f"Log path: {get_log_path()}"),
                ft.Divider(),

                _section_title("Extra watched folders"),
                _hint(
                    "Strict mode also resolves processes whose .exe lives under "
                    "any of these folders (e.g. an unmanaged D:\\Games library)."
                ),
                roots_editor,
                ft.Divider(),

                _section_title("Ignored process names"),
                _hint(
                    "Basenames the watcher should never treat as a game "
                    "(e.g. launchers, helpers)."
                ),
                ignore_editor,
                ft.Divider(),

                _section_title("Learned mappings"),
                _hint(
                    "Per-executable game associations - the watcher's first "
                    "lookup. Auto-learned on confirmation; add manual links for "
                    "games not installed via a recognized launcher."
                ),
                mappings_editor,
                ft.Row([mapping_add_button]),
            ],
            scroll=ft.ScrollMode.AUTO,
            spacing=12,
        ),
    )

    def get_values():
        return {
            "watcher_enabled": bool(enabled.value),
            "watcher_strict_mode": bool(strict_mode.value),
            "watcher_foreground_only": bool(foreground_only.value),
            "watcher_foreground_pause_grace_seconds": _coerce_int(
                fg_grace.value,
                cfg.get("watcher_foreground_pause_grace_seconds", 30),
                minimum=0,
                maximum=300,
            ),
            "watcher_idle_pause_minutes": _coerce_int(
                idle_minutes.value,
                cfg.get("watcher_idle_pause_minutes", 10),
                minimum=0,
                maximum=120,
            ),
            "notifications_on_start": bool(notif_start.value),
            "notifications_on_end": bool(notif_end.value),
            "notifications_on_match_needed": bool(notif_match.value),
            "notifications_bypass_focus_assist": bool(notif_bypass.value),
            "notifications_quiet_hours": _parse_quiet_hours(quiet_hours.value),
            "tray_icon_enabled": bool(tray_enabled.value),
            "watcher_log_level": (log_level.value or "INFO").upper(),
            "watcher_user_roots": get_roots(),
            "watcher_ignore_list": get_ignore(),
            "watcher_process_map": get_map(),
        }

    wiring = {
        "mapping_add_button": mapping_add_button,
        "add_mapping": add_mapping,
        "add_root": add_root,
    }

    return content, get_values, wiring


def _library_names(service):
    """Sorted, deduped game titles from the library (for the mapping picker)."""
    seen = set()
    names = []
    for entry in (getattr(service, "data", None) or []):
        try:
            _idx, row = entry
            name = row[0]
        except Exception:  # noqa: BLE001
            continue
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        names.append(name)
    names.sort(key=str.lower)
    return names


def _open_add_mapping_dialog(page, picker, library_names, on_add, on_close):
    """Sub-dialog: browse to an .exe + pick a library game -> a new mapping.

    Mirrors the legacy ``_prompt_for_new_mapping``. On Add, normalizes the exe
    path the same way the watcher's resolver does (``normcase(normpath(...))``)
    and calls ``on_add(exe_norm, game)``; both Add and Cancel call ``on_close``
    so the caller can re-show the settings dialog (Flet shows one at a time).
    """
    exe_field = ft.TextField(label="Executable", hint_text="path to the game .exe",
                             expand=True)

    async def _browse(_):
        try:
            res = await picker.pick_files(
                dialog_title="Pick the game executable",
                allowed_extensions=["exe"],
                file_type=ft.FilePickerFileType.CUSTOM,
            )
        except Exception:  # noqa: BLE001
            res = None
        if res and getattr(res[0], "path", None):
            exe_field.value = res[0].path
            page.update()

    game_picker, get_game = _build_game_picker(library_names)
    error = ft.Text("", color=ft.Colors.ERROR, visible=False)

    def _show_err(msg):
        error.value = msg
        error.visible = True
        page.update()

    def _ok(_):
        exe = (exe_field.value or "").strip()
        if not exe or not os.path.isfile(exe):
            _show_err("Pick an existing .exe file.")
            return
        game = get_game()
        if not game:
            _show_err("Pick a game from your library.")
            return
        normalized = os.path.normcase(os.path.normpath(exe))
        page.pop_dialog()
        on_add(normalized, game)
        on_close()

    def _cancel(_):
        page.pop_dialog()
        on_close()

    content = ft.Column(
        [
            ft.Row(
                [exe_field,
                 ft.OutlinedButton("Browse…", icon=ft.Icons.FOLDER_OPEN, on_click=_browse)],
                vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8,
            ),
            ft.Text("Which game in your library is this?", size=13),
            game_picker,
            ft.Text("The parent folder is added to the watch list so strict mode "
                    "lets the exe through.",
                    size=11, italic=True, color=ft.Colors.ON_SURFACE_VARIANT),
            error,
        ],
        tight=True, spacing=10, scroll=ft.ScrollMode.AUTO,
    )

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Add mapping"),
        content=ft.Container(width=520, content=content),
        actions=[
            ft.TextButton("Cancel", on_click=_cancel),
            ft.Button("Add", icon=ft.Icons.ADD, on_click=_ok),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    ))


def open_watcher_settings_dialog(page, service, on_saved=None):
    """Open the process-watcher settings dialog.

    Lets the user edit the watcher / notification / tray / logging settings, the
    editable folder and ignored-name lists, and the learned exe->game mappings,
    then save. On save the config keys returned by ``get_values`` are written
    into ``service.config``, persisted via ``config.save_config``, the new log
    level is applied immediately, the dialog is closed, a SnackBar is shown, and
    ``on_saved()`` is invoked when provided. Cancel closes without saving.

    The "Add mapping..." button pops this dialog, opens a browse-for-exe + pick
    -a-game sub-dialog (Flet shows one dialog at a time), then re-shows this same
    dialog instance so in-progress edits are preserved.
    """
    library_names = _library_names(service)

    # FilePicker lives in page.services in Flet 0.85; used by the Add-mapping
    # exe browse. Created per-open (harmless if it stacks with app.py's picker).
    picker = ft.FilePicker()
    try:
        page.services.append(picker)
    except Exception:  # noqa: BLE001
        pass

    content, get_values, wiring = _build_watcher_content(service)

    def _snack(message):
        sb = ft.SnackBar(content=ft.Text(message))
        page.overlay.append(sb)
        sb.open = True
        page.update()

    def on_save(_):
        try:
            values = get_values()
        except ValueError:
            _snack("Quiet hours must be HH:MM-HH:MM (e.g. 23:00-08:00).")
            return

        service.config.update(values)

        if not config_module.save_config(service.config):
            _snack("Failed to save watcher settings.")
            return

        # Apply the log level immediately - no restart needed.
        try:
            set_watcher_log_level(values["watcher_log_level"])
        except Exception:  # noqa: BLE001
            pass

        page.pop_dialog()
        _snack("Watcher settings saved.")
        if on_saved:
            on_saved()

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("Process Watcher Settings"),
        content=content,
        actions=[
            ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
            ft.Button("Save", icon=ft.Icons.SAVE, on_click=on_save),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    def _reopen():
        page.show_dialog(dialog)

    def _on_add_mapping(_):
        # Pop this dialog, collect the mapping, then re-show this same dialog
        # instance (its content controls retain their in-progress values).
        page.pop_dialog()

        def _apply(exe_norm, game):
            wiring["add_mapping"](exe_norm, game)
            # Auto-whitelist the parent folder so strict mode lets the exe
            # through (mirrors the legacy dialog's convenience).
            parent = os.path.dirname(exe_norm)
            if parent:
                wiring["add_root"](parent)

        _open_add_mapping_dialog(page, picker, library_names,
                                 on_add=_apply, on_close=_reopen)

    wiring["mapping_add_button"].on_click = _on_add_mapping

    page.show_dialog(dialog)
