"""A lightweight global loading overlay for the Flet app.

A single translucent overlay (spinner + message) is lazily attached to
``page.overlay`` and toggled on/off. Long, synchronous operations (statistics /
summary chart generation, opening a big library, switching to a heavy view) can
wrap their work in :func:`run_with_loading` so the user sees a spinner instead of
a frozen window.

Why the ``asyncio.sleep(0)`` in :func:`run_with_loading`: most of the heavy work
here is CPU-bound and synchronous (matplotlib), which blocks the Flet event loop.
Yielding once after showing the overlay lets Flutter paint the spinner *before*
the blocking work starts, so the feedback is actually visible.
"""

import asyncio

import flet as ft

_ATTR = "_loading_overlay"


def _ensure_overlay(page):
    ov = getattr(page, _ATTR, None)
    if ov is not None:
        return ov
    label = ft.Text("Loading…", size=14, weight=ft.FontWeight.W_500)
    card = ft.Container(
        padding=ft.Padding(26, 22, 26, 22),
        border_radius=14,
        bgcolor=ft.Colors.with_opacity(0.96, ft.Colors.SURFACE),
        content=ft.Row(
            [ft.ProgressRing(width=26, height=26, stroke_width=3), label],
            spacing=18, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
    ov = ft.Container(
        expand=True,
        visible=False,
        bgcolor=ft.Colors.with_opacity(0.35, ft.Colors.BLACK),
        alignment=ft.Alignment(0, 0),
        content=card,
    )
    ov.data = label  # quick handle to the message text
    setattr(page, _ATTR, ov)
    try:
        page.overlay.append(ov)
    except Exception:
        pass
    return ov


def show_loading(page, message="Loading…"):
    if page is None:
        return
    ov = _ensure_overlay(page)
    try:
        ov.data.value = message
        ov.visible = True
        page.update()
    except Exception:
        pass


def hide_loading(page):
    if page is None:
        return
    ov = getattr(page, _ATTR, None)
    if ov is None:
        return
    try:
        ov.visible = False
        page.update()
    except Exception:
        pass


async def run_with_loading(page, message, work, *args):
    """Show the overlay, run ``work(*args)`` (sync), then hide it.

    Schedule via ``page.run_task(run_with_loading, page, msg, work, *args)``.
    """
    show_loading(page, message)
    try:
        await asyncio.sleep(0.02)  # let the spinner paint before blocking work
    except Exception:
        pass
    try:
        work(*args)
    finally:
        hide_loading(page)
