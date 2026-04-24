"""
Unified Game Hub popup.

A single window that replaces both the old "Actions for <game>" mini-dialog
and the standalone IGDB "Game Details" popup. The hub combines:

  - Cover art + IGDB metadata (genres, summary, time-to-beat, progress bar).
  - "Your stats": platform, status, rating summary, sessions, last played.
  - An inline live timer (Play / Pause / Stop) that persists the session
    through the same `update_time_and_date` helper the old timer window used.
  - Buttons that launch the existing focused sub-dialogs for Edit, Rate,
    Add Session, and Change Match - each hosted under `_launch_modal` so the
    hub hides itself and only one window is visible at a time.

All long network calls (IGDB search / details) run on worker threads that
post results back via `write_event_value`; the match picker runs on the main
thread (Tk is not thread-safe).

Entry point: `show_game_hub_popup(game_row, row_index, data_with_indices,
                                   data_storage, save_filename, parent_window)`.
"""

import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import PySimpleGUI as sg

from config import load_config
from constants import STAR_FILLED, STAR_EMPTY
from data_management import save_data
from discord_integration import get_discord_integration
from ratings import format_rating, show_rating_popup
from session_ui import (
    show_manual_session_popup,
    show_session_feedback_popup,
    update_time_and_date,
)
from ui_components import create_entry_popup
from utilities import calculate_popup_center_location, format_timedelta_with_seconds
from igdb_integration import delete_cached_cover

# We piggy-back on the private helpers in igdb_ui rather than duplicating
# them; they already render the same data the IGDB popup rendered.
from igdb_ui import (
    _COVER_DISPLAY_SIZE,
    _cover_image_for,
    _ensure_cover_cached,
    _format_seconds_short,
    _parse_user_playtime_seconds,
    _pick_auto_match,
    _render_cover_fitted,
    _run_async,
    _show_igdb_error,
    load_igdb_details,
    search_igdb_candidates,
    show_match_dialog,
)


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def _mutation_result(data_with_indices: List) -> Dict[str, Any]:
    """Standard 'something changed in the hub' return envelope.

    main.py treats 'game_hub_mutation' the same way it treats 'game_edited',
    'game_rated', etc. - a full table/summary/charts/Discord refresh.
    """
    return {"action": "game_hub_mutation", "data": data_with_indices}


# ---------------------------------------------------------------------------
# Formatting helpers used only by the hub layout
# ---------------------------------------------------------------------------


def _parse_hhmmss_to_timedelta(value: Optional[str]) -> timedelta:
    """Robustly parse the app's HH:MM:SS (or HH:MM) time_played field."""
    if not value or not isinstance(value, str):
        return timedelta(0)
    try:
        h, m, s = map(int, value.split(":"))
        return timedelta(hours=h, minutes=m, seconds=s)
    except ValueError:
        try:
            h, m = map(int, value.split(":"))
            return timedelta(hours=h, minutes=m)
        except ValueError:
            return timedelta(0)


def _format_rating_inline(rating: Optional[Dict[str, Any]]) -> str:
    """One-line summary of the game's user rating for the metadata column."""
    if not rating:
        return "Not rated"
    try:
        stars = int(rating.get("stars", 0))
    except (TypeError, ValueError):
        stars = 0
    if stars <= 0:
        return "Not rated"
    tags = rating.get("tags") or []
    star_row = STAR_FILLED * stars + STAR_EMPTY * (5 - stars)
    tag_suffix = f"   {', '.join(tags[:3])}" if tags else ""
    return f"{star_row}  ({stars}/5){tag_suffix}"


def _format_last_played(value: Optional[str]) -> str:
    """Show Last played as YYYY-MM-DD for the hub; fall back to the raw value."""
    if not value or not isinstance(value, str):
        return "never"
    # Stored as 'YYYY-MM-DD HH:MM:SS' by update_time_and_date; trim to date.
    return value.split(" ", 1)[0]


def _session_count(game_row: List[Any]) -> int:
    if len(game_row) > 7 and isinstance(game_row[7], list):
        return len(game_row[7])
    return 0


# ---------------------------------------------------------------------------
# Layout construction
# ---------------------------------------------------------------------------


def _build_cover_column(igdb: Optional[Dict[str, Any]]) -> List[List[Any]]:
    """Fixed-size cover column; PIL-scaled PNG bytes when a cover is cached."""
    cover_path = _cover_image_for(igdb) if igdb else None
    cover_bytes = _render_cover_fitted(cover_path) if cover_path else None
    if cover_bytes:
        inner = [sg.Image(data=cover_bytes, key="-HUB-COVER-",
                          size=_COVER_DISPLAY_SIZE)]
    elif cover_path:
        # PIL missing or decode failed - still keep the container size.
        inner = [sg.Image(filename=cover_path, key="-HUB-COVER-",
                          size=_COVER_DISPLAY_SIZE)]
    else:
        inner = [sg.Text("No cover\navailable", justification="center",
                         size=(14, 6), font=("Helvetica", 10, "italic"),
                         key="-HUB-COVER-")]
    return [
        [sg.Column([inner], size=_COVER_DISPLAY_SIZE, pad=(0, 0),
                   element_justification="center")]
    ]


def _build_metadata_column(game_row: List[Any],
                           igdb: Optional[Dict[str, Any]]) -> List[List[Any]]:
    """Right column: title, your-stats, and (when present) IGDB info."""
    game_name = game_row[0]
    platform = game_row[2] if len(game_row) > 2 else ""
    status = game_row[4] if len(game_row) > 4 else ""
    rating = game_row[9] if len(game_row) > 9 else None
    user_seconds = _parse_user_playtime_seconds(game_row[3] if len(game_row) > 3 else None)

    rows: List[List[Any]] = [
        [sg.Text(game_name, font=("Helvetica", 14, "bold"), key="-HUB-NAME-")],
        [sg.Text(f"Platform: {platform or '-'}", font=("Helvetica", 10)),
         sg.Text("   "),
         sg.Text(f"Status: {status or '-'}", font=("Helvetica", 10),
                 key="-HUB-STATUS-")],
    ]

    if igdb:
        genres = ", ".join(igdb.get("genres") or []) or "(none)"
        igdb_rating = igdb.get("aggregated_rating")
        rating_count = igdb.get("aggregated_rating_count") or 0
        igdb_rating_text = (
            f"IGDB Rating: {igdb_rating:.1f}/100  ({rating_count} reviews)"
            if igdb_rating is not None else "IGDB Rating: --"
        )
        summary = igdb.get("summary") or "(no summary)"
        ttb = igdb.get("time_to_beat") or {}
        ttb_text = (
            f"Main: {_format_seconds_short(ttb.get('hastily'))}   "
            f"Main+Extras: {_format_seconds_short(ttb.get('normally'))}   "
            f"Completionist: {_format_seconds_short(ttb.get('completely'))}"
        )
        normally = ttb.get("normally") or 0
        max_scale = max(normally, user_seconds, 1)
        if normally > 0:
            of_main_pct = min(999, int(100 * user_seconds / normally))
            progress_caption = (
                f"Your time: {_format_seconds_short(user_seconds)}  "
                f"({of_main_pct}% of Main Story average)"
            )
        else:
            progress_caption = (
                f"Your time: {_format_seconds_short(user_seconds)}  "
                f"(no IGDB average)"
            )

        rows.extend([
            [sg.Text(f"Genres: {genres}", font=("Helvetica", 10),
                     key="-HUB-GENRES-")],
            [sg.Text(igdb_rating_text, font=("Helvetica", 10),
                     key="-HUB-IGDB-RATING-")],
            [sg.Text("Summary:", font=("Helvetica", 10, "bold"))],
            [sg.Multiline(summary, size=(60, 6), key="-HUB-SUMMARY-",
                          disabled=True, no_scrollbar=False,
                          background_color="#f5f5f5", text_color="black",
                          autoscroll=False)],
            [sg.Text(ttb_text, font=("Helvetica", 9), key="-HUB-TTB-")],
            [sg.Text(progress_caption, font=("Helvetica", 9, "italic"),
                     key="-HUB-PROGRESS-CAPTION-")],
            [sg.ProgressBar(max_scale, orientation="h", size=(40, 12),
                            key="-HUB-PROGRESS-", style="clam")],
        ])
    else:
        rows.append(
            [sg.Text("No IGDB metadata yet. Click Fetch Metadata below.",
                     font=("Helvetica", 10, "italic"),
                     key="-HUB-NO-META-")]
        )

    # Your-stats section - always present, independent of IGDB.
    rows.extend([
        [sg.HorizontalSeparator()],
        [sg.Text("Your rating:", font=("Helvetica", 10, "bold"),
                 size=(12, 1)),
         sg.Text(_format_rating_inline(rating), key="-HUB-RATING-LINE-",
                 font=("Helvetica", 10), size=(45, 1)),
         sg.Button("Rate Game", key="-HUB-RATE-")],
        [sg.Text(f"Sessions: {_session_count(game_row)}",
                 font=("Helvetica", 10), key="-HUB-SESSIONS-"),
         sg.Text("    "),
         sg.Text(
             f"Last played: {_format_last_played(game_row[6] if len(game_row) > 6 else None)}",
             font=("Helvetica", 10), key="-HUB-LAST-PLAYED-")],
    ])

    if igdb:
        rows.append(
            [sg.Text(f"Fetched: {igdb.get('fetched_at', '--')}",
                     font=("Helvetica", 8), text_color="gray",
                     key="-HUB-FETCHED-")]
        )

    return rows


def _build_timer_section(game_row: List[Any]) -> List[List[Any]]:
    """Inline timer UI (Play / Pause / Stop + live current + total labels)."""
    initial_td = _parse_hhmmss_to_timedelta(
        game_row[3] if len(game_row) > 3 else None
    )
    return [
        [sg.Text("Timer", font=("Helvetica", 10, "bold"))],
        [sg.Text("Current session:", size=(15, 1)),
         sg.Text("00:00:00", key="-HUB-TIMER-", size=(10, 1)),
         sg.Text("   Total:", size=(10, 1)),
         sg.Text(format_timedelta_with_seconds(initial_td),
                 key="-HUB-TOTAL-TIME-", size=(12, 1))],
        [sg.Button("PLAY", key="-HUB-PLAY-",
                   button_color=("black", "green")),
         sg.Button("PAUSE", key="-HUB-PAUSE-",
                   button_color=("black", "yellow"), disabled=True),
         sg.Button("STOP", key="-HUB-STOP-",
                   button_color=("black", "red"), disabled=True)],
    ]


def _build_action_rows(igdb: Optional[Dict[str, Any]]) -> List[List[Any]]:
    """Bottom action rows: game actions, IGDB actions, and Close."""
    if igdb:
        igdb_row = [
            sg.Text("IGDB:", size=(7, 1)),
            sg.Button("Re-fetch", key="-HUB-IGDB-FETCH-"),
            sg.Button("Change Match", key="-HUB-IGDB-REMATCH-"),
            sg.Button("Remove Metadata", key="-HUB-IGDB-REMOVE-"),
        ]
    else:
        igdb_row = [
            sg.Text("IGDB:", size=(7, 1)),
            sg.Button("Fetch Metadata", key="-HUB-IGDB-FETCH-"),
        ]

    return [
        [sg.Text("Game:", size=(7, 1)),
         sg.Button("Edit Game", key="-HUB-EDIT-"),
         sg.Button("Add Session", key="-HUB-SESSION-"),
         sg.Button("View Statistics", key="-HUB-STATS-")],
        igdb_row,
        [sg.Push(), sg.Button("Close", key="-HUB-CLOSE-")],
    ]


def _build_hub_layout(game_row: List[Any],
                      igdb: Optional[Dict[str, Any]]) -> List[List[Any]]:
    cover_col = _build_cover_column(igdb)
    meta_col = _build_metadata_column(game_row, igdb)
    return [
        [sg.Column(cover_col, vertical_alignment="top"),
         sg.VSeperator(),
         sg.Column(meta_col, vertical_alignment="top", expand_x=True)],
        [sg.HorizontalSeparator()],
        *_build_timer_section(game_row),
        [sg.HorizontalSeparator()],
        *_build_action_rows(igdb),
    ]


# ---------------------------------------------------------------------------
# Main popup
# ---------------------------------------------------------------------------


# Buttons that mutate data via a sub-dialog or remote call. Disabled while
# the timer is running so the user can't e.g. rename the game mid-session.
_NON_TIMER_ACTION_KEYS = (
    "-HUB-EDIT-", "-HUB-SESSION-", "-HUB-STATS-", "-HUB-RATE-",
    "-HUB-IGDB-FETCH-", "-HUB-IGDB-REMATCH-", "-HUB-IGDB-REMOVE-",
)


def show_game_hub_popup(game_row: List[Any],
                        row_index: int,
                        data_with_indices: List,
                        data_storage: Optional[List],
                        save_filename: Optional[str],
                        parent_window=None) -> Optional[Dict[str, Any]]:
    """Open the unified Game Hub for a single row.

    Returns one of:
      - {'action': 'game_hub_mutation', 'data': data_with_indices} - any mutation
        that should trigger the main-loop refresh (rating, session add, time
        tracked, IGDB fetch/rematch/remove).
      - {'action': 'game_deleted', 'data': data_with_indices} - the user
        triggered Edit Game -> Delete inside the hub.
      - {'action': 'view_statistics', 'game_name': <name>, 'data': ...} -
        the user clicked View Statistics; main.py switches tabs.
      - None - the user closed the hub without making any changes.

    The hub may rebuild itself (close and reopen) after mutations that change
    layout shape (IGDB fetch/remove, edit, session add); the outer loop below
    tracks whether anything changed so the final return reflects all the
    mutations made across the lifetime of the hub.
    """
    ctx = {
        "mutated": False,
        "delete_bubble": False,
        "view_stats_bubble": False,
    }

    while True:
        outcome = _run_hub_window(
            game_row=game_row,
            row_index=row_index,
            data_with_indices=data_with_indices,
            data_storage=data_storage,
            save_filename=save_filename,
            parent_window=parent_window,
            ctx=ctx,
        )
        if outcome == "reopen":
            # Row data / IGDB metadata was mutated in place; reopen with a
            # fresh layout that reflects the new shape. Timer state is lost
            # on rebuild, which is intentional - the timer is disabled during
            # any path that triggers a rebuild.
            continue
        break

    if ctx["delete_bubble"]:
        return {"action": "game_deleted", "data": data_with_indices}
    if ctx["view_stats_bubble"]:
        return {
            "action": "view_statistics",
            "game_name": game_row[0],
            "data": data_with_indices,
        }
    if ctx["mutated"]:
        return _mutation_result(data_with_indices)
    return None


def _run_hub_window(*, game_row, row_index, data_with_indices, data_storage,
                    save_filename, parent_window, ctx) -> str:
    """One hub-window lifetime. Returns 'reopen' or 'close'.

    A dedicated inner function makes the rebuild/close distinction explicit
    and lets the outer `show_game_hub_popup` aggregate mutation state across
    multiple rebuilds of the same hub.
    """
    igdb = game_row[10] if len(game_row) > 10 and isinstance(game_row[10], dict) else None
    layout = _build_hub_layout(game_row, igdb)

    location = calculate_popup_center_location(parent_window, 780, 700) if parent_window else None
    window = sg.Window(f"Game Hub - {game_row[0]}", layout,
                       modal=True, finalize=True,
                       icon="gameslisticon.ico", location=location)

    # Initialize progress bar AFTER finalize (size changes otherwise get
    # lost on some Tk builds).
    if igdb and igdb.get("time_to_beat"):
        user_seconds = _parse_user_playtime_seconds(
            game_row[3] if len(game_row) > 3 else None
        )
        try:
            window["-HUB-PROGRESS-"].update(current_count=user_seconds)
        except Exception:
            pass

    # ---- Timer state -------------------------------------------------------
    timer_state = {
        "running": False,
        "elapsed": timedelta(0),        # accumulated time this session
        "start_time": 0.0,              # time.time() when Play pressed
        "session_start_time": None,     # iso-format first-Play time
        "session_pauses": [],
        "current_pause": None,
        "initial_time": _parse_hhmmss_to_timedelta(
            game_row[3] if len(game_row) > 3 else None
        ),
    }
    discord = get_discord_integration()

    # ---- IGDB state --------------------------------------------------------
    fetching = False
    is_rematch = False
    allow_auto = bool(load_config().get("igdb_auto_match_on_add"))

    def _key_exists(key: str) -> bool:
        """Safe key-existence check for the current layout.

        Some buttons (-HUB-IGDB-REMATCH-, -HUB-IGDB-REMOVE-) are only rendered
        when IGDB metadata exists, so blindly doing `window[key]` on the
        "no metadata" layout pops PySimpleGUI's Key Error dialog before any
        try/except around the call can swallow it.
        """
        try:
            return key in window.key_dict
        except Exception:
            return False

    def _set_non_timer_actions_enabled(enabled: bool) -> None:
        for key in _NON_TIMER_ACTION_KEYS:
            if not _key_exists(key):
                continue
            try:
                window[key].update(disabled=not enabled)
            except Exception:
                pass

    def _set_fetching(active: bool) -> None:
        """Mirror the IGDB popup's fetching indicator."""
        if _key_exists("-HUB-IGDB-FETCH-"):
            try:
                window["-HUB-IGDB-FETCH-"].update(disabled=active)
            except Exception:
                pass
        for key in ("-HUB-IGDB-REMATCH-", "-HUB-IGDB-REMOVE-"):
            if not _key_exists(key):
                continue
            try:
                window[key].update(disabled=active or igdb is None)
            except Exception:
                pass
        window.set_title(
            f"Game Hub - {game_row[0]} (fetching...)" if active
            else f"Game Hub - {game_row[0]}"
        )

    def _launch_modal(call: Callable[[], Any]) -> Any:
        """Run a blocking sub-dialog while hiding the hub window.

        Keeps only one Tk window on screen at a time so the user doesn't see
        the hub stacked behind the sub-dialog (the same fix we made for the
        match picker). Restores the hub regardless of exceptions.
        """
        try:
            window.hide()
        except Exception:
            pass
        try:
            return call()
        finally:
            try:
                window.un_hide()
            except Exception:
                pass

    # ---- Close guard helper -----------------------------------------------
    def _confirm_close_if_timer_running() -> Optional[str]:
        """Ask the user whether to save the running session before closing.

        Returns one of:
          - 'save'   : stop + persist the session, then close.
          - 'discard': drop the elapsed time, then close.
          - 'cancel' : stay in the hub.
        Returns 'save' immediately when the timer isn't running.
        """
        if not timer_state["running"] and timer_state["session_start_time"] is None:
            return "save"  # nothing to save, proceed to close
        choice = _launch_modal(lambda: sg.popup(
            "The timer is still running.\n\nSave this session before closing?",
            title="Timer running",
            custom_text=("Save and Close", "Discard", "Cancel"),
            keep_on_top=True,
            icon="gameslisticon.ico",
        ))
        if choice == "Save and Close":
            return "save"
        if choice == "Discard":
            return "discard"
        return "cancel"

    def _stop_timer_and_persist(save_session: bool) -> None:
        """Finalize the timer - mirror of session_ui.show_popup's Stop path."""
        if timer_state["running"]:
            timer_state["elapsed"] = timedelta(
                seconds=time.time() - timer_state["start_time"]
            )
            timer_state["running"] = False
        # Dangling pause (e.g. Stop pressed while paused).
        if timer_state["current_pause"]:
            timer_state["current_pause"]["incomplete"] = True
            timer_state["session_pauses"].append(timer_state["current_pause"])
            timer_state["current_pause"] = None

        if save_session and timer_state["session_start_time"]:
            session_end_time = datetime.now().isoformat()
            session = {
                "start": timer_state["session_start_time"],
                "end": session_end_time,
                "duration": format_timedelta_with_seconds(timer_state["elapsed"]),
                "pauses": timer_state["session_pauses"],
            }
            # Optional feedback, same yes/no prompt as the old timer.
            feedback_loc = calculate_popup_center_location(
                window, popup_width=400, popup_height=150)
            if _launch_modal(lambda: sg.popup_yes_no(
                "Would you like to add feedback for this session?",
                title="Add Session Feedback",
                location=feedback_loc,
            )) == "Yes":
                feedback = _launch_modal(
                    lambda: show_session_feedback_popup(parent_window=window)
                )
                if feedback:
                    session["feedback"] = feedback

            update_time_and_date(row_index, timer_state["elapsed"],
                                 session, data_with_indices, data_storage)
            if save_filename:
                save_data(data_with_indices, save_filename, data_storage)
            discord.update_presence_session_complete(
                game_row[0],
                format_timedelta_with_seconds(timer_state["elapsed"]),
                game_row[2] if len(game_row) > 2 else None,
            )
            ctx["mutated"] = True
            # Refresh displayed Total / Last-played / Sessions in place.
            _refresh_timer_display_labels()
        # If discarding we don't update_time_and_date; elapsed stays local.

        # Reset timer state for a potential new session in this same hub.
        timer_state["elapsed"] = timedelta(0)
        timer_state["session_start_time"] = None
        timer_state["session_pauses"] = []
        timer_state["current_pause"] = None
        # update_time_and_date rewrote row[3]; re-read it for future Play runs.
        timer_state["initial_time"] = _parse_hhmmss_to_timedelta(
            game_row[3] if len(game_row) > 3 else None
        )
        try:
            window["-HUB-TIMER-"].update("00:00:00")
            window["-HUB-TOTAL-TIME-"].update(
                format_timedelta_with_seconds(timer_state["initial_time"]))
            window["-HUB-PLAY-"].update(disabled=False)
            window["-HUB-PAUSE-"].update(disabled=True)
            window["-HUB-STOP-"].update(disabled=True)
        except Exception:
            pass
        _set_non_timer_actions_enabled(True)

    def _refresh_timer_display_labels() -> None:
        """Re-render total time, sessions, last-played after a persisted stop."""
        try:
            window["-HUB-TOTAL-TIME-"].update(
                format_timedelta_with_seconds(
                    _parse_hhmmss_to_timedelta(
                        game_row[3] if len(game_row) > 3 else None)
                )
            )
            window["-HUB-SESSIONS-"].update(
                f"Sessions: {_session_count(game_row)}")
            window["-HUB-LAST-PLAYED-"].update(
                f"Last played: "
                f"{_format_last_played(game_row[6] if len(game_row) > 6 else None)}"
            )
        except Exception:
            pass

    # ---- Rebuild helpers ---------------------------------------------------
    def _close_and_reopen() -> str:
        """Close the hub and ask the outer loop to reopen it with fresh layout."""
        try:
            window.close()
        except Exception:
            pass
        return "reopen"

    # ----------------------- Main event loop --------------------------------
    try:
        while True:
            event, values = window.read(timeout=100)

            # ---- Close / window-X --------------------------------------
            if event in (sg.WIN_CLOSED, "-HUB-CLOSE-"):
                decision = _confirm_close_if_timer_running()
                if decision == "cancel":
                    continue
                if decision == "save":
                    _stop_timer_and_persist(save_session=True)
                elif decision == "discard":
                    _stop_timer_and_persist(save_session=False)
                break

            # ---- View Statistics (closes hub, bubbles) -----------------
            if event == "-HUB-STATS-":
                if timer_state["running"]:
                    continue  # button should be disabled anyway
                ctx["view_stats_bubble"] = True
                break

            # ---- Edit Game ---------------------------------------------
            if event == "-HUB-EDIT-":
                if timer_state["running"]:
                    continue
                existing_entry = data_with_indices[row_index][1]
                discord.update_presence_editing_game(existing_entry[0])

                popup_values, action_type, rating = _launch_modal(
                    lambda: create_entry_popup(existing_entry, window))

                if action_type is None:
                    # Edit cancelled - restore browsing presence.
                    discord.update_presence_browsing("Games List")
                    continue
                if action_type == "Delete":
                    confirm = _launch_modal(lambda: sg.popup_yes_no(
                        f"Are you sure you want to delete '{existing_entry[0]}'?",
                        title="Confirm Deletion",
                        location=calculate_popup_center_location(
                            window, popup_width=400, popup_height=150),
                    ))
                    if confirm == "Yes":
                        original_idx = data_with_indices[row_index][0]
                        data_with_indices.pop(row_index)
                        if data_storage:
                            for i, (idx, _) in enumerate(data_storage):
                                if idx == original_idx:
                                    data_storage.pop(i)
                                    break
                        if save_filename:
                            save_data(data_with_indices, save_filename, data_storage)
                        _launch_modal(lambda: sg.popup(
                            f"'{existing_entry[0]}' has been deleted.",
                            title="Deletion Complete",
                            location=calculate_popup_center_location(
                                window, popup_width=350, popup_height=120),
                        ))
                        ctx["mutated"] = True
                        ctx["delete_bubble"] = True
                        break
                    continue  # user said No -> stay in hub
                if action_type == "Submit":
                    _apply_edit_submission(
                        row_index=row_index,
                        data_with_indices=data_with_indices,
                        data_storage=data_storage,
                        save_filename=save_filename,
                        popup_values=popup_values,
                        rating=rating,
                    )
                    ctx["mutated"] = True
                    # Row was replaced - re-fetch the reference so later
                    # events see the updated game_row.
                    game_row[:] = data_with_indices[row_index][1]
                    return _close_and_reopen()
                continue

            # ---- Rate Game ---------------------------------------------
            if event == "-HUB-RATE-":
                if timer_state["running"]:
                    continue
                existing_rating = game_row[9] if len(game_row) > 9 else None
                new_rating = _launch_modal(
                    lambda: show_rating_popup(existing_rating, window))
                if new_rating:
                    while len(game_row) <= 10:
                        game_row.append(None)
                    game_row[9] = new_rating
                    # Sync back into data_with_indices / data_storage.
                    if data_storage:
                        original_index = data_with_indices[row_index][0]
                        for i, (idx, _) in enumerate(data_storage):
                            if idx == original_index:
                                data_storage[i] = data_with_indices[row_index]
                                break
                    if save_filename:
                        save_data(data_with_indices, save_filename, data_storage)
                    ctx["mutated"] = True
                    # Update the inline rating line in place - no rebuild
                    # needed since shape hasn't changed.
                    try:
                        window["-HUB-RATING-LINE-"].update(
                            _format_rating_inline(new_rating))
                    except Exception:
                        pass
                continue

            # ---- Add Session -------------------------------------------
            if event == "-HUB-SESSION-":
                if timer_state["running"]:
                    continue
                session = _launch_modal(
                    lambda: show_manual_session_popup(game_row[0], window))
                if session:
                    # Reuse the canonical add-session helper for HH:MM:SS
                    # math + feedback merging + data_storage sync.
                    from session_data import add_manual_session_to_game
                    ok = add_manual_session_to_game(
                        game_row[0], session, data_with_indices, data_storage)
                    if ok:
                        if save_filename:
                            save_data(data_with_indices, save_filename, data_storage)
                        ctx["mutated"] = True
                        # Refresh the row reference in case add_manual_... mutated.
                        game_row[:] = data_with_indices[row_index][1]
                        return _close_and_reopen()
                    else:
                        _launch_modal(lambda: sg.popup_error(
                            f"Failed to add session to {game_row[0]}",
                            title="Error",
                            location=calculate_popup_center_location(
                                window, popup_width=400, popup_height=150),
                        ))
                continue

            # ---- Timer: Play -------------------------------------------
            if event == "-HUB-PLAY-":
                if not timer_state["running"]:
                    if timer_state["session_start_time"] is None:
                        timer_state["session_start_time"] = datetime.now().isoformat()
                        discord.update_presence_playing(
                            game_row[0],
                            datetime.fromisoformat(timer_state["session_start_time"]),
                            game_row[2] if len(game_row) > 2 else None,
                        )
                    timer_state["start_time"] = (
                        time.time() - timer_state["elapsed"].total_seconds()
                    )
                    timer_state["running"] = True
                    window["-HUB-PLAY-"].update(disabled=True)
                    window["-HUB-PAUSE-"].update(disabled=False)
                    window["-HUB-STOP-"].update(disabled=False)
                    _set_non_timer_actions_enabled(False)
                    # Resume from pause?
                    if timer_state["current_pause"]:
                        discord.update_presence_playing(
                            game_row[0],
                            datetime.fromisoformat(timer_state["session_start_time"]),
                            game_row[2] if len(game_row) > 2 else None,
                        )
                        timer_state["current_pause"]["resumed_at"] = datetime.now().isoformat()
                        try:
                            pause_start = datetime.fromisoformat(
                                timer_state["current_pause"]["paused_at"])
                            pause_end = datetime.fromisoformat(
                                timer_state["current_pause"]["resumed_at"])
                            pause_duration = pause_end - pause_start
                            hours, remainder = divmod(pause_duration.total_seconds(), 3600)
                            minutes, seconds = divmod(remainder, 60)
                            timer_state["current_pause"]["pause_duration"] = (
                                f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
                            )
                        except (ValueError, TypeError):
                            timer_state["current_pause"]["pause_duration"] = "00:00:00"
                        timer_state["session_pauses"].append(timer_state["current_pause"])
                        timer_state["current_pause"] = None
                continue

            # ---- Timer: Pause ------------------------------------------
            if event == "-HUB-PAUSE-":
                if timer_state["running"]:
                    timer_state["elapsed"] = timedelta(
                        seconds=time.time() - timer_state["start_time"])
                    timer_state["running"] = False
                    window["-HUB-PLAY-"].update(disabled=False)
                    discord.update_presence_paused(
                        game_row[0],
                        game_row[2] if len(game_row) > 2 else None,
                    )
                    timer_state["current_pause"] = {
                        "paused_at": datetime.now().isoformat(),
                        "elapsed_so_far": format_timedelta_with_seconds(
                            timer_state["elapsed"]),
                    }
                continue

            # ---- Timer: Stop -------------------------------------------
            if event == "-HUB-STOP-":
                _stop_timer_and_persist(save_session=True)
                continue

            # ---- IGDB: Fetch / Re-fetch / Rematch ----------------------
            if event in ("-HUB-IGDB-FETCH-", "-HUB-IGDB-REMATCH-"):
                if fetching or timer_state["running"]:
                    continue
                fetching = True
                is_rematch = (event == "-HUB-IGDB-REMATCH-")
                _set_fetching(True)
                use_existing_id = (
                    event == "-HUB-IGDB-FETCH-"
                    and igdb is not None
                    and igdb.get("igdb_id")
                )
                if use_existing_id:
                    def _worker_details(win, igdb_id):
                        win.write_event_value(
                            "-HUB-IGDB-DETAILS-DONE-",
                            load_igdb_details(igdb_id))
                    _run_async(_worker_details, window, int(igdb["igdb_id"]))
                else:
                    def _worker_search(win, name):
                        win.write_event_value(
                            "-HUB-IGDB-SEARCH-DONE-",
                            search_igdb_candidates(name))
                    _run_async(_worker_search, window, game_row[0])
                continue

            # ---- IGDB: search done (main-thread UI work) ---------------
            if event == "-HUB-IGDB-SEARCH-DONE-":
                result = values[event] or {}
                if result.get("_error"):
                    _show_igdb_error(window, result["_error"])
                    fetching = False
                    is_rematch = False
                    _set_fetching(False)
                    continue
                candidates = result.get("candidates") or []
                user_platform = game_row[2] if len(game_row) > 2 else None
                auto = None
                if allow_auto and candidates and not is_rematch:
                    auto = _pick_auto_match(
                        game_row[0],
                        game_row[1] if len(game_row) > 1 else None,
                        user_platform,
                        candidates,
                    )
                if auto is not None:
                    chosen_id = int(auto["id"])
                else:
                    chosen = _launch_modal(
                        lambda: show_match_dialog(
                            game_row[0], candidates, parent_window or window)
                    )
                    if chosen is None or (isinstance(chosen, dict) and chosen.get("_skip")):
                        fetching = False
                        is_rematch = False
                        _set_fetching(False)
                        continue
                    chosen_id = int(chosen["id"])

                def _worker_details(win, igdb_id):
                    win.write_event_value(
                        "-HUB-IGDB-DETAILS-DONE-",
                        load_igdb_details(igdb_id))
                _run_async(_worker_details, window, chosen_id)
                continue

            # ---- IGDB: details fetched ---------------------------------
            if event == "-HUB-IGDB-DETAILS-DONE-":
                result = values[event] or {}
                fetching = False
                is_rematch = False
                _set_fetching(False)
                if result.get("_error"):
                    _show_igdb_error(window, result["_error"])
                    continue
                if result.get("igdb_id"):
                    # Persist new IGDB dict into the row and sync storages.
                    _save_igdb_metadata(
                        result, game_row, row_index,
                        data_with_indices, data_storage, save_filename)
                    ctx["mutated"] = True
                    return _close_and_reopen()
                continue

            # ---- IGDB: remove metadata ---------------------------------
            if event == "-HUB-IGDB-REMOVE-":
                if timer_state["running"]:
                    continue
                confirm = _launch_modal(lambda: sg.popup_yes_no(
                    "Remove the IGDB metadata for this game?",
                    title="Confirm Remove", icon="gameslisticon.ico",
                ))
                if confirm == "Yes":
                    _save_igdb_metadata(
                        None, game_row, row_index,
                        data_with_indices, data_storage, save_filename)
                    ctx["mutated"] = True
                    return _close_and_reopen()
                continue

            # ---- Timer tick (no explicit event) ------------------------
            # window.read(timeout=100) fires with event == '__TIMEOUT__'
            # (sg.TIMEOUT_KEY). Update timer labels whenever the session
            # has ever been played so a paused timer shows accumulated time.
            if timer_state["running"]:
                current_elapsed = timedelta(
                    seconds=time.time() - timer_state["start_time"])
            else:
                current_elapsed = timer_state["elapsed"]
            try:
                window["-HUB-TIMER-"].update(
                    format_timedelta_with_seconds(current_elapsed))
                window["-HUB-TOTAL-TIME-"].update(
                    format_timedelta_with_seconds(
                        timer_state["initial_time"] + current_elapsed))
            except Exception:
                pass
    finally:
        try:
            window.close()
        except Exception:
            pass

    return "close"


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _save_igdb_metadata(new_igdb: Optional[Dict[str, Any]],
                        game_row: List[Any],
                        row_index: int,
                        data_with_indices: List,
                        data_storage: Optional[List],
                        save_filename: Optional[str]) -> None:
    """Persist an IGDB dict (or None to clear) at row[10] and save.

    When the IGDB id at row[10] changes - either cleared (Remove Metadata) or
    swapped for a new id (Change Match / Re-fetch that resolved to a different
    game) - the previously cached cover art is deleted so the on-disk cache
    doesn't accumulate orphans forever.
    """
    while len(game_row) <= 10:
        game_row.append(None)

    # Capture the previous IGDB id before overwriting so we can decide
    # whether its cached cover is now orphaned.
    old_igdb = game_row[10] if isinstance(game_row[10], dict) else None
    old_id = old_igdb.get("igdb_id") if old_igdb else None
    new_id = new_igdb.get("igdb_id") if isinstance(new_igdb, dict) else None
    if old_id and old_id != new_id:
        delete_cached_cover(old_id)

    game_row[10] = new_igdb
    if data_storage is not None:
        original_index = data_with_indices[row_index][0]
        for i, (idx, _) in enumerate(data_storage):
            if idx == original_index:
                data_storage[i] = data_with_indices[row_index]
                break
    if save_filename:
        save_data(data_with_indices, save_filename, data_storage)


def _apply_edit_submission(*, row_index: int,
                           data_with_indices: List,
                           data_storage: Optional[List],
                           save_filename: Optional[str],
                           popup_values: Dict[str, Any],
                           rating: Optional[Dict[str, Any]]) -> None:
    """Mirror of the Edit-Game Submit path from event_handlers.handle_game_action.

    Extracted here so the hub owns the Edit flow end-to-end. Re-implemented
    (rather than imported) to avoid a circular import back into
    event_handlers, which itself imports show_game_hub_popup.
    """
    # Local import: record_status_change lives with the edit submission logic
    # in event_handlers. Deferred to avoid a circular module-level import.
    from event_handlers import record_status_change

    existing_entry = data_with_indices[row_index][1]
    new_release = popup_values["-NEW-RELEASE-"]
    if new_release == "-" or not new_release.strip():
        new_release_date = "-"
    else:
        new_release_date = datetime.strptime(
            new_release, "%Y-%m-%d").strftime("%Y-%m-%d")

    time_value = popup_values["-NEW-TIME-"]
    if not time_value or time_value in ["00:00:00", "00:00"]:
        time_value = None

    old_status = existing_entry[4]
    new_status = popup_values["-NEW-STATUS-"]

    updated_entry = [
        popup_values["-NEW-NAME-"],
        new_release_date,
        popup_values["-NEW-PLATFORM-"],
        time_value,
        new_status,
        "✅" if popup_values["-NEW-OWNED-"] else "",
        existing_entry[6] if len(existing_entry) > 6 else None,
    ]
    # Preserve sessions.
    if len(existing_entry) > 7 and existing_entry[7] is not None:
        updated_entry.append(existing_entry[7])
    else:
        updated_entry.append([])
    # Preserve status_history.
    if len(existing_entry) > 8 and existing_entry[8] is not None:
        updated_entry.append(existing_entry[8])
    else:
        updated_entry.append([])
    if old_status != new_status:
        record_status_change(updated_entry, old_status, new_status)

    # Apply rating if one came back, otherwise preserve the existing.
    if rating is not None:
        while len(updated_entry) <= 9:
            updated_entry.append(None)
        updated_entry[9] = rating
    elif len(existing_entry) > 9 and existing_entry[9] is not None:
        while len(updated_entry) <= 9:
            updated_entry.append(None)
        updated_entry[9] = existing_entry[9]

    # Preserve IGDB metadata at index 10.
    existing_igdb = existing_entry[10] if len(existing_entry) > 10 else None
    while len(updated_entry) <= 10:
        updated_entry.append(None)
    updated_entry[10] = existing_igdb

    data_with_indices[row_index] = (data_with_indices[row_index][0], updated_entry)

    if data_storage:
        original_index = data_with_indices[row_index][0]
        for i, (idx, _) in enumerate(data_storage):
            if idx == original_index:
                data_storage[i] = data_with_indices[row_index]
                break

    if save_filename:
        save_data(data_with_indices, save_filename, data_storage)
