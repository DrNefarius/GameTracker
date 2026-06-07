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
  * **link executable** (Game Hub) - manually map a known library game to a
    specific .exe (or its install folder) so the watcher tracks it even when
    strict mode would otherwise skip the binary (non-launcher games, MMOs).

All three were PySimpleGUI popups in :mod:`session_watcher_bridge`. This module
renders them in Flet instead. The actual mutation (watcher mappings, retitling,
session persistence) lives in the GUI-free ``SessionWatcherBridge`` helpers
(``get_*_context`` / ``apply_*``), so this layer only collects input and surfaces
the confirmation message - keeping the picker logic identical across both UIs.

The game picker mirrors the Statistics scope selector: a search box over a
virtualized ``ft.ListView`` (not a Dropdown), so a large library stays smooth
and avoids Flet 0.85's giant-editable-menu problem.
"""

import os

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
                              on_click=_on_ignore, icon_color=ft.Colors.RED,
                              style=ft.ButtonStyle(color=ft.Colors.RED)),
            ft.Button("Save mapping", icon=ft.Icons.SAVE, on_click=_on_save,
                      bgcolor=ft.Colors.GREEN, color=ft.Colors.WHITE),
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
        # No decision made: clear the "active picker" flag so a later focus can
        # re-fire it, but keep it queued.
        try:
            bridge.cancel_match_pick(ctx.get("detection_id") or detection_id)
        except Exception:
            pass
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
                              on_click=_on_ignore, icon_color=ft.Colors.RED,
                              style=ft.ButtonStyle(color=ft.Colors.RED)),
            ft.Button("Save & track", icon=ft.Icons.SAVE, on_click=_on_save,
                      bgcolor=ft.Colors.GREEN, color=ft.Colors.WHITE),
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


# --------------------------------------------------------------------------- #
# Link executable (Game Hub -> watcher mapping)
# --------------------------------------------------------------------------- #
def apply_link_executable(game_name, exe_path, scope_dir=False):
    """GUI-free: persist an exe/install-dir -> game mapping in the watcher.

    Mirrors the legacy ``watcher_link_dialog``: remembers the mapping (single
    exe or whole install folder), whitelists the parent folder for strict mode,
    and forces the resolver to re-examine running processes so the mapping takes
    effect without a relaunch. Returns ``(ok, message)``.
    """
    if not exe_path or not os.path.isfile(exe_path):
        return False, "That file doesn't exist."
    try:
        from process_watcher import get_watcher
    except Exception as exc:  # noqa: BLE001
        return False, f"Watcher unavailable: {exc}"
    watcher = get_watcher()
    if watcher is None:
        return False, ("The process watcher isn't initialized. Enable it in "
                       "Process Watcher Settings first.")
    try:
        parent = os.path.dirname(exe_path)
        if scope_dir:
            watcher.remember_installdir_mapping(parent, game_name)
        else:
            watcher.remember_mapping(exe_path, game_name)
        added = watcher.add_user_root(parent)
        try:
            if scope_dir:
                watcher.recheck_install_dir(parent)
            else:
                watcher.recheck_exe(exe_path)
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        return False, f"Saving the link failed: {exc}"

    scope_label = "any .exe in the install folder" if scope_dir else os.path.basename(exe_path)
    root_msg = (" Added the parent folder to the watch list."
                if added else "")
    return True, f"Linked '{game_name}' to {scope_label}.{root_msg}"


def open_link_executable_dialog(page, game_name, game_platform=None, picker=None,
                                on_done=None, on_close=None, notify=None):
    """Flet "Link Executable" dialog for a known library game.

    Lets the user browse to an .exe and choose match scope (single exe vs. whole
    install folder), then persists the watcher mapping via
    :func:`apply_link_executable`. Console-platform games get a confirmation
    first (linking an emulator binary would mis-attribute every future emulator
    session). ``picker`` is an ``ft.FilePicker`` already added to ``page.services``
    (the caller owns it); if omitted, one is created and appended here.

    ``on_done`` fires only after a successful link; ``on_close`` fires on every
    terminal path (link OR cancel) so the caller can restore its own view
    (Flet shows one dialog at a time).
    """
    def _finish():
        if on_close:
            try:
                on_close()
            except Exception:  # noqa: BLE001
                pass
    if picker is None:
        picker = ft.FilePicker()
        try:
            page.services.append(picker)
        except Exception:  # noqa: BLE001
            pass

    exe_field = ft.TextField(label="Executable", hint_text="path to the game .exe",
                             expand=True)
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

    def _notify(msg):
        (notify or (lambda m: _snack(page, m)))(msg)

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

    def _commit():
        ok, message = apply_link_executable(
            game_name, (exe_field.value or "").strip(), scope_dir=(scope.value == "dir"))
        if not ok:
            error.value = message
            error.visible = True
            page.update()
            return
        page.pop_dialog()
        _notify(message)
        if on_done:
            on_done()
        _finish()

    def _on_link(_):
        exe = (exe_field.value or "").strip()
        if not exe or not os.path.isfile(exe):
            error.value = "Pick an existing .exe file."
            error.visible = True
            page.update()
            return
        # Console-platform guard: warn before linking an emulator binary.
        try:
            from utilities import is_console_platform
            console = is_console_platform(game_platform)
        except Exception:  # noqa: BLE001
            console = False
        if console:
            def _confirm(_):
                page.pop_dialog()  # close the warning
                _commit()
            page.show_dialog(ft.AlertDialog(
                modal=True,
                title=ft.Text("Probably not what you want"),
                content=ft.Container(width=460, content=ft.Text(
                    f"'{game_name}' is stored as a console game ({game_platform}). "
                    "Linking an emulator .exe will track every future launch of that "
                    "emulator as this one game.\n\nUse the tray's 'Start Console "
                    "Session' for console titles instead.\n\nLink anyway?")),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                    ft.Button("Link anyway", on_click=_confirm),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            ))
            return
        _commit()

    content = ft.Column(
        [
            ft.Text(f"Link an executable to '{game_name}'", weight=ft.FontWeight.W_600,
                    size=14),
            ft.Text("Pick the .exe that launches this game. The watcher will track "
                    "sessions automatically the next time it runs.",
                    size=12, color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Row([exe_field,
                    ft.OutlinedButton("Browse…", icon=ft.Icons.FOLDER_OPEN, on_click=_browse)],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            ft.Column(
                [ft.Text("Match scope", weight=ft.FontWeight.W_600, size=12), scope],
                tight=True, spacing=2,
            ),
            ft.Text("The parent folder is added to the strict-mode watch list so the "
                    "exe isn't filtered out.",
                    size=11, italic=True, color=ft.Colors.ON_SURFACE_VARIANT),
            error,
        ],
        tight=True, spacing=12,
    )

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Link Executable"),
        content=ft.Container(width=560, content=content),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: (page.pop_dialog(), _finish())),
            ft.Button("Link", icon=ft.Icons.LINK, on_click=_on_link),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    ))
