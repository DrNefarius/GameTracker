"""Summary tab: the five matplotlib charts rendered as images.

Reuses ``visualizations.update_summary_charts(service.data)`` (matplotlib /
Agg, no GUI toolkit), which regenerates five PNG files in the temp dir and
returns a dict of absolute paths (or ``None`` on failure). We render each PNG
with ``ft.Image`` in a responsive ~2-per-row grid, with a "Refresh charts"
button that re-runs the backend.

Because the PNG *paths* stay constant between refreshes (only their bytes
change), Flet would otherwise keep showing the cached first render. To force a
reload we rebuild the ``ft.Image`` controls from scratch on every refresh.
"""

from datetime import timedelta

import flet as ft

from visualizations import update_summary_charts
from utilities import format_timedelta_with_seconds

# (dict key returned by update_summary_charts, section title) in display order.
_CHARTS = [
    ("pie_chart", "Game Status Distribution"),
    ("year_chart", "Games by Release Year"),
    ("playtime_chart", "Top Games by Playtime"),
    ("rating_chart", "Ratings Distribution"),
    ("genre_chart", "Genres Distribution"),
]

# Chart cards take a full row on phones, half a row from medium screens up, so
# the layout lands at roughly two charts per row on a desktop window.
_CARD_COL = {"xs": 12, "md": 6}


class SummaryView:
    """Owns ``self.control`` (a scrollable Column) and ``refresh()``.

    ``refresh()`` re-runs the chart backend and rebuilds the image grid, then
    pushes a ``page.update()`` only when actually mounted (so it is safe to call
    from the constructor or from tests where ``page`` is ``None``).
    """

    def __init__(self, page, service):
        self.page = page
        self.service = service

        self.refresh_btn = ft.FilledButton(
            "Refresh charts",
            icon=ft.Icons.REFRESH,
            on_click=lambda _: self.refresh(),
        )
        self.total_time_text = ft.Text("", size=13, color=ft.Colors.ON_SURFACE_VARIANT)
        header = ft.Row(
            [
                ft.Text("Summary", size=20, weight=ft.FontWeight.BOLD),
                ft.Container(width=16),
                self.total_time_text,
                ft.Container(expand=True),
                self.refresh_btn,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Body host: replaced wholesale on each refresh with either the chart
        # grid (a ResponsiveRow) or a friendly error message.
        self._body = ft.Container(content=self._build_loading(), expand=True)

        self.control = ft.Column(
            [header, self._body],
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            spacing=12,
        )

        self.refresh()

    # ------------------------------------------------------------------ #
    # rendering
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_loading():
        return ft.Container(
            content=ft.Text("Generating charts...", size=14,
                            color=ft.Colors.ON_SURFACE_VARIANT),
            alignment=ft.Alignment(0, 0),
            padding=ft.Padding(0, 24, 0, 24),
        )

    @staticmethod
    def _error_message(detail=None):
        msg = "Could not generate the summary charts."
        if detail:
            msg += f"\n{detail}"
        return ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.BROKEN_IMAGE_OUTLINED, size=48,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Text("No charts to show", size=16),
                    ft.Text(msg, size=12, color=ft.Colors.ON_SURFACE_VARIANT,
                            text_align=ft.TextAlign.CENTER),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            alignment=ft.Alignment(0, 0),
            padding=ft.Padding(0, 32, 0, 32),
            expand=True,
        )

    def _build_card(self, title, path):
        """One chart card: title + image (or a per-chart placeholder)."""
        if path:
            # gapless_playback keeps the previous frame until the new bytes
            # decode (we still rebuild the control, but this avoids a flash).
            inner = ft.Image(
                src=path,
                fit=ft.BoxFit.CONTAIN,
                gapless_playback=True,
                height=320,
                error_content=ft.Text("Image failed to load",
                                      color=ft.Colors.ON_SURFACE_VARIANT),
            )
        else:
            inner = ft.Container(
                content=ft.Text("This chart is unavailable.", size=12,
                                color=ft.Colors.ON_SURFACE_VARIANT),
                alignment=ft.Alignment(0, 0),
                height=320,
            )

        return ft.Container(
            col=_CARD_COL,
            padding=ft.Padding(12, 12, 12, 12),
            border_radius=12,
            bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.ON_SURFACE),
            content=ft.Column(
                [
                    ft.Text(title, size=14, weight=ft.FontWeight.W_600),
                    inner,
                ],
                spacing=8,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _build_grid(self, charts):
        cards = [self._build_card(title, charts.get(key)) for key, title in _CHARTS]
        return ft.ResponsiveRow(cards, run_spacing=12, spacing=12)

    def _total_play_time(self):
        total = 0
        for _, row in self.service.data:
            tv = row[3] if len(row) > 3 else None
            if not isinstance(tv, str):
                continue
            parts = tv.split(":")
            try:
                if len(parts) == 3:
                    h, m, s = map(int, parts)
                    total += h * 3600 + m * 60 + s
                elif len(parts) == 2:
                    h, m = map(int, parts)
                    total += h * 3600 + m * 60
            except ValueError:
                continue
        return format_timedelta_with_seconds(timedelta(seconds=total))

    def refresh(self):
        """Regenerate the PNGs and rebuild the image grid in place."""
        self.total_time_text.value = f"Total play time: {self._total_play_time()}"
        try:
            charts = update_summary_charts(self.service.data)
        except Exception as exc:  # pragma: no cover - defensive
            charts = None
            self._body.content = self._error_message(str(exc))
        else:
            if not charts:
                self._body.content = self._error_message()
            else:
                self._body.content = self._build_grid(charts)

        if self._is_mounted():
            self.page.update()

    # ------------------------------------------------------------------ #
    def _is_mounted(self):
        if self.page is None:
            return False
        try:
            return self.control.page is not None
        except (RuntimeError, AssertionError):
            return False
