"""
Bridge between the process watcher and the existing session pipeline.

This module is the only place that knows about *both* the watcher's event
shape and the games-data shape. main.py routes the relevant events here so
its own event-loop branches stay small.

Public API used by main.py:
    bridge = SessionWatcherBridge(window_provider, data_provider,
                                  data_storage_provider, fn_provider,
                                  discord_provider)
    bridge.build_library_snapshot()   -> list[dict]      (for the watcher)
    bridge.build_state_snapshot()     -> dict            (for the tray)
    bridge.list_recent_console_games(limit=10) -> list[str]
    bridge.handle_event(event_key, payload) -> dict|None

`handle_event` returns a result envelope compatible with the existing main.py
patterns (e.g. ``{'action': 'session_added', 'data': data_with_indices}``) so
the caller can run the same refresh paths used after manual session adds.

It also exposes:
    bridge.confirm_pending_match(detection_id, action, payload)
    bridge.drain_pending_matches_on_focus(window)
    bridge.recover_orphan_session_if_any(parent_window)
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from config import load_config, save_config
from constants import (
    STATUS_IN_PROGRESS,
    STATUS_PENDING,
)
from data_management import save_data
from session_data import add_manual_session_to_game
from utilities import format_timedelta_with_seconds, is_console_platform
from watcher_log import bridge_logger

_log = bridge_logger()


# Type aliases for the lazy-binding callbacks main.py provides. We use
# zero-arg callables so the bridge always sees the *current* values
# (data lists are reassigned across the loop, so capturing by reference
# would go stale).
WindowProvider = Callable[[], Any]
DataProvider = Callable[[], List]
DataStorageProvider = Callable[[], Optional[List]]
FilenameProvider = Callable[[], str]
DiscordProvider = Callable[[], Any]


class SessionWatcherBridge:
    """Transforms watcher events into mutations on the games-data structure."""

    def __init__(
        self,
        window_provider: WindowProvider,
        data_provider: DataProvider,
        data_storage_provider: DataStorageProvider,
        filename_provider: FilenameProvider,
        discord_provider: DiscordProvider,
    ) -> None:
        self._window = window_provider
        self._data = data_provider
        self._data_storage = data_storage_provider
        self._filename = filename_provider
        self._discord = discord_provider

        # Active session ids -> the row index they are accumulating on.
        self._active_sessions: Dict[str, int] = {}
        # detection_id -> payload, queued while user wasn't around to confirm.
        self._pending_matches: Dict[str, Dict[str, Any]] = {}
        # session_id -> last-seen pause start, so end events can patch
        # session shape with idle pauses if needed.
        self._session_pauses: Dict[str, Dict] = {}

    # ------------------------------------------------------------------
    # Snapshots used by other modules (watcher, tray, notifications)
    # ------------------------------------------------------------------

    def build_library_snapshot(self) -> List[Dict]:
        """Compact list of dicts for the watcher's resolver layer 3."""
        snap: List[Dict] = []
        for _idx, row in self._data() or []:
            try:
                name = row[0]
            except Exception:
                continue
            if not name:
                continue
            platform = row[2] if len(row) > 2 else None
            aliases: List[str] = []
            if len(row) > 10 and isinstance(row[10], dict):
                igdb = row[10]
                igdb_name = igdb.get('name')
                if igdb_name and igdb_name != name:
                    aliases.append(igdb_name)
                for a in (igdb.get('alternative_names') or []):
                    if isinstance(a, dict):
                        v = a.get('name')
                        if v:
                            aliases.append(v)
                    elif isinstance(a, str):
                        aliases.append(a)
            snap.append({'name': name, 'platform': platform, 'igdb_aliases': aliases})
        return snap

    def build_state_snapshot(self) -> Dict:
        """Snapshot used by the tray icon. Watcher fills in the other half."""
        return {
            'pending_matches': len(self._pending_matches),
        }

    def list_recent_console_games(self, limit: int = 10) -> List[str]:
        """Console-platform games, ordered for the tray's quick-launch menu.

        Priority groups, applied in order:

        1. **Recently played and still active** - status is either
           ``In progress`` or ``Pending`` *and* ``last_played`` is
           within the last :data:`_RECENT_CONSOLE_DAYS` days.
           ``In progress`` titles come before ``Pending`` ones (the
           user is more likely to resume something they're already
           working on); within each status, newest play first.
           Completed / Dropped games are deliberately excluded from
           this bucket regardless of how recently they were played -
           a game you finished last week still belongs in "Other",
           not at the top of the quick-launch menu.
        2. **In progress backlog** - everything else with status
           ``In progress`` (never played, or last played longer than
           the recent window ago), ordered by release date *ascending*
           so older games surface first.
        3. **Pending backlog** - same shape as #2 but for ``Pending``.
        4. **Everything else** - completed / dropped / unknown-status
           console titles, in stable library order.

        Within each group, ties (e.g. several games with no release
        date) fall back to alphabetical ordering by name. Casing is
        preserved for display.
        """
        # Indices match the table headings defined in ui_components.get_table_column_widths
        # (Name, Release, Platform, Time, Status, Owned, Last Played, Rating).
        IDX_NAME, IDX_RELEASE, IDX_PLATFORM = 0, 1, 2
        IDX_STATUS, IDX_LAST_PLAYED = 4, 6

        # 30-day cutoff for the "recently played" bucket. Re-evaluated
        # per call so leaving the app open across day boundaries
        # doesn't freeze the menu's idea of "recent".
        recent_cutoff = datetime.now() - timedelta(days=_RECENT_CONSOLE_DAYS)

        # Sort key for the recent bucket: (status_rank, -timestamp,
        # name_lower). status_rank 0 = In progress, 1 = Pending, so
        # ascending sort puts In progress first, then within each
        # status the newest play floats to the top.
        STATUS_RANK = {STATUS_IN_PROGRESS: 0, STATUS_PENDING: 1}

        recent: List = []          # (status_rank, -timestamp, name_lower, name)
        in_progress: List = []     # (release_dt, name_lower, name)
        pending: List = []         # (release_dt, name_lower, name)
        other: List = []           # (library_order, name)

        for library_order, (_idx, row) in enumerate(self._data() or []):
            if len(row) <= IDX_PLATFORM:
                continue
            name = row[IDX_NAME]
            platform = row[IDX_PLATFORM] or ''
            if not is_console_platform(platform):
                continue

            last_played_raw = row[IDX_LAST_PLAYED] if len(row) > IDX_LAST_PLAYED else None
            release_raw = row[IDX_RELEASE] if len(row) > IDX_RELEASE else None
            status = (row[IDX_STATUS] if len(row) > IDX_STATUS else '') or ''

            last_played_dt = _parse_last_played(last_played_raw)
            release_dt = _parse_release_date(release_raw)

            # Recent only matters for In progress / Pending titles
            # played inside the cutoff window. Anything older or in a
            # different status falls through to its status backlog (or
            # "other").
            is_recent = (
                last_played_dt is not None
                and last_played_dt >= recent_cutoff
                and status in STATUS_RANK
            )
            if is_recent:
                recent.append((
                    STATUS_RANK[status],
                    -last_played_dt.timestamp(),
                    name.lower(),
                    name,
                ))
                continue

            if status == STATUS_IN_PROGRESS:
                in_progress.append((release_dt or datetime.max, name.lower(), name))
            elif status == STATUS_PENDING:
                pending.append((release_dt or datetime.max, name.lower(), name))
            else:
                # Completed / Dropped / unknown - kept at the bottom
                # in stable library order so they're still reachable
                # without crowding out actionable items.
                other.append((library_order, name))

        recent.sort()
        in_progress.sort()
        pending.sort()
        other.sort()

        ordered: List[str] = []
        seen = set()  # case-insensitive de-dupe in case the library has duplicates

        def _extend(items, name_index: int) -> bool:
            for entry in items:
                n = entry[name_index]
                k = n.lower()
                if k in seen:
                    continue
                seen.add(k)
                ordered.append(n)
                if len(ordered) >= limit:
                    return True
            return False

        if _extend(recent, 3):
            return ordered
        if _extend(in_progress, 2):
            return ordered
        if _extend(pending, 2):
            return ordered
        _extend(other, 1)
        return ordered

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def handle_event(self, event_key: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Route a watcher / toast / tray event. Returns a main.py-style envelope."""
        if event_key == '-PROCESS-DETECTED-':
            return self._on_process_detected(payload)
        if event_key == '-PROCESS-ENDED-':
            return self._on_process_ended(payload)
        if event_key == '-MATCH-AMBIGUOUS-':
            return self._on_match_ambiguous(payload)
        if event_key == '-WATCHER-IDLE-PAUSE-':
            return self._on_idle_pause(payload)
        if event_key == '-WATCHER-STATUS-':
            return self._on_status(payload)
        if event_key == '-TOAST-ACTION-':
            return self._on_toast_action(payload)
        return None

    # ------------------------------------------------------------------
    # Watcher event handlers
    # ------------------------------------------------------------------

    def _on_process_detected(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        game_name = payload.get('game_name')
        session_id = payload.get('session_id')
        if not game_name or not session_id:
            return None

        row_index = self._find_row_index(game_name)
        if row_index is None:
            _log.warning(
                "watcher detected %r but no matching library row; "
                "aborting watcher session and forgetting auto-learned mapping. "
                "Add the game via 'Add Entry' to enable tracking.",
                game_name)
            # Ask the watcher to abandon its ghost Tracking state and undo the
            # auto-learn it performed at session start. Without this, the
            # watcher would stay in Tracking forever (or until the game exits)
            # while accumulating no recordable session, AND it would re-match
            # the same exe next launch via the bad learned mapping.
            try:
                from process_watcher import get_watcher
                watcher = get_watcher()
                if watcher is not None:
                    exe_path = payload.get('exe_path') or ''
                    if exe_path:
                        watcher.forget_mapping(exe_path)
                    watcher.stop_current_session()
            except Exception as exc:  # noqa: BLE001
                _log.warning("ghost-session cleanup failed: %s", exc)
            return None

        self._active_sessions[session_id] = row_index
        self._session_pauses[session_id] = {'start_iso': payload.get('start_time_iso')}
        _log.info("session start session=%s game=%r row=%d",
                  session_id, game_name, row_index)

        # Update Discord presence so external Discord users see the game.
        try:
            discord = self._discord()
            if discord is not None:
                start_dt = datetime.fromisoformat(payload['start_time_iso'])
                row = self._data()[row_index][1]
                platform = row[2] if len(row) > 2 else None
                discord.update_presence_playing(game_name, session_start_time=start_dt, platform=platform)
        except Exception as exc:  # noqa: BLE001
            _log.warning("Discord presence update failed on detect: %s", exc)

        # Notify (cover art if IGDB cached).
        cover_path = self._cover_path_for(row_index)
        try:
            from notifications import notify_session_started
            notify_session_started(session_id, game_name, cover_path)
        except Exception as exc:  # noqa: BLE001
            _log.warning("notify_session_started failed: %s", exc)

        return {'action': 'watcher_session_started', 'game_name': game_name}

    def _on_process_ended(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        session_id = payload.get('session_id')
        game_name = payload.get('game_name')
        duration_str = payload.get('duration_str') or '00:00:00'
        if not session_id or not game_name:
            return None

        row_index = self._active_sessions.pop(session_id, None)
        self._session_pauses.pop(session_id, None)
        if row_index is None:
            row_index = self._find_row_index(game_name)
        if row_index is None:
            _log.warning("session %s ended but no library row for %r",
                         session_id, game_name)
            return None

        # Build the canonical session dict and persist via the existing
        # add_manual_session_to_game pipeline, which also bumps total time
        # and last-played in the same shape the manual timer uses.
        session = {
            'start': payload.get('start_iso'),
            'end': payload.get('end_iso'),
            'duration': duration_str,
            'pauses': payload.get('pauses', []),
            'source': 'auto_watcher',
        }
        try:
            data = self._data()
            ok = add_manual_session_to_game(
                game_name,
                session,
                data,
                self._data_storage(),
            )
        except Exception as exc:  # noqa: BLE001
            _log.error("failed to persist auto-watcher session: %s", exc,
                       exc_info=True)
            ok = False

        if ok:
            try:
                save_data(self._data(), self._filename(), self._data_storage())
                _log.info("session end session=%s game=%r duration=%s persisted",
                          session_id, game_name, duration_str)
            except Exception as exc:  # noqa: BLE001
                _log.error("save_data failed after auto-session: %s", exc)
        else:
            _log.warning("session end session=%s game=%r NOT persisted",
                         session_id, game_name)

        try:
            from notifications import notify_session_ended
            notify_session_ended(session_id, game_name, _humanize_duration(duration_str))
        except Exception as exc:  # noqa: BLE001
            _log.warning("notify_session_ended failed: %s", exc)

        # Hand Discord a "session complete" beat, then fall back to browsing.
        try:
            discord = self._discord()
            if discord is not None:
                row = self._data()[row_index][1]
                platform = row[2] if len(row) > 2 else None
                discord.update_presence_session_complete(
                    game_name, duration_str, platform=platform)
        except Exception as exc:  # noqa: BLE001
            _log.warning("Discord session-complete update failed: %s", exc)

        return {'action': 'session_added', 'data': self._data()}

    def _on_match_ambiguous(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        detection_id = payload.get('detection_id') or uuid.uuid4().hex
        self._pending_matches[detection_id] = payload
        # Surface a toast right away so engaged users can act on it instantly.
        try:
            from notifications import notify_match_confirmation
            notify_match_confirmation(
                detection_id,
                payload.get('exe_basename') or '',
                payload.get('install_dir') or '',
                payload.get('best_guess'),
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("notify_match_confirmation failed: %s", exc)
        return None

    def _on_idle_pause(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        session_id = payload.get('session_id')
        paused = payload.get('paused')
        row_index = self._active_sessions.get(session_id)
        if row_index is None:
            return None
        try:
            discord = self._discord()
            if discord is None:
                return None
            row = self._data()[row_index][1]
            game_name = row[0]
            platform = row[2] if len(row) > 2 else None
            if paused:
                discord.update_presence_paused(game_name, platform=platform)
            else:
                discord.update_presence_playing(game_name, platform=platform)
        except Exception as exc:  # noqa: BLE001
            _log.warning("Discord pause/resume update failed: %s", exc)
        return None

    def _on_status(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # Status events are advisory - the tray uses them to refresh, but the
        # tray module pulls a fresh snapshot itself, so nothing to do here.
        return None

    # ------------------------------------------------------------------
    # Toast / tray action handling
    # ------------------------------------------------------------------

    def _on_toast_action(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        kind = payload.get('kind')
        raw_action = payload.get('action') or ''
        action = raw_action.split('|', 1)[0]
        _log.info("toast action received: kind=%s action=%s (raw=%s)",
                  kind, action, raw_action)

        if kind == 'session_started':
            # `discard` is the new semantic for the toast's
            # "Don't track this session" button - drop the session
            # without writing it to disk. `stop` is kept for backwards
            # compatibility with any in-flight toasts created before the
            # rename, but is now also routed to discard so the user's
            # mental model ("I clicked something at session start so this
            # play won't be saved") matches what we actually do.
            if action in ('discard', 'stop'):
                self._discard_session_via_user(payload.get('session_id'))
            elif action == 'open':
                self._focus_main_window()
            elif action == 'remap':
                self._focus_main_window()
                self._open_remap_dialog()
        elif kind == 'session_ended':
            if action == 'rate':
                self._focus_main_window()
                self._launch_feedback_for_last_session(payload.get('game'))
            elif action == 'open':
                self._focus_main_window()
        elif kind == 'match_confirmation':
            return self.confirm_pending_match(
                payload.get('detection_id') or '',
                action,
                payload,
            )
        return None

    def confirm_pending_match(
        self,
        detection_id: str,
        action: str,
        payload: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Apply a user decision on an ambiguous match."""
        info = self._pending_matches.pop(detection_id, payload)
        exe_path = info.get('exe_path') or ''
        from process_watcher import get_watcher
        watcher = get_watcher()

        if action == 'confirm':
            best = info.get('best_guess')
            if best and watcher is not None:
                watcher.remember_mapping(exe_path, best)
                # If the resolver had to drop strict mode to even see this
                # exe (it wouldn't reach the confidence-toast otherwise),
                # we don't need to whitelist its parent. But if it did
                # reach us via a known root and the user is confirming a
                # different mapping, the parent is already on the list.
        elif action == 'ignore':
            if watcher is not None and exe_path:
                watcher.add_ignore(os.path.basename(exe_path))
        elif action == 'pick':
            # Re-queue this detection for the picker dialog (we popped it
            # above on the assumption that the action was decisive; the
            # picker may end with the user cancelling, in which case we
            # want to keep the entry around so 'drain on focus' can fire
            # the toast again later).
            if detection_id and detection_id not in self._pending_matches:
                self._pending_matches[detection_id] = info
            self._focus_main_window()
            self._open_match_picker_dialog(detection_id, info)
        return None

    def drain_pending_matches_on_focus(self, parent_window) -> None:
        """Show in-app pickers for any matches the user hasn't acted on yet."""
        if not self._pending_matches:
            return
        # Currently we just re-fire toasts for items still pending. A full
        # in-app picker dialog can replace this in a follow-up.
        from notifications import notify_match_confirmation
        for det_id, info in list(self._pending_matches.items()):
            try:
                notify_match_confirmation(
                    det_id,
                    info.get('exe_basename') or '',
                    info.get('install_dir') or '',
                    info.get('best_guess'),
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning("re-fire ambiguous toast failed: %s", exc)

    # ------------------------------------------------------------------
    # Crash recovery
    # ------------------------------------------------------------------

    def recover_orphan_session_if_any(self, parent_window) -> Optional[Dict[str, Any]]:
        """If a session was active when the app crashed, offer to record it."""
        cfg = load_config()
        state = cfg.get('active_session_state')
        if not state:
            return None
        # Clear immediately so a user dismissal doesn't re-prompt next launch.
        cfg['active_session_state'] = None
        save_config(cfg)

        game_name = state.get('game_name')
        start_iso = state.get('start_iso')
        last_tick_iso = state.get('last_tick_iso') or start_iso
        if not (game_name and start_iso and last_tick_iso):
            return None
        try:
            start_dt = datetime.fromisoformat(start_iso)
            end_dt = datetime.fromisoformat(last_tick_iso)
        except Exception:
            return None
        if end_dt <= start_dt:
            return None
        duration = end_dt - start_dt

        try:
            import PySimpleGUI as sg
            from utilities import calculate_popup_center_location
            loc = calculate_popup_center_location(parent_window, popup_width=480, popup_height=180) \
                if parent_window else None
            answer = sg.popup_yes_no(
                f"It looks like a session for '{game_name}' was interrupted "
                f"(approx {format_timedelta_with_seconds(duration)}).\n\n"
                "Would you like to record it now?",
                title="Recover crashed session?",
                location=loc,
            )
        except Exception:
            answer = 'No'

        if answer != 'Yes':
            return None

        session = {
            'start': start_iso,
            'end': last_tick_iso,
            'duration': format_timedelta_with_seconds(duration),
            'pauses': [],
            'source': 'auto_watcher_recovered',
        }
        try:
            ok = add_manual_session_to_game(game_name, session, self._data(), self._data_storage())
        except Exception as exc:  # noqa: BLE001
            _log.error("recover session: add_manual_session failed: %s", exc)
            ok = False
        if ok:
            try:
                save_data(self._data(), self._filename(), self._data_storage())
            except Exception as exc:  # noqa: BLE001
                _log.error("recover session: save_data failed: %s", exc)
            return {'action': 'session_added', 'data': self._data()}
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _find_row_index(self, game_name: str) -> Optional[int]:
        for i, (_idx, row) in enumerate(self._data() or []):
            if row and row[0] == game_name:
                return i
        return None

    def _cover_path_for(self, row_index: int) -> Optional[str]:
        try:
            row = self._data()[row_index][1]
            if len(row) > 10 and isinstance(row[10], dict):
                igdb_id = row[10].get('igdb_id')
                if igdb_id:
                    from igdb_integration import cover_cache_path
                    p = cover_cache_path(int(igdb_id))
                    if os.path.exists(p):
                        return p
        except Exception:
            pass
        return None

    def _focus_main_window(self) -> None:
        win = self._window()
        if win is None:
            return
        try:
            win.bring_to_front()
        except Exception:
            try:
                win.TKroot.deiconify()  # type: ignore[attr-defined]
                win.TKroot.focus_force()  # type: ignore[attr-defined]
            except Exception:
                pass

    def _discard_session_via_user(self, session_id: Optional[str]) -> None:
        """Toast-driven "Don't track this session" handler.

        Drops the active session from the watcher (without going through
        ``_end_active_session``, so it's never persisted), clears bridge
        bookkeeping for that session_id, hides the live start-toast, and
        clears the Discord rich-presence so external observers don't see
        a phantom "Playing X" status linger.
        """
        try:
            from process_watcher import get_watcher
        except Exception as exc:  # noqa: BLE001
            _log.warning("discard: cannot import watcher: %s", exc)
            return
        watcher = get_watcher()
        if watcher is None:
            return

        snapshot = watcher.discard_current_session()
        if snapshot is None:
            _log.info("discard: no active session (toast click was stale)")
            return

        sid = session_id or snapshot.get('session_id')
        if sid:
            self._active_sessions.pop(sid, None)
            self._session_pauses.pop(sid, None)
            try:
                from notifications import dismiss_live_toast
                dismiss_live_toast(sid)
            except Exception as exc:  # noqa: BLE001
                _log.debug("discard: dismiss_live_toast failed: %s", exc)

        # Reset Discord presence to "browsing" so the rich-presence
        # status doesn't keep advertising the discarded game.
        try:
            discord = self._discord()
            if discord is not None and hasattr(discord, 'update_presence_browsing'):
                discord.update_presence_browsing()
        except Exception as exc:  # noqa: BLE001
            _log.debug("discard: presence-browsing failed: %s", exc)

        _log.info("discard: dropped session=%s game=%r (user opted out)",
                  snapshot.get('session_id'), snapshot.get('game_name'))

        try:
            import PySimpleGUI as sg
            sg.popup_quick_message(
                f"OK - this {snapshot.get('game_name') or 'session'} run won't be tracked.",
                keep_on_top=True, background_color='#2d6a4f',
                text_color='white')
        except Exception:
            pass

    def _retitle_active_session_to(self, new_game_name: str) -> bool:
        """Apply a "Wrong game?" correction to the currently running session.

        Calls into the watcher to rename the session in place (preserving
        session_id, start time, and accumulated elapsed seconds), repoints
        our ``_active_sessions`` map at the new library row, refreshes the
        Discord rich-presence so external observers see the corrected
        title, and replaces the live "Now tracking" toast. Returns True on
        success, False if there was no active session or the new title
        isn't in the library.
        """
        new_row = self._find_row_index(new_game_name)
        if new_row is None:
            _log.warning("retitle: %r is not in the library", new_game_name)
            return False
        try:
            from process_watcher import get_watcher
        except Exception as exc:  # noqa: BLE001
            _log.warning("retitle: cannot import watcher: %s", exc)
            return False
        watcher = get_watcher()
        if watcher is None:
            return False

        snapshot = watcher.retitle_active_session(new_game_name)
        if snapshot is None:
            return False

        sid = snapshot['session_id']
        self._active_sessions[sid] = new_row

        # Update Discord presence to the corrected title without resetting
        # the start time (the user has been playing this same process for
        # a while; we don't want the "Elapsed" counter to reset to 0).
        try:
            discord = self._discord()
            if discord is not None:
                start_dt = None
                try:
                    start_iso = snapshot.get('start_iso')
                    if start_iso:
                        start_dt = datetime.fromisoformat(start_iso)
                except Exception:
                    start_dt = None
                row = self._data()[new_row][1]
                platform = row[2] if len(row) > 2 else None
                discord.update_presence_playing(
                    new_game_name,
                    session_start_time=start_dt,
                    platform=platform,
                )
        except Exception as exc:  # noqa: BLE001
            _log.warning("retitle: Discord presence update failed: %s", exc)

        # Replace the live start-toast with one for the corrected title so
        # the user gets visible confirmation in the notifications area.
        try:
            from notifications import notify_session_retitled
            cover_path = self._cover_path_for(new_row)
            notify_session_retitled(sid, new_game_name, cover_path)
        except Exception as exc:  # noqa: BLE001
            _log.warning("retitle: live-toast replace failed: %s", exc)

        return True

    def _open_remap_dialog(self) -> None:
        """Picker dialog for the toast's "Wrong game?" action.

        Shows the user what we currently believe the running process is, lets
        them pick the correct title from the library (or mark the exe as
        never-track), then:
          1. Discards the active (wrong-titled) session so it isn't recorded.
          2. Forgets the auto-learned mapping that misled us.
          3. Saves the user's correction (new mapping or ignore-list entry).
        The watcher will re-detect the still-running process on its next tick
        and start a fresh session under the correct title.
        """
        try:
            from process_watcher import get_watcher
        except Exception as exc:  # noqa: BLE001
            _log.warning("remap dialog: cannot import watcher: %s", exc)
            return
        watcher = get_watcher()
        if watcher is None:
            _log.warning("remap dialog: no watcher instance")
            return

        snapshot = watcher.get_active_session_state()
        if not snapshot:
            self._show_simple_info(
                "Wrong game?",
                "No game is currently being tracked, so there's nothing to remap.")
            return

        wrong_name = snapshot.get('game_name') or '(unknown)'
        exe_path = snapshot.get('exe_path') or ''
        exe_basename = os.path.basename(exe_path) if exe_path else '(unknown)'

        # Library names, sorted, deduped, case-preserving.
        seen_lower = set()
        library_names: List[str] = []
        for entry in self.build_library_snapshot():
            n = entry.get('name')
            if not n:
                continue
            key = n.lower()
            if key in seen_lower:
                continue
            seen_lower.add(key)
            library_names.append(n)
        library_names.sort(key=lambda s: s.lower())

        # Try to preselect by partial-match against the exe basename, so the
        # combobox starts on a plausible candidate when possible.
        preselect = ''
        if exe_basename:
            stem = os.path.splitext(exe_basename)[0].lower()
            for n in library_names:
                if stem and stem in n.lower().replace(' ', ''):
                    preselect = n
                    break

        try:
            import PySimpleGUI as sg
            from utilities import calculate_popup_center_location
        except Exception as exc:  # noqa: BLE001
            _log.warning("remap dialog: PySimpleGUI unavailable: %s", exc)
            return

        layout = [
            [sg.Text("We're currently tracking this process as:")],
            [sg.Text(wrong_name, font=('Arial', 11, 'bold'))],
            [sg.Text(f"Process: {exe_basename}", text_color='#555555')],
            [sg.HorizontalSeparator()],
            [sg.Text("Pick the correct game from your library:")],
            [sg.Combo(library_names, default_value=preselect,
                      key='-REMAP-PICK-', size=(48, 1),
                      enable_events=False)],
            [sg.Text(
                "Tip: if the right title isn't listed, add it via 'Add Entry'\n"
                "first, then re-launch the game.",
                font=('Arial', 8), text_color='#555555')],
            [sg.HorizontalSeparator()],
            [sg.Push(),
             sg.Button("Save mapping", key='-REMAP-SAVE-'),
             sg.Button("Don't track this process", key='-REMAP-IGNORE-'),
             sg.Button("Cancel", key='-REMAP-CANCEL-')],
        ]
        parent = self._window()
        try:
            location = calculate_popup_center_location(parent, 520, 280) if parent else (None, None)
        except Exception:
            location = (None, None)
        win = sg.Window(
            "Wrong game?",
            layout,
            modal=True,
            keep_on_top=True,
            finalize=True,
            location=location,
        )

        chosen: Optional[str] = None
        ignore = False
        while True:
            ev, vals = win.read()
            if ev in (sg.WIN_CLOSED, '-REMAP-CANCEL-'):
                break
            if ev == '-REMAP-SAVE-':
                pick = (vals.get('-REMAP-PICK-') or '').strip()
                if not pick:
                    sg.popup_quick_message(
                        "Pick a game first.",
                        keep_on_top=True, background_color='#444',
                        text_color='white')
                    continue
                if pick.lower() not in seen_lower:
                    sg.popup_quick_message(
                        "That title isn't in your library yet. Add it via 'Add Entry'.",
                        keep_on_top=True, background_color='#444',
                        text_color='white')
                    continue
                # Use the canonical-cased name from the library.
                for n in library_names:
                    if n.lower() == pick.lower():
                        chosen = n
                        break
                break
            if ev == '-REMAP-IGNORE-':
                ignore = True
                break
        win.close()

        if not chosen and not ignore:
            _log.info("remap dialog: cancelled by user (was tracking %r)",
                      wrong_name)
            return

        # Apply the user's decision. Order matters:
        #   1) Update the persisted exe -> game mapping (or ignore-list).
        #      Forget BEFORE remember so collisions caused by case /
        #      trailing-slash path differences resolve cleanly.
        #   2) Then either retitle the live session in place (so the user
        #      doesn't have to restart the game for the correction to
        #      take effect) or discard it (when the user picked "Don't
        #      track this process").
        try:
            if exe_path:
                watcher.forget_mapping(exe_path)

            if ignore:
                # The user said this exe is not a game - drop the live
                # session and put the basename on the never-track list.
                watcher.discard_current_session()
                if exe_basename:
                    watcher.add_ignore(exe_basename)
                _log.info("remap dialog: user marked %s as never-track",
                          exe_basename)
            else:
                # Retitle the live session so the elapsed time keeps
                # accumulating under the correct title - no game restart
                # required.
                if exe_path and chosen:
                    watcher.remember_mapping(exe_path, chosen)
                ok = self._retitle_active_session_to(chosen)
                if not ok:
                    # Retitle couldn't apply (e.g. the session ended
                    # while the dialog was open). Fall back to just
                    # saving the mapping for next launch.
                    _log.warning(
                        "remap dialog: retitle failed; mapping saved for "
                        "next launch only")
                else:
                    _log.info("remap dialog: live session retitled "
                              "%r -> %r (exe=%s)",
                              wrong_name, chosen, exe_basename)
        except Exception as exc:  # noqa: BLE001
            _log.warning("remap dialog: applying decision failed: %s", exc)
            return

        # Friendly confirmation in the main window.
        try:
            import PySimpleGUI as sg
            if ignore:
                sg.popup_quick_message(
                    f"Got it. {exe_basename} will no longer be auto-tracked.",
                    keep_on_top=True, background_color='#2d6a4f',
                    text_color='white')
            else:
                sg.popup_quick_message(
                    f"Now tracking as {chosen}. Your elapsed time so far "
                    f"is preserved.",
                    keep_on_top=True, background_color='#2d6a4f',
                    text_color='white')
        except Exception:
            pass

    def _open_match_picker_dialog(
        self,
        detection_id: str,
        info: Dict[str, Any],
    ) -> None:
        """Picker dialog for the toast's "Pick another" action.

        The watcher saw an unrecognized executable and its best fuzzy
        guess against the user's library scored below the auto-confirm
        threshold. The toast offered "Yes, that's it / Pick another /
        Never for this exe"; this method handles the middle option by
        showing a list of every library title and letting the user
        commit a mapping (or mark the exe as never-track).

        Mirrors the per-game ``Link Executable`` flow: same single-exe
        vs. install-folder scope choice, same auto-whitelist of the
        parent folder so strict mode lets the path through, same
        confirmation banner. Once a mapping is saved the watcher's
        Layer 1 lookup picks it up on the next tick - no game restart
        required (unlike the toast's "Yes" path which only takes effect
        on the next launch).
        """
        try:
            from process_watcher import get_watcher
        except Exception as exc:  # noqa: BLE001
            _log.warning("pick dialog: cannot import watcher: %s", exc)
            return
        watcher = get_watcher()
        if watcher is None:
            _log.warning("pick dialog: no watcher instance")
            return

        exe_path = info.get('exe_path') or ''
        exe_basename = info.get('exe_basename') \
            or (os.path.basename(exe_path) if exe_path else '(unknown)')
        install_dir = info.get('install_dir') or ''
        best_guess = info.get('best_guess')

        # Build the deduped, sorted library names list. Try a partial
        # match against the exe stem to preselect a plausible candidate
        # so the user gets a useful starting point rather than an empty
        # combobox - same UX trick the Wrong-game? remap dialog uses.
        seen_lower = set()
        library_names: List[str] = []
        for entry in self.build_library_snapshot():
            n = entry.get('name')
            if not n:
                continue
            key = n.lower()
            if key in seen_lower:
                continue
            seen_lower.add(key)
            library_names.append(n)
        library_names.sort(key=lambda s: s.lower())

        preselect = ''
        if best_guess and best_guess in library_names:
            preselect = best_guess
        elif exe_basename:
            stem = os.path.splitext(exe_basename)[0].lower()
            stripped_stem = stem.replace(' ', '')
            for n in library_names:
                if stem and stripped_stem in n.lower().replace(' ', ''):
                    preselect = n
                    break

        try:
            import PySimpleGUI as sg
            from utilities import calculate_popup_center_location
        except Exception as exc:  # noqa: BLE001
            _log.warning("pick dialog: PySimpleGUI unavailable: %s", exc)
            return

        guess_line = (f"Best guess: {best_guess} (low confidence)"
                      if best_guess else
                      "No close match in your library.")

        layout = [
            [sg.Text("Detected an unrecognized game",
                     font=('Arial', 12, 'bold'))],
            [sg.Text(f"Process: {exe_basename}",
                     text_color='#555555')],
            [sg.Text(f"Install folder: {install_dir or '(unknown)'}",
                     text_color='#555555')],
            [sg.Text(guess_line, text_color='#555555')],
            [sg.HorizontalSeparator()],
            [sg.Text("Which game in your library is this?")],
            [sg.Combo(library_names, default_value=preselect,
                      key='-PICK-NAME-', size=(48, 1),
                      enable_events=False)],

            [sg.Frame("Match scope", [
                [sg.Radio(
                    "Just this executable",
                    group_id='-PICK-SCOPE-', default=True,
                    key='-PICK-SCOPE-EXE-',
                    tooltip=("Only this specific .exe will be tracked\n"
                             "as the picked game."))],
                [sg.Radio(
                    "Any executable in this folder",
                    group_id='-PICK-SCOPE-', default=False,
                    key='-PICK-SCOPE-DIR-',
                    tooltip=("Treat every .exe under the install folder\n"
                             "as this game. Useful for MMOs and games\n"
                             "whose launcher .exe spawns a separate\n"
                             "renderer .exe."))],
            ], expand_x=True)],

            [sg.Text(
                "Tip: if the right title isn't listed, add it via 'Add Entry'\n"
                "first, then re-launch the game.",
                font=('Arial', 8), text_color='#555555')],
            [sg.HorizontalSeparator()],
            [sg.Push(),
             sg.Button("Save & track", key='-PICK-SAVE-'),
             sg.Button("Don't track this exe", key='-PICK-IGNORE-'),
             sg.Button("Cancel", key='-PICK-CANCEL-')],
        ]

        parent = self._window()
        try:
            location = calculate_popup_center_location(
                parent, 560, 360) if parent else (None, None)
        except Exception:
            location = (None, None)
        win = sg.Window(
            "Pick the matching game",
            layout,
            modal=True,
            keep_on_top=True,
            finalize=True,
            icon='gameslisticon.ico',
            location=location,
        )

        chosen: Optional[str] = None
        ignore = False
        scope_dir = False
        while True:
            ev, vals = win.read()
            if ev in (sg.WIN_CLOSED, '-PICK-CANCEL-'):
                break
            if ev == '-PICK-SAVE-':
                pick = (vals.get('-PICK-NAME-') or '').strip()
                if not pick:
                    sg.popup_quick_message(
                        "Pick a game first.",
                        keep_on_top=True, background_color='#444',
                        text_color='white')
                    continue
                if pick.lower() not in seen_lower:
                    sg.popup_quick_message(
                        "That title isn't in your library yet. "
                        "Add it via 'Add Entry'.",
                        keep_on_top=True, background_color='#444',
                        text_color='white')
                    continue
                # Use the canonical-cased library name.
                for n in library_names:
                    if n.lower() == pick.lower():
                        chosen = n
                        break
                scope_dir = bool(vals.get('-PICK-SCOPE-DIR-'))
                break
            if ev == '-PICK-IGNORE-':
                ignore = True
                break
        win.close()

        if not chosen and not ignore:
            # User cancelled - leave the entry on _pending_matches so
            # drain_pending_matches_on_focus can re-fire the toast later.
            _log.info("pick dialog: cancelled (was %s)", exe_basename)
            return

        # Decision committed - drop this entry from the queue.
        self._pending_matches.pop(detection_id, None)

        try:
            if ignore and exe_basename:
                watcher.add_ignore(exe_basename)
                _log.info("pick dialog: %s added to ignore list",
                          exe_basename)
            elif chosen:
                # Save the appropriate mapping flavor and whitelist the
                # parent folder so strict mode lets future detections
                # through. Same defensive pattern used by the per-game
                # Link Executable dialog.
                if scope_dir and install_dir:
                    watcher.remember_installdir_mapping(install_dir, chosen)
                    _log.info("pick dialog: installdir mapping %s -> %r",
                              install_dir, chosen)
                elif exe_path:
                    watcher.remember_mapping(exe_path, chosen)
                    _log.info("pick dialog: exe mapping %s -> %r",
                              exe_path, chosen)
                if exe_path:
                    watcher.add_user_root(os.path.dirname(exe_path))

                # Force the resolver to re-examine the still-running
                # process(es). Without this, the user would have to quit
                # and re-launch the game for the new mapping to take
                # effect - cached pids are otherwise never reconsidered.
                if scope_dir and install_dir:
                    watcher.recheck_install_dir(install_dir)
                elif exe_path:
                    watcher.recheck_exe(exe_path)

                # If the user mapped this exe to a real game, any OTHER
                # pending detection for the same exe path is now
                # redundant - the next watcher tick will resolve them
                # via Layer 1. Drop them from the queue so we don't
                # re-fire stale toasts on focus.
                self._resolve_twin_pending_matches(exe_path, chosen)
        except Exception as exc:  # noqa: BLE001
            _log.warning("pick dialog: applying decision failed: %s", exc)
            return

        # Friendly confirmation banner.
        try:
            import PySimpleGUI as sg
            if ignore:
                sg.popup_quick_message(
                    f"Got it. {exe_basename} will no longer be auto-tracked.",
                    keep_on_top=True, background_color='#2d6a4f',
                    text_color='white')
            else:
                scope_label = ("the install folder"
                               if scope_dir else exe_basename)
                sg.popup_quick_message(
                    f"Mapped {scope_label} to {chosen}. The next launch "
                    f"will be tracked automatically.",
                    keep_on_top=True, background_color='#2d6a4f',
                    text_color='white')
        except Exception:
            pass

    def _resolve_twin_pending_matches(
        self,
        exe_path: str,
        chosen: str,
    ) -> None:
        """Drop any other pending matches that share `exe_path`.

        The watcher can fire -MATCH-AMBIGUOUS- twice for the same exe
        when a game spawns a sibling process (or when an emulator
        forks). Once the user picks a real mapping for one of them, the
        rest are about to resolve through Layer 1 on the next tick;
        re-firing their toasts on focus would just confuse the user
        with stale prompts pointing at a path that's already mapped.
        """
        if not exe_path:
            return
        target = os.path.normcase(os.path.normpath(exe_path))
        twins = []
        for det_id, payload in list(self._pending_matches.items()):
            other = payload.get('exe_path') or ''
            if not other:
                continue
            if os.path.normcase(os.path.normpath(other)) == target:
                twins.append(det_id)
        for det_id in twins:
            self._pending_matches.pop(det_id, None)
        if twins:
            _log.info(
                "pick dialog: cleared %d duplicate pending match(es) "
                "for %s now mapped to %r",
                len(twins), os.path.basename(exe_path), chosen)

    def _show_simple_info(self, title: str, message: str) -> None:
        """Tiny wrapper so callers don't repeat the import dance."""
        try:
            import PySimpleGUI as sg
            sg.popup(message, title=title, keep_on_top=True)
        except Exception:
            pass

    def _launch_feedback_for_last_session(self, game_name: Optional[str]) -> None:
        if not game_name:
            return
        row_index = self._find_row_index(game_name)
        if row_index is None:
            return
        data = self._data()
        try:
            sessions = data[row_index][1][7]
        except Exception:
            sessions = None
        if not sessions:
            return
        last_session = sessions[-1]
        try:
            from session_ui import show_session_feedback_popup
            existing = last_session.get('feedback')
            feedback = show_session_feedback_popup(existing, parent_window=self._window())
            if feedback:
                last_session['feedback'] = feedback
                save_data(self._data(), self._filename(), self._data_storage())
        except Exception as exc:  # noqa: BLE001
            _log.warning("launching feedback popup failed: %s", exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# How fresh a console game's last_played has to be to count as
# "Recently played" in the tray's quick-launch menu. Anything older
# than this falls through to the status backlog buckets so the menu's
# top entries are always things the user is actively engaging with.
_RECENT_CONSOLE_DAYS = 30




def _parse_last_played(raw) -> Optional[datetime]:
    """Best-effort ``last_played`` parser tolerant of historical formats.

    Older library rows may carry the timestamp as ``YYYY-MM-DD HH:MM:SS``,
    ISO-8601 with a ``T`` separator, or just a date. Anything we can't
    interpret returns ``None`` so the caller can demote that game to a
    later sort group rather than crash.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _parse_release_date(raw) -> Optional[datetime]:
    """Parse the library's ``Release`` column, which is stored as YYYY-MM-DD.

    Some entries carry a partial date ("1998") or "TBA" / blank; those
    fail strict parsing and we return ``None``, which our sort uses as
    "treat as the far future" so released-and-known titles always
    sort before unknown ones within the same status group.
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%d', '%Y-%m', '%Y'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _humanize_duration(duration_str: str) -> str:
    """Convert HH:MM:SS into a friendly string for toasts."""
    try:
        h, m, s = duration_str.split(':')
        h, m, s = int(h), int(m), int(s)
    except Exception:
        return duration_str
    parts = []
    if h:
        parts.append(f"{h}h")
    if m or h:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return ' '.join(parts)
