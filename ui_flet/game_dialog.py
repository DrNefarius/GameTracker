"""Add / Edit / Delete game dialogs for the Flet UI.

Validation here is a dependency-free re-implementation of the rules in the
legacy ``ui_components.validate_entry_form`` + ``create_entry_popup`` (name
required; release date YYYY-MM-DD or '-'; time HH:MM:SS with minutes/seconds
< 60) so the new UI does not import the PySimpleGUI module graph.
"""

import re
from datetime import datetime

import flet as ft

from constants import VALID_STATUSES, STATUS_PENDING
from core.services import new_game_row, edited_game_row

_TIME_RE = re.compile(r"^\d{2,4}:\d{2}:\d{2}$")
_EMPTY_TIMES = ("", "00:00:00", "00:00")


def validate_game_form(name, release, time_value):
    """Return a list of human-readable validation errors (empty == valid)."""
    errors = []
    if not (name or "").strip():
        errors.append("Name is required")

    release = (release or "").strip()
    if release and release != "-":
        try:
            datetime.strptime(release, "%Y-%m-%d")
        except ValueError:
            errors.append("Release date must be YYYY-MM-DD or '-' for unknown")

    tv = (time_value or "").strip()
    if tv and tv not in _EMPTY_TIMES:
        if not _TIME_RE.match(tv):
            errors.append("Time played must be HH:MM:SS (e.g. 12:30:00)")
        else:
            try:
                _h, m, s = map(int, tv.split(":"))
                if m >= 60 or s >= 60:
                    errors.append("Minutes and seconds must be less than 60")
            except ValueError:
                errors.append("Time parts must be valid numbers")
    return errors


def _normalise_time(value):
    value = (value or "").strip()
    return None if value in _EMPTY_TIMES else value


def open_game_dialog(page, service, orig_idx=None, on_saved=None):
    """Open the add (orig_idx=None) or edit dialog. Calls on_saved() after a save."""
    existing = service.get_game(orig_idx) if orig_idx is not None else None
    is_edit = existing is not None

    name = ft.TextField(label="Name *", value=existing[0] if is_edit else "", autofocus=True)
    release = ft.TextField(
        label="Release date (YYYY-MM-DD or -)",
        value=(existing[1] if is_edit else ""),
        hint_text="2024-01-31 or -",
    )
    platform = ft.TextField(label="Platform", value=(existing[2] if is_edit else ""))
    time_field = ft.TextField(
        label="Time played (HH:MM:SS)",
        value=((existing[3] or "") if is_edit else ""),
        hint_text="00:00:00",
    )
    status = ft.Dropdown(
        label="Status",
        value=(existing[4] if is_edit else STATUS_PENDING),
        options=[ft.dropdown.Option(key=s, text=s) for s in VALID_STATUSES],
    )
    owned = ft.Checkbox(label="Owned", value=(existing[5] == "✅") if is_edit else False)
    error_text = ft.Text("", color=ft.Colors.ERROR, visible=False)

    def on_save(_):
        errors = validate_game_form(name.value, release.value, time_field.value)
        if errors:
            error_text.value = "\n".join(errors)
            error_text.visible = True
            page.update()
            return

        tv = _normalise_time(time_field.value)
        if is_edit:
            row = edited_game_row(
                existing,
                name.value.strip(),
                release.value.strip(),
                platform.value.strip(),
                tv,
                status.value,
                owned.value,
            )
            service.update_game(orig_idx, row)
        else:
            row = new_game_row(
                name.value.strip(),
                release.value.strip(),
                platform.value.strip(),
                tv,
                status.value,
                owned.value,
            )
            service.add_game(row)

        service.save()
        page.pop_dialog()
        if on_saved:
            on_saved()

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("Edit game" if is_edit else "Add game"),
        content=ft.Container(
            width=440,
            content=ft.Column(
                [name, release, platform, time_field, status, owned, error_text],
                tight=True,
                spacing=12,
            ),
        ),
        actions=[
            ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
            ft.ElevatedButton("Save", icon=ft.Icons.SAVE, on_click=on_save),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dialog)


def open_status_dialog(page, service, orig_idx, on_done=None):
    """Quick 'Change Status' popup (mirrors clicking the legacy Status cell)."""
    row = service.get_game(orig_idx)
    current = row[4] if row and len(row) > 4 else STATUS_PENDING
    dd = ft.Dropdown(
        label="Status",
        value=current,
        options=[ft.dropdown.Option(key=s, text=s) for s in VALID_STATUSES],
        width=240,
    )

    def _save(_):
        service.set_status(orig_idx, dd.value)
        page.pop_dialog()
        if on_done:
            on_done()

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Change status"),
        content=ft.Container(width=260, content=dd),
        actions=[
            ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
            ft.ElevatedButton("OK", on_click=_save),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    ))


def confirm_delete(page, service, orig_idx, on_done=None):
    """Confirm and delete a game; calls on_done() after deletion."""
    row = service.get_game(orig_idx)
    game_name = row[0] if row else "this game"

    def do_delete(_):
        service.delete_game(orig_idx)
        service.save()
        page.pop_dialog()
        if on_done:
            on_done()

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("Delete game"),
        content=ft.Text(f"Are you sure you want to delete '{game_name}'?"),
        actions=[
            ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
            ft.ElevatedButton(
                "Delete",
                icon=ft.Icons.DELETE,
                color=ft.Colors.WHITE,
                bgcolor=ft.Colors.RED,
                on_click=do_delete,
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dialog)
