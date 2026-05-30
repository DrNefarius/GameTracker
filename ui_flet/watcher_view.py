"""Process-watcher settings dialog for the Flet UI.

A dependency-free (no PySimpleGUI) port of the watcher settings dialog from
``process_watcher_settings.show_process_watcher_settings_dialog``. It edits the
``watcher_*`` / ``notifications_*`` / ``tray_icon_enabled`` config keys through
``service.config`` and persists them with ``config.save_config(service.config)``.

UX mirrors the legacy PySimpleGUI dialog's grouping:
  - General: enable watcher, strict mode, foreground-only + grace seconds,
    idle-pause minutes.
  - Notifications: on-start / on-end / on-match-needed toggles, bypass Focus
    Assist.
  - System tray: show tray icon.
  - Logging: log-level dropdown (DEBUG/INFO/WARNING/ERROR).
  - Editable lists: extra watched folders (``watcher_user_roots``) and ignored
    process names (``watcher_ignore_list``), each rendered as a Column of rows
    with a per-row delete IconButton plus an "Add" TextField + button.

Flet 0.85.2 has no Spin control, so the two integer fields (idle minutes,
foreground grace seconds) are plain ``TextField`` widgets coerced to ``int`` in
``get_values`` (non-numeric input falls back to the existing/default value).

The legacy dialog also edited ``notifications_quiet_hours`` and the learned
process->game mapping table ("Add mapping..." / "Watcher link"). Quiet hours is
ported here. The process->game mapping/link dialog is intentionally deferred
(see ``open_watcher_settings_dialog`` docstring) because it needs a live running
watcher to surface unmatched detections.

``_build_watcher_content`` is split out so the body can be constructed and
inspected without a live Flet page (used by the smoke test).
"""

import flet as ft

import config as config_module

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


def _build_editable_list(values, hint_text, empty_text):
    """Build an editable string-list editor.

    Returns ``(control, get_items_fn)`` where ``control`` is a Flet Column with
    one row per existing item (delete IconButton + label) followed by an "Add"
    TextField + button, and ``get_items_fn()`` returns the current list of
    strings (order preserved, blanks dropped).

    The list is kept in a plain Python list captured in the closure so it works
    without a live page; ``page.update()`` is only called when a real page is
    attached to the controls (it is during normal dialog use).
    """
    items = [str(v) for v in (values or []) if str(v).strip()]

    rows_column = ft.Column(spacing=4, tight=True)
    add_field = ft.TextField(hint_text=hint_text, expand=True, dense=True)
    empty_label = ft.Text(empty_text, italic=True, size=12, color=ft.Colors.GREY)

    def _safe_update(control):
        # control.update() raises if the control is not yet on a page (e.g. in
        # the smoke test). Swallow that so the editor is usable headless.
        try:
            control.update()
        except Exception:  # noqa: BLE001
            pass

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

    def _add(_):
        new_value = (add_field.value or "").strip()
        if not new_value:
            return
        # Case-insensitive dedupe, matching the legacy dialog's behaviour.
        if new_value.lower() not in {i.lower() for i in items}:
            items.append(new_value)
        add_field.value = ""
        _safe_update(add_field)
        _rebuild()

    add_button = ft.ElevatedButton("Add", icon=ft.Icons.ADD, on_click=_add)
    add_field.on_submit = _add

    _rebuild()

    control = ft.Column(
        [
            rows_column,
            ft.Row([add_field, add_button], vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ],
        spacing=8,
        tight=True,
    )

    def get_items():
        return [str(i) for i in items]

    return control, get_items


def _section_title(text):
    return ft.Text(text, weight=ft.FontWeight.BOLD, size=14)


def _hint(text):
    return ft.Text(text, italic=True, size=11, color=ft.Colors.GREY)


def _build_watcher_content(service):
    """Build the watcher settings form.

    Returns ``(control, get_values_fn)`` where:
      - ``control`` is the Flet Control to drop into a dialog's ``content``.
      - ``get_values_fn()`` returns a dict mapping the watcher config keys to
        their new values (correct types: ints for the numeric fields, bools for
        the switches, lists for the editable lists, the log-level string, and
        ``notifications_quiet_hours`` as a 2-element list or ``None``).

    No live page is required to call this, so it is safe to use in tests.
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

    # ---- Editable lists -------------------------------------------------
    roots_editor, get_roots = _build_editable_list(
        cfg.get("watcher_user_roots"),
        hint_text="e.g. D:\\Games",
        empty_text="(no extra folders added yet)",
    )
    ignore_editor, get_ignore = _build_editable_list(
        cfg.get("watcher_ignore_list"),
        hint_text="e.g. launcher.exe",
        empty_text="(no ignored process names yet)",
    )

    content = ft.Container(
        width=560,
        height=520,
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
                log_level,
                _hint(
                    "DEBUG includes per-tick decisions (resolver layers tried, "
                    "fuzzy scores, idle seconds)."
                ),
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
        }

    return content, get_values


def open_watcher_settings_dialog(page, service, on_saved=None):
    """Open the process-watcher settings dialog.

    Lets the user edit the watcher / notification / tray / logging settings plus
    the editable folder and ignored-name lists, then save. On save the config
    keys returned by ``_build_watcher_content``'s ``get_values`` are written into
    ``service.config``, persisted via ``config.save_config``, the dialog is
    closed (``page.pop_dialog``), a SnackBar is shown, and ``on_saved()`` is
    invoked when provided. Cancel closes without saving.

    Deferred: the learned process->game mapping / "watcher link" picker from the
    legacy dialog is not ported here. It needs a live running watcher to surface
    unmatched detections (and the mapping data structures the watcher maintains),
    which is out of scope for this settings screen.
    """
    content, get_values = _build_watcher_content(service)

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
            ft.ElevatedButton("Save", icon=ft.Icons.SAVE, on_click=on_save),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dialog)
