"""Wire Discord Rich Presence into the Flet app (Phase 3D).

The backend ``discord_integration`` module is GUI-free and already drives the
playing/paused/complete presence from the watcher (the bridge calls it through
its ``discord_provider``). This module owns the *Flet side* of the lifecycle:

  * initialize at startup off the UI thread (Discord's IPC handshake can block
    briefly, and we don't want it to stall first paint);
  * keep the library stats + the "browsing" tab presence in sync;
  * expose an enable/disable toggle persisted in config (``discord_enabled``);
  * (cleanup on quit lives in ``watcher_runtime._quit_app``, which runs before
    ``os._exit`` - that bypasses the atexit handler the backend registers).

Everything degrades gracefully: if ``pypresence`` is missing, Discord isn't
running, or the client id is a placeholder, the backend's own ``is_connected``
guards turn every call into a no-op.
"""

import threading

from constants import STATUS_COMPLETED

# Nav index -> Discord tab label (matches the backend's activity_map keys).
TAB_NAMES = ["Games List", "Summary", "Statistics"]


def library_counts(service):
    """Return ``(total_games, completed_games)`` from the loaded library."""
    data = getattr(service, "data", None) or []
    total = len(data)
    completed = 0
    for entry in data:
        try:
            _idx, row = entry
        except Exception:  # noqa: BLE001
            continue
        if row and len(row) > 4 and row[4] == STATUS_COMPLETED:
            completed += 1
    return total, completed


def tab_name(index):
    """Map a NavigationRail index to a Discord tab label."""
    try:
        return TAB_NAMES[index]
    except (IndexError, TypeError):
        return "Games List"


def _apply_browsing(service, tab="Games List"):
    """Push current library stats + a browsing-presence for ``tab`` (no-op when
    Discord isn't connected)."""
    try:
        from discord_integration import get_discord_integration
        d = get_discord_integration()
        if d is None:
            return
        total, completed = library_counts(service)
        d.update_game_library_stats(total, completed)
        d.update_presence_browsing(tab)
    except Exception:  # noqa: BLE001
        pass


def start_discord(service):
    """Initialize Discord per config, off the UI thread (non-blocking).

    After connecting, pushes the initial library stats + browsing presence.
    """
    enabled = bool(service.config.get("discord_enabled", True))

    def _worker():
        try:
            from discord_integration import initialize_discord
            initialize_discord(enabled=enabled)
            if enabled:
                _apply_browsing(service, "Games List")
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_worker, name="discord-init", daemon=True).start()


def set_enabled(service, enabled):
    """Toggle Discord on/off, persist ``discord_enabled``, and apply immediately.

    Done off the UI thread because enabling connects to Discord (which can
    block). Safe to call before/after ``start_discord``.
    """
    enabled = bool(enabled)
    service.config["discord_enabled"] = enabled
    try:
        from config import save_config
        save_config(service.config)
    except Exception:  # noqa: BLE001
        pass

    def _worker():
        try:
            from discord_integration import (
                get_discord_integration,
                initialize_discord,
            )
            d = get_discord_integration()
            if enabled:
                if d is None:
                    initialize_discord(enabled=True)
                else:
                    d.enable_discord()
                _apply_browsing(service, "Games List")
            elif d is not None:
                d.disable_discord()
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_worker, name="discord-toggle", daemon=True).start()


def notify_tab(service, tab):
    """Update browsing presence + library stats for the current tab.

    ``tab`` may be a NavigationRail index (int) or a label (str).
    """
    if isinstance(tab, int):
        tab = tab_name(tab)
    _apply_browsing(service, tab)
