"""
Cross-platform idle and foreground-window helpers used by the watcher.

Public functions:
    get_idle_seconds() -> float
        Seconds since the user's last input event (mouse / keyboard).
        Returns 0.0 on platforms or environments where it can't be measured.

    get_foreground_pid() -> Optional[int]
        PID owning the currently foreground window, or None.

Both functions never raise; they fall back to a safe default so the watcher
treats them as "no info" and behaves as if neither idle nor foreground rules
applied.

Implementation notes:
- Windows: GetLastInputInfo + GetForegroundWindow + GetWindowThreadProcessId
  via ctypes. No external dependencies.
- macOS: HIDIdleTime via ``IOHIDSystem`` (best-effort) or returns 0.0.
- Linux: relies on `xprintidle` if available, else returns 0.0. Most users
  run this app on Windows; Linux/macOS support is intentionally minimal.
"""

from __future__ import annotations

import sys
from typing import Optional


# ---------------------------------------------------------------------------
# Windows backend
# ---------------------------------------------------------------------------


if sys.platform == 'win32':
    import ctypes
    from ctypes import wintypes  # type: ignore[attr-defined]

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [
            ('cbSize', wintypes.UINT),
            ('dwTime', wintypes.DWORD),
        ]

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    _user32.GetLastInputInfo.restype = wintypes.BOOL
    _user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
    _kernel32.GetTickCount.restype = wintypes.DWORD

    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

    def get_idle_seconds() -> float:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        try:
            if not _user32.GetLastInputInfo(ctypes.byref(info)):
                return 0.0
            now_ms = _kernel32.GetTickCount()
            # Both are 32-bit DWORDs; subtraction wraps correctly modulo 2^32
            # for the typical case (idle < 49 days). Cap the result so a stale
            # GetTickCount rollover never reports a bogus huge value.
            delta_ms = (now_ms - info.dwTime) & 0xFFFFFFFF
            return max(0.0, delta_ms / 1000.0)
        except Exception:
            return 0.0

    def get_foreground_pid() -> Optional[int]:
        try:
            hwnd = _user32.GetForegroundWindow()
            if not hwnd:
                return None
            pid = wintypes.DWORD(0)
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return int(pid.value) or None
        except Exception:
            return None

# ---------------------------------------------------------------------------
# macOS backend (best-effort; falls back to 0.0 if Quartz isn't importable)
# ---------------------------------------------------------------------------


elif sys.platform == 'darwin':

    def get_idle_seconds() -> float:
        try:
            import Quartz  # type: ignore
            return float(Quartz.CGEventSourceSecondsSinceLastEventType(
                Quartz.kCGEventSourceStateHIDSystemState,
                Quartz.kCGAnyInputEventType,
            ))
        except Exception:
            return 0.0

    def get_foreground_pid() -> Optional[int]:
        try:
            from AppKit import NSWorkspace  # type: ignore
            app = NSWorkspace.sharedWorkspace().activeApplication()
            if not app:
                return None
            return int(app.get('NSApplicationProcessIdentifier') or 0) or None
        except Exception:
            return None

# ---------------------------------------------------------------------------
# Linux / other - shell out to xprintidle if available
# ---------------------------------------------------------------------------


else:

    def get_idle_seconds() -> float:
        try:
            import subprocess
            out = subprocess.check_output(
                ['xprintidle'], stderr=subprocess.DEVNULL, timeout=1
            )
            return max(0.0, int(out.strip()) / 1000.0)
        except Exception:
            return 0.0

    def get_foreground_pid() -> Optional[int]:
        return None
