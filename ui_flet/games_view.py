"""Games List view: a searchable, sortable, paginated DataTable.

Builds a Flet control tree (``self.control``) and exposes ``refresh()`` to
re-render the current page from ``service.data`` after any mutation.

Sorting is driven by the column headers (``DataColumn.on_sort`` -> a single
handler that reads the event's ``column_index`` / ``ascending``). Pagination is
user-controlled: a "Per page" selector + First/Prev/Next/Last navigation, so a
large library never renders thousands of rows at once.
"""

import math

import flet as ft

from constants import STAR_FILLED, STAR_EMPTY
from core.ratings_logic import get_effective_game_rating
from ui_flet import theme
from ui_flet import loading
from ui_flet.game_dialog import open_status_dialog

PAGE_SIZE_OPTIONS = ["25", "50", "100", "200", "All"]
DEFAULT_PAGE_SIZE = 50


def _time_to_seconds(value):
    if not value or not isinstance(value, str):
        return 0
    parts = value.split(":")
    try:
        if len(parts) == 3:
            h, m, s = map(int, parts)
            return h * 3600 + m * 60 + s
        if len(parts) == 2:
            h, m = map(int, parts)
            return h * 3600 + m * 60
    except ValueError:
        pass
    return 0


def _format_rating(rating):
    if not isinstance(rating, dict):
        return ""
    stars = max(0, min(5, int(rating.get("stars", 0) or 0)))
    prefix = "≈" if rating.get("auto_calculated") else ""
    return prefix + STAR_FILLED * stars + STAR_EMPTY * (5 - stars)


# Display columns: (header, sort_data_index | None). The list position is the
# DataColumn index reported by the on_sort event; ``sort_data_index`` maps it to
# the game-row field used as the sort key (None == not sortable).
_COLUMNS = [
    ("#", None),
    ("Name", 0),
    ("Released", 1),
    ("Platform", 2),
    ("Time", 3),
    ("Status", 4),
    ("Owned", None),
    ("Last Played", 6),
    ("Rating", None),
    ("", None),  # actions
]
_NAME_COL = 1  # default sort (display index of the Name column)


def _sort_value(data_idx, row):
    if data_idx == 1:
        rel = row[1] or ""
        return (rel == "-", rel)            # unknown dates last
    if data_idx == 3:
        return _time_to_seconds(row[3])
    if data_idx == 6:
        lp = row[6] or ""
        return (lp == "", lp)               # never-played last
    if data_idx in (0, 2, 4):
        return (row[data_idx] or "").lower()
    return (row[0] or "").lower()


class GamesView:
    def __init__(self, page, service, on_edit, on_delete, on_add):
        self.page = page
        self.service = service
        self.on_edit = on_edit
        self.on_delete = on_delete
        self.on_add = on_add

        self.query = ""
        cfg = getattr(service, "config", None) or {}
        # ---- sort: always remembered (default: Name, ascending) ----
        sc = cfg.get("games_sort_col", _NAME_COL)
        if not (isinstance(sc, int) and 0 <= sc < len(_COLUMNS)
                and _COLUMNS[sc][1] is not None):
            sc = _NAME_COL
        self.sort_col = sc           # DataColumn (display) index currently sorted by
        self.sort_asc = bool(cfg.get("games_sort_asc", True))

        # ---- opt-in: remember filter / page / rows-per-page (#11) ----
        self.remember_view = bool(cfg.get("remember_library_view", False))
        self.page_size = DEFAULT_PAGE_SIZE   # int, or None for "All"
        self.page_index = 0
        size_value = str(DEFAULT_PAGE_SIZE)
        raw_query = ""
        if self.remember_view:
            raw_query = str(cfg.get("library_query", "") or "")
            self.query = raw_query.strip().lower()
            sv = cfg.get("library_page_size")
            if sv in PAGE_SIZE_OPTIONS:
                size_value = sv
                self.page_size = None if sv == "All" else int(sv)
            try:
                self.page_index = max(0, int(cfg.get("library_page_index", 0)))
            except (TypeError, ValueError):
                self.page_index = 0

        # ---- top controls --------------------------------------------------
        self.search_field = ft.TextField(
            hint_text="Search by name, platform or status...",
            prefix_icon=ft.Icons.SEARCH,
            value=raw_query,
            on_change=self._on_search,
            expand=True,
            dense=True,
        )
        self.page_size_dd = ft.Dropdown(
            label="Per page",
            value=size_value,
            width=120,
            options=[ft.dropdown.Option(key=o, text=o) for o in PAGE_SIZE_OPTIONS],
            on_select=self._on_page_size,
        )
        # The opt-in "remember view" toggle lives in the toolbar's Library menu
        # (see app.py); it calls set_remember_view() on this view.
        self.count_text = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)

        # ---- table ---------------------------------------------------------
        self.table = ft.DataTable(
            columns=self._build_columns(),
            rows=[],
            sort_column_index=self.sort_col,
            sort_ascending=self.sort_asc,
            column_spacing=18,
            heading_row_color=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
            data_row_max_height=52,
            show_checkbox_column=False,
        )
        self._table_host = ft.Column([self.table], scroll=ft.ScrollMode.AUTO, expand=True)
        self._empty = ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.VIDEOGAME_ASSET_OFF, size=48,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Text("No games to show", size=16),
                    ft.Text("Add a game or open a .gmd file.", size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            alignment=ft.Alignment(0, 0),
            expand=True,
        )
        self._body = ft.Container(content=self._table_host, expand=True)

        # ---- pagination bar ------------------------------------------------
        self.first_btn = ft.IconButton(ft.Icons.FIRST_PAGE, tooltip="First page",
                                        on_click=lambda _: self._go(0))
        self.prev_btn = ft.IconButton(ft.Icons.CHEVRON_LEFT, tooltip="Previous page",
                                      on_click=lambda _: self._go(self.page_index - 1))
        self.next_btn = ft.IconButton(ft.Icons.CHEVRON_RIGHT, tooltip="Next page",
                                      on_click=lambda _: self._go(self.page_index + 1))
        self.last_btn = ft.IconButton(ft.Icons.LAST_PAGE, tooltip="Last page",
                                      on_click=lambda _: self._go(self._page_count() - 1))
        self.page_label = ft.Text("", size=12)
        self._pager = ft.Row(
            [self.first_btn, self.prev_btn, self.page_label, self.next_btn, self.last_btn],
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        )

        self.control = ft.Column(
            [
                ft.Row(
                    [
                        self.search_field,
                        self.page_size_dd,
                        ft.FilledButton("Add game", icon=ft.Icons.ADD,
                                        on_click=lambda _: self.on_add(),
                                        bgcolor=ft.Colors.GREEN, color=ft.Colors.WHITE),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self.count_text,
                self._body,
                self._pager,
            ],
            expand=True,
            spacing=8,
        )
        self.refresh()

    # ------------------------------------------------------------------ #
    # construction helpers
    # ------------------------------------------------------------------ #
    def _build_columns(self):
        cols = []
        for label, data_idx in _COLUMNS:
            cols.append(
                ft.DataColumn(
                    label=ft.Text(label, weight=ft.FontWeight.BOLD),
                    numeric=(label == "Time"),
                    on_sort=self._on_sort if data_idx is not None else None,
                )
            )
        return cols

    # ------------------------------------------------------------------ #
    # event handlers
    # ------------------------------------------------------------------ #
    def _on_search(self, _):
        self.query = (self.search_field.value or "").strip().lower()
        self.page_index = 0
        self._persist_view()
        self.refresh()

    def _on_sort(self, e):
        # e.column_index / e.ascending come straight from the header click.
        self.sort_col = e.column_index
        self.sort_asc = bool(e.ascending)
        self.page_index = 0
        self._persist_sort()
        self.refresh()

    def _persist_sort(self):
        try:
            self.service.update_config({
                "games_sort_col": self.sort_col,
                "games_sort_asc": self.sort_asc,
            })
        except Exception:
            pass

    def _on_page_size(self, _):
        value = self.page_size_dd.value
        self.page_size = None if value == "All" else int(value)
        self.page_index = 0
        self._persist_view()
        self._refresh_maybe_loading()

    def _go(self, index):
        self.page_index = max(0, min(index, self._page_count() - 1))
        self._persist_view()
        self._refresh_maybe_loading()

    # ------------------------------------------------------------------ #
    # view persistence (opt-in) + page-change loading
    # ------------------------------------------------------------------ #
    def _persist_view(self):
        """Persist filter / page / rows-per-page when the opt-in toggle is on."""
        if not self.remember_view:
            return
        try:
            self.service.update_config({
                "library_query": self.search_field.value or "",
                "library_page_size": (
                    "All" if self.page_size is None else str(self.page_size)),
                "library_page_index": self.page_index,
            })
        except Exception:
            pass

    def set_remember_view(self, enabled):
        """Enable/disable the opt-in 'remember filter/page/rows' feature.

        Called from the toolbar Library menu. When enabling, the current view is
        captured immediately so it's restored next launch even with no further
        changes."""
        self.remember_view = bool(enabled)
        try:
            self.service.update_config(
                {"remember_library_view": self.remember_view})
        except Exception:
            pass
        if self.remember_view:
            self._persist_view()

    def _refresh_maybe_loading(self):
        """Refresh, showing the loading overlay while a *large* page renders."""
        big = self.page_size is None or (self.page_size and self.page_size >= 100)
        if big and self._is_mounted():
            self.page.run_task(loading.run_with_loading, self.page,
                               "Loading…", self.refresh)
        else:
            self.refresh()

    # ------------------------------------------------------------------ #
    # data shaping
    # ------------------------------------------------------------------ #
    def _filtered_sorted(self):
        items = self.service.data
        if self.query:
            q = self.query
            items = [
                (idx, row)
                for idx, row in items
                if q in str(row[0] or "").lower()
                or q in str(row[2] or "").lower()
                or q in str(row[4] or "").lower()
            ]
        data_idx = _COLUMNS[self.sort_col][1] if 0 <= self.sort_col < len(_COLUMNS) else 0
        if data_idx is None:
            data_idx = 0
        return sorted(items, key=lambda it: _sort_value(data_idx, it[1]),
                      reverse=not self.sort_asc)

    def _page_count(self, total=None):
        if self.page_size is None:
            return 1
        if total is None:
            total = len(self._filtered_sorted())
        return max(1, math.ceil(total / self.page_size))

    def _page_slice(self, items):
        if self.page_size is None:
            return items
        start = self.page_index * self.page_size
        return items[start:start + self.page_size]

    # ------------------------------------------------------------------ #
    # rendering
    # ------------------------------------------------------------------ #
    def _build_row(self, orig_idx, row, number):
        def edit(_):
            self.on_edit(orig_idx)

        def delete(_):
            self.on_delete(orig_idx)

        def change_status(_):
            open_status_dialog(self.page, self.service, orig_idx, on_done=self.refresh)

        cells = [
            ft.DataCell(ft.Text(str(number), color=ft.Colors.ON_SURFACE_VARIANT)),
            ft.DataCell(ft.Text(row[0] or "", weight=ft.FontWeight.W_500), on_tap=edit),
            ft.DataCell(ft.Text(row[1] or "-")),
            ft.DataCell(ft.Text(row[2] or "")),
            ft.DataCell(ft.Text(row[3] or "—")),
            ft.DataCell(theme.status_badge(row), on_tap=change_status),
            ft.DataCell(
                ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN, size=18)
                if row[5] == "✅" else ft.Text("")
            ),
            ft.DataCell(ft.Text((row[6] or "—").split(" ")[0] if row[6] else "—")),
            ft.DataCell(ft.Text(_format_rating(get_effective_game_rating(row)))),
            ft.DataCell(
                ft.Row(
                    [
                        ft.IconButton(ft.Icons.EDIT, tooltip="Edit", icon_size=18,
                                      icon_color=ft.Colors.BLUE, on_click=edit),
                        ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="Delete",
                                      icon_size=18, icon_color=ft.Colors.RED,
                                      on_click=delete),
                    ],
                    spacing=0,
                    tight=True,
                )
            ),
        ]
        return ft.DataRow(cells=cells, color=theme.row_tint(row))

    def refresh(self):
        items = self._filtered_sorted()
        total = len(self.service.data)
        shown_total = len(items)

        page_count = self._page_count(shown_total)
        self.page_index = max(0, min(self.page_index, page_count - 1))
        page_items = self._page_slice(items)

        start_num = (self.page_index * self.page_size + 1) if self.page_size else 1
        self.table.rows = [
            self._build_row(idx, row, start_num + n)
            for n, (idx, row) in enumerate(page_items)
        ]
        self.table.sort_column_index = self.sort_col
        self.table.sort_ascending = self.sort_asc

        # counts + pager labels
        if self.page_size is None or shown_total == 0:
            range_txt = f"{shown_total}"
        else:
            start = self.page_index * self.page_size + 1
            end = start + len(page_items) - 1
            range_txt = f"{start}–{end} of {shown_total}"
        base = f"{shown_total} of {total} games" if self.query else f"{total} games"
        self.count_text.value = base + (f"  ·  showing {range_txt}" if self.page_size else "")
        self.page_label.value = f"Page {self.page_index + 1} / {page_count}"

        at_first = self.page_index <= 0
        at_last = self.page_index >= page_count - 1
        self.first_btn.disabled = self.prev_btn.disabled = at_first
        self.next_btn.disabled = self.last_btn.disabled = at_last
        self._pager.visible = page_count > 1

        self._body.content = self._table_host if page_items else self._empty

        if self._is_mounted():
            self.page.update()

    def set_table_width(self, width):
        """Stretch the DataTable to fill the available horizontal space.

        Without an explicit width the table is laid out with unbounded width and
        shrinks to its content; giving it a bounded width makes it span the area
        and distribute the columns. Called on first layout and on window resize.
        """
        if not width or width <= 0:
            return
        self.table.width = width
        # The table can be unmounted even when the parent column is on the page
        # (e.g. a window resize during the startup splash->UI swap). The width is
        # stored regardless and applies on the next full render, so ignore a
        # transient "not added to the page yet".
        if self._is_mounted():
            try:
                self.table.update()
            except (RuntimeError, AssertionError):
                pass

    def _is_mounted(self):
        if self.page is None:
            return False
        try:
            return self.control.page is not None
        except (RuntimeError, AssertionError):
            return False
