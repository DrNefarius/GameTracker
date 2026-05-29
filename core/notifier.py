"""UINotifier protocol - the seam between the background process watcher and
whatever UI is running.

Phase 3 of the Version2 migration will refactor ``session_watcher_bridge.py`` to
talk to this protocol instead of calling PySimpleGUI directly
(``sg.popup_*`` / ``window.write_event_value`` / ``session_ui`` imports). The
Flet UI then supplies a concrete implementation (SnackBars, dialogs, etc.).

Defined here now so the facade package owns the contract; not yet wired.
"""

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class UINotifier(Protocol):
    def notify(self, title: str, message: str) -> None:
        """Show a transient, non-blocking message (e.g. a SnackBar/toast)."""

    def confirm_match(self, detection: dict) -> Optional[str]:
        """Ask the user to confirm/choose the game for an ambiguous detection.

        Returns the chosen game name, or None if dismissed.
        """

    def request_feedback(self, existing: Optional[dict]) -> Optional[dict]:
        """Prompt for end-of-session feedback/rating; return it or None."""
