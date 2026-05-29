"""Theme + status-colour helpers for the Flet UI.

Status row colours reuse the exact palette from ``constants.py`` (the *_STYLE
pairs are ``(text_hex, bg_hex)``). We apply them as a low-opacity row tint so
they read well on both light and dark themes, rather than as a solid fill.
"""

from datetime import datetime

import flet as ft

from constants import (
    COMPLETED_STYLE,
    DROPPED_STYLE,
    IN_PROGRESS_STYLE,
    FUTURE_RELEASE_STYLE,
    DEFAULT_STYLE,
    STATUS_COMPLETED,
    STATUS_DROPPED,
    STATUS_IN_PROGRESS,
)

# Theme-mode persistence keys <-> Flet enum
_MODE_TO_STR = {
    ft.ThemeMode.LIGHT: "light",
    ft.ThemeMode.DARK: "dark",
    ft.ThemeMode.SYSTEM: "system",
}
_STR_TO_MODE = {v: k for k, v in _MODE_TO_STR.items()}
_CYCLE = [ft.ThemeMode.SYSTEM, ft.ThemeMode.LIGHT, ft.ThemeMode.DARK]


def build_theme() -> ft.Theme:
    """A single seeded Material theme; Flet derives light & dark schemes from it."""
    return ft.Theme(color_scheme_seed=ft.Colors.INDIGO)


def mode_to_str(mode: ft.ThemeMode) -> str:
    return _MODE_TO_STR.get(mode, "system")


def str_to_mode(value: str) -> ft.ThemeMode:
    return _STR_TO_MODE.get((value or "system").lower(), ft.ThemeMode.SYSTEM)


def next_mode(mode: ft.ThemeMode) -> ft.ThemeMode:
    try:
        return _CYCLE[(_CYCLE.index(mode) + 1) % len(_CYCLE)]
    except ValueError:
        return ft.ThemeMode.DARK


def mode_icon(mode: ft.ThemeMode):
    return {
        ft.ThemeMode.SYSTEM: ft.Icons.BRIGHTNESS_AUTO,
        ft.ThemeMode.LIGHT: ft.Icons.LIGHT_MODE,
        ft.ThemeMode.DARK: ft.Icons.DARK_MODE,
    }.get(mode, ft.Icons.BRIGHTNESS_AUTO)


def status_bg_hex(row) -> str:
    """Return the base status colour hex for a game row (matches the old table)."""
    status = row[4] if len(row) > 4 else None
    if status == STATUS_COMPLETED:
        return COMPLETED_STYLE[1]
    if status == STATUS_DROPPED:
        return DROPPED_STYLE[1]
    if status == STATUS_IN_PROGRESS:
        return IN_PROGRESS_STYLE[1]
    # Pending: future release (or unknown date) vs overdue.
    release = row[1] if len(row) > 1 else None
    try:
        if release == "-" or datetime.strptime(release, "%Y-%m-%d") > datetime.now():
            return FUTURE_RELEASE_STYLE[1]
    except (ValueError, TypeError):
        pass
    return DEFAULT_STYLE[1]


def row_tint(row) -> str:
    """Low-opacity status colour for a DataRow background (theme-safe)."""
    return ft.Colors.with_opacity(0.18, status_bg_hex(row))


def status_badge(row) -> ft.Container:
    """A small rounded status chip using the status colour."""
    status = row[4] if len(row) > 4 else ""
    return ft.Container(
        content=ft.Text(status or "-", size=12, color="#000000"),
        bgcolor=status_bg_hex(row),
        padding=ft.Padding(8, 3, 8, 3),
        border_radius=12,
    )
