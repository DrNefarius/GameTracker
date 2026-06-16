"""
Background process watcher.

Polls the OS process table on a worker thread, attributes new game processes
to library entries via a layered resolver, and emits session start / end /
ambiguous-match events into the PySimpleGUI event loop via
``window.write_event_value(...)``.

The watcher NEVER mutates the games data directly. Phase D (the session
bridge in main.py) is responsible for translating these events into
``update_time_and_date`` calls. Keeping this module event-only also makes it
trivial to unit-test the resolver in isolation.

Public API:
    initialize_watcher(window, library_provider, enabled=...) -> ProcessWatcher
    get_watcher() -> Optional[ProcessWatcher]
    cleanup_watcher() -> None

The watcher fires the following events:
    -PROCESS-DETECTED-   payload: {session_id, game_name, exe_path, install_dir,
                                   start_time_iso, store, store_id}
    -PROCESS-ENDED-      payload: {session_id, game_name, end_time_iso,
                                   duration_str, pauses}
    -MATCH-AMBIGUOUS-    payload: {detection_id, exe_path, install_dir,
                                   exe_basename, best_guess}
    -WATCHER-STATUS-     payload: {state: 'idle'|'tracking'|'paused', game_name?}
    -WATCHER-IDLE-PAUSE- payload: {session_id, paused: bool}
"""

from __future__ import annotations

import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Set, Tuple

from watcher_log import (
    core_logger, resolver_logger, state_logger,
)

_log = core_logger()
_resolver_log = resolver_logger()
_state_log = state_logger()

try:
    import psutil  # type: ignore
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False
    _log.warning("psutil not installed - process watcher disabled")

try:
    from rapidfuzz import fuzz  # type: ignore
    _RAPIDFUZZ_AVAILABLE = True
except ImportError:
    _RAPIDFUZZ_AVAILABLE = False
    _log.warning("rapidfuzz not installed - fuzzy matching disabled")

from config import load_config, save_config
from constants import (
    IGNORED_PATH_FRAGMENTS,
    IGNORED_PROCESS_NAMES,
    WATCHER_DEFAULT_IDLE_MINUTES,
    WATCHER_DEFAULT_FOREGROUND_GRACE_SEC,
    WATCHER_DEFAULT_ROOTS,
    WATCHER_OPPORTUNISTIC_RESCAN_MIN_INTERVAL_SEC,
    WATCHER_STRICT_STORE_RETRY_MAX,
    WATCHER_END_GRACE_SEC,
    WATCHER_FUZZY_THRESHOLD,
    WATCHER_POLL_INTERVAL_SEC,
    WATCHER_START_DEBOUNCE_SEC,
    WATCHER_STATE_PERSIST_SEC,
    WATCHER_TRAILING_AUTO_PAUSE_DROP_SEC,
)
from store_manifests import StoreIndex, scan_all_stores
from utilities import format_timedelta_with_seconds


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


# A library provider is a zero-arg callable returning a list of dicts:
#   {'name': str, 'platform': str|None, 'igdb_aliases': List[str]}
# Implemented in main.py - kept abstract so the watcher doesn't import data.
LibraryProvider = Callable[[], List[Dict]]


@dataclass
class _Candidate:
    """A process being observed before debounce promotes it to Tracking."""
    pid: int
    exe_path: str
    install_dir: str
    game_name: str
    store: Optional[str]
    store_id: Optional[str]
    first_seen: float


@dataclass
class _ActiveSession:
    """A live tracked session."""
    session_id: str
    pid: int
    exe_path: str
    install_dir: str
    game_name: str
    store: Optional[str]
    store_id: Optional[str]
    start_iso: str
    start_monotonic: float
    paused: bool = False
    pauses: List[Dict] = field(default_factory=list)
    current_pause: Optional[Dict] = None
    # Set when the process disappears, cleared if a sibling shows up in time.
    pending_end_at: Optional[float] = None
    # Wall-clock timestamp captured at the moment process death was first
    # observed, so the recorded session `end_iso` reflects when the user
    # actually stopped playing rather than when our end-grace period
    # happened to expire 15s later.
    pending_end_iso: Optional[str] = None
    # True for sessions kicked off via the tray's "Start Console Session"
    # flow. Manual sessions have no real process to monitor (pid=0,
    # exe_path=''); the watcher must NOT apply the process-death check or
    # the idle/foreground auto-pause to them - the user is by definition
    # playing on a different device and may be entirely away from the PC.
    manual: bool = False
    # Monotonic timestamp at which the foreground window first stopped
    # being our tracked pid. Used to debounce "user alt-tabs to read a
    # message and tabs back" so we don't log a phantom 5-second pause
    # for every quick context switch. Stays None while the game holds
    # focus; reset to None whenever it does. The pause is only actually
    # begun once (now_mono - foreground_away_since_mono) crosses the
    # configured grace window.
    foreground_away_since_mono: Optional[float] = None


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


class ProcessWatcher:
    """Polling-based watcher with a layered resolver and session state machine."""

    def __init__(self, window, library_provider: LibraryProvider) -> None:
        self._window = window
        self._library_provider = library_provider

        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._pause_evt = threading.Event()  # set => watcher paused (no new sessions)

        self._lock = threading.RLock()
        self._known_pids: Set[int] = set()
        self._candidates: Dict[int, _Candidate] = {}
        self._active: Optional[_ActiveSession] = None
        self._last_state_persist = 0.0

        self._store_index: StoreIndex = StoreIndex()
        self._index_lock = threading.Lock()

        # Opportunistic store rescan (strict mode): when an exe looks like it
        # lives under Steam/Epic/GOG but isn't under whitelisted roots yet,
        # we rescan manifests and evict the pid from _known_pids so the next
        # poll re-runs the resolver. Without the eviction, the process would
        # never be seen as "new" again after the first skip.
        self._strict_store_retry: Dict[int, int] = {}
        self._last_opportunistic_rescan_mono: float = 0.0

        # Pluggable hooks (set by Phase G to handle idle/foreground).
        self.idle_seconds_provider: Optional[Callable[[], float]] = None
        self.foreground_pid_provider: Optional[Callable[[], Optional[int]]] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        if not _PSUTIL_AVAILABLE:
            _log.error("cannot start watcher: psutil unavailable")
            return False
        if self._thread is not None and self._thread.is_alive():
            _log.debug("start() called but watcher thread is already alive")
            return True
        self._stop_evt.clear()
        self._pause_evt.clear()
        # Initial library scan happens on the worker thread so startup isn't
        # blocked by manifest I/O.
        self._thread = threading.Thread(
            target=self._run, name="ProcessWatcherThread", daemon=True
        )
        self._thread.start()
        _end_grace = int(load_config().get('watcher_end_grace_seconds',
                                           WATCHER_END_GRACE_SEC) or 0)
        _log.info("watcher started (poll=%ds, debounce=%ds, end_grace=%ds)",
                  WATCHER_POLL_INTERVAL_SEC, WATCHER_START_DEBOUNCE_SEC,
                  _end_grace)
        self._emit_status('idle')
        return True

    def stop(self) -> None:
        _log.info("watcher stopping")
        self._stop_evt.set()
        # Ensure any active session is closed before the thread exits.
        with self._lock:
            if self._active is not None:
                self._end_active_session(reason='shutdown')
        # Don't join with a long timeout; the loop checks stop_evt every poll.
        if self._thread is not None:
            self._thread.join(timeout=WATCHER_POLL_INTERVAL_SEC + 1)
            self._thread = None
        self._emit_status('idle')
        _log.info("watcher stopped")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_paused(self) -> bool:
        return self._pause_evt.is_set()

    def pause(self) -> None:
        _log.info("watcher paused (manual)")
        self._pause_evt.set()
        with self._lock:
            if self._active is not None and not self._active.paused:
                self._begin_pause(reason='manual')

    def resume(self) -> None:
        _log.info("watcher resumed (manual)")
        self._pause_evt.clear()
        with self._lock:
            if self._active is not None and self._active.paused:
                self._end_pause(reason='manual')

    # ------------------------------------------------------------------
    # External commands (called from main thread)
    # ------------------------------------------------------------------

    def start_manual_session(
        self,
        game_name: str,
        platform: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Kick off a manually-started session for a console / non-PC game.

        The tray icon's "Start Console Session" submenu calls this when the
        user picks a recently played console title - they're about to play
        on a separate device, so the watcher can't observe a process. We
        synthesize an _ActiveSession with ``pid=0``, ``exe_path=''`` and
        ``manual=True``; the rest of the pipeline (toast, Discord
        presence, tray "Tracking: ..." line, end-of-session persistence)
        works through the same -PROCESS-DETECTED-/-PROCESS-ENDED- events
        the auto-tracking flow uses, so console sessions show up in the
        games file in exactly the same shape as a Steam session.

        Returns a small status dict ``{'ok': bool, 'reason': str|None,
        'session_id': str|None}``. Refuses to start when a session is
        already active (caller decides whether to prompt the user to
        stop the old one first).
        """
        with self._lock:
            if self._active is not None:
                return {
                    'ok': False,
                    'reason': 'already_active',
                    'session_id': None,
                    'active_game': self._active.game_name,
                }

            session_id = uuid.uuid4().hex
            now = datetime.now()
            active = _ActiveSession(
                session_id=session_id,
                pid=0,
                exe_path='',
                install_dir='',
                game_name=game_name,
                # Tag the store so reports / debug log lines can tell
                # manually-tracked console sessions apart from PC ones at
                # a glance without inspecting `manual=True` directly.
                store='manual_console',
                store_id=None,
                start_iso=now.isoformat(),
                start_monotonic=time.monotonic(),
                manual=True,
            )
            self._active = active
            _state_log.info(
                "transition Idle -> Tracking (manual) session=%s game=%r "
                "platform=%r",
                session_id, game_name, platform)
            self._persist_active_state()

        # Emit OUTSIDE the lock - the bridge handler runs synchronously
        # in the GUI thread and may itself want to call watcher methods.
        self._emit('-PROCESS-DETECTED-', {
            'session_id': session_id,
            'game_name': game_name,
            'exe_path': '',
            'install_dir': '',
            'start_time_iso': active.start_iso,
            'store': 'manual_console',
            'store_id': None,
            'manual': True,
        })
        self._emit_status('tracking', game_name)
        return {'ok': True, 'reason': None, 'session_id': session_id}

    def stop_current_session(self) -> None:
        """Force-end any active session (called from tray / toast actions)."""
        with self._lock:
            if self._active is not None:
                _log.info("user requested stop of active session %s",
                          self._active.session_id)
                self._end_active_session(reason='user_stop')
            else:
                _log.debug("stop_current_session called with no active session")

    def retitle_active_session(self, new_game_name: str) -> Optional[Dict]:
        """Rename the currently tracked session in place.

        Used by the "Wrong game?" picker so a correction takes effect
        without forcing the user to restart the game. The session_id,
        start_iso, accumulated elapsed time, and pause history are all
        preserved - only the title attached to the session changes.
        Returns a snapshot ``{session_id, old_game_name, new_game_name,
        exe_path, install_dir, pid, start_iso}`` for callers that need to
        update their own bookkeeping (bridge row mapping, Discord
        presence, live toast). Returns ``None`` if there is no active
        session or the new name is empty.
        """
        if not new_game_name:
            return None
        with self._lock:
            if self._active is None:
                return None
            old_name = self._active.game_name
            if old_name == new_game_name:
                return None
            self._active.game_name = new_game_name
            snapshot = {
                'session_id': self._active.session_id,
                'old_game_name': old_name,
                'new_game_name': new_game_name,
                'exe_path': self._active.exe_path,
                'install_dir': self._active.install_dir,
                'pid': self._active.pid,
                'start_iso': self._active.start_iso,
            }
            _log.info("retitle active session %s: %r -> %r",
                      self._active.session_id, old_name, new_game_name)
            self._persist_active_state()
        # Status is consumed by the tray; emit OUTSIDE the lock since
        # ``_emit_status`` writes to the GUI window queue.
        self._emit_status('tracking', new_game_name)
        return snapshot

    def discard_current_session(self) -> Optional[Dict]:
        """Drop the active session WITHOUT emitting -PROCESS-ENDED-.

        Used when the user picks "Wrong game?" - we don't want to record the
        partial session under the wrong title. Returns a snapshot of what
        was discarded (game_name, exe_path, pid, session_id) so the caller
        can decide whether to learn a new mapping for the same exe.
        """
        with self._lock:
            if self._active is None:
                _log.debug("discard_current_session called with no active session")
                return None
            snapshot = {
                'session_id': self._active.session_id,
                'game_name': self._active.game_name,
                'exe_path': self._active.exe_path,
                'install_dir': self._active.install_dir,
                'pid': self._active.pid,
            }
            _log.info("discarding active session %s game=%r (user remap)",
                      self._active.session_id, self._active.game_name)
            self._active = None
            self._persist_active_state()  # clears it
            self._emit_status('idle')
            # Also clear the candidate cache for this exe so the next tick
            # doesn't immediately re-promote it under the (about-to-be-replaced)
            # mapping during the new mapping's debounce window.
            try:
                self._candidates.pop(snapshot['pid'], None)
            except Exception:
                pass
            return snapshot

    def rescan_stores(self) -> None:
        """Re-run the store-manifest scan (callable from menu)."""
        _log.info("rescanning store manifests")
        try:
            idx = scan_all_stores()
        except Exception as exc:  # noqa: BLE001
            _log.error("store rescan failed: %s", exc, exc_info=True)
            return
        with self._index_lock:
            self._store_index = idx
        _log.info("store rescan complete: %d entries discovered", len(idx.entries))
        # Persist the lightweight serializable form so startup can show
        # store-derived games before the next async rescan completes.
        try:
            cfg = load_config()
            cfg['watcher_store_index'] = idx.to_serializable()
            save_config(cfg)
        except Exception as exc:  # noqa: BLE001
            _log.warning("failed to persist store index: %s", exc)

    def _maybe_opportunistic_store_rescan(self, now_mono: float) -> bool:
        """Re-scan Steam/Epic/GOG manifests if the throttle window allows.

        Returns True when a scan actually ran. Used when strict mode
        rejects an exe whose path still looks like a legitimate store
        install (e.g. user launched a game right after Steam finished
        downloading it, before our cached index listed that folder).
        """
        min_gap = float(WATCHER_OPPORTUNISTIC_RESCAN_MIN_INTERVAL_SEC)
        if now_mono - self._last_opportunistic_rescan_mono < min_gap:
            return False
        self._last_opportunistic_rescan_mono = now_mono
        _resolver_log.info("opportunistic store rescan (strict path miss)")
        self.rescan_stores()
        return True

    def get_state_snapshot(self) -> Dict:
        """Return a tray-friendly summary of the current state."""
        with self._lock:
            if self._active is None:
                tracking_text = 'idle'
                tracking_game = None
            else:
                elapsed = self._active_elapsed_seconds()
                tracking_text = _format_short_duration(elapsed)
                tracking_game = self._active.game_name
            return {
                'watcher_running': self.is_running(),
                'watcher_paused': self.is_paused() or (self._active is not None and self._active.paused),
                'tracking_game': tracking_game,
                'tracking_text': tracking_text,
            }

    def get_active_session_state(self) -> Optional[Dict]:
        """Serializable snapshot persisted to config for crash recovery."""
        with self._lock:
            if self._active is None:
                return None
            return {
                'session_id': self._active.session_id,
                'game_name': self._active.game_name,
                'pid': self._active.pid,
                'exe_path': self._active.exe_path,
                'start_iso': self._active.start_iso,
                'last_tick_iso': datetime.now().isoformat(),
            }

    # ------------------------------------------------------------------
    # User ops on the learned mapping
    # ------------------------------------------------------------------

    def remember_mapping(self, exe_path: str, game_name: str) -> None:
        cfg = load_config()
        m = dict(cfg.get('watcher_process_map', {}) or {})
        m[_normpath(exe_path)] = game_name
        cfg['watcher_process_map'] = m
        save_config(cfg)

    def forget_mapping(self, exe_path: str) -> None:
        cfg = load_config()
        m = dict(cfg.get('watcher_process_map', {}) or {})
        m.pop(_normpath(exe_path), None)
        cfg['watcher_process_map'] = m
        save_config(cfg)

    def add_ignore(self, basename: str) -> None:
        cfg = load_config()
        ig = list(cfg.get('watcher_ignore_list', []) or [])
        bn = basename.lower()
        if bn not in ig:
            ig.append(bn)
        cfg['watcher_ignore_list'] = ig
        save_config(cfg)

    def remember_installdir_mapping(self, install_dir: str, game_name: str) -> None:
        """Record an install-directory -> game mapping (Layer 1b).

        Use this for games that spawn multiple executables under the same
        install root (a launcher .exe that hands off to a 64-bit
        renderer .exe, an MMO updater .exe vs the actual game.exe, etc.) -
        any process whose path begins with `install_dir` will be matched
        as `game_name` without needing per-binary entries in the regular
        `watcher_process_map`.
        """
        if not install_dir or not game_name:
            return
        cfg = load_config()
        m = dict(cfg.get('watcher_installdir_map', {}) or {})
        m[_normpath_with_sep(install_dir)] = game_name
        cfg['watcher_installdir_map'] = m
        save_config(cfg)

    def forget_installdir_mapping(self, install_dir: str) -> None:
        if not install_dir:
            return
        cfg = load_config()
        m = dict(cfg.get('watcher_installdir_map', {}) or {})
        m.pop(_normpath_with_sep(install_dir), None)
        cfg['watcher_installdir_map'] = m
        save_config(cfg)

    def recheck_exe(self, exe_path: str) -> int:
        """Force the resolver to reconsider any running process for `exe_path`.

        The resolver only walks processes that aren't already in
        ``_known_pids`` - once a pid has been emitted as ambiguous (or
        skipped for any reason), the watcher never looks at it again
        until that pid disappears. That's a problem after the user
        commits a mapping for a currently-running exe through the
        "Pick another" picker: without invalidating the cached pids,
        nothing happens until they restart the game.

        Returns the number of cached pids that were dropped, so callers
        can log / surface it. Idempotent and safe to call when the exe
        isn't actually running (returns 0).
        """
        if not exe_path:
            return 0
        target = _normpath(exe_path)
        # Walk the live process list; we have to ask each Process for
        # its exe (not its name) because ``_known_pids`` is just a flat
        # set of ints with no path metadata attached.
        dropped = 0
        try:
            attrs = ['pid', 'exe']
            with self._lock:
                cached_pids = set(self._known_pids)
            for proc in psutil.process_iter(attrs):
                try:
                    pid = proc.info.get('pid')
                    exe = proc.info.get('exe') or ''
                except Exception:
                    continue
                if not exe or pid not in cached_pids:
                    continue
                if _normpath(exe) == target:
                    with self._lock:
                        if pid in self._known_pids:
                            self._known_pids.discard(pid)
                            dropped += 1
        except Exception as exc:  # noqa: BLE001
            _log.debug("recheck_exe(%s) iteration failed: %s",
                       exe_path, exc)
        if dropped:
            _log.info("recheck_exe %s: dropped %d cached pid(s); "
                      "next tick will re-resolve them",
                      exe_path, dropped)
        return dropped

    def recheck_install_dir(self, install_dir: str) -> int:
        """Force the resolver to reconsider every running pid under `install_dir`.

        Counterpart of ``recheck_exe`` for install-folder mappings: any
        process whose exe lives under the picked install dir gets
        evicted from ``_known_pids`` so the next tick promotes it via
        the new Layer 1b mapping. Useful for MMOs and games whose
        launcher .exe spawns a sibling renderer .exe under the same
        root - we want both to start tracking on the same tick rather
        than waiting for one to die.
        """
        if not install_dir:
            return 0
        prefix = _normpath_with_sep(install_dir).lower()
        dropped = 0
        try:
            attrs = ['pid', 'exe']
            with self._lock:
                cached_pids = set(self._known_pids)
            for proc in psutil.process_iter(attrs):
                try:
                    pid = proc.info.get('pid')
                    exe = (proc.info.get('exe') or '').lower()
                except Exception:
                    continue
                if not exe or pid not in cached_pids:
                    continue
                if exe.startswith(prefix):
                    with self._lock:
                        if pid in self._known_pids:
                            self._known_pids.discard(pid)
                            dropped += 1
        except Exception as exc:  # noqa: BLE001
            _log.debug("recheck_install_dir(%s) iteration failed: %s",
                       install_dir, exc)
        if dropped:
            _log.info("recheck_install_dir %s: dropped %d cached pid(s); "
                      "next tick will re-resolve them",
                      install_dir, dropped)
        return dropped

    def force_track_exe(self, exe_path: str, game_name: str) -> bool:
        """Immediately begin tracking a currently-running exe as `game_name`.

        Used when the user resolves an ambiguous-detection toast (the "best
        guess" notification): the process is already running, so rather than
        wait for a relaunch or the next debounce window we start the session
        right now. Skips the start-debounce because the user has explicitly
        confirmed the mapping. No-op (returns False) if the exe isn't running
        or a session is already active. Mirrors the locked promotion path in
        ``_promote_candidates``.
        """
        if not exe_path or not game_name:
            return False
        target = _normpath(exe_path)
        found_pid = None
        try:
            for proc in psutil.process_iter(['pid', 'exe']):
                try:
                    if _normpath(proc.info.get('exe') or '') == target:
                        found_pid = proc.info.get('pid')
                        break
                except Exception:
                    continue
        except Exception as exc:  # noqa: BLE001
            _log.warning("force_track_exe: process_iter failed: %s", exc)
            return False
        if found_pid is None:
            _log.info("force_track_exe: %s is not currently running", exe_path)
            return False

        with self._lock:
            if self._active is not None:
                _log.info("force_track_exe: a session is already active; skipping")
                return False
            cand = _Candidate(
                pid=found_pid,
                exe_path=exe_path,
                install_dir=os.path.dirname(exe_path),
                game_name=game_name,
                store=None,
                store_id=None,
                first_seen=time.monotonic(),
            )
            self._candidates.clear()
            self._known_pids.add(found_pid)
            _log.info("force_track_exe: starting session now for %r (pid=%s)",
                      game_name, found_pid)
            self._start_session(cand)
        return True

    def add_user_root(self, folder: str) -> bool:
        """Add `folder` to the strict-mode whitelist.

        Strict mode (default on) only resolves processes whose executable
        lives under one of: a Steam library, an Epic install root, a GOG
        install root, or `watcher_user_roots`. Manually-linked games like
        a Guild Wars install at D:/Games/GuildWars need their parent dir
        on this list or strict mode will silently drop the process before
        the resolver ever sees it. Idempotent + path-normalized; returns
        True only when `folder` was newly added (so callers can choose
        whether to surface a confirmation message).
        """
        if not folder:
            return False
        try:
            normalized = os.path.abspath(folder)
        except Exception:
            normalized = folder
        cfg = load_config()
        roots = list(cfg.get('watcher_user_roots') or [])
        # Compare in case-insensitive normalized form to avoid duplicate
        # entries that differ only in casing or trailing slash.
        existing_keys = {_normpath_with_sep(r).lower() for r in roots}
        if _normpath_with_sep(normalized).lower() in existing_keys:
            return False
        roots.append(normalized)
        cfg['watcher_user_roots'] = roots
        save_config(cfg)
        _log.info("added user_root %s", normalized)
        return True

    def remove_user_root(self, folder: str) -> bool:
        """Remove `folder` from the strict-mode whitelist (case-insensitive)."""
        if not folder:
            return False
        target = _normpath_with_sep(folder).lower()
        cfg = load_config()
        roots = list(cfg.get('watcher_user_roots') or [])
        new_roots = [r for r in roots if _normpath_with_sep(r).lower() != target]
        if len(new_roots) == len(roots):
            return False
        cfg['watcher_user_roots'] = new_roots
        save_config(cfg)
        _log.info("removed user_root %s", folder)
        return True

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        _log.debug("watcher worker thread started")
        # Boot: load any cached store index so the first poll already has data.
        cfg = load_config()
        cached = cfg.get('watcher_store_index') or {}
        if cached:
            try:
                with self._index_lock:
                    self._store_index = StoreIndex.from_serializable(cached)
                _log.info("loaded cached store index (%d entries)",
                          len(self._store_index.entries))
            except Exception as exc:
                _log.warning("failed to load cached store index: %s", exc)
        # Then refresh in the background so updates land within seconds.
        try:
            self.rescan_stores()
        except Exception as exc:  # noqa: BLE001
            _log.error("initial store scan failed: %s", exc, exc_info=True)

        while not self._stop_evt.is_set():
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001
                _log.error("watcher tick error: %s", exc, exc_info=True)
            # Slice the sleep so stop() returns quickly.
            for _ in range(WATCHER_POLL_INTERVAL_SEC * 4):
                if self._stop_evt.is_set():
                    break
                time.sleep(0.25)
        _log.debug("watcher worker thread exiting")

    def _tick(self) -> None:
        now_mono = time.monotonic()

        # 1. Enumerate processes and diff against last tick.
        current_pids: Set[int] = set()
        new_procs: List["psutil.Process"] = []
        try:
            iterator = psutil.process_iter(attrs=['pid'])
        except Exception as exc:  # noqa: BLE001
            _log.error("process_iter failed: %s", exc)
            return
        for proc in iterator:
            try:
                pid = proc.info['pid']
            except Exception:
                continue
            current_pids.add(pid)
            if pid not in self._known_pids:
                new_procs.append(proc)

        gone_pids = self._known_pids - current_pids
        self._known_pids = current_pids

        if self._strict_store_retry and gone_pids:
            for pid in gone_pids:
                self._strict_store_retry.pop(pid, None)

        # 2. Resolve any new processes (only if watcher not paused and no
        #    active session - we don't start a second session until the first ends).
        if not self.is_paused():
            self._consider_new_processes(new_procs, now_mono)

        # 3. Promote candidates whose debounce has expired and process still alive.
        self._promote_candidates(now_mono, current_pids)

        # 4. Sweep candidates whose pid has died before debounce.
        self._sweep_dead_candidates(current_pids)

        # 5. If we have an active session, check whether its pid is still alive.
        with self._lock:
            if self._active is not None:
                self._check_active_session(now_mono, current_pids, gone_pids)

        # 6. Apply idle / foreground pause logic if hooks exist.
        self._apply_idle_and_foreground_state(now_mono)

        # 7. Periodically persist active session state for crash recovery.
        if now_mono - self._last_state_persist >= WATCHER_STATE_PERSIST_SEC:
            self._last_state_persist = now_mono
            self._persist_active_state()

    # ------------------------------------------------------------------
    # Resolver pipeline
    # ------------------------------------------------------------------

    def _consider_new_processes(
        self,
        new_procs: List["psutil.Process"],
        now_mono: float,
    ) -> None:
        if not new_procs:
            return
        cfg = load_config()
        ignore_user = {b.lower() for b in (cfg.get('watcher_ignore_list') or [])}
        process_map = cfg.get('watcher_process_map', {}) or {}
        installdir_map = cfg.get('watcher_installdir_map', {}) or {}
        excluded = set(cfg.get('watcher_per_game_excluded') or [])
        strict = bool(cfg.get('watcher_strict_mode', True))
        user_roots = cfg.get('watcher_user_roots') or []

        with self._index_lock:
            store_idx = self._store_index
        roots = self._effective_roots(store_idx, user_roots)
        library = self._library_provider() or []

        with self._lock:
            already_active = self._active is not None

        opportunistic_pass_done = False
        for proc in new_procs:
            try:
                name = (proc.name() or '').lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
                continue
            if not name:
                continue
            if name in IGNORED_PROCESS_NAMES:
                _resolver_log.debug("skip pid=%s name=%s reason=builtin_ignore",
                                    proc.pid, name)
                continue
            if name in ignore_user:
                _resolver_log.debug("skip pid=%s name=%s reason=user_ignore",
                                    proc.pid, name)
                continue
            try:
                exe_path = proc.exe() or ''
            except (psutil.AccessDenied, psutil.NoSuchProcess, Exception) as exc:
                _resolver_log.debug("skip pid=%s name=%s reason=no_exe (%s)",
                                    proc.pid, name, type(exc).__name__)
                continue
            if not exe_path:
                continue
            exe_norm = _normpath(exe_path)
            lower_norm = exe_norm.lower()
            ignored_frag = next(
                (f for f in IGNORED_PATH_FRAGMENTS if f in lower_norm), None)
            if ignored_frag:
                _resolver_log.debug("skip pid=%s exe=%s reason=path_fragment(%s)",
                                    proc.pid, exe_norm, ignored_frag)
                continue
            if strict and not _path_under_any(exe_norm, roots):
                skip = True
                store_like = _looks_like_store_install_path(exe_norm)
                if store_like:
                    attempts = self._strict_store_retry.get(proc.pid, 0)
                    if attempts < WATCHER_STRICT_STORE_RETRY_MAX:
                        if not opportunistic_pass_done:
                            ran = self._maybe_opportunistic_store_rescan(now_mono)
                            if ran:
                                with self._index_lock:
                                    store_idx = self._store_index
                                roots = self._effective_roots(store_idx, user_roots)
                            opportunistic_pass_done = True
                        if _path_under_any(exe_norm, roots):
                            self._strict_store_retry.pop(proc.pid, None)
                            skip = False
                        else:
                            self._strict_store_retry[proc.pid] = attempts + 1
                            self._known_pids.discard(proc.pid)
                            _resolver_log.info(
                                "strict_outside_roots: evict pid=%s for retry "
                                "after store rescan (attempt %d/%d) exe=%s",
                                proc.pid, attempts + 1,
                                WATCHER_STRICT_STORE_RETRY_MAX, exe_norm)
                    else:
                        _resolver_log.debug(
                            "skip pid=%s exe=%s reason=strict_outside_roots "
                            "(store-like path, retry budget exhausted)",
                            proc.pid, exe_norm)
                if skip:
                    if not store_like:
                        _resolver_log.debug(
                            "skip pid=%s exe=%s reason=strict_outside_roots",
                            proc.pid, exe_norm)
                    continue

            # Layer 1: learned exact-path mapping.
            layer = None
            game_name: Optional[str] = process_map.get(exe_norm)
            store: Optional[str] = None
            store_id: Optional[str] = None
            install_dir = ''
            if game_name is not None:
                layer = 'L1_learned_path'

            if game_name is None:
                # Layer 1b: learned install-dir prefix.
                for d, n in installdir_map.items():
                    dn = _normpath_with_sep(d)
                    if exe_norm.startswith(dn):
                        game_name = n
                        install_dir = d
                        layer = 'L1b_learned_dir'
                        break

            if game_name is None:
                # Layer 2: store manifest lookup.
                appid = _steam_appid_from(proc)
                if appid:
                    entry = store_idx.lookup_steam_appid(appid)
                    if entry:
                        game_name = entry.name
                        install_dir = entry.install_dir
                        store, store_id = entry.store, entry.store_id
                        layer = f'L2_steam_appid({appid})'
                if game_name is None:
                    entry = store_idx.lookup_by_path(exe_norm)
                    if entry:
                        game_name = entry.name
                        install_dir = entry.install_dir
                        store, store_id = entry.store, entry.store_id
                        layer = f'L2_{entry.store}_path'

            # Store manifests may use different spelling than the library.
            # Skip when the resolved name is already a library title (learned
            # mappings, exact store hits, etc.).
            if (game_name is not None and library
                    and not _name_in_library(game_name, library)):
                aligned = _align_to_library(game_name, library)
                if aligned and aligned != game_name:
                    _resolver_log.debug("aligned '%s' -> '%s' to library spelling",
                                        game_name, aligned)
                    game_name = aligned

            if game_name is None:
                # Layer 3: fuzzy match against library names.
                guess, score = _fuzzy_match_library(exe_norm, library)
                if guess and score >= WATCHER_FUZZY_THRESHOLD:
                    game_name = guess
                    layer = f'L3_fuzzy({score})'
                else:
                    _resolver_log.info(
                        "ambiguous: pid=%s exe=%s best_guess=%r score=%s "
                        "(below threshold %d)",
                        proc.pid, exe_norm, guess, score, WATCHER_FUZZY_THRESHOLD)
                    # Layer 4: ask the user, but skip if a session is already
                    # active (avoid spamming during gameplay).
                    if not already_active:
                        self._emit_ambiguous(proc, exe_norm, guess)
                    else:
                        _resolver_log.debug(
                            "suppress ambiguous toast: session %s already active",
                            self._active.session_id if self._active else '?')
                    continue

            if game_name in excluded:
                _resolver_log.info("skip exe=%s reason=per_game_excluded(%s)",
                                   exe_norm, game_name)
                continue

            # Library-membership gate. Manifest-based matches (L1, L2) can
            # name a real Steam/Epic/GOG title that the user simply hasn't
            # added to GamesList yet. Promoting one to Tracking would create
            # a ghost session that the bridge can't attribute, so instead
            # surface it as an ambiguous detection: the user gets a toast
            # asking them to add the game to their library, and the watcher
            # stays Idle.
            if not _name_in_library(game_name, library):
                _resolver_log.info(
                    "skip exe=%s game=%r reason=not_in_user_library "
                    "(matched via %s; add it via Add Entry to enable tracking)",
                    exe_norm, game_name, layer)
                if not already_active:
                    self._emit_ambiguous(proc, exe_norm, game_name)
                continue

            # Stash as candidate; debounce promotion handled in _promote_candidates.
            if not install_dir:
                install_dir = _guess_install_dir(exe_norm, roots)
            with self._lock:
                if self._active is not None:
                    active = self._active
                    same_game = ((active.game_name or '').strip().lower()
                                 == (game_name or '').strip().lower())
                    if active.pending_end_at is not None and same_game:
                        # The SAME game reappeared while the session was in its
                        # end-grace: a relaunch / launcher->engine handoff (e.g. a
                        # custom-mapped openmw.exe -> Morrowind that runs from a
                        # different folder than _find_sibling expects). Continue
                        # the existing session on the new pid rather than dropping
                        # it or splitting into a second session.
                        _state_log.info(
                            "same game %r relaunched during end-grace "
                            "(pid %s -> %s); continuing the session",
                            active.game_name, active.pid, proc.pid)
                        active.pid = proc.pid
                        active.pending_end_at = None
                        active.pending_end_iso = None
                        return
                    if active.pending_end_at is not None:
                        # A *different* game launched during the end-grace -> the
                        # old session really ended. Finalize it now (anchored to
                        # when its process died) and let the new game be tracked
                        # instead of dropped-and-forgotten (a process is only
                        # resolved once).
                        _state_log.info(
                            "different game %r launched during end-grace of %r; "
                            "finalizing the ending session early",
                            game_name, active.game_name)
                        self._end_active_session(reason='superseded_by_new_game')
                        # _active is now None; fall through to stash the candidate.
                    else:
                        _resolver_log.debug(
                            "drop candidate pid=%s game=%s reason=session_active",
                            proc.pid, game_name)
                        return
                if proc.pid in self._candidates:
                    continue
                _resolver_log.info(
                    "candidate pid=%s game=%r exe=%s layer=%s "
                    "(debounce %ds)",
                    proc.pid, game_name, exe_norm, layer,
                    WATCHER_START_DEBOUNCE_SEC)
                _state_log.info("transition Idle -> Candidate (%s)", game_name)
                self._candidates[proc.pid] = _Candidate(
                    pid=proc.pid,
                    exe_path=exe_norm,
                    install_dir=install_dir,
                    game_name=game_name,
                    store=store,
                    store_id=store_id,
                    first_seen=time.monotonic(),
                )

    def _promote_candidates(self, now_mono: float, alive_pids: Set[int]) -> None:
        with self._lock:
            if self._active is not None:
                return
            ready: List[_Candidate] = []
            for pid, cand in self._candidates.items():
                if pid not in alive_pids:
                    continue
                age = now_mono - cand.first_seen
                if age >= WATCHER_START_DEBOUNCE_SEC:
                    ready.append(cand)
                else:
                    _state_log.debug("candidate pid=%s game=%r age=%.1fs (debounce %ds)",
                                     cand.pid, cand.game_name, age,
                                     WATCHER_START_DEBOUNCE_SEC)
            if not ready:
                return
            chosen = self._choose_candidate(ready)
            dropped = len(self._candidates) - 1
            if dropped > 0:
                _state_log.debug("dropping %d sibling candidate(s) on promotion",
                                 dropped)
            self._candidates.clear()
            self._start_session(chosen)

    def _choose_candidate(self, ready: List["_Candidate"]) -> "_Candidate":
        """Pick which ready candidate to promote.

        A game often spawns several processes under the same install dir - e.g. a
        launcher/stub plus the actual windowed game (Arx Fatalis: arx.exe spawns
        bin\\x64\\arx.exe). Tracking the windowless launcher trips foreground-only
        auto-pause, because the foreground window belongs to the game and not the
        launcher. So when more than one candidate is ready, prefer the one that
        currently owns the foreground window; otherwise fall back to the
        first-seen candidate.
        """
        if len(ready) > 1 and self.foreground_pid_provider is not None:
            try:
                fg_pid = self.foreground_pid_provider()
            except Exception:  # noqa: BLE001
                fg_pid = None
            if fg_pid:
                for cand in ready:
                    if cand.pid == fg_pid:
                        _state_log.info(
                            "multiple ready candidates for %r; promoting the "
                            "foreground process pid=%s (exe=%s)",
                            cand.game_name, cand.pid, cand.exe_path)
                        return cand
        return ready[0]

    def _sweep_dead_candidates(self, alive_pids: Set[int]) -> None:
        with self._lock:
            for pid in list(self._candidates):
                if pid not in alive_pids:
                    cand = self._candidates.pop(pid, None)
                    if cand is not None:
                        _state_log.info(
                            "candidate pid=%s game=%r died before debounce "
                            "(age=%.1fs); transition Candidate -> Idle",
                            pid, cand.game_name,
                            time.monotonic() - cand.first_seen)

    def _check_active_session(
        self,
        now_mono: float,
        alive_pids: Set[int],
        gone_pids: Set[int],
    ) -> None:
        active = self._active
        if active is None:
            return

        # Manual sessions (tray "Start Console Session") are user-driven:
        # they don't end until the user explicitly clicks Stop. There's
        # no real PID to compare against alive_pids - active.pid is 0,
        # which would never appear in the live process set and would
        # therefore trip the end-grace path on the very first tick.
        if active.manual:
            return

        if active.pid in alive_pids:
            return  # still running

        # The process is gone. The end-grace window (how long it can stay gone
        # before we conclude the session) is user-configurable; fall back to the
        # built-in default. Read here (only while a tracked process is missing,
        # i.e. the few Ending ticks) so a settings change applies without a
        # restart. NB: the end-of-session / "rate it" toast only fires once this
        # whole window has elapsed.
        end_grace_sec = int(load_config().get('watcher_end_grace_seconds',
                                              WATCHER_END_GRACE_SEC) or 0)

        # If we already started the end grace, see if a sibling under the same
        # install dir appeared.
        if active.pending_end_at is None:
            active.pending_end_at = now_mono
            active.pending_end_iso = datetime.now().isoformat()
            _state_log.info(
                "tracked pid=%s gone; entering Ending (grace %ds)",
                active.pid, end_grace_sec)

        # Look for a sibling.
        sibling = self._find_sibling(active.install_dir, alive_pids)
        if sibling is not None:
            new_exe = sibling.info.get('exe', '?') if hasattr(sibling, 'info') else '?'
            _state_log.info(
                "sibling handoff: %s -> %s (pid %s -> %s); transition "
                "Ending -> Tracking",
                active.exe_path, new_exe, active.pid, sibling.pid)
            active.pid = sibling.pid
            active.pending_end_at = None
            active.pending_end_iso = None
            return

        if now_mono - active.pending_end_at >= end_grace_sec:
            _state_log.info(
                "end grace expired (%.1fs >= %ds); transition Ending -> Idle",
                now_mono - active.pending_end_at, end_grace_sec)
            self._end_active_session(reason='process_gone')

    def _find_sibling(
        self,
        install_dir: str,
        alive_pids: Set[int],
    ) -> Optional["psutil.Process"]:
        if not install_dir:
            return None
        prefix = _normpath_with_sep(install_dir).lower()
        for pid in alive_pids:
            try:
                proc = psutil.Process(pid)
                exe = proc.exe()
            except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
                continue
            if not exe:
                continue
            if exe.lower().startswith(prefix):
                name = (proc.name() or '').lower()
                if name in IGNORED_PROCESS_NAMES:
                    continue
                proc.info = {'exe': exe}  # for the debug log above
                return proc
        return None

    # ------------------------------------------------------------------
    # Session start / end / pause helpers
    # ------------------------------------------------------------------

    def _start_session(self, cand: _Candidate) -> None:
        session_id = uuid.uuid4().hex
        now = datetime.now()
        active = _ActiveSession(
            session_id=session_id,
            pid=cand.pid,
            exe_path=cand.exe_path,
            install_dir=cand.install_dir,
            game_name=cand.game_name,
            store=cand.store,
            store_id=cand.store_id,
            start_iso=now.isoformat(),
            start_monotonic=time.monotonic(),
        )
        self._active = active
        _state_log.info(
            "transition Candidate -> Tracking session=%s game=%r pid=%s "
            "exe=%s store=%s",
            session_id, cand.game_name, cand.pid, cand.exe_path, cand.store)

        # Auto-learn the path mapping the first time we attribute a session
        # via store manifest or fuzzy match - subsequent runs hit Layer 1
        # immediately and skip the resolver entirely.
        try:
            self.remember_mapping(cand.exe_path, cand.game_name)
        except Exception as exc:  # noqa: BLE001
            _log.warning("failed to learn mapping for %s: %s", cand.exe_path, exc)

        self._emit('-PROCESS-DETECTED-', {
            'session_id': session_id,
            'game_name': cand.game_name,
            'exe_path': cand.exe_path,
            'install_dir': cand.install_dir,
            'start_time_iso': active.start_iso,
            'store': cand.store,
            'store_id': cand.store_id,
        })
        self._emit_status('tracking', cand.game_name)

    def _end_active_session(self, reason: str) -> None:
        active = self._active
        if active is None:
            return

        # Anchor the session's true end time. If the process was already
        # observed dead (the session is in its end-grace, pending_end_iso set)
        # we use the moment we first saw it disappear, NOT the much-later
        # moment we finalize - whether that's the grace expiring, a shutdown,
        # or a newly launched game superseding it. Otherwise an auto-tracked
        # session would appear up to the full grace window longer than reality.
        if active.pending_end_iso is not None:
            try:
                end_dt = datetime.fromisoformat(active.pending_end_iso)
            except Exception:
                end_dt = datetime.now()
            end_mono = active.pending_end_at if active.pending_end_at is not None \
                else time.monotonic()
        else:
            end_dt = datetime.now()
            end_mono = time.monotonic()

        # Close any dangling pause at the true end time, then decide
        # whether to keep it. Auto-pauses (idle/foreground) that were
        # still open when the session ended are usually just the user's
        # exit gesture (clicking outside the game window seconds before
        # closing it) - dropping the short ones avoids a phantom pause
        # being recorded on every auto-tracked session. Long auto-pauses
        # (e.g. user idled for 30 minutes then quit) and any manual pause
        # are always kept, because that time genuinely wasn't playtime.
        if active.current_pause is not None:
            cur = active.current_pause
            cur['end'] = end_dt.isoformat()
            cur['incomplete'] = True
            keep = True
            try:
                ps = datetime.fromisoformat(cur['start'])
                pause_seconds = max(0.0, (end_dt - ps).total_seconds())
            except Exception:
                pause_seconds = 0.0
            cur_reason = (cur.get('reason') or '')
            is_auto = cur_reason.startswith('auto_idle_or_focus')
            if is_auto and pause_seconds < WATCHER_TRAILING_AUTO_PAUSE_DROP_SEC:
                keep = False
                _state_log.info(
                    "dropping trailing auto-pause (%.1fs < %ds) reason=%r "
                    "as exit-gesture artifact",
                    pause_seconds,
                    WATCHER_TRAILING_AUTO_PAUSE_DROP_SEC,
                    cur_reason)
            if keep:
                active.pauses.append(cur)
            active.current_pause = None
            active.paused = False

        elapsed_sec = self._active_elapsed_seconds(end_mono=end_mono)
        duration = format_timedelta_with_seconds(timedelta(seconds=elapsed_sec))

        payload = {
            'session_id': active.session_id,
            'game_name': active.game_name,
            'exe_path': active.exe_path,
            'install_dir': active.install_dir,
            'start_iso': active.start_iso,
            'end_iso': end_dt.isoformat(),
            'duration_str': duration,
            'pauses': active.pauses,
            'reason': reason,
        }
        _state_log.info(
            "session ended session=%s game=%r duration=%s pauses=%d reason=%s",
            active.session_id, active.game_name, duration,
            len(active.pauses), reason)
        self._active = None
        self._persist_active_state()  # clears it
        self._emit('-PROCESS-ENDED-', payload)
        self._emit_status('idle')

    def _active_elapsed_seconds(self, end_mono: Optional[float] = None) -> int:
        active = self._active
        if active is None:
            return 0
        if end_mono is None:
            end_mono = time.monotonic()
        total = end_mono - active.start_monotonic
        # Subtract completed pauses.
        for p in active.pauses:
            try:
                ps = datetime.fromisoformat(p['start'])
                pe = datetime.fromisoformat(p['end'])
                total -= max(0.0, (pe - ps).total_seconds())
            except Exception:
                continue
        # Subtract current pause's elapsed portion (so live elapsed is honest).
        if active.current_pause is not None:
            try:
                ps = datetime.fromisoformat(active.current_pause['start'])
                total -= max(0.0, (datetime.now() - ps).total_seconds())
            except Exception:
                pass
        return max(0, int(total))

    def _begin_pause(self, reason: str) -> None:
        active = self._active
        if active is None or active.paused:
            return
        active.paused = True
        active.current_pause = {
            'start': datetime.now().isoformat(),
            'reason': reason,
        }
        _state_log.info("transition Tracking -> Paused (%s) game=%r", reason,
                        active.game_name)
        self._emit('-WATCHER-IDLE-PAUSE-', {'session_id': active.session_id, 'paused': True})
        self._emit_status('paused', active.game_name)

    def _end_pause(self, reason: str) -> None:
        active = self._active
        if active is None or not active.paused:
            return
        active.paused = False
        if active.current_pause is not None:
            active.current_pause['end'] = datetime.now().isoformat()
            active.current_pause['resume_reason'] = reason
            active.pauses.append(active.current_pause)
            active.current_pause = None
        _state_log.info("transition Paused -> Tracking (%s) game=%r", reason,
                        active.game_name)
        self._emit('-WATCHER-IDLE-PAUSE-', {'session_id': active.session_id, 'paused': False})
        self._emit_status('tracking', active.game_name)

    # ------------------------------------------------------------------
    # Idle + foreground gating (Phase G plugs hooks in)
    # ------------------------------------------------------------------

    def _apply_idle_and_foreground_state(self, now_mono: float) -> None:
        # Quick check without the lock to skip the work entirely when idle.
        active = self._active
        if active is None:
            return
        # Manual console sessions don't watch the PC at all - the user is
        # by design playing on a different device, so input idle and
        # foreground-window heuristics carry no signal about whether the
        # session is still going. Without this guard the watcher would
        # auto-pause ~10 minutes in (default idle threshold) and corrupt
        # every console session's recorded duration.
        if active.manual:
            return
        # Once the tracked process is gone (Ending state) we deliberately
        # stop touching pause/resume: the user has already exited the game,
        # so any "foreground changed" or "now idle" signal we'd see here is
        # just an artifact of them clicking back to the desktop. Without
        # this guard, the brief auto-pause that often fires 1-2 polls
        # before process death gets recorded as a phantom pause on every
        # auto-tracked session.
        if active.pending_end_at is not None:
            return

        # Read config and call ctypes hooks WITHOUT the watcher lock - they
        # do file I/O / cross-thread work and we don't want to block any
        # main-thread caller (e.g. tray.get_state_snapshot) waiting on us.
        cfg = load_config()
        idle_min = int(cfg.get('watcher_idle_pause_minutes', WATCHER_DEFAULT_IDLE_MINUTES) or 0)
        foreground_only = bool(cfg.get('watcher_foreground_only', False))
        # Grace window before a foreground change actually triggers a
        # pause. Lets the user briefly check Discord / a browser tab /
        # an inventory wiki without polluting their session log with a
        # 4-second pause entry every time. 0 disables debouncing (legacy
        # behaviour: pause the instant focus shifts).
        fg_grace_sec = int(cfg.get(
            'watcher_foreground_pause_grace_seconds',
            WATCHER_DEFAULT_FOREGROUND_GRACE_SEC) or 0)

        idle_s: Optional[float] = None
        if idle_min > 0 and self.idle_seconds_provider is not None:
            try:
                idle_s = float(self.idle_seconds_provider() or 0.0)
            except Exception:
                idle_s = 0.0

        fg_pid: Optional[int] = None
        if foreground_only and self.foreground_pid_provider is not None:
            try:
                fg_pid = self.foreground_pid_provider()
            except Exception:
                fg_pid = None

        with self._lock:
            active = self._active
            if active is None:
                return

            should_pause = False
            reasons: List[str] = []
            if idle_s is not None:
                _state_log.debug("idle_seconds=%.1f threshold=%ds", idle_s,
                                 idle_min * 60)
                if idle_s >= idle_min * 60:
                    should_pause = True
                    reasons.append(f"idle({int(idle_s)}s)")

            if foreground_only:
                fg_away = (fg_pid is None or fg_pid != active.pid)
                _state_log.debug(
                    "foreground_pid=%s tracked_pid=%s away=%s grace=%ds",
                    fg_pid, active.pid, fg_away, fg_grace_sec)
                if fg_away:
                    # Start the grace clock the first poll we see focus
                    # leave the game; once it's been held away for the
                    # configured window, escalate to an actual pause.
                    if active.foreground_away_since_mono is None:
                        active.foreground_away_since_mono = now_mono
                    away_for = now_mono - active.foreground_away_since_mono
                    if away_for >= fg_grace_sec:
                        should_pause = True
                        reasons.append(
                            f"foreground({fg_pid},away={int(away_for)}s)")
                    else:
                        _state_log.debug(
                            "foreground away for %.1fs (grace=%ds), "
                            "deferring pause",
                            away_for, fg_grace_sec)
                else:
                    # Game has focus again: reset the grace clock so a
                    # later alt-tab gets a fresh debounce window rather
                    # than triggering immediately.
                    active.foreground_away_since_mono = None

            if should_pause and not active.paused:
                self._begin_pause(
                    reason='auto_idle_or_focus:' + ','.join(reasons))
            elif not should_pause and active.paused:
                cur = active.current_pause
                if cur and (cur.get('reason') or '').startswith('auto_idle_or_focus'):
                    self._end_pause(reason='auto_resume')

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _persist_active_state(self) -> None:
        try:
            cfg = load_config()
            cfg['active_session_state'] = self.get_active_session_state()
            save_config(cfg)
        except Exception as exc:  # noqa: BLE001
            _log.warning("failed to persist active session state: %s", exc)

    def _emit(self, key: str, payload: Dict) -> None:
        if self._window is None:
            return
        try:
            self._window.write_event_value(key, payload)
            _log.debug("emit %s payload_keys=%s", key, list(payload.keys()))
        except Exception as exc:  # noqa: BLE001
            _log.warning("watcher emit(%s) failed: %s", key, exc)

    def _emit_status(self, state: str, game_name: Optional[str] = None) -> None:
        self._emit('-WATCHER-STATUS-', {'state': state, 'game_name': game_name})

    def _emit_ambiguous(
        self,
        proc: "psutil.Process",
        exe_norm: str,
        best_guess: Optional[str],
    ) -> None:
        try:
            install_dir = os.path.dirname(exe_norm)
            basename = os.path.basename(exe_norm)
        except Exception:
            install_dir = exe_norm
            basename = exe_norm
        self._emit('-MATCH-AMBIGUOUS-', {
            'detection_id': uuid.uuid4().hex,
            'exe_path': exe_norm,
            'install_dir': install_dir,
            'exe_basename': basename,
            'best_guess': best_guess,
        })

    def _effective_roots(
        self,
        store_idx: StoreIndex,
        user_roots: List[str],
    ) -> List[str]:
        roots = list(store_idx.install_roots())
        for r in user_roots:
            n = _normpath_with_sep(r)
            if n not in roots:
                roots.append(n)
        for r in WATCHER_DEFAULT_ROOTS:
            n = _normpath_with_sep(r)
            if n not in roots:
                roots.append(n)
        return roots


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------


_WATCHER: Optional[ProcessWatcher] = None


def initialize_watcher(
    window,
    library_provider: LibraryProvider,
    enabled: bool = False,
) -> Optional[ProcessWatcher]:
    """Construct and (optionally) start the global watcher.

    The watcher always exists once initialized so callers can pass it to the
    tray and notification modules; only `start()` is gated on `enabled`.
    """
    global _WATCHER
    if not _PSUTIL_AVAILABLE:
        return None
    if _WATCHER is not None:
        return _WATCHER
    _WATCHER = ProcessWatcher(window, library_provider)
    if enabled:
        _WATCHER.start()
    return _WATCHER


def get_watcher() -> Optional[ProcessWatcher]:
    return _WATCHER


def cleanup_watcher() -> None:
    global _WATCHER
    if _WATCHER is not None:
        _WATCHER.stop()
        _WATCHER = None


# ---------------------------------------------------------------------------
# Pure helpers (testable in isolation)
# ---------------------------------------------------------------------------


_STEAM_APPID_RE = re.compile(r'-SteamAppId[ =](\d+)')


def _steam_appid_from(proc) -> Optional[str]:
    """Best-effort Steam appid extraction from a process's cmdline."""
    try:
        cmd = proc.cmdline()
    except Exception:
        return None
    if not cmd:
        return None
    full = ' '.join(cmd)
    m = _STEAM_APPID_RE.search(full)
    if m:
        return m.group(1)
    # Sometimes the appid appears as a bare numeric arg between known launch flags.
    return None


def _normpath(p: str) -> str:
    try:
        return os.path.normcase(os.path.normpath(p))
    except Exception:
        return p


def _normpath_with_sep(p: str) -> str:
    n = _normpath(p)
    if not n.endswith(os.sep):
        n += os.sep
    return n


def _path_under_any(path: str, roots: List[str]) -> bool:
    pl = path.lower()
    for r in roots:
        if pl.startswith(r.lower()):
            return True
    return False


def _looks_like_store_install_path(exe_norm: str) -> bool:
    """Heuristic: exe path resembles a Steam/Epic/GOG game install.

    Used only when strict mode rejects the path - triggers an
    opportunistic manifest rescan and a one-tick pid eviction so a
    freshly installed game can be picked up without restarting the app.
    """
    p = exe_norm.lower().replace('/', '\\')
    return any(
        marker in p
        for marker in (
            '\\steamapps\\common\\',
            '\\epic games\\',
            '\\gog galaxy\\games\\',
        )
    )


def _guess_install_dir(exe_path: str, roots: List[str]) -> str:
    """Best-effort install dir for an exe: the matching root + first subfolder."""
    pl = exe_path.lower()
    for r in roots:
        rl = r.lower()
        if pl.startswith(rl):
            tail = exe_path[len(r):]
            first = tail.split(os.sep, 1)[0]
            return os.path.join(r.rstrip(os.sep), first)
    return os.path.dirname(exe_path)


def _fuzzy_match_library(
    exe_path: str,
    library: List[Dict],
) -> Tuple[Optional[str], int]:
    """Return (best_game_name, score) or (None, 0). 0-100 scale."""
    if not _RAPIDFUZZ_AVAILABLE or not library:
        return None, 0
    install_basename = os.path.basename(os.path.dirname(exe_path))
    exe_stem, _ = os.path.splitext(os.path.basename(exe_path))
    candidates = [c for c in (install_basename, exe_stem) if c]
    if not candidates:
        return None, 0

    best_name: Optional[str] = None
    best_score = 0
    for entry in library:
        names: List[str] = []
        n = entry.get('name')
        if n:
            names.append(n)
        for alias in (entry.get('igdb_aliases') or []):
            if alias:
                names.append(alias)
        for cand in candidates:
            for n in names:
                s = int(fuzz.token_set_ratio(_clean_for_fuzz(cand), _clean_for_fuzz(n)))
                if s > best_score:
                    best_score = s
                    best_name = entry.get('name')  # canonical library name
    return best_name, best_score


_FUZZ_NORMALIZE_RE = re.compile(r'[^a-z0-9 ]+')


def _clean_for_fuzz(s: str) -> str:
    s = (s or '').lower()
    s = s.replace('_', ' ').replace('-', ' ').replace('.', ' ')
    s = _FUZZ_NORMALIZE_RE.sub(' ', s)
    return ' '.join(s.split())


def _align_to_library(name: str, library: List[Dict]) -> Optional[str]:
    """If the library has an entry whose name closely matches `name`, return it."""
    if not _RAPIDFUZZ_AVAILABLE or not library:
        return None
    target = _clean_for_fuzz(name)
    name_fold = name.casefold()
    best_name: Optional[str] = None
    best_score = 0
    for entry in library:
        n = entry.get('name')
        if not n:
            continue
        s = int(fuzz.token_set_ratio(target, _clean_for_fuzz(n)))
        if s < WATCHER_FUZZY_THRESHOLD:
            continue
        if best_name is None:
            best_score = s
            best_name = n
            continue
        if s > best_score:
            best_score = s
            best_name = n
        elif s == best_score:
            n_exact = n.casefold() == name_fold
            best_exact = best_name.casefold() == name_fold
            if n_exact and not best_exact:
                best_name = n
            elif n_exact == best_exact and len(n) > len(best_name):
                best_name = n
    return best_name


def _name_in_library(game_name: str, library: List[Dict]) -> bool:
    """Case-insensitive membership check across primary + IGDB alias names."""
    if not game_name or not library:
        return False
    target = game_name.casefold()
    for entry in library:
        name = entry.get('name')
        if name and name.casefold() == target:
            return True
        for alias in (entry.get('igdb_aliases') or []):
            if alias and alias.casefold() == target:
                return True
    return False


def _format_short_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
