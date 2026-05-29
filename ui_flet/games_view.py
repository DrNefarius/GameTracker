"""Games List view: a searchable, sortable DataTable bound to the library.

Builds a Flet control tree (``self.control``) and exposes ``refresh()`` to
re-render rows from ``service.data`` after any mutation. Row backgrounds are
tinted by status (see ``theme.row_tint``).
"""

import flet as ft

from constants import STAR_FILLED, STAR_EMPTY
from ui_flet import theme


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
    stars = int(rating.get("stars", 0) or 0)
    stars = max(0, min(5, stars))
    prefix = "≈" if rating.get("auto_calculated") else ""
    return prefix + STAR_FILLED * stars + STAR_EMPTY * (5 - stars)


# Display columns: (header, data_row_index_for_sorting_or_None, sortable)
_COLUMNS = [
    ("Name", 0, True),
    ("Released", 1, True),
    ("Platform", 2, True),
    ("Time", 3, True),
    ("Status", 4, True),
    ("Owned", 5, False),
    ("Last Played", 6, True),
    ("Rating", None, False),
    ("", None, False),  # actions
]


def _sort_value(col_index, row):
    if col_index == 0:
        return (row[0] or "").lower()
    if col_index == 1:
        rel = row[1] or ""
        return (rel == "-", rel)
    if col_index == 2:
        return (row[2] or "").lower()
    if col_index == 3:
        return _time_to_seconds(row[3])
    if col_index == 4:
        return (row[4] or "").lower()
    if col_index == 6:
        lp = row[6] or ""
        return (lp == "", lp)
    return (row[0] or "").lower()


class GamesView:
    def __init__(self, page, service, on_edit, on_delete, on_add):
        self.page = page
        self.service = service
        self.on_edit = on_edit
        self.on_delete = on_delete
        self.on_add = on_add

        self.query = ""
        self.sort_col = 0       # display column index
        self.sort_asc = True

        self.search_field = ft.TextField(
            hint_text="Search by name, platform or status...",
            prefix_icon=ft.Icons.SEARCH,
            on_change=self._on_search,
            expand=True,
            dense=True,
        )
        self.count_text = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
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
        # Scroll host so large libraries don't overflow the window.
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

        self.control = ft.Column(
            [
                ft.Row(
                    [
                        self.search_field,
                        ft.FilledButton("Add game", icon=ft.Icons.ADD,
                                        on_click=lambda _: self.on_add()),
                    ],
                ),
                self.count_text,
                self._body,
            ],
            expand=True,
            spacing=8,
        )
        self.refresh()

    # ------------------------------------------------------------------ #
    def _build_columns(self):
        cols = []
        for display_index, (label, _data_idx, sortable) in enumerate(_COLUMNS):
            cols.append(
                ft.DataColumn(
                    label=ft.Text(label, weight=ft.FontWeight.BOLD),
                    on_sort=(lambda e, ci=display_index: self._on_sort(ci)) if sortable else None,
                )
            )
        return cols

    def _on_search(self, _):
        self.query = (self.search_field.value or "").strip().lower()
        self.refresh()

    def _on_sort(self, display_index):
        if display_index == self.sort_col:
            self.sort_asc = not self.sort_asc
        else:
            self.sort_col = display_index
            self.sort_asc = True
        self.refresh()

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
        items = sorted(items, key=lambda it: _sort_value(self.sort_col, it[1]),
                       reverse=not self.sort_asc)
        return items

    def _build_row(self, orig_idx, row):
        def edit(_):
            self.on_edit(orig_idx)

        def delete(_):
            self.on_delete(orig_idx)

        name_cell = ft.DataCell(
            ft.Text(row[0] or "", weight=ft.FontWeight.W_500), on_tap=edit
        )
        cells = [
            name_cell,
            ft.DataCell(ft.Text(row[1] or "-")),
            ft.DataCell(ft.Text(row[2] or "")),
            ft.DataCell(ft.Text(row[3] or "—")),
            ft.DataCell(theme.status_badge(row)),
            ft.DataCell(
                ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN, size=18)
                if row[5] == "✅"
                else ft.Text("")
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
        self.table.rows = [self._build_row(idx, row) for idx, row in items]
        self.table.sort_column_index = self.sort_col
        self.table.sort_ascending = self.sort_asc

        total = len(self.service.data)
        shown = len(items)
        self.count_text.value = (
            f"{shown} of {total} games" if self.query else f"{total} games"
        )
        self._body.content = self._table_host if items else self._empty

        # Only push to the client once the control is mounted. In Flet 0.85,
        # accessing ``control.page`` *raises* until the control is added to the
        # page, so probe it defensively (during __init__ it isn't mounted yet).
        if self._is_mounted():
            self.page.update()

    def _is_mounted(self):
        if self.page is None:
            return False
        try:
            return self.control.page is not None
        except (RuntimeError, AssertionError):
            return False
