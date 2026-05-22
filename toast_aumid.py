"""
Register a custom Windows toast AppUserModelID (AUMID) for GamesList Manager.

``InteractableWindowsToaster`` defaults to the Command Prompt AUMID when none is
supplied, which makes toasts show as "Eingabeaufforderung" / cmd.exe. Fixing that
only requires:

  1. Registry entries under ``HKCU\\Software\\Classes\\AppUserModelId\\{AUMID}``
     (DisplayName + IconUri).
  2. Passing that AUMID as ``notifierAUMID`` when creating the toaster.

A Start Menu shortcut is the classic Microsoft requirement for *cold* toast
activation (user clicks a toast in Action Center after the app has exited, via
a registered COM server on the shortcut). GamesList handles toast buttons in-process
while the app is running, so we do not install a shortcut here.
"""

from __future__ import annotations

import sys
import winreg
from pathlib import Path
from typing import Optional

from constants import GITHUB_OWNER, GITHUB_REPO
from watcher_log import toast_logger

_log = toast_logger()

TOAST_AUMID = f"{GITHUB_OWNER}.{GITHUB_REPO}"
TOAST_APP_NAME = "GamesList Manager"

_ENSURED = False


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _icon_path() -> Optional[Path]:
    icon = _app_dir() / "gameslisticon.ico"
    return icon if icon.is_file() else None


def _register_aumid_registry(icon: Optional[Path]) -> None:
    key_path = rf"SOFTWARE\Classes\AppUserModelId\{TOAST_AUMID}"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, TOAST_APP_NAME)
        if icon is not None:
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(icon.resolve()))


def _set_process_aumid() -> None:
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(TOAST_AUMID)
    except Exception as exc:  # noqa: BLE001
        _log.debug("SetCurrentProcessExplicitAppUserModelID failed: %s", exc)


def ensure_toast_registration() -> str:
    """Register toast identity once per process. Returns the AUMID string."""
    global _ENSURED
    if _ENSURED or sys.platform != "win32":
        return TOAST_AUMID

    icon = _icon_path()
    try:
        _register_aumid_registry(icon)
        _set_process_aumid()
        _ENSURED = True
        _log.debug("toast AUMID registered: %s icon=%s", TOAST_AUMID, bool(icon))
    except Exception as exc:  # noqa: BLE001
        _log.warning("toast AUMID registration failed (toasts may show as cmd): %s",
                     exc)
    return TOAST_AUMID
