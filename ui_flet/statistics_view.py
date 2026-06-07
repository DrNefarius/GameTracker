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
    find_most_active_period,
)
from session_visualizations import (
    create_session_timeline_chart,
    create_session_distribution_chart,
    create_status_timeline_chart,
    create_session_heatmap,
)
from utilities import format_timedelta_with_seconds
from pause_utils import total_session_pause_timedelta
from core.ratings_logic import format_rating, get_session_rating_summary
from ui_flet import theme
from ui_flet.session_dialogs import (
    open_session_actions_dialog,
    open_manual_session_dialog,
    open_activity_log_dialog,
)

ALL_GAMES = "__all__"
_HEATMAP_WEEKS = 53

# Chart tabs: (kind, label). The scope (all games vs. the selected game) follows
# the game selection automatically rather than being baked into the chart choice.
_CHART_TABS = [
    ("timeline", "Timeline"),
    ("distribution", "Distribution"),
    ("status", "Status timeline"),
    ("heatmap", "Gaming heatmap"),
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


def _normalize_picked_date(value):
    """Recover the calendar date a DatePicker user selected.

    Flet's DatePicker returns a UTC-based datetime, so e.g. picking Apr 30 in a
    UTC+2 locale arrives as Apr 29 22:00. Rounding to the nearest day fixes the
    off-by-one for any timezone offset under 12h.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value + timedelta(hours=12)).date()
    return value


def _labeled(label, control):
    """A small text label stacked above a control.

    Used instead of a Dropdown's built-in floating label, which gets clipped at
    the top of a tightly-laid-out tab/row (see #3)."""
    return ft.Column(
        [ft.Text(label, size=12, color=ft.Colors.ON_SURFACE_VARIANT), control],
        spacing=3, tight=True,
    )


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
def _rating_stars_from_feedback(session):
    rating = (session.get("feedback") or {}).get("rating") or {}
    stars = rating.get("stars")
    if not stars:
        return ""
    try:
        stars = int(stars)
        return "★" * stars + "☆" * (5 - stars)
    except (ValueError, TypeError):
        return ""


def _session_notes(session):
    text = (session.get("feedback") or {}).get("text")
    return " ".join(str(text).split()) if text else ""


def open_date_activity_dialog(page, data, target_date, game_name=None):
    """Modal listing all sessions on ``target_date`` with Prev/Next-day nav.

    Mirrors the legacy daily-activity view: per-session game, time range,
    duration, paused time, total (duration+pause), rating and notes, plus a day
    summary. ``game_name`` (optional) restricts the view to a single game.
    """
    sessions = _sessions_for_day(data, target_date, game_name)

    if sessions:
        cards = []
        total_dur = timedelta()
        games = set()
        for s in sessions:
            dur_td = _duration_to_timedelta(s.get("duration"))
            pause_td = total_session_pause_timedelta(s)
            total_dur += dur_td
            games.add(s.get("game", game_name or ""))
            sub = (f"Paused {format_timedelta_with_seconds(pause_td)}  ·  "
                   f"Total {format_timedelta_with_seconds(dur_td + pause_td)}")
            lines = [
                ft.Row([
                    ft.Text(s.get("game", game_name or ""),
                            weight=ft.FontWeight.W_600, expand=True),
                    ft.Text(f"{_session_time_range(s)}  ·  {s.get('duration', '00:00:00')}",
                            size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                ]),
                ft.Row([
                    ft.Text(sub, size=12, color=ft.Colors.ON_SURFACE_VARIANT, expand=True),
                    ft.Text(_rating_stars_from_feedback(s), size=12),
                ]),
            ]
            notes = _session_notes(s)
            if notes:
                lines.append(ft.Text(notes, size=12, color=ft.Colors.ON_SURFACE_VARIANT))
            cards.append(ft.Container(
                padding=ft.Padding(10, 8, 10, 8), border_radius=8,
                bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.ON_SURFACE),
                content=ft.Column(lines, spacing=2, tight=True),
            ))
        summary = ft.Text(
            f"{len(sessions)} session{'s' if len(sessions) != 1 else ''}  ·  "
            f"{format_timedelta_with_seconds(total_dur)} played  ·  "
            f"{len(games)} game{'s' if len(games) != 1 else ''}",
            size=13, color=ft.Colors.ON_SURFACE_VARIANT,
        )
        body = ft.Column([summary, *cards], spacing=8, scroll=ft.ScrollMode.AUTO, tight=True)
    else:
        body = ft.Container(
            content=ft.Text("No gaming activity recorded for this day.",
                            color=ft.Colors.ON_SURFACE_VARIANT),
            alignment=ft.Alignment(0, 0), expand=True,
        )

    def _go(delta):
        page.pop_dialog()
        open_date_activity_dialog(page, data, target_date + timedelta(days=delta), game_name)

    scope = f" — {game_name}" if game_name else ""
    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(f"Daily activity · {target_date.strftime('%A, %B %d, %Y')}{scope}"),
        content=ft.Container(width=600, height=440, content=body),
        actions=[
            ft.TextButton("◀ Previous day", on_click=lambda e: _go(-1)),
            ft.TextButton("Next day ▶", on_click=lambda e: _go(1)),
            ft.Button("Close", on_click=lambda e: page.pop_dialog()),
        ],
        actions_alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )
    page.show_dialog(dialog)


class StatisticsView:
    """Owns ``self.control`` (scrollable Column) and ``refresh()``."""

    def __init__(self, page, service):
        self.page = page
        self.service = service
        self.selected_game = None            # None == All games
        self._active_tab = 0                 # index into _CHART_TABS
        self.heatmap_year = None             # None == rolling last 12 months
        self.dist_type = "line"              # line / scatter / box / histogram
        self.heatmap_window_months = 1       # gaming-heatmap chart window
        self.heatmap_end_date = None         # None == latest
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

        # ---- scope selector: filterable, virtualized list -----------------
        # A search box + ft.ListView (ListView lazily renders only visible rows,
        # so a 900+ game library stays smooth) - approximates the legacy
        # filterable Listbox without the dropdown's giant-menu problem.
        self.game_search = ft.TextField(
            hint_text="Filter games…", prefix_icon=ft.Icons.SEARCH,
            on_change=self._on_game_search, dense=True, expand=True,
        )
        self.game_list = ft.ListView(spacing=2, padding=ft.Padding(4, 4, 4, 4))
        self._game_panel = ft.Column(
            [
                ft.Text("Scope", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                self.game_search,
                ft.Container(
                    self.game_list, height=200, border_radius=8,
                    bgcolor=ft.Colors.with_opacity(0.03, ft.Colors.ON_SURFACE),
                ),
            ],
            spacing=6,
        )

        # ---- selected-game header (cover + name + metadata) ---------------
        # Prominently identifies the game the rest of the screen is scoped to.
        self.game_header = ft.Container(visible=False)

        # ---- contributions heatmap ----------------------------------------
        self.heatmap_caption = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.heatmap_host = ft.Container(content=ft.Text("…"))
        self.year_dd = ft.Dropdown(
            label="Period", value="rolling", width=200, dense=True,
            options=[ft.dropdown.Option(key="rolling", text="Last 12 months")],
            on_select=self._on_year_select,
        )
        heatmap_section = ft.Column(
            [
                # NOTE: do NOT put an expand=True child in a wrap=True Row — Flutter
                # forbids Expanded inside a Wrap and Flet renders it as a large grey
                # error box. Title goes on its own line; controls wrap on their own.
                ft.Text("Contributions", size=16, weight=ft.FontWeight.W_600),
                ft.Row(
                    [
                        self.year_dd,
                        ft.OutlinedButton("Today", on_click=lambda e: open_date_activity_dialog(
                            self.page, self.service.data, date.today(), self.selected_game)),
                        ft.OutlinedButton("Yesterday", on_click=lambda e: open_date_activity_dialog(
                            self.page, self.service.data, date.today() - timedelta(days=1),
                            self.selected_game)),
                        ft.OutlinedButton("Pick date…", icon=ft.Icons.EVENT,
                                          on_click=self._open_date_picker),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True,
                ),
                self.heatmap_caption,
                # The heatmap is a WIDE row (~53 week columns) but short; it needs
                # HORIZONTAL scroll. A vertical-scroll Column here expands to a huge
                # height inside the outer scrollable Column (the "giant grey box").
                ft.Row([self.heatmap_host], scroll=ft.ScrollMode.AUTO,
                       vertical_alignment=ft.CrossAxisAlignment.START),
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
        # Shown only when the selected game has no IGDB match yet (#10).
        self.fetch_meta_btn = ft.OutlinedButton(
            "Fetch metadata", icon=ft.Icons.CLOUD_DOWNLOAD,
            on_click=self._on_fetch_metadata, visible=False)
        self._game_detail = ft.Column(
            [
                self.game_totals,
                ft.Row(
                    [
                        ft.FilledButton("Add session", icon=ft.Icons.ADD,
                                        on_click=self._on_add_session,
                                        bgcolor=ft.Colors.GREEN, color=ft.Colors.WHITE),
                        ft.OutlinedButton("View activity log", icon=ft.Icons.HISTORY_EDU,
                                          on_click=self._on_view_activity_log),
                        self.fetch_meta_btn,
                    ],
                    spacing=10, wrap=True,
                ),
                # Sessions + Status history side by side to conserve vertical
                # space (stacks on narrow widths).
                ft.ResponsiveRow(
                    [
                        ft.Container(
                            col={"xs": 12, "md": 7},
                            content=ft.Column(
                                [
                                    ft.Text("Sessions", size=15, weight=ft.FontWeight.W_600),
                                    ft.Container(height=320, content=ft.Column(
                                        [self.sessions_table], scroll=ft.ScrollMode.AUTO)),
                                ],
                                spacing=8, tight=True,
                            ),
                        ),
                        ft.Container(
                            col={"xs": 12, "md": 5},
                            content=ft.Column(
                                [
                                    ft.Text("Status history", size=15,
                                            weight=ft.FontWeight.W_600),
                                    ft.Container(height=320, content=ft.Column(
                                        [self.status_table], scroll=ft.ScrollMode.AUTO)),
                                ],
                                spacing=8, tight=True,
                            ),
                        ),
                    ],
                    run_spacing=10, spacing=10,
                ),
            ],
            spacing=10, visible=False,
        )
        self._game_empty = ft.Container(
            content=ft.Text("Select a game above to see its rating, sessions and history.",
                            size=13, color=ft.Colors.ON_SURFACE_VARIANT),
            padding=ft.Padding(0, 8, 0, 8),
        )

        # ---- charts (tabbed; scope follows the game selection) ------------
        # Labels are rendered ABOVE the dropdowns via _labeled() rather than the
        # Dropdown's floating label, which gets clipped at the top of the tab (#3).
        self.dist_type_dd = ft.Dropdown(
            value="line", width=190,
            options=[ft.dropdown.Option(key=k, text=t) for k, t in
                     (("line", "Line Chart"), ("scatter", "Scatter Plot"),
                      ("box", "Box Plot"), ("histogram", "Histogram"))],
            on_select=self._on_dist_type_select,
        )
        self.hm_window_dd = ft.Dropdown(
            value="1", width=170,
            options=[ft.dropdown.Option(key=k, text=t) for k, t in
                     (("1", "1 Month"), ("3", "3 Months"), ("6", "6 Months"), ("12", "1 Year"))],
            on_select=self._on_hm_window,
        )
        self.hm_period = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self._heatmap_controls = ft.Row(
            [
                _labeled("Window", self.hm_window_dd),
                ft.IconButton(ft.Icons.CHEVRON_LEFT, tooltip="Earlier", on_click=self._hm_prev),
                ft.IconButton(ft.Icons.CHEVRON_RIGHT, tooltip="Later", on_click=self._hm_next),
                ft.OutlinedButton("Latest", on_click=self._hm_latest),
                ft.OutlinedButton("Most active", on_click=self._hm_most_active),
                self.hm_period,
            ],
            wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        # One lazily-rendered host per tab (only the active tab's chart renders).
        self._chart_hosts = {
            kind: ft.Container(content=self._chart_placeholder("Loading chart…"),
                               alignment=ft.Alignment(0, 0), padding=ft.Padding(0, 8, 0, 8))
            for kind, _ in _CHART_TABS
        }
        # Charts don't scroll: each tab shows a single fixed-size graph, so the
        # per-tab columns render at their natural height (no inner scrollbar).
        tab_views = ft.TabBarView(
            controls=[
                ft.Column([self._chart_hosts["timeline"]]),
                ft.Column([_labeled("Distribution type", self.dist_type_dd),
                           self._chart_hosts["distribution"]]),
                ft.Column([self._chart_hosts["status"]]),
                ft.Column([self._heatmap_controls, self._chart_hosts["heatmap"]]),
            ],
            expand=True,
        )
        self.chart_tabs = ft.Tabs(
            length=len(_CHART_TABS), selected_index=0, on_change=self._on_chart_tab,
            content=ft.Column(
                [
                    ft.TabBar(tabs=[ft.Tab(label=lbl) for _, lbl in _CHART_TABS],
                              scrollable=True),
                    # Top padding so the first control's floating label (e.g. the
                    # Distribution dropdown) isn't clipped by the tab bar. Taller
                    # than before to fit dropdown + graph without an inner scroll.
                    ft.Container(tab_views, height=500, padding=ft.Padding(0, 22, 0, 0)),
                ],
                spacing=8,
            ),
        )

        # ---- assemble ------------------------------------------------------
        self.control = ft.Column(
            [
                ft.Text("Statistics", size=20, weight=ft.FontWeight.BOLD),
                header,
                ft.Divider(height=1),
                self._game_panel,
                self.game_header,
                heatmap_section,
                ft.Divider(height=1),
                self.rating_section,
                self._game_empty,
                self._game_detail,
                ft.Divider(height=1),
                ft.Text("Charts", size=16, weight=ft.FontWeight.W_600),
                self.chart_tabs,
            ],
            expand=True, scroll=ft.ScrollMode.AUTO, spacing=12,
        )

        # Stats + charts render lazily on the first navigation to this tab (see
        # app.on_nav_change, wrapped in a loading overlay) rather than in the
        # constructor — generating a chart on startup slows the first paint.

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
    # ---- scope (filterable list) ----
    def _on_game_search(self, _):
        self._refresh_game_list()
        if self._is_mounted():
            self.page.update()

    def _game_item(self, label, value):
        selected = (value == self.selected_game)
        return ft.Container(
            content=ft.Text(label, size=13,
                            weight=ft.FontWeight.W_600 if selected else None,
                            color=ft.Colors.PRIMARY if selected else None),
            on_click=lambda e, v=value: self._select_scope(v),
            padding=ft.Padding(10, 6, 10, 6), border_radius=6,
            bgcolor=ft.Colors.with_opacity(0.14, ft.Colors.PRIMARY) if selected else None,
        )

    def _refresh_game_list(self):
        q = (self.game_search.value or "").strip().lower()
        names = [n for n in self._games_with_history() if q in n.lower()]
        items = [self._game_item("All games", None)]
        items += [self._game_item(n, n) for n in names]
        self.game_list.controls = items

    def _select_scope(self, value):
        self.selected_game = value  # None == All games
        self._refresh_game_list()
        self._render_game_header()
        self._render_contributions()
        self._render_rating_comparison()
        self._render_game_detail()
        self._render_active_chart()
        if self._is_mounted():
            self.page.update()

    # ---- chart tabs ----
    def _on_chart_tab(self, e):
        self._active_tab = e.control.selected_index or 0
        self._render_active_chart()
        if self._is_mounted():
            self.page.update()

    def _on_year_select(self, _):
        value = self.year_dd.value
        self.heatmap_year = None if value in (None, "rolling") else int(value)
        self._render_contributions()
        if self._is_mounted():
            self.page.update()

    def _on_dist_type_select(self, _):
        self.dist_type = self.dist_type_dd.value or "line"
        self._render_chart_kind("distribution")
        if self._is_mounted():
            self.page.update()

    # ---- gaming-heatmap window navigation ----
    def _hm_sessions(self):
        if self.selected_game:
            return get_game_sessions(self.service.data, self.selected_game) or []
        return extract_all_sessions(self.service.data)

    def _on_hm_window(self, _):
        self.heatmap_window_months = int(self.hm_window_dd.value or "1")
        self._render_chart_kind("heatmap")
        if self._is_mounted():
            self.page.update()

    def _hm_shift(self, months):
        base = self.heatmap_end_date or date.today()
        self.heatmap_end_date = min(base + timedelta(days=months * 30), date.today())
        self._render_chart_kind("heatmap")
        if self._is_mounted():
            self.page.update()

    def _hm_prev(self, _):
        self._hm_shift(-self.heatmap_window_months)

    def _hm_next(self, _):
        self._hm_shift(self.heatmap_window_months)

    def _hm_latest(self, _):
        self.heatmap_end_date = None
        self._render_chart_kind("heatmap")
        if self._is_mounted():
            self.page.update()

    def _hm_most_active(self, _):
        self.heatmap_end_date = find_most_active_period(
            self._hm_sessions(), self.heatmap_window_months)
        self._render_chart_kind("heatmap")
        if self._is_mounted():
            self.page.update()

    # ---- per-game actions ----
    def _on_add_session(self, _):
        if self.selected_game:
            open_manual_session_dialog(self.page, self.service, self.selected_game,
                                       on_saved=self.refresh)

    def _on_view_activity_log(self, _):
        if self.selected_game:
            open_activity_log_dialog(self.page, self.service, self.selected_game)

    def _on_fetch_metadata(self, _):
        """Fetch IGDB metadata for the selected game (when it has none yet)."""
        name = self.selected_game
        if not name:
            return
        orig_idx = next((idx for idx, row in self.service.data if row[0] == name), None)
        if orig_idx is None:
            return
        from ui_flet.igdb_match import open_match_picker
        open_match_picker(self.page, self.service, orig_idx, on_done=self.refresh)

    def select_game(self, name):
        """Programmatically focus a game (used by Game Hub's 'View Statistics')."""
        self.selected_game = name
        self.refresh()

    def _open_date_picker(self, _):
        """Pick any date and open its daily-activity dialog (current scope)."""
        if self.page is None:
            return

        def on_pick(e):
            value = e.control.value
            if not value:
                return
            day = _normalize_picked_date(value)
            open_date_activity_dialog(self.page, self.service.data, day,
                                      self.selected_game)

        self.page.show_dialog(
            ft.DatePicker(
                first_date=datetime(2000, 1, 1),
                last_date=datetime.now(),
                value=datetime.now(),
                on_change=on_pick,
            )
        )

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
        years = set()
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
            years.add(d.year)

        # Year picker: "Last 12 months" + each year with data (preserve selection).
        self.year_dd.options = [ft.dropdown.Option(key="rolling", text="Last 12 months")] + [
            ft.dropdown.Option(key=str(y), text=str(y)) for y in sorted(years, reverse=True)
        ]
        if self.heatmap_year is not None and self.heatmap_year not in years:
            self.heatmap_year = None
            self.year_dd.value = "rolling"

        if self.heatmap_year is None:
            window_end = date.today()
            window_start = window_end - timedelta(days=_HEATMAP_WEEKS * 7 - 1)
            period = "last 12 months"
        else:
            window_start = date(self.heatmap_year, 1, 1)
            window_end = date(self.heatmap_year, 12, 31)
            period = str(self.heatmap_year)

        grid_start = window_start - timedelta(days=window_start.weekday())  # align Monday
        week_cols = []
        cur = grid_start
        while cur <= window_end:
            cells = []
            for wd in range(7):
                day = cur + timedelta(days=wd)
                if day < window_start or day > window_end:
                    cells.append(ft.Container(width=13, height=13))  # padding
                else:
                    cnt, secs = by_day.get(day, [0, 0.0])
                    cells.append(self._day_cell(day, cnt, secs))
            week_cols.append(ft.Column(cells, spacing=3, tight=True))
            cur += timedelta(days=7)

        self.heatmap_host.content = ft.Row(week_cols, spacing=3, tight=True)
        scope = self.selected_game or "All games"
        active_days = sum(1 for d, v in by_day.items()
                          if v[0] > 0 and window_start <= d <= window_end)
        self.heatmap_caption.value = (
            f"{scope} · {period} · {active_days} active day"
            f"{'s' if active_days != 1 else ''} (click a day for details)"
        )

    def _row_for_selected(self):
        for _, row in self.service.data:
            if row[0] == self.selected_game:
                return row
        return None

    @staticmethod
    def _meta_chip(icon, text):
        return ft.Container(
            padding=ft.Padding(8, 4, 10, 4), border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
            content=ft.Row(
                [ft.Icon(icon, size=14, color=ft.Colors.ON_SURFACE_VARIANT),
                 ft.Text(text, size=12)],
                spacing=5, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _render_game_header(self):
        """Prominent header for the selected game: cover + name + key metadata."""
        name = self.selected_game
        row = self._row_for_selected() if name else None
        if not name or row is None:
            self.game_header.visible = False
            self.game_header.content = None
            return
        self.game_header.visible = True
        igdb = row[10] if len(row) > 10 and isinstance(row[10], dict) else None

        # cover (IGDB cached image) or a placeholder tile
        cover = None
        if igdb:
            try:
                from ui_flet.game_hub import cover_path_for
                cp = cover_path_for(igdb)
                if cp:
                    cover = ft.Image(
                        src=cp, width=92, height=128, fit=ft.BoxFit.COVER, border_radius=8,
                        error_content=ft.Icon(ft.Icons.IMAGE_NOT_SUPPORTED_OUTLINED, size=32))
            except Exception:
                cover = None
        if cover is None:
            cover = ft.Container(
                width=92, height=128, border_radius=8, alignment=ft.Alignment(0, 0),
                bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
                content=ft.Icon(ft.Icons.VIDEOGAME_ASSET, size=34,
                                color=ft.Colors.ON_SURFACE_VARIANT))

        # metadata chips
        chips = []
        if row[2]:
            chips.append(self._meta_chip(ft.Icons.DEVICES, str(row[2])))
        chips.append(ft.Container(content=theme.status_badge(row)))
        if row[3]:
            chips.append(self._meta_chip(ft.Icons.SCHEDULE, str(row[3])))
        released = row[1] if len(row) > 1 else None
        if released and released != "-":
            chips.append(self._meta_chip(ft.Icons.EVENT, str(released)))

        # rating line: manual stars + IGDB aggregated rating when present
        manual = row[9] if len(row) > 9 and isinstance(row[9], dict) else None
        rating_bits = []
        if manual:
            rating_bits.append(ft.Text(format_rating(manual) or "", size=16))
        agg = (igdb or {}).get("aggregated_rating")
        if agg:
            rating_bits.append(ft.Text(f"IGDB {round(float(agg))}/100", size=12,
                                       color=ft.Colors.ON_SURFACE_VARIANT))

        right = [ft.Text(name, size=22, weight=ft.FontWeight.BOLD,
                         selectable=True, max_lines=2)]
        right.append(ft.Row(chips, wrap=True, spacing=8, run_spacing=6))
        if rating_bits:
            right.append(ft.Row(rating_bits, spacing=12,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER))
        if igdb:
            genres = ", ".join(igdb.get("genres") or [])
            if genres:
                right.append(ft.Text(f"Genres: {genres}", size=12,
                                     color=ft.Colors.ON_SURFACE_VARIANT))
            summary = igdb.get("summary")
            if summary:
                flat = " ".join(str(summary).split())
                right.append(ft.Text(
                    flat[:240] + ("…" if len(flat) > 240 else ""),
                    size=12, color=ft.Colors.ON_SURFACE_VARIANT, max_lines=3))

        self.game_header.content = ft.Container(
            padding=ft.Padding(14, 12, 14, 12), border_radius=12,
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE),
            content=ft.Row(
                [cover, ft.Column(right, spacing=8, expand=True, tight=True)],
                spacing=16, vertical_alignment=ft.CrossAxisAlignment.START,
            ),
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

        # Offer "Fetch metadata" only when the game has no real IGDB match (#10).
        row = self._row_for_selected()
        igdb = row[10] if row and len(row) > 10 and isinstance(row[10], dict) else None
        self.fetch_meta_btn.visible = not bool(igdb and igdb.get("igdb_id"))

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

        session_rows = []
        for s in sorted(sessions, key=_sort_key, reverse=True):
            def _open(e, sess=s):
                open_session_actions_dialog(self.page, self.service, self.selected_game,
                                            sess, on_done=self.refresh)
            session_rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(_format_session_start(s)), on_tap=_open),
                ft.DataCell(ft.Text(str(s.get("duration", "00:00:00"))), on_tap=_open),
                ft.DataCell(ft.Text(_session_details_summary(s)), on_tap=_open),
            ]))
        self.sessions_table.rows = session_rows
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

    def _scope_sessions(self):
        """Sessions for the current scope: one game, or every session library-wide."""
        if self.selected_game:
            return get_game_sessions(self.service.data, self.selected_game) or []
        return extract_all_sessions(self.service.data)

    def _render_active_chart(self):
        idx = self._active_tab if 0 <= self._active_tab < len(_CHART_TABS) else 0
        self._render_chart_kind(_CHART_TABS[idx][0])

    def _render_chart_kind(self, kind):
        """Render one tab's chart into its host. Scope follows the game selection;
        'all games' uses every session, a selected game uses just that game's."""
        host = self._chart_hosts.get(kind)
        if host is None:
            return
        # Status timeline is per-game only.
        if kind == "status" and not self.selected_game:
            host.content = self._chart_placeholder(
                "Select a game to view its status timeline.")
            return
        try:
            game = self.selected_game
            scope = self._scope_sessions()
            if kind == "timeline":
                buf = create_session_timeline_chart(scope, game_name=game)
            elif kind == "distribution":
                buf = create_session_distribution_chart(scope, game_name=game,
                                                        chart_type=self.dist_type)
            elif kind == "status":
                buf = create_status_timeline_chart(
                    get_status_history(self.service.data, game), game_name=game)
            elif kind == "heatmap":
                # create_session_heatmap now lives in the GUI-free
                # session_visualizations module (imported at top), so the Flet UI
                # no longer touches the PySimpleGUI-importing session_management.
                buf = create_session_heatmap(scope, game, self.heatmap_window_months,
                                             self.heatmap_end_date)
                end = self.heatmap_end_date or date.today()
                start = end - timedelta(days=self.heatmap_window_months * 30)
                self.hm_period.value = f"{start.isoformat()} → {end.isoformat()}"
            else:
                buf = None

            if buf is None:
                host.content = self._chart_placeholder("No chart available.")
                return
            host.content = ft.Image(
                src=self._write_png(buf), fit=ft.BoxFit.CONTAIN,
                gapless_playback=True, height=380,
                error_content=ft.Text("Image failed to load",
                                      color=ft.Colors.ON_SURFACE_VARIANT),
            )
        except Exception as exc:  # pragma: no cover - defensive
            host.content = self._chart_placeholder(f"Could not generate chart.\n{exc}")

    def refresh(self):
        self._render_overall()

        names = self._games_with_history()
        if self.selected_game is not None and self.selected_game not in names:
            self.selected_game = None
        self._refresh_game_list()

        self._render_game_header()
        self._render_contributions()
        self._render_rating_comparison()
        self._render_game_detail()
        self._render_active_chart()

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
