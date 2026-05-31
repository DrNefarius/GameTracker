"""Run the background process-watcher inside the Flet app.

``process_watcher`` runs in its own thread and "emits" events by calling
``write_event_value(key, payload)`` on whatever object it was handed; the
``notifications`` module (OS toasts) posts toast-button clicks the same way via
``bind_window``. We give both a :class:`FletWatcherSink` that marshals each event
onto the Flet event loop (``page.run_task`` — safe to call from any thread) and
feeds it to the existing :class:`SessionWatcherBridge`, then refreshes the UI.

The bridge's detect / end / pause / status handlers are already GUI-free (they
persist sessions and fire OS toasts), so auto-tracking works as-is. The bridge's
interactive bits (focus / discard message / info) are routed through a
:class:`FletWatcherNotifier`. The end-of-session "Rate" toast action is handled
here directly: it opens the Flet feedback dialog and attaches the result to the
game's most recent session.

Not yet ported (Phase 3C): the in-app match-picker / remap dialogs and crash
orphan-recovery — those only trigger for unrecognized games or a mid-session
crash; until then an ambiguous detection just shows its OS toast.
"""

from datetime import datetime

import flet as ft


class FletWatcherNotifier:
    """UINotifier impl for the watcher bridge: SnackBars + window focus."""

    def __init__(self, page):
        self.page = page

    def notify(self, title, message=None):
        text = message if message is not None else title
        try:
            bar = ft.SnackBar(content=ft.Text(text), duration=3500)
            self.page.overlay.append(bar)
            bar.open = True
            self.page.update()
        except Exception:
            pass

    def info(self, title, message):
        self.notify(title, message)

    def focus(self):
        try:
            self.page.window.visible = True
            self.page.window.to_front()
            self.page.update()
        except Exception:
            pass


class FletWatcherSink:
    """A ``write_event_value``-compatible event sink that hops onto the Flet loop."""

    def __init__(self, page, bridge, service, refresh_cb, notifier):
        self.page = page
        self.bridge = bridge
        self.service = service
        self.refresh_cb = refresh_cb
        self.notifier = notifier

    # process_watcher / notifications call this from their own threads.
    def write_event_value(self, key, payload=None):
        try:
            self.page.run_task(self._dispatch, key, payload or {})
        except Exception as exc:  # pragma: no cover - defensive
            print(f"watcher sink: could not marshal {key}: {exc}")

    async def _dispatch(self, key, payload):
        try:
            if key == "-TOAST-ACTION-" and self._handle_toast_action(payload):
                self._safe_update()
                return
            result = self.bridge.handle_event(key, payload)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"watcher dispatch error on {key}: {exc}")
            return

        if result and result.get("action") in ("session_added", "watcher_session_started"):
            try:
                self.refresh_cb()
            except Exception:
                pass
        self._safe_update()

    def _safe_update(self):
        try:
            self.page.update()
        except Exception:
            pass

    def _handle_toast_action(self, payload):
        """Intercept the interactive end-of-session 'Rate' toast button.

        Returns True if fully handled here (so the bridge's sg path isn't used).
        """
        action = (payload.get("action") or "").split("|", 1)[0]
        if payload.get("kind") == "session_ended" and action == "rate":
            self.notifier.focus()
            game = payload.get("game")
            if game:
                self._rate_last_session(game)
            return True
        return False

    def _rate_last_session(self, game_name):
        from ui_flet.session_dialogs import open_feedback_dialog
        from session_data import get_game_sessions

        sessions = get_game_sessions(self.service.data, game_name) or []
        if not sessions:
            return

        def _start_key(s):
            try:
                return datetime.fromisoformat(s.get("start", ""))
            except (ValueError, TypeError):
                return datetime.min

        target = max(sessions, key=_start_key)

        def _on_result(feedback):
            if feedback is not None:
                target["feedback"] = feedback
                self.service.save()
                try:
                    self.refresh_cb()
                except Exception:
                    pass

        open_feedback_dialog(self.page, existing=target.get("feedback"), on_result=_on_result)


def start_watcher(page, service, refresh_cb):
    """Construct the bridge + sink, point notifications + the watcher at them,
    and start the watcher if it's enabled in config.

    Returns the ProcessWatcher (or None). The caller must keep the returned
    bridge/sink alive (they are stashed on the returned watcher's ``_flet`` attr).
    """
    from session_watcher_bridge import SessionWatcherBridge
    from process_watcher import initialize_watcher
    from notifications import bind_window

    notifier = FletWatcherNotifier(page)
    bridge = SessionWatcherBridge(
        window_provider=lambda: None,
        data_provider=lambda: service.data,
        data_storage_provider=lambda: None,
        filename_provider=lambda: service.filename,
        discord_provider=lambda: None,   # Discord wired in Phase 3D
        notifier=notifier,
    )
    sink = FletWatcherSink(page, bridge, service, refresh_cb, notifier)
    bind_window(sink)

    enabled = bool(service.config.get("watcher_enabled", False))
    watcher = initialize_watcher(sink, library_provider=bridge.build_library_snapshot,
                                 enabled=enabled)
    if watcher is not None:
        # Keep strong refs so they aren't GC'd while the watcher thread runs.
        watcher._flet = (bridge, sink, notifier)  # type: ignore[attr-defined]
    return watcher
