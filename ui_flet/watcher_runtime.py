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
:class:`FletWatcherNotifier`. The interactive toast actions that need a dialog
are handled here directly, opening Flet dialogs (``ui_flet.watcher_dialogs``)
that delegate mutation to the bridge's GUI-free apply helpers:

  * end-of-session **Rate** -> feedback dialog, attached to the last session;
  * session-start **Wrong game?** -> remap dialog;
  * ambiguous-match **Pick another** -> match-picker dialog.

Crash orphan-recovery (offer to record a session interrupted by a crash) is
driven from ``app.main`` at startup via the same bridge helpers.
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
        # NB: page.window.to_front() / center() are async coroutines in Flet
        # 0.85, so calling them synchronously is a no-op. Use the synchronous
        # window *properties* instead: restore + focus, and briefly flip
        # always_on_top to nudge the window to the foreground (the reliable
        # cross-platform trick), then release it.
        try:
            win = self.page.window
            win.visible = True
            win.minimized = False
            win.focused = True
            win.always_on_top = True
            self.page.update()
            win.always_on_top = False
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
        self.tray = None                       # set by start_watcher
        self.on_watcher_state_changed = None   # set by app.py to sync the toggle

    # process_watcher / notifications call this from their own threads.
    def write_event_value(self, key, payload=None):
        try:
            self.page.run_task(self._dispatch, key, payload or {})
        except Exception as exc:  # pragma: no cover - defensive
            print(f"watcher sink: could not marshal {key}: {exc}")

    async def _dispatch(self, key, payload):
        try:
            if key == "-TRAY-ACTION-":
                if (payload or {}).get("action") == "quit":
                    await self._quit_app()   # async: window.destroy() is a coroutine
                    return
                self._handle_tray_action(payload)
                self._safe_update()
                return
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
        """Intercept the interactive toast buttons that need a Flet dialog.

        Handles the end-of-session 'Rate', the session-start 'Wrong game?'
        remap, and the ambiguous-match 'Pick another' actions here so they open
        Flet dialogs instead of the bridge's PySimpleGUI ones. Returns True when
        fully handled (so the bridge's sg path isn't used). Everything else
        (discard / confirm / ignore / dismiss) is GUI-free and falls through to
        the bridge.
        """
        kind = payload.get("kind")
        action = (payload.get("action") or "").split("|", 1)[0]

        if kind == "session_ended" and action == "rate":
            self.notifier.focus()
            game = payload.get("game")
            if game:
                self._rate_last_session(game)
            return True

        if kind == "session_started" and action == "remap":
            self.notifier.focus()
            self._open_remap_dialog()
            return True

        if kind == "match_confirmation" and action == "pick":
            self.notifier.focus()
            self._open_match_picker_dialog(payload.get("detection_id") or "", payload)
            return True

        return False

    def _open_remap_dialog(self):
        from ui_flet.watcher_dialogs import open_remap_dialog
        open_remap_dialog(self.page, self.bridge, notify=self.notifier.notify)

    def _open_match_picker_dialog(self, detection_id, payload):
        from ui_flet.watcher_dialogs import open_match_picker_dialog
        open_match_picker_dialog(self.page, self.bridge, detection_id, payload,
                                 notify=self.notifier.notify)

    def drain_pending_matches_on_focus(self):
        """Re-fire OS toasts for any unresolved ambiguous matches.

        Called when the window regains focus. The bridge method is GUI-free (it
        only re-posts toasts); clicking "Pick another" then routes back here and
        opens the Flet picker.
        """
        try:
            self.bridge.drain_pending_matches_on_focus(None)
        except Exception:
            pass

    def _handle_tray_action(self, payload):
        from process_watcher import get_watcher
        action = payload.get("action")
        sub = payload.get("payload") or {}
        w = get_watcher()
        if action == "open_app":
            self.notifier.focus()
        elif action == "pause_watcher":
            if w is not None:
                w.pause()
        elif action == "resume_watcher":
            if w is not None:
                w.resume()
        elif action == "start_watcher":
            if w is not None:
                w.start()
            self.service.config["watcher_enabled"] = True
            try:
                from config import save_config
                save_config(self.service.config)
            except Exception:
                pass
            if self.on_watcher_state_changed:
                try:
                    self.on_watcher_state_changed(True)
                except Exception:
                    pass
        elif action == "stop_session":
            if w is not None:
                w.stop_current_session()
        elif action == "start_console":
            game = sub.get("game")
            if game and w is not None:
                w.start_manual_session(game, self._platform_for(game))

    def _platform_for(self, game_name):
        for _idx, row in self.service.data:
            if row and row[0] == game_name:
                return row[2] if len(row) > 2 else None
        return None

    async def _quit_app(self):
        # Stop the active session cleanly, then close. window.destroy() is an
        # async coroutine in Flet 0.85 (must be awaited), and we os._exit() as a
        # guaranteed fallback so the process can't linger.
        try:
            from process_watcher import cleanup_watcher
            cleanup_watcher()
        except Exception:
            pass
        try:
            from tray_icon import cleanup_tray
            cleanup_tray()
        except Exception:
            pass
        try:
            # Clears the rich presence + closes the IPC socket. Must be explicit:
            # os._exit below bypasses the atexit handler the module registers.
            from discord_integration import cleanup_discord
            cleanup_discord()
        except Exception:
            pass
        try:
            self.page.window.prevent_close = False
            await self.page.window.destroy()
        except Exception:
            pass
        import os
        os._exit(0)

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

    Returns the FletWatcherSink (``.tray`` is the TrayIcon or None). The
    watcher/bridge/notifier are kept alive via the watcher's ``_flet`` attr.
    """
    from session_watcher_bridge import SessionWatcherBridge
    from process_watcher import initialize_watcher
    from notifications import bind_window
    from discord_integration import get_discord_integration

    notifier = FletWatcherNotifier(page)
    bridge = SessionWatcherBridge(
        window_provider=lambda: None,
        data_provider=lambda: service.data,
        data_storage_provider=lambda: None,
        filename_provider=lambda: service.filename,
        # Lazy provider: returns the live Discord integration (or None until it
        # finishes connecting) so playing/paused/complete presence is driven
        # straight from the watcher's session events.
        discord_provider=get_discord_integration,
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
        # Idle / foreground hooks so idle-pause + foreground-only tracking work.
        try:
            from idle_detection import get_idle_seconds, get_foreground_pid
            watcher.idle_seconds_provider = get_idle_seconds
            watcher.foreground_pid_provider = get_foreground_pid
        except Exception as exc:  # pragma: no cover - platform-dependent
            print(f"idle/foreground hooks unavailable: {exc}")

    # System tray (its own thread). Uses the same sink for its actions.
    if service.config.get("tray_icon_enabled", True):
        try:
            from tray_icon import initialize_tray

            def _tray_state():
                snap = {}
                if watcher is not None:
                    try:
                        snap.update(watcher.get_state_snapshot() or {})
                    except Exception:
                        pass
                snap.update(bridge.build_state_snapshot() or {})
                return snap

            sink.tray = initialize_tray(
                sink, get_state=_tray_state,
                get_console_games=lambda: bridge.list_recent_console_games(15))
        except Exception as exc:  # pragma: no cover - platform-dependent
            print(f"tray init failed: {exc}")
    return sink
