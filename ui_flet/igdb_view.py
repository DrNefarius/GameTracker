"""IGDB settings dialog for the Flet UI.

A dependency-free (no PySimpleGUI) port of the IGDB settings dialog from
``igdb_ui.show_igdb_settings_dialog``. It edits the four IGDB-related config
keys (``igdb_client_id``, ``igdb_client_secret``, ``igdb_enabled``,
``igdb_auto_match_on_add``) through ``service.config`` and persists them with
``config.save_config(service.config)``.

The network "Test connection" button reuses the sg-free backend in
``igdb_integration`` (``IGDBClient(...).test_connection()``), which performs a
Twitch OAuth client-credentials token fetch plus a trivial IGDB query. The call
is blocking, so it runs on a worker thread via ``page.run_thread`` and updates a
status Text when it finishes, keeping the UI responsive.

``_build_settings_content`` is split out so the body can be constructed and
inspected without a live Flet page (used by the smoke test).
"""

import flet as ft

import config as config_module
from igdb_integration import (
    IGDBAuthError,
    IGDBClient,
    IGDBError,
    reset_igdb_client,
)


def _build_settings_content(service):
    """Build the settings form controls.

    Returns ``(content, get_values_fn, refs)`` where:
      - ``content`` is the Flet Control to drop into a dialog's ``content``.
      - ``get_values_fn()`` returns the current
        ``(client_id, client_secret, enabled, auto_match)`` tuple, with the two
        string values stripped.
      - ``refs`` is a dict of the individual controls (``client_id``,
        ``client_secret``, ``enabled``, ``auto_match``, ``status``) so callers
        (and tests) can wire handlers / assert state without re-parsing the
        tree.

    No live page is required to call this, so it is safe to use in tests.
    """
    cfg = getattr(service, "config", {}) or {}

    enabled = ft.Checkbox(
        label="Enable IGDB integration",
        value=bool(cfg.get("igdb_enabled", False)),
    )
    client_id = ft.TextField(
        label="Client ID",
        value=str(cfg.get("igdb_client_id", "") or ""),
        width=420,
    )
    client_secret = ft.TextField(
        label="Client Secret",
        value=str(cfg.get("igdb_client_secret", "") or ""),
        password=True,
        can_reveal_password=True,
        width=420,
    )
    auto_match = ft.Checkbox(
        label="Auto-apply strong matches when fetching metadata",
        value=bool(cfg.get("igdb_auto_match_on_add", False)),
        tooltip="Skip the picker when a single candidate matches name + year",
    )
    status = ft.Text("", color=ft.Colors.GREY, selectable=True)

    content = ft.Container(
        width=460,
        content=ft.Column(
            [
                ft.Text(
                    "IGDB integration uses a Twitch application. "
                    "See IGDB_SETUP.md for steps.",
                    italic=True,
                    size=12,
                    color=ft.Colors.GREY,
                ),
                enabled,
                client_id,
                client_secret,
                auto_match,
                status,
            ],
            tight=True,
            spacing=12,
        ),
    )

    def get_values():
        return (
            (client_id.value or "").strip(),
            (client_secret.value or "").strip(),
            bool(enabled.value),
            bool(auto_match.value),
        )

    refs = {
        "client_id": client_id,
        "client_secret": client_secret,
        "enabled": enabled,
        "auto_match": auto_match,
        "status": status,
    }
    return content, get_values, refs


def open_igdb_settings_dialog(page, service, on_saved=None):
    """Open the IGDB settings dialog.

    Lets the user edit the IGDB credentials / flags, test the connection, and
    save. On save the four config keys are written into ``service.config``,
    persisted via ``config.save_config``, the cached IGDB client is reset, the
    dialog is closed, and ``on_saved()`` is invoked when provided. Cancel closes
    without saving.
    """
    content, get_values, refs = _build_settings_content(service)
    status = refs["status"]

    def _snack(message):
        sb = ft.SnackBar(content=ft.Text(message))
        page.overlay.append(sb)
        sb.open = True
        page.update()

    def on_test(_):
        client_id, client_secret, _enabled, _auto = get_values()
        if not client_id or not client_secret:
            status.value = "Enter both Client ID and Client Secret first."
            status.color = ft.Colors.ORANGE
            page.update()
            return

        status.value = "Testing connection..."
        status.color = ft.Colors.GREY
        test_button.disabled = True
        page.update()

        def _worker():
            try:
                IGDBClient(client_id, client_secret).test_connection()
                ok, message, color = True, "Connection succeeded.", ft.Colors.GREEN
            except IGDBAuthError as exc:
                ok, message, color = False, f"Auth failed: {exc}", ft.Colors.RED
            except IGDBError as exc:
                ok, message, color = False, f"IGDB error: {exc}", ft.Colors.RED
            except Exception as exc:  # noqa: BLE001
                ok, message, color = False, f"Unexpected error: {exc}", ft.Colors.RED

            status.value = message
            status.color = color
            test_button.disabled = False
            page.update()
            _snack(message if ok else f"Test connection failed: {message}")

        # Run the blocking network call off the UI thread.
        page.run_thread(_worker)

    def on_save(_):
        client_id, client_secret, enabled_val, auto_val = get_values()
        service.config["igdb_client_id"] = client_id
        service.config["igdb_client_secret"] = client_secret
        service.config["igdb_enabled"] = enabled_val
        service.config["igdb_auto_match_on_add"] = auto_val

        if not config_module.save_config(service.config):
            status.value = "Failed to save settings."
            status.color = ft.Colors.RED
            page.update()
            _snack("Failed to save IGDB settings.")
            return

        # Force the next get_igdb_client() to rebuild with the new credentials.
        reset_igdb_client()
        page.pop_dialog()
        if on_saved:
            on_saved()

    test_button = ft.Button(
        "Test connection",
        icon=ft.Icons.WIFI_TETHERING,
        on_click=on_test,
    )

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("IGDB Settings"),
        content=content,
        actions=[
            test_button,
            ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
            ft.Button("Save", icon=ft.Icons.SAVE, on_click=on_save,
                      bgcolor=ft.Colors.GREEN, color=ft.Colors.WHITE),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dialog)
