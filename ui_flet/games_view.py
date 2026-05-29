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
from ui_flet import theme

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


# Display columns: (header, sortable). Index in this list == DataColumn index
# reported by the on_sort event, and is mapped to a sort key by _sort_value().
_COLUMNS = [
    ("Name", True),
    ("Released", True),
    ("Platform", True),
    ("Time", True),
    ("Status", True),
    ("Owned", False),
    ("Last Played", True),
    ("Rating", False),
    ("", False),  # actions
]


def _sort_value(col_index, row):
    if col_index == 1:
        rel = row[1] or ""
        return (rel == "-", rel)            # unknown dates last
    if col_index == 2:
        return (row[2] or "").lower()
    if col_index == 3:
        return _time_to_seconds(row[3])
    if col_index == 4:
        return (row[4] or "").lower()
    if col_index == 6:
        lp = row[6] or ""
        return (lp == "", lp)               # never-played last
    # default / col 0
    return (row[0] or "").lower()


class GamesView:
    def __init__(self, page, service, on_edit, on_delete, on_add):
        self.page = page
        self.service = service
        self.on_edit = on_edit
        self.on_delete = on_delete
        self.on_add = on_add

        self.query = ""
        self.sort_col = 0          # DataColumn index currently sorted by
        self.sort_asc = True
        self.page_size = DEFAULT_PAGE_SIZE   # int, or None for "All"
        self.page_index = 0

        # ---- top controls --------------------------------------------------
        self.search_field = ft.TextField(
            hint_text="Search by name, platform or status...",
            prefix_icon=ft.Icons.SEARCH,
            on_change=self._on_search,
            expand=True,
            dense=True,
        )
        self.page_size_dd = ft.Dropdown(
            label="Per page",
            value=str(DEFAULT_PAGE_SIZE),
            width=120,
            options=[ft.dropdown.Option(key=o, text=o) for o in PAGE_SIZE_OPTIONS],
            on_select=self._on_page_size,
        )
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
                                        on_click=lambda _: self.on_add()),
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
        for label, sortable in _COLUMNS:
            cols.append(
                ft.DataColumn(
                    label=ft.Text(label, weight=ft.FontWeight.BOLD),
                    numeric=(label == "Time"),
                    on_sort=self._on_sort if sortable else None,
                )
            )
        return cols

    # ------------------------------------------------------------------ #
    # event handlers
    # ------------------------------------------------------------------ #
    def _on_search(self, _):
        self.query = (self.search_field.value or "").strip().lower()
        self.page_index = 0
        self.refresh()

    def _on_sort(self, e):
        # e.column_index / e.ascending come straight from the header click.
        self.sort_col = e.column_index
        self.sort_asc = bool(e.ascending)
        self.page_index = 0
        self.refresh()

    def _on_page_size(self, _):
        value = self.page_size_dd.value
        self.page_size = None if value == "All" else int(value)
        self.page_index = 0
        self.refresh()

    def _go(self, index):
        self.page_index = max(0, min(index, self._page_count() - 1))
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
        return sorted(items, key=lambda it: _sort_value(self.sort_col, it[1]),
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
    def _build_row(self, orig_idx, row):
        def edit(_):
            self.on_edit(orig_idx)

        def delete(_):
            self.on_delete(orig_idx)

        cells = [
            ft.DataCell(ft.Text(row[0] or "", weight=ft.FontWeight.W_500), on_tap=edit),
            ft.DataCell(ft.Text(row[1] or "-")),
            ft.DataCell(ft.Text(row[2] or "")),
            ft.DataCell(ft.Text(row[3] or "—")),
            ft.DataCell(theme.status_badge(row)),
            ft.DataCell(
                ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN, size=18)
                if row[5] == "✅" else ft.Text("")
            ),
            ft.DataCell(ft.Text((row[6] or "—").split(" ")[0] if row[6] else "—")),
            ft.DataCell(ft.Text(_format_rating(row[9] if len(row) > 9 else None))),
            ft.DataCell(
                ft.Row(
                    [
                        ft.IconButton(ft.Icons.EDIT, tooltip="Edit", icon_size=18,
                                      on_click=edit),
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

        self.table.rows = [self._build_row(idx, row) for idx, row in page_items]
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

    def _is_mounted(self):
        if self.page is None:
            return False
        try:
            return self.control.page is not None
        except (RuntimeError, AssertionError):
            return False
