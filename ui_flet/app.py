"""Flet application shell: window setup, navigation, toolbar, theme toggle.

Entry point ``main(page)`` is invoked by ``app_flet.py`` via ``ft.run``.
"""

import asyncio
import os
import threading

import flet as ft

from config import load_config, save_config
from core.services import GameLibraryService
from ui_flet import theme
from ui_flet import help_view
from ui_flet import loading
from ui_flet.games_view import GamesView
from ui_flet.game_dialog import open_game_dialog, confirm_delete
from ui_flet.game_hub import open_game_hub
from ui_flet.summary_view import SummaryView
from ui_flet.statistics_view import StatisticsView
from ui_flet.igdb_view import open_igdb_settings_dialog
from ui_flet import igdb_match
from ui_flet.watcher_view import open_watcher_settings_dialog
from ui_flet.watcher_runtime import start_watcher
from ui_flet import discord_runtime
from ui_flet import update_view


def _placeholder(icon, title):
    return ft.Container(
        content=ft.Column(
            [
                ft.Icon(icon, size=56, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text(title, size=18),
                ft.Text("Coming in a later Version2 phase.", size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        ),
        alignment=ft.Alignment(0, 0),
        expand=True,
    )


def main(page: ft.Page):
    """Paint a splash immediately, then defer the heavy build.

    The real build reads the library and generates matplotlib charts, which
    blocks the event loop; running it after one loop yield lets Flutter paint the
    spinner first instead of leaving the window blank for a couple of seconds.
    """
    page.title = "GameTracker"
    page.padding = 0
    try:
        page.theme = theme.build_theme()
    except Exception:
        pass

    # Restore the last window size + position (saved on move/resize), or fall
    # back to sensible defaults. Applied here in the wrapper so the window opens
    # at the right geometry before the splash paints.
    try:
        cfg = load_config()
    except Exception:
        cfg = {}
    def _num(v, lo, hi):
        try:
            v = float(v)
            return v if lo <= v <= hi else None
        except (TypeError, ValueError):
            return None

    try:
        page.window.min_width = 900
        page.window.min_height = 600
        page.window.width = _num(cfg.get("window_width"), 600, 20000) or 1200
        page.window.height = _num(cfg.get("window_height"), 400, 20000) or 800
        left = _num(cfg.get("window_left"), -20000, 20000)
        top = _num(cfg.get("window_top"), -20000, 20000)
        if left is not None and top is not None:
            page.window.left = left
            page.window.top = top
        if cfg.get("window_maximized"):
            page.window.maximized = True
    except Exception:
        pass

    # Center via the page's root view (robust against the initial window-resize
    # reflow, which otherwise left the spinner clipped at the top).
    try:
        page.vertical_alignment = ft.MainAxisAlignment.CENTER
        page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    except Exception:
        pass
    splash = ft.Column(
        [
            ft.ProgressRing(width=42, height=42, stroke_width=4),
            ft.Text("Loading GameTracker…", size=15, color=ft.Colors.ON_SURFACE_VARIANT),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        alignment=ft.MainAxisAlignment.CENTER, spacing=18, tight=True,
    )
    page.add(splash)
    page.update()

    async def _go():
        # Let the window settle (it resizes to the configured size on first show)
        # and the splash paint before kicking off the blocking build.
        try:
            await asyncio.sleep(0.15)
        except Exception:
            pass
        _build_main(page)

    page.run_task(_go)


def _build_main(page: ft.Page):
    service = GameLibraryService()
    service.bootstrap()

    # ---- theme ----------------------------------------------------------
    # Window geometry was already restored in the splash wrapper (main); don't
    # reset it here or we'd clobber the restored size/position.
    page.title = "GameTracker"
    page.padding = 0
    page.theme = theme.build_theme()
    page.theme_mode = theme.str_to_mode(service.config.get("theme_mode", "system"))

    # ---- file picker (lives in services in Flet 0.85) -------------------
    file_picker = ft.FilePicker()
    page.services.append(file_picker)

    def snack(message, error=False):
        bar = ft.SnackBar(
            content=ft.Text(message),
            bgcolor=ft.Colors.ERROR if error else None,
            duration=2500,
        )
        page.overlay.append(bar)
        bar.open = True
        page.update()

    # ---- games view + CRUD callbacks ------------------------------------
    def do_add():
        open_game_dialog(page, service, None,
                         on_saved=lambda: (games_view.refresh(), snack("Game added")))

    def show_in_statistics(game_name):
        # Switch to the Statistics tab and focus the given game (used by the
        # Game Hub's "View Statistics" action). rail/pages/content_area are
        # defined later in main(); resolved at call time.
        rail.selected_index = 2
        content_area.content = pages[2]
        page.update()
        page.run_task(loading.run_with_loading, page, "Loading statistics…",
                      statistics_view.select_game, game_name)

    def do_edit(orig_idx):
        # A row click / edit icon opens the full Game Hub (which itself offers
        # Edit, Add session, Rate, View Statistics, Delete, and an inline timer).
        open_game_hub(page, service, orig_idx, on_changed=games_view.refresh,
                      on_view_statistics=show_in_statistics)

    def do_delete(orig_idx):
        confirm_delete(page, service, orig_idx,
                       on_done=lambda: (games_view.refresh(), snack("Game deleted")))

    games_view = GamesView(page, service, on_edit=do_edit, on_delete=do_delete, on_add=do_add)
    summary_view = SummaryView(page, service)
    statistics_view = StatisticsView(page, service)

    # ---- file operations (FilePicker methods are async) -----------------
    def _picked_path(result):
        if not result:
            return None
        first = result[0]
        return getattr(first, "path", None)

    async def do_open(_):
        result = await file_picker.pick_files(
            dialog_title="Open game library (.gmd)",
            allowed_extensions=["gmd"],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        path = _picked_path(result)
        if not path:
            return
        loading.show_loading(page, "Opening library…")
        await asyncio.sleep(0.02)
        try:
            service.open_path(path)
            games_view.refresh()
            discord_runtime.notify_tab(service, rail.selected_index)  # refresh stats
            _refresh_db_label()
            snack(f"Loaded {len(service.data)} games")
        except Exception as exc:
            snack(f"Open failed: {exc}", error=True)
        finally:
            loading.hide_loading(page)

    async def do_import(_):
        result = await file_picker.pick_files(
            dialog_title="Import library from Excel (.xlsx)",
            allowed_extensions=["xlsx"],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        path = _picked_path(result)
        if not path:
            return
        loading.show_loading(page, "Importing from Excel…")
        await asyncio.sleep(0.02)
        try:
            service.import_excel(path)
            games_view.refresh()
            discord_runtime.notify_tab(service, rail.selected_index)  # refresh stats
            _refresh_db_label()
            snack(f"Imported {len(service.data)} games")
        except Exception as exc:
            snack(f"Import failed: {exc}", error=True)
        finally:
            loading.hide_loading(page)

    async def do_save_as(_):
        path = await file_picker.save_file(
            dialog_title="Save library as",
            file_name="games.gmd",
            allowed_extensions=["gmd"],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        if not path:
            return
        ok = service.save_as(path)
        _refresh_db_label()
        snack("Saved" if ok else "Save failed", error=not ok)

    def do_save(_):
        snack("Saved" if service.save() else "Save failed", error=False)

    # ---- helpers: pill-styled toolbar buttons + loaded-database chip ----
    # PopupMenuButton(content=...) and a bare Row render as an unstyled
    # rectangle; wrapping the content in a rounded, padded Container gives every
    # toolbar button the same "pill" shape (matching a Material button) and lets
    # the Row's spacing separate them.
    def _pill_bg(active=False, accent=None):
        if active and accent:
            return ft.Colors.with_opacity(0.20, accent)
        return ft.Colors.with_opacity(0.07, ft.Colors.ON_SURFACE)

    def _pill(content, on_click=None, tooltip=None, active=False, accent=None):
        return ft.Container(
            padding=ft.Padding(13, 7, 11, 7), border_radius=18,
            bgcolor=_pill_bg(active, accent),
            content=content, on_click=on_click, tooltip=tooltip,
            ink=bool(on_click),
        )

    # Accent colors that signal an "enabled" toggle.
    _DISCORD_ACCENT = ft.Colors.BLUE
    _WATCHER_ACCENT = ft.Colors.GREEN

    def _menu_label(icon, text):
        """Pill content for a menu button: icon + text + a dropdown caret."""
        return _pill(ft.Row(
            [ft.Icon(icon, size=18), ft.Text(text, size=13),
             ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=16, color=ft.Colors.ON_SURFACE_VARIANT)],
            spacing=4, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ))

    def _db_display():
        fn = getattr(service, "filename", None)
        return os.path.basename(fn) if fn else "No database loaded"

    db_label = ft.Text(_db_display(), size=12, weight=ft.FontWeight.W_500)
    db_chip = ft.Container(
        padding=ft.Padding(8, 3, 10, 3), border_radius=8,
        bgcolor=ft.Colors.with_opacity(0.07, ft.Colors.ON_SURFACE),
        tooltip="Currently loaded game database",
        content=ft.Row(
            [ft.Icon(ft.Icons.STORAGE, size=14, color=ft.Colors.ON_SURFACE_VARIANT), db_label],
            spacing=5, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )

    def _refresh_db_label():
        name = _db_display()
        db_label.value = name
        page.title = f"GameTracker — {name}"
        try:
            page.update()
        except Exception:
            pass

    # ---- theme toggle ----------------------------------------------------
    def toggle_theme(_):
        page.theme_mode = theme.next_mode(page.theme_mode)
        theme_btn.icon = theme.mode_icon(page.theme_mode)
        theme_btn.tooltip = f"Theme: {theme.mode_to_str(page.theme_mode)}"
        service.config["theme_mode"] = theme.mode_to_str(page.theme_mode)
        save_config(service.config)
        page.update()

    theme_btn = ft.IconButton(
        icon=theme.mode_icon(page.theme_mode),
        tooltip=f"Theme: {theme.mode_to_str(page.theme_mode)}",
        on_click=toggle_theme,
    )

    # ---- process-watcher On/Off toggle (lives in the Watcher menu) ------
    def _watcher_content(enabled):
        col = _WATCHER_ACCENT if enabled else ft.Colors.ON_SURFACE_VARIANT
        return _pill(
            ft.Row(
                [ft.Icon(ft.Icons.VISIBILITY if enabled else ft.Icons.VISIBILITY_OFF,
                         size=18, color=col),
                 ft.Text("Watcher", size=13, color=col),
                 ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=16, color=ft.Colors.ON_SURFACE_VARIANT)],
                spacing=4, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            active=enabled, accent=_WATCHER_ACCENT,
        )

    def toggle_watcher(_):
        from process_watcher import get_watcher
        new_enabled = not bool(service.config.get("watcher_enabled", False))
        service.config["watcher_enabled"] = new_enabled
        save_config(service.config)
        w = get_watcher()
        if w is not None:
            try:
                w.start() if new_enabled else w.stop()
            except Exception as ex:  # pragma: no cover - defensive
                print("watcher toggle failed:", ex)
        _sync_watcher_btn(new_enabled)
        snack(f"Process Watcher {'enabled' if new_enabled else 'disabled'}")

    _watcher_on = bool(service.config.get("watcher_enabled", False))
    watcher_enabled_item = ft.PopupMenuItem(
        content=ft.Text("Enabled"), checked=_watcher_on, on_click=toggle_watcher)
    watcher_menu = ft.PopupMenuButton(
        content=_watcher_content(_watcher_on),
        tooltip="Process Watcher",
        items=[
            watcher_enabled_item,
            ft.PopupMenuItem(content=ft.Text("Settings…"), icon=ft.Icons.SETTINGS,
                             on_click=lambda e: open_watcher_settings_dialog(page, service)),
            ft.PopupMenuItem(content=ft.Text("Rescan Game Libraries"), icon=ft.Icons.RADAR,
                             on_click=lambda e: igdb_match.rescan_game_libraries(page)),
        ],
    )

    def _sync_watcher_btn(enabled):
        watcher_menu.content = _watcher_content(enabled)
        watcher_enabled_item.checked = bool(enabled)
        watcher_menu.tooltip = f"Process Watcher: {'On' if enabled else 'Off'}"
        try:
            page.update()
        except Exception:
            pass

    # ---- Discord Rich Presence On/Off toggle ----------------------------
    def _discord_row(enabled):
        col = _DISCORD_ACCENT if enabled else ft.Colors.ON_SURFACE_VARIANT
        return ft.Row(
            [ft.Icon(ft.Icons.DISCORD, size=18, color=col),
             ft.Text("Discord", size=13, color=col)],
            spacing=6, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def toggle_discord(_):
        new_enabled = not bool(service.config.get("discord_enabled", True))
        discord_runtime.set_enabled(service, new_enabled)
        discord_btn.content = _discord_row(new_enabled)
        discord_btn.bgcolor = _pill_bg(new_enabled, _DISCORD_ACCENT)
        discord_btn.tooltip = f"Discord Rich Presence: {'On' if new_enabled else 'Off'}"
        snack(f"Discord Rich Presence {'enabled' if new_enabled else 'disabled'}")
        page.update()

    _discord_on = bool(service.config.get("discord_enabled", True))
    discord_btn = _pill(
        _discord_row(_discord_on), on_click=toggle_discord,
        tooltip=f"Discord Rich Presence: {'On' if _discord_on else 'Off'}",
        active=_discord_on, accent=_DISCORD_ACCENT,
    )

    # ---- toolbar: labeled, consolidated menus ---------------------------
    file_menu = ft.PopupMenuButton(
        content=_menu_label(ft.Icons.FOLDER, "File"),
        tooltip="Open, save and import",
        items=[
            ft.PopupMenuItem(content=ft.Text("Save"), icon=ft.Icons.SAVE, on_click=do_save),
            ft.PopupMenuItem(content=ft.Text("Open .gmd…"), icon=ft.Icons.FOLDER_OPEN,
                             on_click=do_open),
            ft.PopupMenuItem(content=ft.Text("Save As…"), icon=ft.Icons.SAVE_AS,
                             on_click=do_save_as),
            ft.PopupMenuItem(),
            ft.PopupMenuItem(content=ft.Text("Import from Excel…"), icon=ft.Icons.UPLOAD_FILE,
                             on_click=do_import),
            ft.PopupMenuItem(),
            # Fully quit (not close-to-tray). Reuses the tray "quit" path so the
            # watcher/Discord/tray are torn down cleanly. watcher_sink is assigned
            # later in this function; the closure resolves it at click time.
            ft.PopupMenuItem(content=ft.Text("Quit GameTracker"), icon=ft.Icons.LOGOUT,
                             on_click=lambda e: watcher_sink.write_event_value(
                                 "-TRAY-ACTION-", {"action": "quit"})),
        ],
    )
    # Opt-in: remember the Games list filter / page / rows-per-page across runs.
    remember_view_item = ft.PopupMenuItem(
        content=ft.Text("Remember filter, page & rows on exit"),
        checked=bool(getattr(games_view, "remember_view", False)),
    )

    def _toggle_remember_view(_):
        new_enabled = not bool(getattr(games_view, "remember_view", False))
        games_view.set_remember_view(new_enabled)
        remember_view_item.checked = new_enabled
        snack(f"Remembering library view: {'on' if new_enabled else 'off'}")
        page.update()

    remember_view_item.on_click = _toggle_remember_view

    library_menu = ft.PopupMenuButton(
        content=_menu_label(ft.Icons.CLOUD_SYNC, "Library"),
        tooltip="IGDB metadata tools & library view settings",
        items=[
            ft.PopupMenuItem(content=ft.Text("IGDB Settings…"), icon=ft.Icons.SETTINGS,
                             on_click=lambda e: open_igdb_settings_dialog(page, service)),
            ft.PopupMenuItem(content=ft.Text("Enrich Library from IGDB"),
                             icon=ft.Icons.AUTO_FIX_HIGH,
                             on_click=lambda e: igdb_match.open_enrich_library(
                                 page, service, on_done=games_view.refresh)),
            ft.PopupMenuItem(),
            remember_view_item,
        ],
    )
    updates_menu = ft.PopupMenuButton(
        content=_menu_label(ft.Icons.SYSTEM_UPDATE, "Updates"),
        tooltip="Check for and install updates",
        items=[
            ft.PopupMenuItem(content=ft.Text("Check for Updates"), icon=ft.Icons.REFRESH,
                             on_click=lambda e: update_view.check_for_updates_manual(page)),
            ft.PopupMenuItem(content=ft.Text("Update Settings…"), icon=ft.Icons.TUNE,
                             on_click=lambda e: update_view.open_update_settings_dialog(page, service)),
        ],
    )
    help_menu = ft.PopupMenuButton(
        content=_menu_label(ft.Icons.HELP_OUTLINE, "Help"),
        tooltip="Guides, troubleshooting and about",
        items=[
            ft.PopupMenuItem(content=ft.Text("User Guide"), icon=ft.Icons.MENU_BOOK,
                             on_click=lambda e: help_view.open_user_guide(page)),
            ft.PopupMenuItem(content=ft.Text("Feature Tour"), icon=ft.Icons.TOUR,
                             on_click=lambda e: help_view.open_feature_tour(page)),
            ft.PopupMenuItem(content=ft.Text("Data Format"), icon=ft.Icons.DATA_OBJECT,
                             on_click=lambda e: help_view.open_data_format_info(page)),
            ft.PopupMenuItem(content=ft.Text("Troubleshooting"), icon=ft.Icons.BUILD,
                             on_click=lambda e: help_view.open_troubleshooting(page)),
            ft.PopupMenuItem(content=ft.Text("Release Notes"), icon=ft.Icons.NEW_RELEASES,
                             on_click=lambda e: help_view.open_release_notes(page)),
            ft.PopupMenuItem(content=ft.Text("Report a Bug"), icon=ft.Icons.BUG_REPORT,
                             on_click=lambda e: help_view.open_bug_report_info(page)),
            ft.PopupMenuItem(),
            ft.PopupMenuItem(content=ft.Text("About GameTracker"), icon=ft.Icons.INFO_OUTLINE,
                             on_click=lambda e: help_view.open_about_dialog(page)),
        ],
    )

    toolbar = ft.Container(
        bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.ON_SURFACE),
        padding=ft.Padding(14, 6, 10, 6),
        content=ft.Row(
            [
                ft.Icon(ft.Icons.SPORTS_ESPORTS),
                ft.Text("GameTracker", size=18, weight=ft.FontWeight.BOLD),
                db_chip,
                ft.Container(expand=True),
                file_menu,
                library_menu,
                watcher_menu,
                discord_btn,
                updates_menu,
                help_menu,
                ft.VerticalDivider(width=1),
                theme_btn,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        ),
    )

    # ---- navigation + content swap --------------------------------------
    pages = [
        games_view.control,
        summary_view.control,
        statistics_view.control,
    ]
    content_area = ft.Container(content=pages[0], expand=True, padding=12)

    def on_nav_change(e):
        idx = e.control.selected_index
        content_area.content = pages[idx]
        discord_runtime.notify_tab(service, idx)  # browsing-presence per tab
        page.update()
        # Summary/Statistics regenerate matplotlib charts (can block briefly on a
        # big library) — show the loading overlay while they render.
        if idx == 1:
            page.run_task(loading.run_with_loading, page,
                          "Generating summary…", summary_view.refresh)
        elif idx == 2:
            page.run_task(loading.run_with_loading, page,
                          "Crunching statistics…", statistics_view.refresh)

    # Stretch the games table to fill the width, and keep it responsive.
    # Subtract the nav rail + divider + content padding + scrollbar slack.
    _CHROME_W = 120

    def apply_table_width(total_width):
        games_view.set_table_width(max(420, (total_width or 1200) - _CHROME_W))

    def on_resize(e):
        apply_table_width(e.width)

    page.on_resize = on_resize

    rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=72,
        destinations=[
            ft.NavigationRailDestination(icon=ft.Icons.LIST, label="Games"),
            ft.NavigationRailDestination(icon=ft.Icons.INSIGHTS, label="Summary"),
            ft.NavigationRailDestination(icon=ft.Icons.BAR_CHART, label="Statistics"),
        ],
        on_change=on_nav_change,
    )

    # Replace the startup splash with the real UI (restore the root alignment
    # the splash centered with, so the real layout fills normally).
    try:
        page.vertical_alignment = ft.MainAxisAlignment.START
        page.horizontal_alignment = ft.CrossAxisAlignment.START
    except Exception:
        pass
    page.controls.clear()
    page.add(
        ft.Column(
            [
                toolbar,
                ft.Row(
                    [rail, ft.VerticalDivider(width=1), content_area],
                    expand=True,
                    spacing=0,
                ),
            ],
            expand=True,
            spacing=0,
        )
    )

    # Initial table width (page.width may not be known until the first resize).
    apply_table_width(getattr(page, "width", None) or 1200)
    _refresh_db_label()  # reflect the loaded database name in the title bar

    # ---- Discord Rich Presence -----------------------------------------
    # Initializes off the UI thread (the IPC handshake can block); the watcher
    # bridge drives playing/paused/complete presence via discord_provider.
    discord_runtime.start_discord(service)

    # ---- background process watcher + system tray ----------------------
    # Runs in its own thread; events are marshalled onto the Flet loop and
    # auto-recorded sessions refresh the games list.
    watcher_sink = start_watcher(page, service, refresh_cb=games_view.refresh)

    # Keep the Watcher menu's icon/checkmark in sync when the watcher state is
    # changed elsewhere (tray, auto idle-pause). Uses the menu-aware helper
    # defined alongside the toolbar.
    watcher_sink.on_watcher_state_changed = _sync_watcher_btn

    # Single-instance activation: when a second launch is blocked, it pings this
    # instance; bring our (possibly tray-hidden) window to the front by reusing
    # the tray "open" path. write_event_value marshals onto the Flet loop, so
    # this is safe to call from the guard's listener thread.
    try:
        from single_instance import get_instance
        _inst = get_instance()
        if _inst is not None:
            _inst.on_activate = lambda: watcher_sink.write_event_value(
                "-TRAY-ACTION-", {"action": "open_app"})
    except Exception:
        pass

    # Window events: persist geometry + close-to-tray + drain matches on focus.
    _has_tray = getattr(watcher_sink, "tray", None) is not None
    _geom = {"timer": None}

    def _capture_geometry():
        """Read the current window geometry into config (call on the UI thread)."""
        try:
            w = page.window
            maximized = bool(getattr(w, "maximized", False))
            service.config["window_maximized"] = maximized
            # Only record size/position while NOT maximized, so restoring an
            # un-maximized window returns to the user's chosen size.
            if not maximized:
                if w.width:
                    service.config["window_width"] = int(w.width)
                if w.height:
                    service.config["window_height"] = int(w.height)
                if w.left is not None:
                    service.config["window_left"] = int(w.left)
                if w.top is not None:
                    service.config["window_top"] = int(w.top)
        except Exception:
            pass

    def _schedule_geom_save():
        # Capture now (on the loop thread), debounce the disk write so a drag
        # doesn't write config.json on every pixel.
        _capture_geometry()
        t = _geom.get("timer")
        if t is not None:
            t.cancel()
        nt = threading.Timer(0.8, lambda: save_config(service.config))
        nt.daemon = True
        _geom["timer"] = nt
        nt.start()

    _GEOM_EVENTS = (
        ft.WindowEventType.RESIZED, ft.WindowEventType.RESIZE,
        ft.WindowEventType.MOVED, ft.WindowEventType.MOVE,
        ft.WindowEventType.MAXIMIZE, ft.WindowEventType.UNMAXIMIZE,
        "resized", "resize", "moved", "move", "maximize", "unmaximize",
    )

    def _on_window_event(e):
        etype = getattr(e, "type", None)
        # Persist size/position as the user moves/resizes/maximizes the window.
        if etype in _GEOM_EVENTS:
            _schedule_geom_save()
        # Close-to-tray: with a tray icon active, the window's X hides to the
        # tray (use tray -> Quit to actually exit) so the watcher keeps running.
        if _has_tray and etype in (ft.WindowEventType.CLOSE, "close"):
            # Save the final geometry synchronously before the window hides.
            _capture_geometry()
            save_config(service.config)
            page.window.visible = False
            page.update()
            # One-time hint so the user knows the app didn't actually quit.
            if not service.config.get("tray_close_hint_shown"):
                try:
                    from notifications import notify_info
                    notify_info(
                        "GameTracker is still running",
                        "Minimized to the system tray — session tracking "
                        "continues. Use the tray icon to reopen or quit.")
                except Exception:
                    pass
                service.config["tray_close_hint_shown"] = True
                save_config(service.config)
            return
        # On regaining focus, re-surface any unresolved ambiguous-match toasts
        # the user hasn't acted on (clicking "Pick another" opens the Flet
        # match-picker).
        if etype in (ft.WindowEventType.FOCUS, "focus"):
            watcher_sink.drain_pending_matches_on_focus()

    try:
        if _has_tray:
            page.window.prevent_close = True
        page.window.on_event = _on_window_event
    except Exception:
        pass

    # ---- crash orphan-session recovery ---------------------------------
    # If a session was active when the app last closed (e.g. a crash left
    # active_session_state in config), offer to record the interrupted play
    # time. Scheduled on the loop so it runs once the page is live.
    async def _post_startup():
        try:
            from ui_flet.watcher_dialogs import open_orphan_recovery_dialog
            open_orphan_recovery_dialog(page, watcher_sink.bridge,
                                        refresh_cb=games_view.refresh)
        except Exception:
            pass
        # Auto-updater: show the post-update success popup (if we just updated)
        # and check for new updates when enabled.
        try:
            update_view.startup_check(page, service)
        except Exception:
            pass

    try:
        page.run_task(_post_startup)
    except Exception:
        pass
