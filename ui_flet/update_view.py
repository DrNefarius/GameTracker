"""Auto-updater UI for the Flet app (Phase 3E) - Flet port of ``update_ui.py``.

The backend ``auto_updater`` is GUI-free. This module renders the user-facing
flow on top of it:

  * **Update available** notification - version info + GitHub markdown release
    notes (``ft.Markdown``, far nicer than the legacy HTML->text scrub) + a link
    to the release.
  * **Download** + **staging** progress dialogs (``ft.ProgressBar``) with cancel.
  * **Install confirmation** and a **restart** prompt.
  * **Update Settings** dialog (check-on-startup toggle + Check now / Open
    downloads / Clear downloads).
  * **Success** popup shown on the launch after an update completed.
  * ``startup_check`` (success popup + an auto-check when enabled).

Long-running backend calls (check / download / stage) run on daemon threads;
their progress callbacks fire from those threads, so every UI mutation hops back
onto the Flet loop via ``page.run_task``.

Note: actually replacing files + relaunching only takes full effect in a
**packaged build** (the backend stages into the install dir and restarts via an
external script). From source it stages against the repo; the UI is fully
portable but that final install step is a packaged-build concern.
"""

import html
import os
import re
import subprocess
import sys
import threading
import webbrowser

import flet as ft


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _run_on(page, coro_fn, *args):
    """Schedule an async UI update on the Flet loop from any thread."""
    try:
        page.run_task(coro_fn, *args)
    except Exception:
        pass


def _snack(page, message, error=False):
    try:
        bar = ft.SnackBar(
            content=ft.Text(message),
            bgcolor=ft.Colors.ERROR if error else None,
            duration=3000,
        )
        page.overlay.append(bar)
        bar.open = True
        page.update()
    except Exception:
        pass


def _snack_from_thread(page, message, error=False):
    async def _u():
        _snack(page, message, error=error)
    _run_on(page, _u)


def _open_path(path):
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def _info_dialog(page, title, message):
    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text(title),
        content=ft.Text(message),
        actions=[ft.TextButton("OK", on_click=lambda e: page.pop_dialog())],
        actions_alignment=ft.MainAxisAlignment.END,
    ))


def _downloads_dir():
    from config import get_config_dir
    return os.path.join(get_config_dir(), "downloads")


# GitHub release bodies frequently embed images as raw HTML <img> tags (the
# release editor inserts them when you paste/drag an image), e.g.
#   <img width="860" alt="foo" src="https://github.com/user-attachments/...">
# ft.Markdown does NOT parse raw inline HTML, so those tags render as literal
# text. flutter_markdown *does* render Markdown image syntax (![alt](url)) as a
# network image, so rewrite each <img> into that form before rendering.
_IMG_TAG_RE = re.compile(r"<img\b[^>]*?/?>", re.IGNORECASE | re.DOTALL)


def _attr(tag, name):
    """Extract an HTML attribute value (double/single/unquoted) from a tag."""
    m = re.search(
        r"\b" + name + r"""\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
        tag, re.IGNORECASE,
    )
    if not m:
        return None
    return m.group(1) or m.group(2) or m.group(3)


def _notes_to_markdown(text):
    """Rewrite raw HTML <img> tags in release notes to Markdown image syntax."""
    if not text:
        return text

    def _repl(m):
        tag = m.group(0)
        src = _attr(tag, "src")
        if not src:
            return ""  # drop a srcless tag rather than leave raw HTML behind
        alt = _attr(tag, "alt") or "image"
        return "![{0}]({1})".format(html.unescape(alt), html.unescape(src))

    return _IMG_TAG_RE.sub(_repl, text)


# --------------------------------------------------------------------------- #
# Update Settings
# --------------------------------------------------------------------------- #
def open_update_settings_dialog(page, service=None):
    """Update preferences: check-on-startup toggle + manual actions."""
    from auto_updater import get_updater
    updater = get_updater()

    check_cb = ft.Checkbox(
        label="Check for updates when the app starts",
        value=bool(getattr(updater, "check_on_startup_enabled", True)),
    )

    def _save(_):
        try:
            updater.set_check_on_startup_enabled(bool(check_cb.value))
        except Exception:
            pass
        page.pop_dialog()
        _snack(page, "Update settings saved.")

    def _check_now(_):
        page.pop_dialog()
        check_for_updates_manual(page)

    def _open_downloads(_):
        d = _downloads_dir()
        os.makedirs(d, exist_ok=True)
        _open_path(d)

    def _clear_downloads(_):
        page.pop_dialog()
        _clear_downloads_flow(page)

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Update Settings"),
        content=ft.Container(width=460, content=ft.Column(
            [
                ft.Text(f"Current version: {getattr(updater, 'current_version', '?')}",
                        size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                check_cb,
                ft.Divider(),
                ft.Text("Manual actions", weight=ft.FontWeight.W_600, size=12),
                ft.Row(
                    [
                        ft.OutlinedButton("Check now", icon=ft.Icons.REFRESH,
                                          on_click=_check_now),
                        ft.OutlinedButton("Open downloads", icon=ft.Icons.FOLDER_OPEN,
                                          on_click=_open_downloads),
                        ft.OutlinedButton("Clear downloads", icon=ft.Icons.DELETE_OUTLINE,
                                          on_click=_clear_downloads),
                    ],
                    wrap=True, spacing=8,
                ),
            ],
            tight=True, spacing=12,
        )),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
            ft.Button("Save", icon=ft.Icons.SAVE, on_click=_save),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    ))


def _clear_downloads_flow(page):
    import shutil
    d = _downloads_dir()
    files = ([f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))]
             if os.path.isdir(d) else [])
    if not files:
        _info_dialog(page, "Clear downloads", "No downloaded updates to clear.")
        return

    def _do(_):
        page.pop_dialog()
        try:
            shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)
            _snack(page, f"Cleared {len(files)} downloaded file(s).")
        except Exception as exc:  # noqa: BLE001
            _snack(page, f"Clear failed: {exc}", error=True)

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Clear downloads"),
        content=ft.Text(f"Delete {len(files)} downloaded update file(s)?"),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
            ft.Button("Delete", icon=ft.Icons.DELETE, color=ft.Colors.WHITE,
                      bgcolor=ft.Colors.RED, on_click=_do),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    ))


# --------------------------------------------------------------------------- #
# Manual "Check for updates"
# --------------------------------------------------------------------------- #
def check_for_updates_manual(page, on_no_update=None):
    """Show a checking spinner, query GitHub off-thread, then notify."""
    from auto_updater import get_updater
    updater = get_updater()

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Checking for updates"),
        content=ft.Container(width=340, content=ft.Row(
            [ft.ProgressRing(width=22, height=22), ft.Text("Contacting GitHub…")],
            spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER)),
    ))

    holder = {}

    def _work():
        try:
            holder["info"] = updater.check_for_updates()
        except Exception as exc:  # noqa: BLE001
            holder["error"] = str(exc)

        async def _after():
            page.pop_dialog()  # close the checking dialog
            if holder.get("error"):
                _snack(page, f"Update check failed: {holder['error']}", error=True)
                return
            info = holder.get("info")
            if info:
                open_update_notification(page, info)
            else:
                _info_dialog(page, "No updates",
                             "You're running the latest version "
                             f"({getattr(updater, 'current_version', '?')}).")
                if on_no_update:
                    on_no_update()

        _run_on(page, _after)

    threading.Thread(target=_work, name="update-check", daemon=True).start()


# --------------------------------------------------------------------------- #
# Update-available notification
# --------------------------------------------------------------------------- #
def open_update_notification(page, update_info):
    from auto_updater import get_updater

    current = update_info.get("current_version", "?")
    new = update_info.get("version", "?")
    name = update_info.get("name") or f"Version {new}"
    notes = _notes_to_markdown(update_info.get("notes") or "_No release notes provided._")
    url = update_info.get("url")

    def _download(_):
        page.pop_dialog()
        run_update_process(page, update_info)

    def _disable(_):
        try:
            get_updater().set_check_on_startup_enabled(False)
        except Exception:
            pass
        page.pop_dialog()
        _snack(page, "Startup update checks disabled.")

    body_controls = [
        ft.Row([ft.Icon(ft.Icons.SYSTEM_UPDATE, color=ft.Colors.PRIMARY),
                ft.Text("Update available", size=18, weight=ft.FontWeight.BOLD)],
               spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Text(f"Current: {current}    →    New: {new}", size=13),
        ft.Text(name, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
        ft.Divider(),
        ft.Text("Release notes", weight=ft.FontWeight.W_600, size=12),
        ft.Container(
            content=ft.Column(
                [ft.Markdown(
                    notes, selectable=True,
                    extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                    on_tap_link=lambda e: webbrowser.open(e.data),
                    image_error_content=ft.Text("🖼️ (image failed to load)",
                                                italic=True, size=11),
                )],
                scroll=ft.ScrollMode.AUTO, tight=True,
            ),
            height=320, padding=ft.Padding(8, 8, 8, 8), border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.03, ft.Colors.ON_SURFACE),
        ),
    ]
    if url:
        body_controls.append(
            ft.TextButton("View release on GitHub", icon=ft.Icons.OPEN_IN_NEW,
                          on_click=lambda e: webbrowser.open(url)))

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("GameTracker — Update Available"),
        content=ft.Container(width=640, content=ft.Column(
            body_controls, tight=True, spacing=10)),
        actions=[
            ft.TextButton("Close", on_click=lambda e: page.pop_dialog()),
            ft.OutlinedButton("Disable startup checks", on_click=_disable),
            ft.Button("Download & Install", icon=ft.Icons.DOWNLOAD, on_click=_download),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    ))


# --------------------------------------------------------------------------- #
# Download -> confirm -> stage -> restart
# --------------------------------------------------------------------------- #
def run_update_process(page, update_info):
    """Download the update (reusing an existing download if present), then hand
    off to the install-confirmation step."""
    from auto_updater import get_updater
    updater = get_updater()
    version = update_info.get("version", "?")

    bar = ft.ProgressBar(value=0)
    pct = ft.Text("0%")
    status = ft.Text("Starting download…")
    cancel_flag = threading.Event()

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Downloading update"),
        content=ft.Container(width=440, content=ft.Column(
            [status, bar, pct], tight=True, spacing=12)),
        actions=[ft.TextButton("Cancel", on_click=lambda e: cancel_flag.set())],
        actions_alignment=ft.MainAxisAlignment.END,
    ))

    def _progress(p, msg=None):
        async def _u():
            try:
                bar.value = max(0.0, min(1.0, float(p or 0) / 100.0))
                pct.value = f"{int(p or 0)}%"
                if msg is not None:
                    status.value = msg
                page.update()
            except Exception:
                pass
        _run_on(page, _u)

    def _close_dialog():
        async def _u():
            page.pop_dialog()
            page.update()
        _run_on(page, _u)

    def _worker():
        path = None
        try:
            existing = updater.check_existing_download(version)
        except Exception:
            existing = None
        if existing and os.path.exists(existing):
            path = existing
        else:
            try:
                path = updater.download_update(lambda p: _progress(p, "Downloading…"),
                                               cancel_flag)
            except Exception:
                path = None

        if cancel_flag.is_set():
            _close_dialog()
            _snack_from_thread(page, "Download cancelled.")
            return
        if not path:
            _close_dialog()
            _snack_from_thread(page, "Failed to download the update.", error=True)
            return

        async def _confirm():
            page.pop_dialog()  # close the download dialog
            _open_install_confirmation(page, path, update_info)
        _run_on(page, _confirm)

    threading.Thread(target=_worker, name="update-download", daemon=True).start()


def _open_install_confirmation(page, download_path, update_info):
    def _install(_):
        page.pop_dialog()
        _stage_and_restart(page, download_path, update_info)

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Ready to install"),
        content=ft.Container(width=520, content=ft.Column(
            [
                ft.Text("The update has been downloaded.", size=13),
                ft.Text(download_path, size=11, color=ft.Colors.ON_SURFACE_VARIANT,
                        selectable=True),
                ft.Divider(),
                ft.Text("Installing will:", weight=ft.FontWeight.W_600, size=12),
                ft.Text("• close the app so files can be replaced\n"
                        "• back up the current version\n"
                        "• run an external updater, then relaunch automatically",
                        size=12),
                ft.Text("Save any unsaved work before continuing.", size=12,
                        italic=True, color=ft.Colors.ON_SURFACE_VARIANT),
            ],
            tight=True, spacing=8,
        )),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
            ft.Button("Install now", icon=ft.Icons.SYSTEM_UPDATE_ALT, on_click=_install),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    ))


def _stage_and_restart(page, download_path, update_info):
    from auto_updater import get_updater
    updater = get_updater()

    bar = ft.ProgressBar(value=0)
    pct = ft.Text("0%")
    status = ft.Text("Preparing update…")

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Staging update"),
        content=ft.Container(width=440, content=ft.Column(
            [status, bar, pct], tight=True, spacing=12)),
    ))

    def _progress(p, s=None):
        async def _u():
            try:
                bar.value = max(0.0, min(1.0, float(p or 0) / 100.0))
                pct.value = f"{int(p or 0)}%"
                if s is not None:
                    status.value = s
                page.update()
            except Exception:
                pass
        _run_on(page, _u)

    def _worker():
        ok = False
        try:
            ok = bool(updater.install_update(download_path, _progress))
        except Exception:
            ok = False

        async def _after():
            page.pop_dialog()  # close staging dialog
            if ok:
                _open_restart_prompt(page)
            else:
                _snack(page, "Failed to stage the update. Please try again.", error=True)
        _run_on(page, _after)

    threading.Thread(target=_worker, name="update-stage", daemon=True).start()


def _open_restart_prompt(page):
    def _restart(_):
        page.pop_dialog()
        _do_restart()

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Update staged"),
        content=ft.Container(width=460, content=ft.Text(
            "The update is ready. GameTracker will close and an external updater "
            "will replace the files and relaunch the app.")),
        actions=[
            ft.Button("Restart & update now", icon=ft.Icons.RESTART_ALT, on_click=_restart),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    ))


def _do_restart():
    """Tear down background subsystems so files unlock + presence clears, launch
    the updater script (``restart_application``), then hard-exit."""
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
        from ui_flet.discord_runtime import shutdown as discord_shutdown
        discord_shutdown(timeout=1.0)
    except Exception:
        pass
    try:
        from auto_updater import get_updater
        get_updater().restart_application()  # launches updater, then sys.exit(0)
    except SystemExit:
        pass
    except Exception:
        pass
    os._exit(0)


# --------------------------------------------------------------------------- #
# Post-update success popup
# --------------------------------------------------------------------------- #
def show_update_success(page, info):
    previous = info.get("previous_version", "?")
    new = info.get("new_version", "?")
    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Update complete"),
        content=ft.Container(width=420, content=ft.Column(
            [
                ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN),
                        ft.Text("Update successful!", size=16, weight=ft.FontWeight.BOLD)],
                       spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text(f"Updated from {previous} to {new}.", size=13),
                ft.Text("• files updated\n• previous version backed up\n"
                        "• app relaunched", size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT),
            ],
            tight=True, spacing=10,
        )),
        actions=[ft.Button("Great!", on_click=lambda e: page.pop_dialog())],
        actions_alignment=ft.MainAxisAlignment.END,
    ))


# --------------------------------------------------------------------------- #
# Startup hook
# --------------------------------------------------------------------------- #
def startup_check(page, service=None):
    """Initialize the updater; show a success popup if we just updated, then
    auto-check for new updates when enabled (off-thread)."""
    from auto_updater import initialize_updater
    updater = initialize_updater(check_on_startup=False)

    try:
        success_info = updater.check_for_update_success()
        if success_info:
            show_update_success(page, success_info)
    except Exception:
        pass

    if not bool(getattr(updater, "check_on_startup_enabled", False)):
        return

    def _work():
        try:
            info = updater.check_for_updates()
        except Exception:
            info = None
        if info:
            async def _show():
                open_update_notification(page, info)
            _run_on(page, _show)

    threading.Thread(target=_work, name="update-startup-check", daemon=True).start()
