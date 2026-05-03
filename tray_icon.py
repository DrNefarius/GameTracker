"""
System-tray icon for GamesList Manager.

Runs `pystray` on a dedicated background thread. Tray menu actions post
events into the main PySimpleGUI loop via ``write_event_value('-TRAY-...', ...)``
so the existing dispatcher is the single source of truth.

The tray icon is purely a UI shell - it never reads or mutates the games
data directly. It pulls a small "state snapshot" (currently tracking, paused,
recent console games) from the watcher via callback, so nothing in this
module imports the watcher at module-load time (avoids a circular import).
"""

from __future__ import annotations

import os
import threading
from typing import Callable, List, Optional

from watcher_log import tray_logger

_log = tray_logger()

try:
    import pystray  # type: ignore
    from PIL import Image  # type: ignore
    _PYSTRAY_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    _log.warning("pystray not available, tray icon disabled: %s", _exc)
    _PYSTRAY_AVAILABLE = False


_ICON_PATH = 'gameslisticon.ico'


class TrayIcon:
    """Lightweight wrapper around a `pystray.Icon` instance."""

    def __init__(
        self,
        window,
        get_state: Callable[[], dict],
        get_console_games: Callable[[], List[str]],
    ) -> None:
        self._window = window
        self._get_state = get_state
        self._get_console_games = get_console_games
        self._icon: Optional["pystray.Icon"] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        # Refresh coordination: a single dedicated thread drains pending
        # refresh requests so the caller (typically the GUI thread) never
        # blocks inside pystray's cross-thread Win32 plumbing.
        self._refresh_evt = threading.Event()
        self._refresh_thread: Optional[threading.Thread] = None
        self._refresh_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> bool:
        if not _PYSTRAY_AVAILABLE:
            return False
        if self._icon is not None:
            return True
        try:
            image = Image.open(_ICON_PATH) if os.path.exists(_ICON_PATH) else self._fallback_image()
        except Exception:
            image = self._fallback_image()

        self._icon = pystray.Icon(
            "gameslist_manager",
            image,
            "GamesList Manager",
            menu=self._build_menu(),
        )

        self._thread = threading.Thread(
            target=self._icon.run, name="TrayIconThread", daemon=True
        )
        self._thread.start()

        # Dedicated refresh worker. It coalesces multiple refresh requests
        # into one rebuild, isolating slow/blocking pystray calls from the
        # GUI thread.
        self._refresh_thread = threading.Thread(
            target=self._refresh_worker,
            name="TrayRefreshThread",
            daemon=True,
        )
        self._refresh_thread.start()
        return True

    def stop(self) -> None:
        self._stop_evt.set()
        # Wake the refresh worker so it can exit promptly.
        self._refresh_evt.set()
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None

    def refresh(self) -> None:
        """Request an async, coalescing menu rebuild.

        Returns immediately. The actual rebuild runs on the dedicated
        refresh worker thread so the GUI thread is never blocked by
        pystray's Win32 cross-thread calls (which can hang under
        contention or while a context menu is open).
        """
        if self._icon is None or self._stop_evt.is_set():
            return
        self._refresh_evt.set()

    def _refresh_worker(self) -> None:
        # A coarse rate limit (250ms) to absorb event bursts; multiple
        # refresh requests collapse into a single rebuild.
        while not self._stop_evt.is_set():
            self._refresh_evt.wait()
            if self._stop_evt.is_set():
                break
            self._refresh_evt.clear()
            with self._refresh_lock:
                if self._icon is None:
                    continue
                try:
                    self._icon.menu = self._build_menu()
                    self._icon.update_menu()
                except Exception as exc:  # noqa: BLE001
                    _log.warning("tray refresh failed: %s", exc)
            # Throttle bursts; 250ms is well under user perception while
            # still letting back-to-back state changes coalesce.
            self._stop_evt.wait(0.25)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post(self, action: str, payload: Optional[dict] = None) -> None:
        if self._window is None:
            return
        try:
            self._window.write_event_value(
                '-TRAY-ACTION-',
                {'action': action, 'payload': payload or {}},
            )
            _log.info("tray action: %s payload=%s", action, payload or {})
        except Exception as exc:  # noqa: BLE001
            _log.warning("tray dispatch failed: %s", exc)

    def _build_menu(self) -> "pystray.Menu":
        state = {}
        try:
            state = self._get_state() or {}
        except Exception as exc:  # noqa: BLE001
            _log.warning("tray state callback failed: %s", exc)

        watcher_running = bool(state.get('watcher_running'))
        watcher_paused = bool(state.get('watcher_paused'))
        tracking_game = state.get('tracking_game')
        tracking_text = state.get('tracking_text', 'Idle')
        header_label = (
            f"Tracking: {tracking_game} - {tracking_text}"
            if tracking_game else "Idle"
        )

        # Console-session submenu items, built eagerly. pystray accepts a
        # tuple/list of MenuItems for nested menus; the dynamic-callable
        # form takes a zero-arg factory and gets invoked lazily, but the
        # signature surprised us in practice (pystray version-dependent),
        # so we just snapshot the list when the parent menu rebuilds.
        try:
            console_games = list(self._get_console_games() or [])
        except Exception as exc:  # noqa: BLE001
            _log.warning("console-games callback failed: %s", exc)
            console_games = []
        if console_games:
            # pystray.MenuItem inspects `action.__code__.co_argcount` and
            # rejects anything with more than 2 positional params - even
            # defaults count. So we can't use `lambda _i, _it, _g=g: ...`;
            # instead a tiny factory closes over `game` cleanly with the
            # required (icon, item) signature.
            def _make_console_action(game_name):
                def _start_console(_icon, _item):
                    self._post('start_console', {'game': game_name})
                return _start_console

            console_items = tuple(
                pystray.MenuItem(g, _make_console_action(g))
                for g in console_games[:15]
            )
        else:
            console_items = (
                pystray.MenuItem("(no recent console games)", None, enabled=False),
            )

        items = [
            pystray.MenuItem(header_label, None, enabled=False),
            pystray.Menu.SEPARATOR,
        ]
        if watcher_running:
            if watcher_paused:
                items.append(pystray.MenuItem(
                    "Resume Watcher",
                    lambda _i, _it: self._post('resume_watcher'),
                ))
            else:
                items.append(pystray.MenuItem(
                    "Pause Watcher",
                    lambda _i, _it: self._post('pause_watcher'),
                ))
        else:
            items.append(pystray.MenuItem(
                "Start Watcher",
                lambda _i, _it: self._post('start_watcher'),
            ))

        items.extend([
            pystray.MenuItem(
                "Stop Current Session",
                lambda _i, _it: self._post('stop_session'),
                enabled=tracking_game is not None,
            ),
            pystray.MenuItem("Start Console Session", pystray.Menu(*console_items)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Open GamesList Manager",
                lambda _i, _it: self._post('open_app'),
                default=True,
            ),
            pystray.MenuItem(
                "Quit",
                lambda _i, _it: self._post('quit'),
            ),
        ])
        return pystray.Menu(*items)

    @staticmethod
    def _fallback_image():
        """A 64x64 transparent PNG placeholder if the .ico is missing."""
        return Image.new('RGBA', (64, 64), (60, 110, 200, 255))


# Module-level singleton ---------------------------------------------------

_TRAY: Optional[TrayIcon] = None


def initialize_tray(
    window,
    get_state: Callable[[], dict],
    get_console_games: Callable[[], List[str]],
) -> Optional[TrayIcon]:
    """Construct and start the tray icon. Returns the instance, or None."""
    global _TRAY
    if not _PYSTRAY_AVAILABLE:
        return None
    if _TRAY is not None:
        return _TRAY
    tray = TrayIcon(window, get_state, get_console_games)
    if tray.start():
        _TRAY = tray
        return _TRAY
    return None


def get_tray() -> Optional[TrayIcon]:
    return _TRAY


def cleanup_tray() -> None:
    global _TRAY
    if _TRAY is not None:
        _TRAY.stop()
        _TRAY = None
