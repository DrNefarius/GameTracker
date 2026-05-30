"""Statistics screen (Flet 0.85.2 port of the legacy PySimpleGUI Statistics tab).

Layout
------
* an overall-stats header (total sessions, total play time, average session
  length, most active day) computed from every session in the library;
* a game picker (``ft.Dropdown``) restricted to games that actually have
  sessions or status history. Picking a game shows
    - per-game totals (session count + total time),
    - a sessions table (Start / Duration / Details),
    - a status-history table (Date / From / To);
* a charts area whose chart is chosen with a small ``ft.Dropdown`` (NOT the
  awkward new ``ft.Tabs``) that swaps the rendered ``ft.Image``.

Backend reuse (all PySimpleGUI-free)
------------------------------------
* ``session_data`` -> ``extract_all_sessions``, ``calculate_session_statistics``,
  ``get_game_sessions``, ``get_status_history`` (pure data helpers).
* ``session_visualizations`` -> ``create_session_timeline_chart``,
  ``create_session_distribution_chart``, ``create_status_timeline_chart``.
  Each returns a ``BytesIO`` of PNG bytes; we spool that to a temp file so
  ``ft.Image(src=<path>)`` can load it. (This module imports only matplotlib /
  numpy, never PySimpleGUI.)
* ``utilities.format_timedelta_with_seconds`` for timedelta display.

Deferred: the GitHub-style contributions heatmap
(``session_management.create_github_contributions_canvas``) is drawn directly
onto a PySimpleGUI/tkinter Canvas and has no Figure/PNG generator, and its home
module imports PySimpleGUI. It is left as a labelled placeholder.

The ``page.update()`` is guarded by the standard mounted check so the view is
safe to build and ``refresh()`` with ``page=None`` (tests / pre-mount).
"""

import os
import tempfile
import uuid
from datetime import datetime, timedelta

import flet as ft

from session_data import (
    extract_all_sessions,
    calculate_session_statistics,
    get_game_sessions,
    get_status_history,
)
from session_visualizations import (
    create_session_timeline_chart,
    create_session_distribution_chart,
    create_status_timeline_chart,
)
from utilities import format_timedelta_with_seconds


# Chart selector: (key, label, kind). ``kind`` decides which backend generator
# runs and what data it gets. "all-*" charts use every session; the per-game
# charts use the selected game's sessions / history.
_CHART_OPTIONS = [
    ("all_timeline", "All sessions: timeline", "all_timeline"),
    ("all_distribution", "All sessions: length distribution", "all_distribution"),
    ("game_timeline", "Selected game: session timeline", "game_timeline"),
    ("game_distribution", "Selected game: length distribution", "game_distribution"),
    ("game_status", "Selected game: status timeline", "game_status"),
    ("contributions", "Contributions heatmap", "contributions"),
]


def _duration_to_timedelta(duration):
    """Parse an "HH:MM:SS" duration string into a timedelta (zero on failure)."""
    if isinstance(duration, timedelta):
        return duration
    if not isinstance(duration, str):
        return timedelta()
    parts = duration.split(":")
    if len(parts) == 3:
        try:
            h, m, s = map(int, parts)
            return timedelta(hours=h, minutes=m, seconds=s)
        except ValueError:
            return timedelta()
    return timedelta()


def _format_session_start(session):
    """Human-friendly start timestamp for a session row."""
    start = session.get("start")
    if not start:
        return "—"
    try:
        return datetime.fromisoformat(start).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(start)


def _session_details_summary(session):
    """One-line summary of a session's feedback (rating stars + trimmed note)."""
    feedback = session.get("feedback") or {}
    parts = []

    rating = feedback.get("rating") or {}
    stars = rating.get("stars")
    if stars:
        try:
            stars = int(stars)
            parts.append("★" * stars + "☆" * (5 - stars))
        except (ValueError, TypeError):
            pass

    text = feedback.get("text")
    if text:
        flat = " ".join(str(text).split())
        parts.append(flat[:80] + "…" if len(flat) > 80 else flat)

    return "  ".join(parts) if parts else "—"


def _format_status_timestamp(change):
    ts = change.get("timestamp")
    if not ts:
        return "—"
    try:
        return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(ts)


class StatisticsView:
    """Owns ``self.control`` (a scrollable Column) and ``refresh()``.

    ``refresh()`` recomputes the overall stats from ``service.data``, repopulates
    the game picker (preserving the current selection when still valid), and
    re-renders the currently selected game's tables + the active chart. It pushes
    ``page.update()`` only when mounted, so it is safe to call from the
    constructor and from tests where ``page`` is ``None``.
    """

    def __init__(self, page, service):
        self.page = page
        self.service = service

        self.selected_game = None
        self.selected_chart = _CHART_OPTIONS[0][0]
        # Per-instance temp dir for chart PNGs; a fresh filename per render forces
        # Flet to reload the image instead of showing a stale cached frame.
        self._tmp_dir = tempfile.gettempdir()

        # ---- overall stats header -----------------------------------------
        self.stat_sessions = self._stat_card(ft.Icons.PLAY_CIRCLE_OUTLINE, "Total sessions", "0")
        self.stat_time = self._stat_card(ft.Icons.SCHEDULE, "Total play time", "00:00:00")
        self.stat_avg = self._stat_card(ft.Icons.TIMELAPSE, "Avg session", "00:00:00")
        self.stat_active = self._stat_card(ft.Icons.CALENDAR_MONTH, "Most active day", "—")
        header = ft.ResponsiveRow(
            [
                ft.Container(self.stat_sessions, col={"xs": 6, "md": 3}),
                ft.Container(self.stat_time, col={"xs": 6, "md": 3}),
                ft.Container(self.stat_avg, col={"xs": 6, "md": 3}),
                ft.Container(self.stat_active, col={"xs": 6, "md": 3}),
            ],
            run_spacing=10,
            spacing=10,
        )

        # ---- game picker ---------------------------------------------------
        self.game_dd = ft.Dropdown(
            label="Game",
            hint_text="Pick a game to see its sessions",
            options=[],
            on_select=self._on_game_select,
            width=360,
        )
        self.game_totals = ft.Text("", size=13, color=ft.Colors.ON_SURFACE_VARIANT)

        self.sessions_table = ft.DataTable(
            columns=[
                ft.DataColumn(label=ft.Text("Start", weight=ft.FontWeight.BOLD)),
                ft.DataColumn(label=ft.Text("Duration", weight=ft.FontWeight.BOLD)),
                ft.DataColumn(label=ft.Text("Details", weight=ft.FontWeight.BOLD)),
            ],
            rows=[],
            show_checkbox_column=False,
            column_spacing=24,
            heading_row_color=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
        )
        self.status_table = ft.DataTable(
            columns=[
                ft.DataColumn(label=ft.Text("Date", weight=ft.FontWeight.BOLD)),
                ft.DataColumn(label=ft.Text("From", weight=ft.FontWeight.BOLD)),
                ft.DataColumn(label=ft.Text("To", weight=ft.FontWeight.BOLD)),
            ],
            rows=[],
            show_checkbox_column=False,
            column_spacing=24,
            heading_row_color=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
        )

        self._game_detail = ft.Container(
            content=ft.Column(
                [
                    self.game_totals,
                    ft.Text("Sessions", size=15, weight=ft.FontWeight.W_600),
                    ft.Column([self.sessions_table], scroll=ft.ScrollMode.AUTO),
                    ft.Divider(height=1),
                    ft.Text("Status history", size=15, weight=ft.FontWeight.W_600),
                    ft.Column([self.status_table], scroll=ft.ScrollMode.AUTO),
                ],
                spacing=10,
            ),
            visible=False,
        )
        self._game_empty = ft.Container(
            content=ft.Text(
                "Select a game above to see its sessions and status history.",
                size=13,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
            padding=ft.Padding(0, 8, 0, 8),
        )

        # ---- charts area ---------------------------------------------------
        self.chart_dd = ft.Dropdown(
            label="Chart",
            value=self.selected_chart,
            options=[ft.dropdown.Option(key=k, text=label) for k, label, _ in _CHART_OPTIONS],
            on_select=self._on_chart_select,
            width=360,
        )
        self._chart_host = ft.Container(
            content=self._chart_placeholder("Loading chart…"),
            alignment=ft.Alignment(0, 0),
            padding=ft.Padding(0, 8, 0, 8),
        )

        # ---- assemble ------------------------------------------------------
        self.control = ft.Column(
            [
                ft.Text("Statistics", size=20, weight=ft.FontWeight.BOLD),
                header,
                ft.Divider(height=1),
                ft.Text("Per-game breakdown", size=16, weight=ft.FontWeight.W_600),
                self.game_dd,
                self._game_empty,
                self._game_detail,
                ft.Divider(height=1),
                ft.Text("Charts", size=16, weight=ft.FontWeight.W_600),
                self.chart_dd,
                self._chart_host,
            ],
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            spacing=12,
        )

        self.refresh()

    # ------------------------------------------------------------------ #
    # construction helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _stat_card(icon, label, value):
        """A small stat tile; ``value`` Text is updated in place on refresh."""
        value_text = ft.Text(value, size=18, weight=ft.FontWeight.BOLD)
        card = ft.Container(
            padding=ft.Padding(14, 12, 14, 12),
            border_radius=12,
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE),
            content=ft.Row(
                [
                    ft.Icon(icon, color=ft.Colors.PRIMARY, size=26),
                    ft.Column(
                        [
                            ft.Text(label, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                            value_text,
                        ],
                        spacing=2,
                        tight=True,
                    ),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        # Stash the value Text on the container so refresh() can mutate it.
        card.data = value_text
        return card

    @staticmethod
    def _chart_placeholder(message):
        return ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.IMAGE_NOT_SUPPORTED_OUTLINED, size=40,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Text(message, size=13, color=ft.Colors.ON_SURFACE_VARIANT,
                            text_align=ft.TextAlign.CENTER),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            alignment=ft.Alignment(0, 0),
            padding=ft.Padding(0, 32, 0, 32),
        )

    # ------------------------------------------------------------------ #
    # event handlers
    # ------------------------------------------------------------------ #
    def _on_game_select(self, _):
        self.selected_game = self.game_dd.value or None
        self._render_game_detail()
        # Refresh the chart too if it's a per-game chart.
        if self.selected_chart.startswith("game_"):
            self._render_chart()
        if self._is_mounted():
            self.page.update()

    def _on_chart_select(self, _):
        self.selected_chart = self.chart_dd.value or _CHART_OPTIONS[0][0]
        self._render_chart()
        if self._is_mounted():
            self.page.update()

    # ------------------------------------------------------------------ #
    # data shaping
    # ------------------------------------------------------------------ #
    def _games_with_history(self):
        """Names of games that have at least one session or status-history entry."""
        names = []
        for _, row in self.service.data:
            name = row[0]
            has_sessions = len(row) > 7 and row[7]
            has_history = len(row) > 8 and row[8]
            if has_sessions or has_history:
                names.append(name)
        return sorted(names, key=lambda n: (n or "").lower())

    # ------------------------------------------------------------------ #
    # rendering
    # ------------------------------------------------------------------ #
    def _render_overall(self):
        stats = calculate_session_statistics(extract_all_sessions(self.service.data))
        self.stat_sessions.data.value = str(stats.get("total_count", 0))
        self.stat_time.data.value = format_timedelta_with_seconds(stats.get("total_time"))
        self.stat_avg.data.value = format_timedelta_with_seconds(stats.get("avg_length"))

        active = stats.get("most_active_day") or {}
        day = active.get("day")
        count = active.get("count", 0)
        if day:
            day_str = day.strftime("%Y-%m-%d") if hasattr(day, "strftime") else str(day)
            self.stat_active.data.value = f"{day_str} ({count})"
        else:
            self.stat_active.data.value = "—"

    def _render_game_detail(self):
        name = self.selected_game
        if not name:
            self._game_detail.visible = False
            self._game_empty.visible = True
            return

        self._game_empty.visible = False
        self._game_detail.visible = True

        sessions = get_game_sessions(self.service.data, name) or []
        history = get_status_history(self.service.data, name) or []

        # Per-game totals.
        total = timedelta()
        for s in sessions:
            total += _duration_to_timedelta(s.get("duration"))
        self.game_totals.value = (
            f"{len(sessions)} session{'s' if len(sessions) != 1 else ''}  ·  "
            f"total {format_timedelta_with_seconds(total)}"
        )

        # Sessions table (newest first).
        def _sort_key(s):
            try:
                return datetime.fromisoformat(s.get("start", ""))
            except (ValueError, TypeError):
                return datetime.min

        rows = []
        for s in sorted(sessions, key=_sort_key, reverse=True):
            rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(_format_session_start(s))),
                        ft.DataCell(ft.Text(str(s.get("duration", "00:00:00")))),
                        ft.DataCell(ft.Text(_session_details_summary(s))),
                    ]
                )
            )
        self.sessions_table.rows = rows

        # Status-history table (chronological).
        hist_rows = []
        for change in sorted(history, key=lambda c: c.get("timestamp", "")):
            hist_rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(_format_status_timestamp(change))),
                        ft.DataCell(ft.Text(str(change.get("from") or "—"))),
                        ft.DataCell(ft.Text(str(change.get("to") or "—"))),
                    ]
                )
            )
        self.status_table.rows = hist_rows

    def _write_png(self, buf):
        """Spool a PNG BytesIO to a fresh temp file and return its absolute path."""
        path = os.path.join(self._tmp_dir, f"stats_chart_{uuid.uuid4().hex}.png")
        with open(path, "wb") as f:
            f.write(buf.getvalue())
        return path

    def _render_chart(self):
        kind = next((k for key, _, k in _CHART_OPTIONS if key == self.selected_chart), None)

        # Contributions heatmap is canvas-only in the legacy app -> deferred.
        if kind == "contributions":
            self._chart_host.content = self._chart_placeholder(
                "Contributions heatmap — coming soon"
            )
            return

        # Per-game charts need a selected game with data.
        if kind in ("game_timeline", "game_distribution", "game_status"):
            if not self.selected_game:
                self._chart_host.content = self._chart_placeholder(
                    "Select a game above to view this chart."
                )
                return

        try:
            if kind == "all_timeline":
                buf = create_session_timeline_chart(
                    extract_all_sessions(self.service.data)
                )
            elif kind == "all_distribution":
                buf = create_session_distribution_chart(
                    extract_all_sessions(self.service.data), chart_type="histogram"
                )
            elif kind == "game_timeline":
                buf = create_session_timeline_chart(
                    get_game_sessions(self.service.data, self.selected_game),
                    game_name=self.selected_game,
                )
            elif kind == "game_distribution":
                buf = create_session_distribution_chart(
                    get_game_sessions(self.service.data, self.selected_game),
                    game_name=self.selected_game,
                    chart_type="histogram",
                )
            elif kind == "game_status":
                buf = create_status_timeline_chart(
                    get_status_history(self.service.data, self.selected_game),
                    game_name=self.selected_game,
                )
            else:
                buf = None

            if buf is None:
                self._chart_host.content = self._chart_placeholder("No chart available.")
                return

            path = self._write_png(buf)
            self._chart_host.content = ft.Image(
                src=path,
                fit=ft.BoxFit.CONTAIN,
                gapless_playback=True,
                height=360,
                error_content=ft.Text(
                    "Image failed to load", color=ft.Colors.ON_SURFACE_VARIANT
                ),
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._chart_host.content = self._chart_placeholder(
                f"Could not generate chart.\n{exc}"
            )

    def refresh(self):
        """Recompute overall stats, repopulate the picker, re-render tables/chart."""
        self._render_overall()

        # Repopulate the game picker, preserving the current selection if valid.
        names = self._games_with_history()
        self.game_dd.options = [ft.dropdown.Option(key=n, text=n) for n in names]
        if self.selected_game not in names:
            self.selected_game = None
            self.game_dd.value = None

        self._render_game_detail()
        self._render_chart()

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
