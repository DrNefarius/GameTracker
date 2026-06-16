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
import time
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
        notifier=None,
    ) -> None:
        self._window = window_provider
        self._data = data_provider
        self._data_storage = data_storage_provider
        self._filename = filename_provider
        self._discord = discord_provider
        # UI-agnostic notifier (core.notifier.UINotifier). The bridge routes all
        # of its UI interactions through it; the Flet UI always supplies one.
        self._notifier = notifier

        # Active session ids -> the row index they are accumulating on.
        self._active_sessions: Dict[str, int] = {}
        # detection_id -> payload, queued while user wasn't around to confirm.
        self._pending_matches: Dict[str, Dict[str, Any]] = {}
        # detection_ids whose match-picker dialog is currently open. The
        # focus-drain must NOT re-fire these (the user is already resolving them),
        # otherwise focusing the window to open the picker re-fires the toast in
        # an endless loop.
        self._active_pickers: set = set()
        # detection_id -> monotonic time of its last toast, so the focus-drain
        # rate-limits re-fires (a focused window + toast can otherwise re-trigger
        # the focus event repeatedly, spamming the toast).
        self._last_refire: Dict[str, float] = {}
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
        # Collapse to ONE pending entry per exe: the watcher re-sees a running
        # process every few seconds, and without this each detection created a
        # fresh entry (new uuid) + toast, which then all got re-fired on focus.
        exe_path = (payload.get('exe_path') or '').strip().lower()
        detection_id = payload.get('detection_id')
        if exe_path:
            for did, info in self._pending_matches.items():
                if (info.get('exe_path') or '').strip().lower() == exe_path:
                    detection_id = did      # reuse the existing entry for this exe
                    break
        if not detection_id:
            detection_id = uuid.uuid4().hex

        already_pending = detection_id in self._pending_matches
        self._pending_matches[detection_id] = payload

        # Only toast on the FIRST detection of this exe; a repeat detection of an
        # already-queued exe must not fire another toast (the focus-drain handles
        # resurfacing, rate-limited).
        if already_pending:
            return None
        try:
            from notifications import notify_match_confirmation
            notify_match_confirmation(
                detection_id,
                payload.get('exe_basename') or '',
                payload.get('install_dir') or '',
                payload.get('best_guess'),
                exe_path=payload.get('exe_path'),
            )
            self._last_refire[detection_id] = time.monotonic()
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
            elif action in ('dismiss', 'close', 'open', 'default'):
                # `close` / `open` = older toasts. `default` = body tap when
                # the shell does not forward ``toast.launch`` as ``arguments``.
                try:
                    from notifications import dismiss_live_toast
                    dismiss_live_toast(payload.get('session_id') or '')
                except Exception:
                    pass
        elif kind == 'session_ended':
            if action in ('dismiss', 'close', 'open'):
                # `close` / `open` are older toasts; all three only clear the
                # notification (no app focus).
                try:
                    from notifications import dismiss_live_toast
                    dismiss_live_toast(payload.get('session_id') or '')
                except Exception:
                    pass
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
                if exe_path:
                    watcher.add_user_root(os.path.dirname(exe_path))
                # Start tracking the already-running process right away instead
                # of waiting for a relaunch.
                if exe_path:
                    watcher.force_track_exe(exe_path, best)
        elif action == 'ignore':
            # The exe to ignore is a basename. Prefer the full path from the
            # queued entry, but fall back to the basename the toast itself
            # carries ('exe' from the toast payload, or 'exe_basename' from the
            # queued entry) so "Never for this exe" still persists when the
            # in-memory entry is gone - e.g. the toast was clicked from the
            # Action Center after the detection cleared, or after an app restart.
            basename = (os.path.basename(exe_path) if exe_path
                        else (info.get('exe') or info.get('exe_basename') or '').strip())
            if watcher is not None and basename:
                watcher.add_ignore(basename)
        return None

    def begin_match_pick(self, detection_id: str) -> None:
        """Mark a detection as 'being picked' so the focus-drain won't re-fire it.

        Call this BEFORE focusing the window to open the picker, so the focus
        event doesn't re-fire the very toast we're handling (the endless-loop bug).
        """
        if detection_id:
            self._active_pickers.add(detection_id)

    def cancel_match_pick(self, detection_id: str) -> None:
        """Picker dismissed without a decision: stop treating it as active so it
        can be re-fired on a later focus, but don't drop the queued detection."""
        self._active_pickers.discard(detection_id)

    #: Minimum seconds between re-firing the SAME detection's toast on focus.
    _REFIRE_COOLDOWN_SEC = 60.0

    def drain_pending_matches_on_focus(self, parent_window) -> None:
        """Re-surface toasts for matches the user hasn't acted on yet.

        Rate-limited and active-picker-aware: a focused window plus a toast can
        re-trigger the focus event repeatedly, so without these guards the same
        toast would spam endlessly.
        """
        if not self._pending_matches:
            return
        from notifications import notify_match_confirmation
        now = time.monotonic()
        for det_id, info in list(self._pending_matches.items()):
            # Skip detections whose picker is open right now (the user is already
            # resolving them) ...
            if det_id in self._active_pickers:
                continue
            # ... and those re-fired very recently (breaks the focus<->toast loop).
            if now - self._last_refire.get(det_id, 0.0) < self._REFIRE_COOLDOWN_SEC:
                continue
            try:
                notify_match_confirmation(
                    det_id,
                    info.get('exe_basename') or '',
                    info.get('install_dir') or '',
                    info.get('best_guess'),
                    exe_path=info.get('exe_path'),
                )
                self._last_refire[det_id] = now
            except Exception as exc:  # noqa: BLE001
                _log.warning("re-fire ambiguous toast failed: %s", exc)

    # ------------------------------------------------------------------
    # Crash recovery
    # ------------------------------------------------------------------

    def get_orphan_recovery_context(self) -> Optional[Dict[str, Any]]:
        """Read (and clear) the persisted active-session state, if recoverable.

        GUI-free half of crash recovery: returns ``{game_name, start_iso,
        last_tick_iso, duration_str}`` when a usable orphan session is found,
        else ``None``. The config entry is cleared immediately so a user
        dismissal doesn't re-prompt next launch. The caller (any UI) asks the
        user whether to record it, then passes the same dict to
        :meth:`apply_orphan_recovery`.
        """
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
        return {
            'game_name': game_name,
            'start_iso': start_iso,
            'last_tick_iso': last_tick_iso,
            'duration_str': format_timedelta_with_seconds(end_dt - start_dt),
        }

    def apply_orphan_recovery(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Persist a recovered crash session from a context dict.

        Returns a ``{'action': 'session_added', 'data': ...}`` envelope on
        success (so the caller can refresh its views) or ``None``.
        """
        if not ctx:
            return None
        game_name = ctx.get('game_name')
        session = {
            'start': ctx.get('start_iso'),
            'end': ctx.get('last_tick_iso'),
            'duration': ctx.get('duration_str'),
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

    def _sorted_library_names(self) -> List[str]:
        """Deduped (case-insensitive), case-preserving, alpha-sorted library names.

        Shared by the remap / match-picker dialogs so every picker shows the
        same option set regardless of which UI renders it.
        """
        seen_lower = set()
        names: List[str] = []
        for entry in self.build_library_snapshot():
            n = entry.get('name')
            if not n:
                continue
            key = n.lower()
            if key in seen_lower:
                continue
            seen_lower.add(key)
            names.append(n)
        names.sort(key=lambda s: s.lower())
        return names

    @staticmethod
    def _preselect_from_exe(exe_basename: str, library_names: List[str]) -> str:
        """Best-effort partial match of the exe stem against library titles.

        Gives the picker a plausible starting selection so the user usually
        only has to confirm. Returns '' when nothing looks close.
        """
        if not exe_basename:
            return ''
        stem = os.path.splitext(exe_basename)[0].lower()
        stripped = stem.replace(' ', '')
        for n in library_names:
            if stem and stripped in n.lower().replace(' ', ''):
                return n
        return ''

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

        msg = f"OK - this {snapshot.get('game_name') or 'session'} run won't be tracked."
        if self._notifier is not None:
            try:
                self._notifier.notify("Session not tracked", msg)
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

    def get_remap_context(self) -> Optional[Dict[str, Any]]:
        """Data needed to render the "Wrong game?" remap dialog.

        Returns ``{wrong_name, exe_path, exe_basename, library_names,
        preselect}`` when a session is currently being tracked, else ``None``
        (no watcher OR nothing tracked - the caller shows an info message).
        GUI-free so any UI can build its own picker from it.
        """
        try:
            from process_watcher import get_watcher
        except Exception as exc:  # noqa: BLE001
            _log.warning("remap ctx: cannot import watcher: %s", exc)
            return None
        watcher = get_watcher()
        if watcher is None:
            return None
        snapshot = watcher.get_active_session_state()
        if not snapshot:
            return None

        exe_path = snapshot.get('exe_path') or ''
        exe_basename = os.path.basename(exe_path) if exe_path else '(unknown)'
        library_names = self._sorted_library_names()
        return {
            'wrong_name': snapshot.get('game_name') or '(unknown)',
            'exe_path': exe_path,
            'exe_basename': exe_basename,
            'library_names': library_names,
            'preselect': self._preselect_from_exe(exe_basename, library_names),
        }

    def apply_remap_decision(
        self,
        ctx: Dict[str, Any],
        chosen: Optional[str] = None,
        ignore: bool = False,
    ) -> Optional[str]:
        """Apply a "Wrong game?" decision collected from any UI.

        ``chosen`` is a canonical library name to retitle the live session to;
        ``ignore=True`` means the user marked the exe as never-track. Forgets
        the misleading auto-learned mapping first, then either retitles the
        live session in place (preserving elapsed time) or discards it and
        adds the exe to the ignore list. Returns a short confirmation message
        for the UI to surface, or ``None`` when nothing was applied.
        """
        exe_path = (ctx or {}).get('exe_path') or ''
        exe_basename = (ctx or {}).get('exe_basename') or '(unknown)'
        if not chosen and not ignore:
            _log.info("remap: cancelled by user (was tracking %r)",
                      (ctx or {}).get('wrong_name'))
            return None
        try:
            from process_watcher import get_watcher
        except Exception as exc:  # noqa: BLE001
            _log.warning("remap apply: cannot import watcher: %s", exc)
            return None
        watcher = get_watcher()
        if watcher is None:
            return None

        # Order matters: forget the bad mapping BEFORE remembering the new one
        # so case / trailing-slash path collisions resolve cleanly.
        try:
            if exe_path:
                watcher.forget_mapping(exe_path)

            if ignore:
                watcher.discard_current_session()
                if exe_path and exe_basename:
                    watcher.add_ignore(exe_basename)
                _log.info("remap: user marked %s as never-track", exe_basename)
                return f"Got it. {exe_basename} will no longer be auto-tracked."

            if exe_path and chosen:
                watcher.remember_mapping(exe_path, chosen)
            ok = self._retitle_active_session_to(chosen)
            if not ok:
                _log.warning("remap: retitle failed; mapping saved for "
                             "next launch only")
            else:
                _log.info("remap: live session retitled -> %r (exe=%s)",
                          chosen, exe_basename)
            return (f"Now tracking as {chosen}. Your elapsed time so far "
                    f"is preserved.")
        except Exception as exc:  # noqa: BLE001
            _log.warning("remap apply: applying decision failed: %s", exc)
            return None

    def get_match_pick_context(
        self,
        detection_id: str,
        payload: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Data needed to render the "Pick the matching game" dialog.

        Reads the ambiguous-detection info (preferring the richer queued
        ``_pending_matches`` entry over the leaner toast ``payload``) and keeps
        it queued so a cancelled picker can still be re-fired on focus. Returns
        ``{detection_id, exe_path, exe_basename, install_dir, best_guess,
        library_names, preselect}`` or ``None`` if there is no watcher.
        """
        try:
            from process_watcher import get_watcher
        except Exception as exc:  # noqa: BLE001
            _log.warning("pick ctx: cannot import watcher: %s", exc)
            return None
        if get_watcher() is None:
            _log.warning("pick ctx: no watcher instance")
            return None

        info = self._pending_matches.get(detection_id) or payload or {}
        # Keep it queued until a decision is committed (cancel re-fires later),
        # and mark it active so the focus-drain won't re-fire it while the picker
        # is open.
        if detection_id:
            if detection_id not in self._pending_matches:
                self._pending_matches[detection_id] = info
            self._active_pickers.add(detection_id)

        exe_path = info.get('exe_path') or ''
        exe_basename = info.get('exe_basename') \
            or (os.path.basename(exe_path) if exe_path else '(unknown)')
        best_guess = info.get('best_guess')
        library_names = self._sorted_library_names()

        if best_guess and best_guess in library_names:
            preselect = best_guess
        else:
            preselect = self._preselect_from_exe(exe_basename, library_names)

        return {
            'detection_id': detection_id,
            'exe_path': exe_path,
            'exe_basename': exe_basename,
            'install_dir': info.get('install_dir') or '',
            'best_guess': best_guess,
            'library_names': library_names,
            'preselect': preselect,
        }

    def apply_match_pick_decision(
        self,
        ctx: Dict[str, Any],
        chosen: Optional[str] = None,
        ignore: bool = False,
        scope_dir: bool = False,
    ) -> Optional[str]:
        """Apply a match-picker decision collected from any UI.

        ``chosen`` is a canonical library name; ``ignore=True`` marks the exe
        as never-track; ``scope_dir=True`` maps the whole install folder rather
        than just the one exe. Saves the mapping, whitelists the parent folder
        for strict mode, forces the resolver to re-examine the running
        process(es), and drops duplicate pending detections for the same exe.
        Returns a confirmation message for the UI, or ``None``. A cancel
        (neither chosen nor ignore) leaves the detection queued for re-fire.
        """
        detection_id = (ctx or {}).get('detection_id') or ''
        exe_path = (ctx or {}).get('exe_path') or ''
        exe_basename = (ctx or {}).get('exe_basename') or '(unknown)'
        install_dir = (ctx or {}).get('install_dir') or ''
        if not chosen and not ignore:
            _log.info("pick: cancelled (was %s)", exe_basename)
            return None
        try:
            from process_watcher import get_watcher
        except Exception as exc:  # noqa: BLE001
            _log.warning("pick apply: cannot import watcher: %s", exc)
            return None
        watcher = get_watcher()
        if watcher is None:
            return None

        # Decision committed - drop this entry from the queue (and stop treating
        # its picker as active).
        self._pending_matches.pop(detection_id, None)
        self._active_pickers.discard(detection_id)

        try:
            if ignore and exe_basename:
                watcher.add_ignore(exe_basename)
                _log.info("pick: %s added to ignore list", exe_basename)
                return f"Got it. {exe_basename} will no longer be auto-tracked."
            if chosen:
                # Save the appropriate mapping flavor and whitelist the parent
                # folder so strict mode lets future detections through.
                if scope_dir and install_dir:
                    watcher.remember_installdir_mapping(install_dir, chosen)
                    _log.info("pick: installdir mapping %s -> %r",
                              install_dir, chosen)
                elif exe_path:
                    watcher.remember_mapping(exe_path, chosen)
                    _log.info("pick: exe mapping %s -> %r", exe_path, chosen)
                if exe_path:
                    watcher.add_user_root(os.path.dirname(exe_path))

                # Start tracking the already-running process IMMEDIATELY. If it
                # isn't running anymore (or a session is already active), fall
                # back to re-examining on the next tick / relaunch.
                started = bool(exe_path) and watcher.force_track_exe(exe_path, chosen)
                if not started:
                    if scope_dir and install_dir:
                        watcher.recheck_install_dir(install_dir)
                    elif exe_path:
                        watcher.recheck_exe(exe_path)

                # Drop any twin pending detections for the same exe.
                self._resolve_twin_pending_matches(exe_path, chosen)

                scope_label = "the install folder" if scope_dir else exe_basename
                if started:
                    return f"Now tracking {chosen}."
                return (f"Mapped {scope_label} to {chosen}. It'll be tracked "
                        f"the next time it's running.")
        except Exception as exc:  # noqa: BLE001
            _log.warning("pick apply: applying decision failed: %s", exc)
            return None
        return None

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
        if self._notifier is not None:
            try:
                self._notifier.info(title, message)
            except Exception:
                pass

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
