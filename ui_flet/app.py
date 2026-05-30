"""Flet application shell: window setup, navigation, toolbar, theme toggle.

Entry point ``main(page)`` is invoked by ``app_flet.py`` via ``ft.run``.
"""

import flet as ft

from config import save_config
from core.services import GameLibraryService
from ui_flet import theme
from ui_flet import help_view
from ui_flet.games_view import GamesView
from ui_flet.game_dialog import open_game_dialog, confirm_delete
from ui_flet.game_hub import open_game_hub
from ui_flet.summary_view import SummaryView
from ui_flet.statistics_view import StatisticsView
from ui_flet.igdb_view import open_igdb_settings_dialog
from ui_flet.watcher_view import open_watcher_settings_dialog


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
    service = GameLibraryService()
    service.bootstrap()

    # ---- window / theme -------------------------------------------------
    page.title = "GameTracker"
    page.padding = 0
    page.theme = theme.build_theme()
    page.theme_mode = theme.str_to_mode(service.config.get("theme_mode", "system"))
    try:
        page.window.width = 1200
        page.window.height = 800
        page.window.min_width = 900
        page.window.min_height = 600
        # Note: page.window.center() is async in Flet 0.85; setting an explicit
        # size is enough and avoids an un-awaited-coroutine warning in sync main().
    except Exception:
        pass  # window object unavailable in web mode

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

    def do_edit(orig_idx):
        # A row click / edit icon opens the full Game Hub (which itself offers
        # Edit, Add session, Rate, Delete, and an inline timer).
        open_game_hub(page, service, orig_idx, on_changed=games_view.refresh)

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
        try:
            service.open_path(path)
            games_view.refresh()
            snack(f"Loaded {len(service.data)} games")
        except Exception as exc:
            snack(f"Open failed: {exc}", error=True)

    async def do_import(_):
        result = await file_picker.pick_files(
            dialog_title="Import library from Excel (.xlsx)",
            allowed_extensions=["xlsx"],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        path = _picked_path(result)
        if not path:
            return
        try:
            service.import_excel(path)
            games_view.refresh()
            snack(f"Imported {len(service.data)} games")
        except Exception as exc:
            snack(f"Import failed: {exc}", error=True)

    async def do_save_as(_):
        path = await file_picker.save_file(
            dialog_title="Save library as",
            file_name="games.gmd",
            allowed_extensions=["gmd"],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        if not path:
            return
        snack("Saved" if service.save_as(path) else "Save failed", error=not service.filename)

    def do_save(_):
        snack("Saved" if service.save() else "Save failed", error=False)

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

    # ---- toolbar ---------------------------------------------------------
    toolbar = ft.Container(
        bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.ON_SURFACE),
        padding=ft.Padding(14, 8, 14, 8),
        content=ft.Row(
            [
                ft.Icon(ft.Icons.SPORTS_ESPORTS),
                ft.Text("GameTracker", size=18, weight=ft.FontWeight.BOLD),
                ft.Container(expand=True),
                ft.IconButton(ft.Icons.SAVE, tooltip="Save", on_click=do_save),
                ft.IconButton(ft.Icons.FOLDER_OPEN, tooltip="Open .gmd", on_click=do_open),
                ft.IconButton(ft.Icons.SAVE_AS, tooltip="Save As", on_click=do_save_as),
                ft.IconButton(ft.Icons.UPLOAD_FILE, tooltip="Import Excel", on_click=do_import),
                ft.IconButton(ft.Icons.CLOUD_SYNC, tooltip="IGDB Settings",
                              on_click=lambda e: open_igdb_settings_dialog(page, service)),
                ft.IconButton(ft.Icons.SETTINGS, tooltip="Process Watcher settings",
                              on_click=lambda e: open_watcher_settings_dialog(page, service)),
                ft.PopupMenuButton(
                    icon=ft.Icons.HELP_OUTLINE,
                    tooltip="Help",
                    items=[
                        ft.PopupMenuItem(content=ft.Text("User Guide"),
                                         on_click=lambda e: help_view.open_user_guide(page)),
                        ft.PopupMenuItem(content=ft.Text("Feature Tour"),
                                         on_click=lambda e: help_view.open_feature_tour(page)),
                        ft.PopupMenuItem(content=ft.Text("Data Format"),
                                         on_click=lambda e: help_view.open_data_format_info(page)),
                        ft.PopupMenuItem(content=ft.Text("Troubleshooting"),
                                         on_click=lambda e: help_view.open_troubleshooting(page)),
                        ft.PopupMenuItem(content=ft.Text("Release Notes"),
                                         on_click=lambda e: help_view.open_release_notes(page)),
                        ft.PopupMenuItem(content=ft.Text("Report a Bug"),
                                         on_click=lambda e: help_view.open_bug_report_info(page)),
                        ft.PopupMenuItem(),
                        ft.PopupMenuItem(content=ft.Text("About GameTracker"),
                                         on_click=lambda e: help_view.open_about_dialog(page)),
                    ],
                ),
                theme_btn,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
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
        if idx == 1:
            summary_view.refresh()      # regenerate charts from current data
        elif idx == 2:
            statistics_view.refresh()   # recompute stats + repopulate pickers
        page.update()

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
