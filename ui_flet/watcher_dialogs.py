"""Flet ports of the watcher's interactive dialogs (Phase 3C).

The process watcher occasionally needs the user to make a decision the
auto-tracker can't make on its own:

  * **match-picker** - an unrecognized executable started; the user picks which
    library game it is and whether to map just that .exe or the whole install
    folder (or marks it never-track).
  * **remap** ("Wrong game?") - the running session is attributed to the wrong
    title; the user re-points it at the correct game, preserving elapsed time.
  * **crash orphan recovery** - a session was active when the app last closed;
    the user is offered to record the interrupted play time.

All three were PySimpleGUI popups in :mod:`session_watcher_bridge`. This module
renders them in Flet instead. The actual mutation (watcher mappings, retitling,
session persistence) lives in the GUI-free ``SessionWatcherBridge`` helpers
(``get_*_context`` / ``apply_*``), so this layer only collects input and surfaces
the confirmation message - keeping the picker logic identical across both UIs.

The game picker mirrors the Statistics scope selector: a search box over a
virtualized ``ft.ListView`` (not a Dropdown), so a large library stays smooth
and avoids Flet 0.85's giant-editable-menu problem.
"""

import flet as ft


def _safe_update(control):
    """Call ``control.update()`` only if it is mounted (its ``.page`` raises
    until then, so a truthiness check is not safe pre-mount)."""
    try:
        if control.page is not None:
            control.update()
    except (RuntimeError, AssertionError):
        pass


def _snack(page, message):
    """Transient confirmation banner (used for the apply-decision result)."""
    try:
        bar = ft.SnackBar(content=ft.Text(message), duration=3500)
        page.overlay.append(bar)
        bar.open = True
        page.update()
    except Exception:
        pass


def _info(page, title, message):
    """A simple OK-only info dialog."""
    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text(title),
        content=ft.Text(message),
        actions=[ft.TextButton("OK", on_click=lambda e: page.pop_dialog())],
        actions_alignment=ft.MainAxisAlignment.END,
    ))


def _build_game_picker(library_names, preselect=""):
    """A searchable, virtualized library-name picker.

    Returns ``(control, get_selected)`` where ``get_selected()`` yields the
    currently selected canonical game name (or ``None``). Mirrors the
    Statistics scope selector so big libraries stay responsive.
    """
    state = {"selected": preselect or None}
    search = ft.TextField(
        hint_text="Filter games…", prefix_icon=ft.Icons.SEARCH, dense=True,
    )
    listview = ft.ListView(spacing=2, padding=ft.Padding(4, 4, 4, 4))

    def _item(name):
        selected = (name == state["selected"])
        return ft.Container(
            content=ft.Text(
                name, size=13,
                weight=ft.FontWeight.W_600 if selected else None,
                color=ft.Colors.PRIMARY if selected else None,
            ),
            on_click=lambda e, n=name: _select(n),
            padding=ft.Padding(10, 6, 10, 6), border_radius=6,
            bgcolor=ft.Colors.with_opacity(0.14, ft.Colors.PRIMARY) if selected else None,
        )

    def _refresh():
        q = (search.value or "").strip().lower()
        names = [n for n in library_names if q in n.lower()]
        listview.controls = [_item(n) for n in names]
        _safe_update(listview)

    def _select(name):
        state["selected"] = name
        _refresh()

    search.on_change = lambda _: _refresh()
    _refresh()

    panel = ft.Column(
        [
            search,
            ft.Container(
                listview, height=220, border_radius=8,
                bgcolor=ft.Colors.with_opacity(0.03, ft.Colors.ON_SURFACE),
            ),
        ],
        spacing=6, tight=True,
    )
    return panel, (lambda: state["selected"])


# --------------------------------------------------------------------------- #
# Remap ("Wrong game?")
# --------------------------------------------------------------------------- #
def open_remap_dialog(page, bridge, on_done=None, notify=None):
    """Flet "Wrong game?" remap dialog.

    Re-points the live session at the correct library game (preserving elapsed
    time) or marks the exe as never-track. ``notify`` is an optional callable
    used to surface the confirmation message (falls back to a SnackBar).
    """
    ctx = bridge.get_remap_context()
    if not ctx:
        _info(page, "Wrong game?",
              "No game is currently being tracked, so there's nothing to remap.")
        if on_done:
            on_done()
        return

    picker, get_selected = _build_game_picker(ctx["library_names"], ctx["preselect"])
    error = ft.Text("", color=ft.Colors.ERROR, visible=False)

    def _confirm(message):
        if message:
            (notify or (lambda m: _snack(page, m)))(message)
        if on_done:
            on_done()

    def _on_save(_):
        chosen = get_selected()
        if not chosen:
            error.value = "Pick a game first."
            error.visible = True
            page.update()
            return
        page.pop_dialog()
        _confirm(bridge.apply_remap_decision(ctx, chosen=chosen))

    def _on_ignore(_):
        page.pop_dialog()
        _confirm(bridge.apply_remap_decision(ctx, ignore=True))

    def _on_cancel(_):
        page.pop_dialog()
        if on_done:
            on_done()

    content = ft.Column(
        [
            ft.Text("We're currently tracking this process as:", size=13),
            ft.Text(ctx["wrong_name"], weight=ft.FontWeight.BOLD, size=15),
            ft.Text(f"Process: {ctx['exe_basename']}", size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Divider(),
            ft.Text("Pick the correct game from your library:", size=13),
            picker,
            ft.Text("Tip: if the right title isn't listed, add it via 'Add game' "
                    "first, then re-launch the game.",
                    size=11, italic=True, color=ft.Colors.ON_SURFACE_VARIANT),
            error,
        ],
        tight=True, spacing=10, scroll=ft.ScrollMode.AUTO,
    )

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Wrong game?"),
        content=ft.Container(width=480, content=content),
        actions=[
            ft.TextButton("Cancel", on_click=_on_cancel),
            ft.OutlinedButton("Don't track this process", icon=ft.Icons.BLOCK,
                              on_click=_on_ignore),
            ft.Button("Save mapping", icon=ft.Icons.SAVE, on_click=_on_save),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    ))


# --------------------------------------------------------------------------- #
# Match picker ("Pick the matching game")
# --------------------------------------------------------------------------- #
def open_match_picker_dialog(page, bridge, detection_id, payload,
                             on_done=None, notify=None):
    """Flet match-picker for an unrecognized game's "Pick another" toast action.

    Lets the user pick the library game and the match scope (just this .exe vs.
    every .exe under the install folder), or mark the exe never-track. A cancel
    leaves the detection queued so it can be re-fired on focus.
    """
    ctx = bridge.get_match_pick_context(detection_id, payload or {})
    if not ctx:
        if on_done:
            on_done()
        return

    picker, get_selected = _build_game_picker(ctx["library_names"], ctx["preselect"])
    scope = ft.RadioGroup(
        value="exe",
        content=ft.Column(
            [
                ft.Radio(value="exe", label="Just this executable"),
                ft.Radio(value="dir", label="Any executable in this folder"),
            ],
            tight=True, spacing=0,
        ),
    )
    error = ft.Text("", color=ft.Colors.ERROR, visible=False)
    guess_line = (f"Best guess: {ctx['best_guess']} (low confidence)"
                  if ctx["best_guess"] else "No close match in your library.")

    def _confirm(message):
        if message:
            (notify or (lambda m: _snack(page, m)))(message)
        if on_done:
            on_done()

    def _on_save(_):
        chosen = get_selected()
        if not chosen:
            error.value = "Pick a game first."
            error.visible = True
            page.update()
            return
        page.pop_dialog()
        _confirm(bridge.apply_match_pick_decision(
            ctx, chosen=chosen, scope_dir=(scope.value == "dir")))

    def _on_ignore(_):
        page.pop_dialog()
        _confirm(bridge.apply_match_pick_decision(ctx, ignore=True))

    def _on_cancel(_):
        page.pop_dialog()
        if on_done:
            on_done()

    content = ft.Column(
        [
            ft.Text("Detected an unrecognized game", weight=ft.FontWeight.BOLD, size=15),
            ft.Text(f"Process: {ctx['exe_basename']}", size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Text(f"Install folder: {ctx['install_dir'] or '(unknown)'}", size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Text(guess_line, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Divider(),
            ft.Text("Which game in your library is this?", size=13),
            picker,
            ft.Column(
                [
                    ft.Text("Match scope", weight=ft.FontWeight.W_600, size=12),
                    scope,
                ],
                tight=True, spacing=2,
            ),
            ft.Text("Tip: if the right title isn't listed, add it via 'Add game' "
                    "first, then re-launch the game.",
                    size=11, italic=True, color=ft.Colors.ON_SURFACE_VARIANT),
            error,
        ],
        tight=True, spacing=8, scroll=ft.ScrollMode.AUTO,
    )

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Pick the matching game"),
        content=ft.Container(width=520, content=content),
        actions=[
            ft.TextButton("Cancel", on_click=_on_cancel),
            ft.OutlinedButton("Don't track this exe", icon=ft.Icons.BLOCK,
                              on_click=_on_ignore),
            ft.Button("Save & track", icon=ft.Icons.SAVE, on_click=_on_save),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    ))


# --------------------------------------------------------------------------- #
# Crash orphan-session recovery
# --------------------------------------------------------------------------- #
def open_orphan_recovery_dialog(page, bridge, refresh_cb=None):
    """If a session was active when the app last closed, offer to record it.

    Reads + clears the persisted state via the bridge; shows a yes/no dialog;
    on Yes records the interrupted session and calls ``refresh_cb``. No-op when
    there is nothing to recover.
    """
    ctx = bridge.get_orphan_recovery_context()
    if not ctx:
        return

    def _on_yes(_):
        page.pop_dialog()
        result = bridge.apply_orphan_recovery(ctx)
        if result and result.get("action") == "session_added" and refresh_cb:
            try:
                refresh_cb()
            except Exception:
                pass

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Recover crashed session?"),
        content=ft.Container(width=440, content=ft.Text(
            f"It looks like a session for '{ctx['game_name']}' was interrupted "
            f"(approx {ctx['duration_str']}).\n\nWould you like to record it now?")),
        actions=[
            ft.TextButton("No", on_click=lambda e: page.pop_dialog()),
            ft.Button("Yes, record it", icon=ft.Icons.SAVE, on_click=_on_yes),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    ))
