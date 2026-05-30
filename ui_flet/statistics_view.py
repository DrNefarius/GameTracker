"""Statistics screen (Flet 0.85.2 port of the legacy PySimpleGUI Statistics tab).

Parity with the legacy tab:
* overall-stats header (total sessions, total play time, avg session length,
  most active day) over every session in the library;
* a scope selector with an **All games** option plus every game that has
  sessions/history;
* a native **contributions heatmap** (GitHub-style day grid) for the selected
  scope (all games, or a single game). Clicking a day opens a **date-activity**
  dialog listing that day's sessions;
* a **rating comparison** (session-based auto rating vs. the manual rating,
  including common tags and the manual rating comment) for the selected game;
* per-game **sessions/activity log** + **status-history** tables;
* a charts area (session timeline / length distribution / status timeline),
  chosen with a small dropdown.

Backend reuse (all PySimpleGUI-free): ``session_data`` data helpers,
``session_visualizations`` chart generators (return PNG ``BytesIO``),
``core.ratings_logic`` (pure rating math), ``utilities``.
"""

import os
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, date, timedelta

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
from core.ratings_logic import format_rating, get_session_rating_summary

ALL_GAMES = "__all__"
_HEATMAP_WEEKS = 53

_CHART_OPTIONS = [
    ("all_timeline", "All sessions: timeline", "all_timeline"),
    ("all_distribution", "All sessions: length distribution", "all_distribution"),
    ("game_timeline", "Selected game: session timeline", "game_timeline"),
    ("game_distribution", "Selected game: length distribution", "game_distribution"),
    ("game_status", "Selected game: status timeline", "game_status"),
]


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
def _duration_to_timedelta(duration):
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
    start = session.get("start")
    if not start:
        return "—"
    try:
        return datetime.fromisoformat(start).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(start)


def _session_time_range(session):
    """'HH:MM–HH:MM' from start/end isoformat, falling back to just start."""
    start = session.get("start")
    end = session.get("end")
    try:
        s = datetime.fromisoformat(start).strftime("%H:%M") if start else "?"
    except (ValueError, TypeError):
        s = "?"
    try:
        e = datetime.fromisoformat(end).strftime("%H:%M") if end else None
    except (ValueError, TypeError):
        e = None
    return f"{s}–{e}" if e else s


def _session_details_summary(session):
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


def _sessions_for_scope(data, game_name=None):
    """Sessions for one game (tagged with 'game') or all sessions library-wide."""
    if game_name:
        return [dict(s, game=game_name) for s in (get_game_sessions(data, game_name) or [])]
    return extract_all_sessions(data)


def _sessions_for_day(data, target_date, game_name=None):
    out = []
    for s in _sessions_for_scope(data, game_name):
        start = s.get("start")
        if not start:
            continue
        try:
            if datetime.fromisoformat(start).date() == target_date:
                out.append(s)
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda s: s.get("start", ""))
    return out


def _intensity_color(count):
    if count <= 0:
        return ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE)
    if count <= 2:
        return ft.Colors.with_opacity(0.30, ft.Colors.GREEN)
    if count <= 4:
        return ft.Colors.with_opacity(0.52, ft.Colors.GREEN)
    if count <= 6:
        return ft.Colors.with_opacity(0.74, ft.Colors.GREEN)
    return ft.Colors.GREEN


# --------------------------------------------------------------------------- #
# date-activity dialog
# --------------------------------------------------------------------------- #
def open_date_activity_dialog(page, data, target_date, game_name=None):
    """Modal listing all sessions on ``target_date`` (optionally one game)."""
    sessions = _sessions_for_day(data, target_date, game_name)

    if sessions:
        rows = []
        for s in sessions:
            label = s.get("game", game_name or "")
            line = f"{_session_time_range(s)}  ·  {s.get('duration', '00:00:00')}"
            details = _session_details_summary(s)
            rows.append(
                ft.Container(
                    padding=ft.Padding(10, 8, 10, 8),
                    border_radius=8,
                    bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.ON_SURFACE),
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(label, weight=ft.FontWeight.W_600, expand=True),
                                    ft.Text(line, size=12,
                                            color=ft.Colors.ON_SURFACE_VARIANT),
                                ]
                            ),
                            ft.Text(details, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                        ],
                        spacing=2,
                        tight=True,
                    ),
                )
            )
        total = timedelta()
        for s in sessions:
            total += _duration_to_timedelta(s.get("duration"))
        header = ft.Text(
            f"{len(sessions)} session{'s' if len(sessions) != 1 else ''}  ·  "
            f"{format_timedelta_with_seconds(total)} played",
            size=13, color=ft.Colors.ON_SURFACE_VARIANT,
        )
        body = ft.Column([header, *rows], spacing=8, scroll=ft.ScrollMode.AUTO, tight=True)
    else:
        body = ft.Text("No sessions recorded on this day.",
                       color=ft.Colors.ON_SURFACE_VARIANT)

    scope = f" — {game_name}" if game_name else ""
    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(f"Activity on {target_date.isoformat()}{scope}"),
        content=ft.Container(width=560, height=420, content=body),
        actions=[ft.TextButton("Close", on_click=lambda e: page.pop_dialog())],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dialog)


class StatisticsView:
    """Owns ``self.control`` (scrollable Column) and ``refresh()``."""

    def __init__(self, page, service):
        self.page = page
        self.service = service
        self.selected_game = None            # None == All games
        self.selected_chart = _CHART_OPTIONS[0][0]
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
            run_spacing=10, spacing=10,
        )

        # ---- scope selector -----------------------------------------------
        self.game_dd = ft.Dropdown(
            label="Scope",
            value=ALL_GAMES,
            options=[],
            on_select=self._on_game_select,
            width=360,
        )

        # ---- contributions heatmap ----------------------------------------
        self.heatmap_caption = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.heatmap_host = ft.Container(content=ft.Text("…"))
        heatmap_section = ft.Column(
            [
                ft.Text("Contributions", size=16, weight=ft.FontWeight.W_600),
                self.heatmap_caption,
                ft.Column([self.heatmap_host], scroll=ft.ScrollMode.AUTO),
                self._heatmap_legend(),
            ],
            spacing=6,
        )

        # ---- rating comparison --------------------------------------------
        self.auto_rating_host = ft.Container(expand=True)
        self.manual_rating_host = ft.Container(expand=True)
        self.rating_section = ft.Column(
            [
                ft.Text("Rating comparison", size=16, weight=ft.FontWeight.W_600),
                ft.ResponsiveRow(
                    [
                        ft.Container(self.auto_rating_host, col={"xs": 12, "md": 6}),
                        ft.Container(self.manual_rating_host, col={"xs": 12, "md": 6}),
                    ],
                    run_spacing=10, spacing=10,
                ),
            ],
            spacing=8,
            visible=False,
        )

        # ---- per-game tables ----------------------------------------------
        self.game_totals = ft.Text("", size=13, color=ft.Colors.ON_SURFACE_VARIANT)
        self.sessions_table = ft.DataTable(
            columns=[
                ft.DataColumn(label=ft.Text("Start", weight=ft.FontWeight.BOLD)),
                ft.DataColumn(label=ft.Text("Duration", weight=ft.FontWeight.BOLD)),
                ft.DataColumn(label=ft.Text("Notes / rating", weight=ft.FontWeight.BOLD)),
            ],
            rows=[], show_checkbox_column=False, column_spacing=24,
            heading_row_color=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
        )
        self.status_table = ft.DataTable(
            columns=[
                ft.DataColumn(label=ft.Text("Date", weight=ft.FontWeight.BOLD)),
                ft.DataColumn(label=ft.Text("From", weight=ft.FontWeight.BOLD)),
                ft.DataColumn(label=ft.Text("To", weight=ft.FontWeight.BOLD)),
            ],
            rows=[], show_checkbox_column=False, column_spacing=24,
            heading_row_color=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
        )
        self._game_detail = ft.Column(
            [
                self.game_totals,
                ft.Text("Activity log (sessions)", size=15, weight=ft.FontWeight.W_600),
                ft.Column([self.sessions_table], scroll=ft.ScrollMode.AUTO),
                ft.Divider(height=1),
                ft.Text("Status history", size=15, weight=ft.FontWeight.W_600),
                ft.Column([self.status_table], scroll=ft.ScrollMode.AUTO),
            ],
            spacing=10, visible=False,
        )
        self._game_empty = ft.Container(
            content=ft.Text("Select a game above to see its rating, sessions and history.",
                            size=13, color=ft.Colors.ON_SURFACE_VARIANT),
            padding=ft.Padding(0, 8, 0, 8),
        )

        # ---- charts --------------------------------------------------------
        self.chart_dd = ft.Dropdown(
            label="Chart",
            value=self.selected_chart,
            options=[ft.dropdown.Option(key=k, text=label) for k, label, _ in _CHART_OPTIONS],
            on_select=self._on_chart_select,
            width=360,
        )
        self._chart_host = ft.Container(
            content=self._chart_placeholder("Loading chart…"),
            alignment=ft.Alignment(0, 0), padding=ft.Padding(0, 8, 0, 8),
        )

        # ---- assemble ------------------------------------------------------
        self.control = ft.Column(
            [
                ft.Text("Statistics", size=20, weight=ft.FontWeight.BOLD),
                header,
                ft.Divider(height=1),
                self.game_dd,
                heatmap_section,
                ft.Divider(height=1),
                self.rating_section,
                self._game_empty,
                self._game_detail,
                ft.Divider(height=1),
                ft.Text("Charts", size=16, weight=ft.FontWeight.W_600),
                self.chart_dd,
                self._chart_host,
            ],
            expand=True, scroll=ft.ScrollMode.AUTO, spacing=12,
        )

        self.refresh()

    # ------------------------------------------------------------------ #
    # construction helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _stat_card(icon, label, value):
        value_text = ft.Text(value, size=18, weight=ft.FontWeight.BOLD)
        card = ft.Container(
            padding=ft.Padding(14, 12, 14, 12), border_radius=12,
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE),
            content=ft.Row(
                [
                    ft.Icon(icon, color=ft.Colors.PRIMARY, size=26),
                    ft.Column(
                        [ft.Text(label, size=12, color=ft.Colors.ON_SURFACE_VARIANT), value_text],
                        spacing=2, tight=True,
                    ),
                ],
                spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
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
                horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8,
            ),
            alignment=ft.Alignment(0, 0), padding=ft.Padding(0, 32, 0, 32),
        )

    @staticmethod
    def _heatmap_legend():
        squares = [
            ft.Container(width=12, height=12, border_radius=2, bgcolor=_intensity_color(c))
            for c in (0, 1, 3, 5, 7)
        ]
        return ft.Row(
            [ft.Text("Less", size=11, color=ft.Colors.ON_SURFACE_VARIANT), *squares,
             ft.Text("More", size=11, color=ft.Colors.ON_SURFACE_VARIANT)],
            spacing=4, tight=True,
        )

    def _rating_card(self, title, body_controls):
        return ft.Container(
            padding=ft.Padding(14, 12, 14, 12), border_radius=12,
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE),
            content=ft.Column(
                [ft.Text(title, size=13, weight=ft.FontWeight.W_600,
                         color=ft.Colors.ON_SURFACE_VARIANT), *body_controls],
                spacing=6, tight=True,
            ),
        )

    # ------------------------------------------------------------------ #
    # event handlers
    # ------------------------------------------------------------------ #
    def _on_game_select(self, _):
        value = self.game_dd.value
        self.selected_game = None if value in (None, ALL_GAMES) else value
        self._render_contributions()
        self._render_rating_comparison()
        self._render_game_detail()
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
        names = []
        for _, row in self.service.data:
            has_sessions = len(row) > 7 and row[7]
            has_history = len(row) > 8 and row[8]
            if has_sessions or has_history:
                names.append(row[0])
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
        if day:
            day_str = day.strftime("%Y-%m-%d") if hasattr(day, "strftime") else str(day)
            self.stat_active.data.value = f"{day_str} ({active.get('count', 0)})"
        else:
            self.stat_active.data.value = "—"

    def _day_cell(self, day, count, seconds):
        tip = (f"{day.isoformat()}: {count} session{'s' if count != 1 else ''}"
               f", {format_timedelta_with_seconds(timedelta(seconds=int(seconds)))}")
        return ft.Container(
            width=13, height=13, border_radius=2, bgcolor=_intensity_color(count),
            tooltip=tip,
            on_click=(lambda e, d=day: open_date_activity_dialog(
                self.page, self.service.data, d, self.selected_game)),
        )

    def _render_contributions(self):
        sessions = _sessions_for_scope(self.service.data, self.selected_game)
        by_day = defaultdict(lambda: [0, 0.0])
        for s in sessions:
            start = s.get("start")
            if not start:
                continue
            try:
                d = datetime.fromisoformat(start).date()
            except (ValueError, TypeError):
                continue
            by_day[d][0] += 1
            by_day[d][1] += _duration_to_timedelta(s.get("duration")).total_seconds()

        end = date.today()
        start_day = end - timedelta(days=_HEATMAP_WEEKS * 7 - 1)
        start_day -= timedelta(days=start_day.weekday())  # align to Monday

        week_cols = []
        cur = start_day
        while cur <= end:
            cells = []
            for wd in range(7):
                day = cur + timedelta(days=wd)
                if day > end:
                    cells.append(ft.Container(width=13, height=13))
                else:
                    cnt, secs = by_day.get(day, [0, 0.0])
                    cells.append(self._day_cell(day, cnt, secs))
            week_cols.append(ft.Column(cells, spacing=3, tight=True))
            cur += timedelta(days=7)

        self.heatmap_host.content = ft.Row(week_cols, spacing=3, tight=True)
        scope = self.selected_game or "All games"
        active_days = sum(1 for v in by_day.values() if v[0] > 0)
        self.heatmap_caption.value = (
            f"{scope} · {start_day.isoformat()} → {end.isoformat()} · "
            f"{active_days} active day{'s' if active_days != 1 else ''} "
            f"(click a day for details)"
        )

    def _render_rating_comparison(self):
        if not self.selected_game:
            self.rating_section.visible = False
            return
        self.rating_section.visible = True

        sessions = get_game_sessions(self.service.data, self.selected_game) or []
        auto = get_session_rating_summary(sessions)
        manual = None
        for _, row in self.service.data:
            if row[0] == self.selected_game:
                manual = row[9] if len(row) > 9 and isinstance(row[9], dict) else None
                break

        # Auto (session-based)
        if auto:
            auto_body = [
                ft.Text(format_rating({"stars": auto["average_stars"]}) or "—", size=22),
                ft.Text(f"Avg {auto['exact_average']:.1f} over "
                        f"{auto['total_rated_sessions']} rated session"
                        f"{'s' if auto['total_rated_sessions'] != 1 else ''}",
                        size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text("Common tags: " + (", ".join(auto["most_common_tags"]) or "none"),
                        size=12, color=ft.Colors.ON_SURFACE_VARIANT),
            ]
        else:
            auto_body = [ft.Text("No session ratings yet.", size=12,
                                 color=ft.Colors.ON_SURFACE_VARIANT)]
        self.auto_rating_host.content = self._rating_card(
            "Auto-calculated (from sessions)", auto_body)

        # Manual
        if manual:
            tags = manual.get("tags") or []
            comment = manual.get("comment")
            manual_body = [
                ft.Text(format_rating(manual) or "—", size=22),
                ft.Text("Tags: " + (", ".join(tags) if tags else "none"),
                        size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text("Comment: " + (comment if comment else "no comment"),
                        size=12, color=ft.Colors.ON_SURFACE_VARIANT),
            ]
        else:
            manual_body = [ft.Text("No manual rating set.", size=12,
                                   color=ft.Colors.ON_SURFACE_VARIANT)]
        self.manual_rating_host.content = self._rating_card("Your rating (manual)", manual_body)

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

        total = timedelta()
        for s in sessions:
            total += _duration_to_timedelta(s.get("duration"))
        self.game_totals.value = (
            f"{len(sessions)} session{'s' if len(sessions) != 1 else ''}  ·  "
            f"total {format_timedelta_with_seconds(total)}"
        )

        def _sort_key(s):
            try:
                return datetime.fromisoformat(s.get("start", ""))
            except (ValueError, TypeError):
                return datetime.min

        self.sessions_table.rows = [
            ft.DataRow(cells=[
                ft.DataCell(ft.Text(_format_session_start(s))),
                ft.DataCell(ft.Text(str(s.get("duration", "00:00:00")))),
                ft.DataCell(ft.Text(_session_details_summary(s))),
            ])
            for s in sorted(sessions, key=_sort_key, reverse=True)
        ]
        self.status_table.rows = [
            ft.DataRow(cells=[
                ft.DataCell(ft.Text(_format_status_timestamp(c))),
                ft.DataCell(ft.Text(str(c.get("from") or "—"))),
                ft.DataCell(ft.Text(str(c.get("to") or "—"))),
            ])
            for c in sorted(history, key=lambda c: c.get("timestamp", ""))
        ]

    def _write_png(self, buf):
        path = os.path.join(self._tmp_dir, f"stats_chart_{uuid.uuid4().hex}.png")
        with open(path, "wb") as f:
            f.write(buf.getvalue())
        return path

    def _render_chart(self):
        kind = next((k for key, _, k in _CHART_OPTIONS if key == self.selected_chart), None)
        if kind in ("game_timeline", "game_distribution", "game_status") and not self.selected_game:
            self._chart_host.content = self._chart_placeholder(
                "Select a game above to view this chart.")
            return
        try:
            if kind == "all_timeline":
                buf = create_session_timeline_chart(extract_all_sessions(self.service.data))
            elif kind == "all_distribution":
                buf = create_session_distribution_chart(
                    extract_all_sessions(self.service.data), chart_type="histogram")
            elif kind == "game_timeline":
                buf = create_session_timeline_chart(
                    get_game_sessions(self.service.data, self.selected_game),
                    game_name=self.selected_game)
            elif kind == "game_distribution":
                buf = create_session_distribution_chart(
                    get_game_sessions(self.service.data, self.selected_game),
                    game_name=self.selected_game, chart_type="histogram")
            elif kind == "game_status":
                buf = create_status_timeline_chart(
                    get_status_history(self.service.data, self.selected_game),
                    game_name=self.selected_game)
            else:
                buf = None

            if buf is None:
                self._chart_host.content = self._chart_placeholder("No chart available.")
                return
            self._chart_host.content = ft.Image(
                src=self._write_png(buf), fit=ft.BoxFit.CONTAIN,
                gapless_playback=True, height=360,
                error_content=ft.Text("Image failed to load",
                                      color=ft.Colors.ON_SURFACE_VARIANT),
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._chart_host.content = self._chart_placeholder(
                f"Could not generate chart.\n{exc}")

    def refresh(self):
        self._render_overall()

        names = self._games_with_history()
        self.game_dd.options = [ft.dropdown.Option(key=ALL_GAMES, text="All games")] + [
            ft.dropdown.Option(key=n, text=n) for n in names
        ]
        if self.selected_game is not None and self.selected_game not in names:
            self.selected_game = None
            self.game_dd.value = ALL_GAMES

        self._render_contributions()
        self._render_rating_comparison()
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
