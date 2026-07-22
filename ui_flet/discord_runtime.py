"""Wire Discord Rich Presence into the Flet app (Phase 3D).

The backend ``discord_integration`` module is GUI-free and already drives the
playing/paused/complete presence from the watcher (the bridge calls it through
its ``discord_provider``). This module owns the *Flet side* of the lifecycle:
startup init, library-stats/browsing presence, the enable/disable toggle (config
``discord_enabled``), and cleanup on quit.

**Threading (important).** pypresence's synchronous ``Presence`` drives its own
asyncio loop via ``run_until_complete``. In the Flet app every natural caller -
tab handlers *and* watcher events (which are marshalled onto the Flet loop via
``page.run_task``) - runs on the thread that owns the *running* Flet asyncio
loop, where ``run_until_complete`` raises "Cannot run the event loop while
another loop is running". (The legacy PySimpleGUI app never hit this: its event
loop is synchronous.) So we funnel **every** Discord operation - connect, update,
clear, close, and the watcher's presence calls - onto a single dedicated daemon
worker thread that never runs an asyncio loop. That also serializes access to
pypresence's non-thread-safe socket. The watcher reaches the worker through
:func:`provider` (a thread-confining proxy) wired as the bridge's
``discord_provider``.

Everything degrades gracefully: if ``pypresence`` is missing, Discord isn't
running, or the client id is a placeholder, the backend's ``is_connected``
guards turn every call into a no-op.
"""

import queue
import threading

from constants import STATUS_COMPLETED

# Nav index -> Discord tab label (matches the backend's activity_map keys).
TAB_NAMES = ["Games List", "Summary", "Statistics"]

# Single-thread work queue: every pypresence call runs here, off the Flet loop.
_q: "queue.Queue" = None  # type: ignore[assignment]
_worker: "threading.Thread" = None  # type: ignore[assignment]
_worker_lock = threading.Lock()


def _ensure_worker():
    global _q, _worker
    with _worker_lock:
        if _worker is None:
            _q = queue.Queue()
            _worker = threading.Thread(
                target=_run, name="discord-worker", daemon=True)
            _worker.start()


def _run():
    while True:
        fn, done = _q.get()
        try:
            if fn is not None:
                fn()
        except Exception:  # noqa: BLE001
            pass
        finally:
            if done is not None:
                done.set()
            _q.task_done()


def _submit(fn, wait=False, timeout=None):
    """Queue ``fn`` to run on the Discord worker thread.

    With ``wait=True`` block (up to ``timeout`` s) until it has run - used by
    :func:`shutdown` so the presence clear flushes before the process exits.
    """
    _ensure_worker()
    done = threading.Event() if wait else None
    _q.put((fn, done))
    if done is not None:
        done.wait(timeout)


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Worker-thread bodies (run via _submit)
# --------------------------------------------------------------------------- #
def _do_apply_browsing(service, tab):
    """Push current library stats + a browsing presence (worker thread)."""
    from discord_integration import get_discord_integration
    d = get_discord_integration()
    if d is None:
        return
    try:
        total, completed = library_counts(service)
        d.update_game_library_stats(total, completed)
        d.update_presence_browsing(tab)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Public API (called from the Flet/UI thread; work hops to the worker)
# --------------------------------------------------------------------------- #
def start_discord(service):
    """Initialize Discord per config on the worker thread, then push the initial
    browsing presence. Non-blocking for the caller."""
    enabled = bool(service.config.get("discord_enabled", True))

    def _work():
        from discord_integration import initialize_discord
        initialize_discord(enabled=enabled)
        if enabled:
            _do_apply_browsing(service, "Games List")

    _submit(_work)


def set_enabled(service, enabled):
    """Toggle Discord on/off, persist ``discord_enabled``, apply on the worker."""
    enabled = bool(enabled)
    try:
        service.update_config({"discord_enabled": enabled})
    except Exception:  # noqa: BLE001
        service.config["discord_enabled"] = enabled

    def _work():
        from discord_integration import get_discord_integration, initialize_discord
        d = get_discord_integration()
        if enabled:
            if d is None:
                initialize_discord(enabled=True)
            else:
                d.enable_discord()
            _do_apply_browsing(service, "Games List")
        elif d is not None:
            d.disable_discord()

    _submit(_work)


def notify_tab(service, tab):
    """Update browsing presence + library stats for the current tab.

    ``tab`` may be a NavigationRail index (int) or a label (str).
    """
    if isinstance(tab, int):
        tab = tab_name(tab)
    _submit(lambda: _do_apply_browsing(service, tab))


def shutdown(timeout=2.0):
    """Disconnect Discord on the worker thread and wait briefly so the presence
    clear actually flushes before the process exits (``os._exit`` won't wait)."""
    def _work():
        from discord_integration import cleanup_discord
        cleanup_discord()

    _submit(_work, wait=True, timeout=timeout)


# --------------------------------------------------------------------------- #
# Thread-confining proxy for the watcher bridge's discord_provider
# --------------------------------------------------------------------------- #
class _DiscordProxy:
    """Forwards every method call onto the Discord worker thread.

    The watcher bridge does ``discord = provider(); discord.update_presence_*()``
    from the Flet loop; routing through here keeps pypresence off that loop.
    Fire-and-forget (returns ``None``); a missing/None integration is a no-op.
    """

    def __getattr__(self, name):
        def _method(*args, **kwargs):
            def _call():
                from discord_integration import get_discord_integration
                d = get_discord_integration()
                if d is None:
                    return
                fn = getattr(d, name, None)
                if callable(fn):
                    fn(*args, **kwargs)
            _submit(_call)
        return _method


_PROXY = _DiscordProxy()


def provider():
    """Stable proxy for the bridge's ``discord_provider`` - routes the watcher's
    playing/paused/complete presence onto the Discord worker thread."""
    return _PROXY
