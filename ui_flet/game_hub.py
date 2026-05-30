"""Game Hub screen (Flet 0.85.2 port of the legacy PySimpleGUI ``game_hub.py``).

A single full-screen modal ``ft.AlertDialog`` that brings together everything
about one game:

  * Header   - name, platform, status badge, total time, last-played, rating
               stars (reusing the ``★``/``☆`` glyphs from ``constants``).
  * IGDB     - cover (only when a *local* cached PNG already exists; never
               fetched from the network here), genres, summary and
               time-to-beat, plus a "Remove metadata" button. Fetch / rematch
               are intentionally NOT ported (they need the unported IGDB match
               picker).
  * Timer    - an inline Play / Pause / Stop stopwatch. Elapsed time is tracked
               from timestamps (a monotonic-style ``start_time`` baseline plus
               accumulated ``elapsed``) and a background ``threading.Thread``
               repaints the elapsed label ~once a second. On Stop the elapsed
               session is persisted through ``session_data.add_manual_session_to_game``
               (the same helper the manual-session dialog uses), which bumps the
               game's total time + last-played.
  * Tables   - a Sessions table (Start / Duration / Feedback) and a Status
               history table (Date / From / To).
  * Actions  - Edit, Add session, Rate and Delete, each delegating to the
               existing focused dialogs in ``game_dialog`` / ``session_dialogs``.

Backend reuse (all PySimpleGUI-free):
  * ``core.services.GameLibraryService`` for read/update/save.
  * ``ui_flet.theme.status_badge`` for the status chip.
  * ``ui_flet.game_dialog.open_game_dialog`` / ``confirm_delete`` for Edit/Delete.
  * ``ui_flet.session_dialogs.open_manual_session_dialog`` / ``open_feedback_dialog``
    for Add-session / Rate.
  * ``session_data.add_manual_session_to_game`` for the timer-stop persistence.
  * ``utilities.format_timedelta_with_seconds`` for HH:MM:SS rendering.
  * ``igdb_integration.cover_cache_path`` (pure local-path resolver, no network)
    to decide whether a cover image can be shown.

Nested dialogs
--------------
Flet stacks dialogs via ``page.show_dialog`` / ``page.pop_dialog``. Opening a
child dialog (Edit / Add session / Rate / Delete) while the hub dialog is open
*replaces* the visible dialog with the child (the child sits on top of the
stack). To keep behaviour sane we close the hub before opening the child and,
once the child has saved, re-open a freshly-rebuilt hub (so the header/tables
reflect the mutation) and call ``on_changed`` so the underlying games list also
refreshes. Delete is terminal: it closes everything and only calls
``on_changed``.

Public entry point: ``open_game_hub(page, service, orig_idx, on_changed=None)``.
"""

import asyncio
import threading
import time
from datetime import datetime, timedelta

import flet as ft

from constants import STAR_FILLED, STAR_EMPTY
from utilities import format_timedelta_with_seconds
from session_data import add_manual_session_to_game
from ui_flet import theme
from ui_flet.game_dialog import open_game_dialog, confirm_delete
from ui_flet.session_dialogs import (
    open_manual_session_dialog,
    open_feedback_dialog,
    open_session_actions_dialog,
)

# ``cover_cache_path`` is a pure local-path resolver (no network); we only show
# a cover when its PNG already exists on disk. Imported defensively so the hub
# still works if the IGDB module is unavailable for any reason.
try:
    from igdb_integration import cover_cache_path
except Exception:  # pragma: no cover - defensive
    cover_cache_path = None


# --------------------------------------------------------------------------- #
# Pure helpers (no Page required - unit-testable)
# --------------------------------------------------------------------------- #
def parse_hhmmss_to_timedelta(value):
    """Parse the app's HH:MM:SS (or HH:MM) time field into a timedelta."""
    if isinstance(value, timedelta):
        return value
    if not value or not isinstance(value, str):
        return timedelta(0)
    parts = value.split(":")
    try:
        if len(parts) == 3:
            h, m, s = map(int, parts)
            return timedelta(hours=h, minutes=m, seconds=s)
        if len(parts) == 2:
            h, m = map(int, parts)
            return timedelta(hours=h, minutes=m)
    except ValueError:
        pass
    return timedelta(0)


def format_rating_stars(rating):
    """Return a star string for a game rating dict (or '' when unrated).

    Mirrors the games-list rendering: '★' x stars + '☆' x (5-stars), prefixed
    with '≈' when the rating was auto-calculated.
    """
    if not isinstance(rating, dict):
        return ""
    try:
        stars = int(rating.get("stars", 0) or 0)
    except (TypeError, ValueError):
        stars = 0
    stars = max(0, min(5, stars))
    if stars <= 0 and not rating.get("auto_calculated"):
        return ""
    prefix = "≈" if rating.get("auto_calculated") else ""
    return prefix + STAR_FILLED * stars + STAR_EMPTY * (5 - stars)


def build_timer_session(session_start_iso, elapsed, end_iso=None):
    """Build the session dict a stopped timer should persist.

    ``session_start_iso`` is the ISO timestamp captured on the first Play;
    ``elapsed`` is a timedelta of accumulated play time; ``end_iso`` defaults to
    now. Returns the same shape the manual-session dialog produces (so
    ``add_manual_session_to_game`` accepts it unchanged).
    """
    if end_iso is None:
        end_iso = datetime.now().isoformat()
    return {
        "start": session_start_iso,
        "end": end_iso,
        "duration": format_timedelta_with_seconds(elapsed),
        "pauses": [],
    }


def cover_path_for(igdb):
    """Return an existing local cover PNG path for an IGDB dict, else None.

    Never triggers a network download - we only surface a cover that is
    already cached on disk (matching the task's "display only" constraint).
    """
    if not isinstance(igdb, dict) or cover_cache_path is None:
        return None
    igdb_id = igdb.get("igdb_id")
    if not igdb_id:
        return None
    try:
        import os
        path = cover_cache_path(int(igdb_id))
        return path if os.path.exists(path) else None
    except Exception:  # pragma: no cover - defensive
        return None


def _format_seconds_short(seconds):
    """IGDB time-to-beat values are in seconds; render as e.g. '12h 30m'."""
    if not seconds:
        return "--"
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "--"
    if seconds <= 0:
        return "--"
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def _format_session_start(session):
    start = session.get("start")
    if not start:
        return "—"
    try:
        return datetime.fromisoformat(start).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(start)


def _session_feedback_summary(session):
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
        parts.append(flat[:60] + "…" if len(flat) > 60 else flat)
    return "  ".join(parts) if parts else "—"


def _format_status_timestamp(change):
    ts = change.get("timestamp")
    if not ts:
        return "—"
    try:
        return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(ts)


# --------------------------------------------------------------------------- #
# Hub controller
# --------------------------------------------------------------------------- #
class GameHub:
    """Owns one Game Hub dialog instance (its content + timer + refresh)."""

    def __init__(self, page, service, orig_idx, on_changed=None, on_view_statistics=None):
        self.page = page
        self.service = service
        self.orig_idx = orig_idx
        self.on_changed = on_changed
        self.on_view_statistics = on_view_statistics

        # Current row snapshot (re-read from the service on every refresh()).
        self.row = service.get_game(orig_idx) or []
        self.game_name = self.row[0] if self.row else ""

        # ---- timer state --------------------------------------------------
        self._timer_lock = threading.Lock()
        self._timer_running = False
        self._timer_elapsed = timedelta(0)     # accumulated play time
        self._timer_start_monotonic = 0.0      # time.time() baseline when running
        self._session_start_iso = None         # ISO of the first Play
        self._ticker_active = False            # an async tick loop is running

        # ---- controls referenced by refresh()/timer ----------------------
        self.header_holder = ft.Container()
        self.igdb_holder = ft.Container()
        self.elapsed_text = ft.Text("00:00:00", size=34, weight=ft.FontWeight.BOLD,
                                    font_family="monospace")
        self.total_time_text = ft.Text("", size=13, color=ft.Colors.ON_SURFACE_VARIANT)
        self.play_btn = ft.Button("Play", icon=ft.Icons.PLAY_ARROW,
                                          on_click=self._on_play)
        self.pause_btn = ft.Button("Pause", icon=ft.Icons.PAUSE,
                                           on_click=self._on_pause, disabled=True)
        self.stop_btn = ft.Button("Stop", icon=ft.Icons.STOP,
                                          on_click=self._on_stop, disabled=True)

        self.sessions_table = ft.DataTable(
            columns=[
                ft.DataColumn(label=ft.Text("Start", weight=ft.FontWeight.BOLD)),
                ft.DataColumn(label=ft.Text("Duration", weight=ft.FontWeight.BOLD)),
                ft.DataColumn(label=ft.Text("Feedback", weight=ft.FontWeight.BOLD)),
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

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(self.game_name or "Game Hub", size=20, weight=ft.FontWeight.BOLD),
            content=ft.Container(width=900, height=640, content=self._build_body()),
            actions=[ft.TextButton("Close", on_click=lambda _: self._close())],
            actions_alignment=ft.MainAxisAlignment.END,
            on_dismiss=lambda _: self._stop_timer_thread(),
        )

        # Populate dynamic sections.
        self.refresh()

    # ------------------------------------------------------------------ #
    # body construction
    # ------------------------------------------------------------------ #
    def _build_body(self):
        timer_card = ft.Container(
            padding=ft.Padding(16, 14, 16, 14),
            border_radius=12,
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE),
            content=ft.Column(
                [
                    ft.Text("Session timer", size=15, weight=ft.FontWeight.W_600),
                    ft.Row(
                        [self.elapsed_text, ft.Container(width=16), self.total_time_text],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row([self.play_btn, self.pause_btn, self.stop_btn], spacing=10),
                ],
                spacing=10,
                tight=True,
            ),
        )

        actions_row = ft.Row(
            [
                ft.OutlinedButton("Edit", icon=ft.Icons.EDIT, on_click=self._on_edit),
                ft.OutlinedButton("Add session", icon=ft.Icons.ADD,
                                  on_click=self._on_add_session),
                ft.OutlinedButton("Rate", icon=ft.Icons.STAR, on_click=self._on_rate),
                ft.OutlinedButton("View Statistics", icon=ft.Icons.BAR_CHART,
                                  on_click=self._on_view_statistics),
                ft.OutlinedButton("Delete", icon=ft.Icons.DELETE_OUTLINE,
                                  on_click=self._on_delete),
            ],
            spacing=10,
            wrap=True,
        )

        return ft.Column(
            [
                self.header_holder,
                self.igdb_holder,
                timer_card,
                actions_row,
                ft.Divider(height=1),
                ft.Text("Sessions", size=15, weight=ft.FontWeight.W_600),
                ft.Container(height=240,
                             content=ft.Column([self.sessions_table], scroll=ft.ScrollMode.AUTO)),
                ft.Divider(height=1),
                ft.Text("Status history", size=15, weight=ft.FontWeight.W_600),
                ft.Container(height=160,
                             content=ft.Column([self.status_table], scroll=ft.ScrollMode.AUTO)),
            ],
            spacing=14,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def _build_header(self):
        row = self.row
        platform = row[2] if len(row) > 2 else ""
        total = row[3] if len(row) > 3 else None
        last_played = row[6] if len(row) > 6 else None
        rating = row[9] if len(row) > 9 else None

        star_text = format_rating_stars(rating)
        rating_control = (
            ft.Text(star_text, size=16, color=ft.Colors.AMBER)
            if star_text else ft.Text("Not rated", size=13, italic=True,
                                      color=ft.Colors.ON_SURFACE_VARIANT)
        )

        def _meta(label, value):
            return ft.Column(
                [
                    ft.Text(label, size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                    value if isinstance(value, ft.Control)
                    else ft.Text(value, size=14, weight=ft.FontWeight.W_500),
                ],
                spacing=2,
                tight=True,
            )

        return ft.Container(
            padding=ft.Padding(0, 0, 0, 4),
            content=ft.Row(
                [
                    _meta("Platform", platform or "—"),
                    _meta("Status", theme.status_badge(row)),
                    _meta("Total time", total or "—"),
                    _meta("Last played",
                          (last_played.split(" ")[0] if last_played else "—")),
                    _meta("Rating", rating_control),
                ],
                spacing=28,
                wrap=True,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        )

    def _build_igdb_panel(self):
        igdb = self.row[10] if len(self.row) > 10 else None
        if not isinstance(igdb, dict):
            return ft.Container(visible=False)

        cover_path = cover_path_for(igdb)
        if cover_path:
            cover = ft.Image(src=cover_path, fit=ft.BoxFit.CONTAIN,
                             width=160, height=224, border_radius=8,
                             error_content=ft.Icon(ft.Icons.IMAGE_NOT_SUPPORTED_OUTLINED,
                                                    size=48))
        else:
            cover = ft.Container(
                width=160, height=224, border_radius=8,
                bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
                alignment=ft.Alignment(0, 0),
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.IMAGE_OUTLINED, size=40,
                                color=ft.Colors.ON_SURFACE_VARIANT),
                        ft.Text("No cover", size=12,
                                color=ft.Colors.ON_SURFACE_VARIANT),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=6,
                ),
            )

        genres = ", ".join(igdb.get("genres") or []) or "(none)"
        summary = igdb.get("summary") or "(no summary)"
        ttb = igdb.get("time_to_beat") or {}
        ttb_text = (
            f"Main: {_format_seconds_short(ttb.get('hastily'))}    "
            f"Main+Extras: {_format_seconds_short(ttb.get('normally'))}    "
            f"Completionist: {_format_seconds_short(ttb.get('completely'))}"
        )

        info = ft.Column(
            [
                ft.Text("IGDB metadata", size=15, weight=ft.FontWeight.W_600),
                ft.Text(f"Genres: {genres}", size=13),
                ft.Text(ttb_text, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text("Summary:", size=12, weight=ft.FontWeight.BOLD),
                ft.Container(
                    height=120,
                    content=ft.Column(
                        [ft.Text(summary, size=12, selectable=True)],
                        scroll=ft.ScrollMode.AUTO,
                    ),
                ),
                ft.OutlinedButton("Remove metadata", icon=ft.Icons.DELETE_SWEEP,
                                  on_click=self._on_remove_metadata),
            ],
            spacing=8,
            expand=True,
        )

        return ft.Container(
            padding=ft.Padding(12, 12, 12, 12),
            border_radius=12,
            bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.ON_SURFACE),
            content=ft.Row([cover, info], spacing=16,
                           vertical_alignment=ft.CrossAxisAlignment.START),
        )

    # ------------------------------------------------------------------ #
    # refresh
    # ------------------------------------------------------------------ #
    def refresh(self):
        """Re-read the row from the service and re-render header / panels / tables."""
        self.row = self.service.get_game(self.orig_idx) or []

        self.header_holder.content = self._build_header()
        self.igdb_holder.content = self._build_igdb_panel()
        self.igdb_holder.visible = isinstance(
            self.row[10] if len(self.row) > 10 else None, dict
        )

        total = self.row[3] if len(self.row) > 3 else None
        initial_td = parse_hhmmss_to_timedelta(total)
        self.total_time_text.value = f"Total: {format_timedelta_with_seconds(initial_td)}"

        # sessions table (newest first)
        sessions = self.row[7] if len(self.row) > 7 and self.row[7] else []

        def _skey(s):
            try:
                return datetime.fromisoformat(s.get("start", ""))
            except (ValueError, TypeError):
                return datetime.min

        session_rows = []
        for s in sorted(sessions, key=_skey, reverse=True):
            def _open(e, sess=s):
                self._on_session_tap(sess)
            session_rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(_format_session_start(s)), on_tap=_open),
                ft.DataCell(ft.Text(str(s.get("duration", "00:00:00"))), on_tap=_open),
                ft.DataCell(ft.Text(_session_feedback_summary(s)), on_tap=_open),
            ]))
        self.sessions_table.rows = session_rows

        # status-history table (chronological)
        history = self.row[8] if len(self.row) > 8 and self.row[8] else []
        self.status_table.rows = [
            ft.DataRow(cells=[
                ft.DataCell(ft.Text(_format_status_timestamp(c))),
                ft.DataCell(ft.Text(str(c.get("from") or "—"))),
                ft.DataCell(ft.Text(str(c.get("to") or "—"))),
            ])
            for c in sorted(history, key=lambda c: c.get("timestamp", ""))
        ]

        self._update()

    def _notify_changed(self):
        if self.on_changed:
            try:
                self.on_changed()
            except Exception:  # pragma: no cover - caller-supplied
                pass

    # ------------------------------------------------------------------ #
    # timer
    # ------------------------------------------------------------------ #
    def _current_elapsed(self):
        with self._timer_lock:
            if self._timer_running:
                return timedelta(seconds=time.time() - self._timer_start_monotonic)
            return self._timer_elapsed

    def _render_elapsed(self):
        self.elapsed_text.value = format_timedelta_with_seconds(self._current_elapsed())

    async def _ticker(self):
        """Repaint the elapsed label ~once a second on Flet's event loop.

        This MUST run via ``page.run_task`` (not a plain thread): in Flet 0.85
        ``page.update()`` only propagates from the event loop, which is why the
        previous background-thread timer updated the display only on Pause/Stop.
        """
        try:
            while self._ticker_active and self._timer_running:
                self._render_elapsed()
                try:
                    self.page.update()
                except Exception:  # pragma: no cover - defensive
                    pass
                await asyncio.sleep(1)
        finally:
            self._ticker_active = False

    def _start_timer_thread(self):
        if self._ticker_active or self.page is None:
            return
        self._ticker_active = True
        self.page.run_task(self._ticker)

    def _stop_timer_thread(self):
        self._ticker_active = False

    def _on_play(self, _):
        with self._timer_lock:
            if self._timer_running:
                return
            if self._session_start_iso is None:
                self._session_start_iso = datetime.now().isoformat()
            # Baseline so elapsed continues from accumulated value.
            self._timer_start_monotonic = time.time() - self._timer_elapsed.total_seconds()
            self._timer_running = True
        self.play_btn.disabled = True
        self.pause_btn.disabled = False
        self.stop_btn.disabled = False
        self._start_timer_thread()
        self._render_elapsed()
        self._update()

    def _on_pause(self, _):
        with self._timer_lock:
            if not self._timer_running:
                return
            self._timer_elapsed = timedelta(
                seconds=time.time() - self._timer_start_monotonic
            )
            self._timer_running = False
        self._stop_timer_thread()
        self.play_btn.disabled = False
        self.pause_btn.disabled = True
        self.stop_btn.disabled = False
        self._render_elapsed()
        self._update()

    def _on_stop(self, _):
        # Freeze elapsed and stop the repaint thread.
        with self._timer_lock:
            if self._timer_running:
                self._timer_elapsed = timedelta(
                    seconds=time.time() - self._timer_start_monotonic
                )
                self._timer_running = False
            elapsed = self._timer_elapsed
            session_start = self._session_start_iso
        self._stop_timer_thread()

        persisted = False
        if session_start is not None and elapsed.total_seconds() > 0:
            session = build_timer_session(session_start, elapsed)
            add_manual_session_to_game(self.game_name, session, self.service.data)
            self.service.save()
            persisted = True

        # Reset timer for a possible next session.
        with self._timer_lock:
            self._timer_elapsed = timedelta(0)
            self._session_start_iso = None
            self._timer_running = False
        self.elapsed_text.value = "00:00:00"
        self.play_btn.disabled = False
        self.pause_btn.disabled = True
        self.stop_btn.disabled = True

        if persisted:
            self.refresh()          # re-render header totals + sessions table
            self._notify_changed()
        else:
            self._update()

    # ------------------------------------------------------------------ #
    # action buttons (delegate to existing dialogs)
    # ------------------------------------------------------------------ #
    def _reopen_after_child(self):
        """After a child dialog saved: notify caller + re-open a fresh hub.

        The child dialog already called ``page.pop_dialog()`` on save, so the
        hub dialog is no longer on screen. We rebuild from scratch (cheapest,
        guarantees the new row shape is reflected) and re-show it.
        """
        self._notify_changed()
        GameHub(self.page, self.service, self.orig_idx, self.on_changed,
                self.on_view_statistics).open()

    def _on_edit(self, _):
        # Closing the hub first keeps a single dialog visible; the edit dialog
        # re-opens the hub via on_saved.
        self.page.pop_dialog()
        self._stop_timer_thread()
        open_game_dialog(self.page, self.service, self.orig_idx,
                         on_saved=self._reopen_after_child)

    def _on_add_session(self, _):
        self.page.pop_dialog()
        self._stop_timer_thread()
        open_manual_session_dialog(self.page, self.service, self.game_name,
                                   on_saved=self._reopen_after_child)

    def _on_rate(self, _):
        existing = self.row[9] if len(self.row) > 9 else None
        # open_feedback_dialog's existing= expects a feedback-shaped dict
        # ({'text':..., 'rating': {...}}); a game rating is the rating dict
        # itself, so wrap it.
        existing_feedback = {"rating": existing} if isinstance(existing, dict) else None

        def _result(feedback):
            # Cancel -> feedback is None -> just re-open the hub unchanged.
            if feedback and feedback.get("rating"):
                row = list(self.service.get_game(self.orig_idx) or [])
                while len(row) <= 9:
                    row.append(None)
                row[9] = feedback["rating"]
                self.service.update_game(self.orig_idx, row)
                self.service.save()
                self._notify_changed()
            GameHub(self.page, self.service, self.orig_idx, self.on_changed,
                    self.on_view_statistics).open()

        self.page.pop_dialog()
        self._stop_timer_thread()
        open_feedback_dialog(self.page, existing=existing_feedback, on_result=_result)

    def _on_delete(self, _):
        self.page.pop_dialog()
        self._stop_timer_thread()
        # confirm_delete pops its own dialog on confirm; on_done fires only on
        # an actual delete. We deliberately do NOT re-open the hub here.
        confirm_delete(self.page, self.service, self.orig_idx,
                       on_done=self._notify_changed)

    def _on_view_statistics(self, _):
        # Close the hub and hand off to the shell, which switches to the
        # Statistics tab and selects this game.
        name = self.game_name
        self._close()
        if self.on_view_statistics and name:
            self.on_view_statistics(name)

    def _on_session_tap(self, session):
        # Single-dialog model: pop the hub, manage the session, then re-open a
        # fresh hub so the sessions table + totals reflect any change.
        self.page.pop_dialog()
        self._stop_timer_thread()
        open_session_actions_dialog(self.page, self.service, self.game_name, session,
                                    on_done=self._reopen_after_child)

    def _on_remove_metadata(self, _):
        row = list(self.service.get_game(self.orig_idx) or [])
        while len(row) <= 10:
            row.append(None)
        row[10] = None
        self.service.update_game(self.orig_idx, row)
        self.service.save()
        self.refresh()
        self._notify_changed()
        self._snack("IGDB metadata removed")

    # ------------------------------------------------------------------ #
    # lifecycle / plumbing
    # ------------------------------------------------------------------ #
    def open(self):
        self.page.show_dialog(self.dialog)
        return self.dialog

    def _close(self):
        self._stop_timer_thread()
        self.page.pop_dialog()

    def _snack(self, message):
        try:
            bar = ft.SnackBar(content=ft.Text(message), duration=2000)
            self.page.overlay.append(bar)
            bar.open = True
            self.page.update()
        except Exception:  # pragma: no cover - defensive
            pass

    def _is_mounted(self):
        if self.page is None:
            return False
        try:
            return self.dialog.page is not None
        except (RuntimeError, AssertionError):
            return False

    def _update(self):
        if self._is_mounted():
            try:
                self.page.update()
            except (RuntimeError, AssertionError):
                pass


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def open_game_hub(page, service, orig_idx, on_changed=None, on_view_statistics=None):
    """Open the Game Hub modal for ``orig_idx``.

    ``on_changed`` (optional) is called whenever the hub mutates the library
    (timer-stop session, edit, add-session, rate, delete, remove-metadata) so
    the caller can refresh its games list. ``on_view_statistics`` (optional) is
    called with the game name when the user clicks "View Statistics". Returns
    the ``GameHub`` controller.
    """
    hub = GameHub(page, service, orig_idx, on_changed=on_changed,
                  on_view_statistics=on_view_statistics)
    hub.open()
    return hub
