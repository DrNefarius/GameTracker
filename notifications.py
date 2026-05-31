"""
Toast notifications for the process watcher.

Thin wrapper over the `windows-toasts` package. All public functions soft-fail
to a no-op (returning ``None``) when:
  - we are not on Windows, or
  - `windows-toasts` is not installed, or
  - the user has disabled the relevant notification category in config, or
  - we are inside the configured quiet hours.

Action buttons on a toast are wired back into the main PySimpleGUI event loop
via ``window.write_event_value('-TOAST-ACTION-', payload)`` so the existing
event-loop dispatcher in main.py is the single source of truth for what each
action does.
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, time as dtime
from typing import Any, Callable, Dict, Optional

from config import load_config
from toast_aumid import TOAST_APP_NAME, ensure_toast_registration
from watcher_log import toast_logger

_log = toast_logger()

# windows-toasts is Windows-only; gate the import so the module imports cleanly
# on non-Windows hosts (the public functions become no-ops).
_TOASTS_AVAILABLE = False
_InteractableToaster = None
_Toast = None
_ToastButton = None
_ToastDisplayImage = None
_ToastScenario = None

if sys.platform == 'win32':
    try:
        # `InteractableWindowsToaster` is required for action buttons + the
        # `on_activated` callback to receive the button's `arguments`.
        # `WindowsToaster` (basic) silently drops buttons with a UserWarning.
        from windows_toasts import (
            InteractableWindowsToaster as _InteractableToaster,  # type: ignore
            Toast as _Toast,  # type: ignore
            ToastButton as _ToastButton,  # type: ignore
            ToastDisplayImage as _ToastDisplayImage,  # type: ignore
            ToastScenario as _ToastScenario,  # type: ignore
        )
        _TOASTS_AVAILABLE = True
    except Exception as _exc:  # noqa: BLE001 - any import error -> no toasts
        _log.warning("windows-toasts not available, notifications disabled: %s",
                     _exc)
        _TOASTS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_TOASTER = None  # lazily constructed WindowsToaster
_TOASTER_LOCK = threading.Lock()
# Map session_id -> last Toast instance, so end notifications can replace the
# corresponding start notification in the Action Center.
_LIVE_TOASTS: Dict[str, Any] = {}
# The PySimpleGUI Window the watcher is attached to. Set by `bind_window`.
_WINDOW = None


def bind_window(window) -> None:
    """Register the main PySimpleGUI window so toast actions can post events."""
    global _WINDOW
    _WINDOW = window


def is_available() -> bool:
    return _TOASTS_AVAILABLE


def _get_toaster():
    """Construct (once) and return the shared InteractableWindowsToaster."""
    global _TOASTER
    if not _TOASTS_AVAILABLE:
        return None
    with _TOASTER_LOCK:
        if _TOASTER is None:
            try:
                aumid = ensure_toast_registration()
                _TOASTER = _InteractableToaster(  # type: ignore[misc]
                    TOAST_APP_NAME,
                    notifierAUMID=aumid,
                )
                _log.debug("InteractableWindowsToaster constructed aumid=%s", aumid)
            except Exception as exc:  # noqa: BLE001
                _log.error("failed to construct InteractableWindowsToaster: %s", exc)
                _TOASTER = None
        return _TOASTER


# ---------------------------------------------------------------------------
# Quiet-hours / config helpers
# ---------------------------------------------------------------------------


def _in_quiet_hours(config: Dict[str, Any]) -> bool:
    """True when the current local time falls inside notifications_quiet_hours.

    Config value is either ``None`` or a 2-element list ``["HH:MM", "HH:MM"]``.
    Spans crossing midnight (e.g. 23:00 -> 08:00) are supported.
    """
    qh = config.get('notifications_quiet_hours')
    if not qh or len(qh) != 2:
        return False
    try:
        start_h, start_m = (int(p) for p in qh[0].split(':'))
        end_h, end_m = (int(p) for p in qh[1].split(':'))
    except Exception:
        return False
    now = datetime.now().time()
    start = dtime(start_h, start_m)
    end = dtime(end_h, end_m)
    if start <= end:
        return start <= now < end
    # Span crosses midnight.
    return now >= start or now < end


def _apply_focus_assist_bypass(toast, config: Dict[str, Any]) -> None:
    """Escalate a toast to the Reminder scenario so it pops over fullscreen apps.

    Windows' Focus Assist auto-enables a "playing a game" rule that suppresses
    normal toasts while a fullscreen / exclusive-fullscreen app is foreground -
    which is exactly when the watcher most wants to confirm tracking has
    started. The Reminder scenario is the lightest-weight category that
    reliably breaks through Focus Assist; the trade-off is the toast stays on
    screen until the user dismisses it (rather than auto-fading after ~5s).

    No-op when:
      - the user hasn't opted in (config flag off, default off);
      - we don't have access to the ToastScenario enum (older windows-toasts).
    """
    try:
        if not config.get('notifications_bypass_focus_assist', False):
            return
        if _ToastScenario is None:
            return
        toast.scenario = _ToastScenario.Reminder  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        _log.debug("scenario escalation failed: %s", exc)


def _post_action(payload: Dict[str, Any]) -> None:
    """Forward a toast action back to the PySimpleGUI event loop."""
    if _WINDOW is None:
        return
    try:
        _WINDOW.write_event_value('-TOAST-ACTION-', payload)
        _log.debug("toast action dispatched: %s", payload.get('action'))
    except Exception as exc:  # noqa: BLE001
        _log.warning("failed to dispatch toast action: %s", exc)


def _make_activation_callback(default_payload: Dict[str, Any]) -> Callable:
    """Build an `on_activated` handler that posts the action's argument back.

    `arguments` strings come from ToastButton(arguments=...). Body-click
    activations (no button) carry the empty string; we route those as the
    default payload's "default" action.
    """
    def _on_activated(event_args) -> None:
        try:
            action = getattr(event_args, 'arguments', '') or 'default'
        except Exception:
            action = 'default'
        payload = dict(default_payload)
        payload['action'] = action
        _post_action(payload)
    return _on_activated


# ---------------------------------------------------------------------------
# Public notification functions
# ---------------------------------------------------------------------------


def notify_info(title: str, message: str = "") -> bool:
    """Show a simple informational toast (no action buttons).

    Used for UI hints like "still running in the tray". Returns True if shown.
    """
    toaster = _get_toaster()
    if toaster is None or _Toast is None:
        return False
    try:
        toast = _Toast()
        toast.text_fields = [title, message] if message else [title]
        toaster.show_toast(toast)
        return True
    except Exception as exc:  # noqa: BLE001
        _log.error("notify_info failed: %s", exc)
        return False


def notify_session_started(
    session_id: str,
    game_name: str,
    cover_path: Optional[str] = None,
) -> None:
    """Show the 'Now tracking ...' toast. No-op if disabled / unavailable."""
    config = load_config()
    if not config.get('notifications_on_start', True):
        return
    if _in_quiet_hours(config):
        return
    toaster = _get_toaster()
    if toaster is None or _Toast is None or _ToastButton is None:
        return

    try:
        toast = _Toast()
        toast.text_fields = [f"Now tracking: {game_name}", "Started just now"]
        if cover_path and os.path.exists(cover_path) and _ToastDisplayImage is not None:
            try:
                toast.images = [_ToastDisplayImage.fromPath(cover_path)]  # type: ignore[attr-defined]
            except Exception:
                pass
        # Body-click and "Dismiss" only clear the toast (no app focus).
        toast.launch = f"dismiss|{session_id}"
        toast.AddAction(_ToastButton("Dismiss", arguments=f"dismiss|{session_id}"))
        toast.AddAction(_ToastButton("Wrong game?", arguments=f"remap|{session_id}"))
        toast.AddAction(_ToastButton("Don't track this session",
                                     arguments=f"discard|{session_id}"))
        toast.on_activated = _make_activation_callback({
            'kind': 'session_started',
            'session_id': session_id,
            'game': game_name,
        })
        _apply_focus_assist_bypass(toast, config)
        toaster.show_toast(toast)
        _LIVE_TOASTS[session_id] = toast
        _log.info("notify_session_started session=%s game=%r cover=%s bypass=%s",
                  session_id, game_name, bool(cover_path),
                  bool(config.get('notifications_bypass_focus_assist', False)))
    except Exception as exc:  # noqa: BLE001
        _log.error("notify_session_started failed: %s", exc, exc_info=True)


def dismiss_live_toast(session_id: str) -> bool:
    """Hide the start-toast for a session, if it's still on screen.

    Used when a session is discarded ("Don't track this session") or
    retitled in place - we don't want the original "Now tracking: WRONG"
    toast lingering in Action Center after the user already reacted to
    it. Returns True if a live toast was found and the dismissal call
    didn't error; False otherwise.
    """
    toaster = _get_toaster()
    prev = _LIVE_TOASTS.pop(session_id, None)
    if toaster is None or prev is None:
        return False
    try:
        toaster.remove_toast(prev)  # type: ignore[attr-defined]
        _log.debug("dismissed live start-toast for session=%s", session_id)
        return True
    except Exception as exc:  # noqa: BLE001
        _log.debug("dismiss_live_toast failed for session=%s: %s",
                   session_id, exc)
        return False


def notify_session_retitled(
    session_id: str,
    new_game_name: str,
    cover_path: Optional[str] = None,
) -> None:
    """Replace the live start-toast after a "Wrong game?" correction.

    Hides the old (mistitled) start-toast and pops a fresh one for the
    corrected title with the same action buttons - so the user gets
    visible confirmation that tracking is now under the right name and
    can immediately use Dismiss / Wrong game? / Don't track this session
    against the corrected session.
    """
    dismiss_live_toast(session_id)

    config = load_config()
    if not config.get('notifications_on_start', True):
        return
    if _in_quiet_hours(config):
        return
    toaster = _get_toaster()
    if toaster is None or _Toast is None or _ToastButton is None:
        return

    try:
        toast = _Toast()
        toast.text_fields = [f"Now tracking: {new_game_name}", "Updated from 'Wrong game?'"]
        if cover_path and os.path.exists(cover_path) and _ToastDisplayImage is not None:
            try:
                toast.images = [_ToastDisplayImage.fromPath(cover_path)]  # type: ignore[attr-defined]
            except Exception:
                pass
        toast.launch = f"dismiss|{session_id}"
        toast.AddAction(_ToastButton("Dismiss", arguments=f"dismiss|{session_id}"))
        toast.AddAction(_ToastButton("Wrong game?", arguments=f"remap|{session_id}"))
        toast.AddAction(_ToastButton("Don't track this session",
                                     arguments=f"discard|{session_id}"))
        toast.on_activated = _make_activation_callback({
            'kind': 'session_started',
            'session_id': session_id,
            'game': new_game_name,
        })
        _apply_focus_assist_bypass(toast, config)
        toaster.show_toast(toast)
        _LIVE_TOASTS[session_id] = toast
        _log.info("notify_session_retitled session=%s game=%r",
                  session_id, new_game_name)
    except Exception as exc:  # noqa: BLE001
        _log.error("notify_session_retitled failed: %s", exc, exc_info=True)


def notify_session_ended(
    session_id: str,
    game_name: str,
    duration_text: str,
) -> None:
    """Replace the start toast (if any) with an 'ended + rate?' toast."""
    config = load_config()
    if not config.get('notifications_on_end', True):
        return
    if _in_quiet_hours(config):
        return
    toaster = _get_toaster()
    if toaster is None or _Toast is None or _ToastButton is None:
        return

    try:
        # Best-effort: hide the live start toast so it doesn't accumulate.
        prev = _LIVE_TOASTS.pop(session_id, None)
        if prev is not None:
            try:
                toaster.remove_toast(prev)  # type: ignore[attr-defined]
            except Exception:
                pass

        toast = _Toast()
        toast.text_fields = [
            f"{game_name} - {duration_text}",
            "Tap to add notes or a rating",
        ]
        # Body-click defaults to opening the rating dialog.
        toast.launch = f"rate|{session_id}"
        toast.AddAction(_ToastButton("Rate it", arguments=f"rate|{session_id}"))
        toast.AddAction(_ToastButton("Dismiss", arguments=f"dismiss|{session_id}"))
        toast.on_activated = _make_activation_callback({
            'kind': 'session_ended',
            'session_id': session_id,
            'game': game_name,
            'duration': duration_text,
        })
        _apply_focus_assist_bypass(toast, config)
        toaster.show_toast(toast)
        _log.info("notify_session_ended session=%s game=%r duration=%s",
                  session_id, game_name, duration_text)
    except Exception as exc:  # noqa: BLE001
        _log.error("notify_session_ended failed: %s", exc, exc_info=True)


def notify_match_confirmation(
    detection_id: str,
    exe_basename: str,
    install_dir: str,
    best_guess: Optional[str],
) -> None:
    """Toast asking the user to confirm an ambiguous detection."""
    config = load_config()
    if not config.get('notifications_on_match_needed', True):
        return
    if _in_quiet_hours(config):
        return
    toaster = _get_toaster()
    if toaster is None or _Toast is None or _ToastButton is None:
        return

    try:
        guess_line = f"Best guess: {best_guess}" if best_guess else "No close match in your library"
        toast = _Toast()
        toast.text_fields = [
            f"Detected '{exe_basename}'",
            guess_line,
            install_dir,
        ]
        # Body click brings up the picker (safer than auto-confirming a guess).
        toast.launch = f"pick|{detection_id}"
        if best_guess:
            toast.AddAction(_ToastButton("Yes, that's it",
                                         arguments=f"confirm|{detection_id}"))
        toast.AddAction(_ToastButton("Pick another",
                                     arguments=f"pick|{detection_id}"))
        toast.AddAction(_ToastButton("Never for this exe",
                                     arguments=f"ignore|{detection_id}"))
        toast.on_activated = _make_activation_callback({
            'kind': 'match_confirmation',
            'detection_id': detection_id,
            'exe': exe_basename,
            'install_dir': install_dir,
            'best_guess': best_guess,
        })
        _apply_focus_assist_bypass(toast, config)
        toaster.show_toast(toast)
        _log.info("notify_match_confirmation detection=%s exe=%s best_guess=%r",
                  detection_id, exe_basename, best_guess)
    except Exception as exc:  # noqa: BLE001
        _log.error("notify_match_confirmation failed: %s", exc, exc_info=True)
