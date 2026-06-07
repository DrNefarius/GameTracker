"""Remove leftover files from a pre-2.0.0 (PySimpleGUI / cx_Freeze) install.

When a legacy v1.x client auto-updates to v2.0.0, its (immutable, already
published) updater does ``robocopy <new> <install> /E`` — a MERGE that copies the
new Flet files in but never deletes the old ones. The result works, but the old
cx_Freeze / Python 3.10 runtime is left behind alongside the new Flet / Python
3.12 one (~114 MB of cruft: the whole ``share/`` tree, ``python310.dll``, and the
cx_Freeze ``lib/`` payload which case-merges into Flet's ``Lib/``).

This module removes exactly those leftovers on the first launch after such an
upgrade. The target list was computed by diffing a fresh legacy cx_Freeze build
against a fresh Flet build (paths present ONLY in the legacy build). A directory
is removed wholesale ONLY when no Flet file lives under it; directories shared
with Flet's stdlib (e.g. ``lib/importlib``) are pruned file-by-file instead, so
Flet's own files are never touched.

Trigger: ``update_view.startup_check`` calls :func:`maybe_cleanup_after_upgrade`
with the ``previous_version`` from ``update_flag.json``; cleanup runs only when
that version is < 2.0.0 AND we are the packaged Flet build.
"""

import os
import shutil

# Directories present only in the legacy build — safe to delete recursively
# (no Flet file lives under any of these). Forward-slash relative to install dir.
LEGACY_DIRS = [
    "lib/PIL", "lib/PyInstaller", "lib/PySimpleGUI", "lib/_pyinstaller_hooks_contrib",
    "lib/_pytest", "lib/altgraph", "lib/backports", "lib/certifi", "lib/chardet",
    "lib/charset_normalizer", "lib/colorama", "lib/contourpy", "lib/core", "lib/cycler",
    "lib/dateutil", "lib/et_xmlfile", "lib/exceptiongroup", "lib/fontTools", "lib/idna",
    "lib/importlib_metadata", "lib/iniconfig", "lib/jaraco", "lib/jinja2", "lib/kiwisolver",
    "lib/macholib", "lib/markupsafe", "lib/matplotlib", "lib/more_itertools",
    "lib/mpl_toolkits", "lib/numpy", "lib/numpy.libs", "lib/openpyxl", "lib/ordlookup",
    "lib/packaging", "lib/pkg_resources", "lib/platformdirs", "lib/pluggy", "lib/psutil",
    "lib/pydoc_data", "lib/pygments", "lib/pyparsing", "lib/pypresence", "lib/pystray",
    "lib/pytest", "lib/rapidfuzz", "lib/requests", "lib/tkinter", "lib/tomli", "lib/urllib3",
    "lib/win32ctypes", "lib/windows_toasts", "lib/winrt", "lib/yaml", "lib/zipp",
    "lib/zstandard", "share",
]

# Individual legacy-only files (live in directories otherwise shared with Flet,
# or at the install root). NOTE: gameslisticon.ico is deliberately NOT listed —
# it is harmless and may serve as the toast icon.
LEGACY_FILES = [
    "frozen_application_license.txt",
    "lib/_asyncio.pyd", "lib/_bz2.pyd", "lib/_ctypes.pyd", "lib/_decimal.pyd",
    "lib/_elementtree.pyd", "lib/_hashlib.pyd", "lib/_lzma.pyd", "lib/_multiprocessing.pyd",
    "lib/_overlapped.pyd", "lib/_queue.pyd", "lib/_socket.pyd", "lib/_sqlite3.pyd",
    "lib/_ssl.pyd", "lib/_tkinter.pyd", "lib/_uuid.pyd",
    "lib/importlib/_adapters.pyc", "lib/importlib/_common.pyc", "lib/importlib/resources.pyc",
    "lib/libcrypto-1_1.dll", "lib/libffi-7.dll", "lib/library.dat", "lib/library.zip",
    "lib/libssl-1_1.dll", "lib/pyexpat.pyd", "lib/select.pyd", "lib/sqlite3.dll",
    "lib/tcl86t.dll", "lib/tk86t.dll", "lib/unicodedata.pyd",
    "python310.dll",
]

# Files that must exist for a dir to look like a real Flet install (sanity guard
# so we never delete from an unexpected directory).
_FLET_MARKERS = ("GameTracker.exe", "python312.dll", "flutter_windows.dll")


def _within(base: str, target: str) -> bool:
    """True if realpath(target) stays inside base (anti path-traversal)."""
    try:
        base_r = os.path.realpath(base)
        tgt_r = os.path.realpath(target)
        return tgt_r != base_r and (tgt_r == base_r or tgt_r.startswith(base_r + os.sep))
    except Exception:
        return False


def _dir_size(path: str) -> int:
    total = 0
    for dp, _dns, fns in os.walk(path):
        for fn in fns:
            try:
                total += os.path.getsize(os.path.join(dp, fn))
            except OSError:
                pass
    return total


def _resolve_install_dir():
    """Return the packaged Flet install dir, or None if we are not that build."""
    try:
        from auto_updater import _resolve_install_target, _serious_python_extract_dir
        if _serious_python_extract_dir() is None:
            return None  # source / non-Flet build -> never clean
        install_dir, _exe = _resolve_install_target()
        return install_dir
    except Exception:
        return None


def is_pre_2_0(previous_version) -> bool:
    """True if the given version string is older than 2.0.0."""
    if not previous_version:
        return False
    try:
        from auto_updater import get_updater
        return get_updater().version_compare(str(previous_version), "2.0.0") == -1
    except Exception:
        return False


def cleanup_legacy_files(install_dir=None, dry_run: bool = False) -> dict:
    """Delete the known pre-2.0.0 leftovers from ``install_dir``.

    Returns a stats dict. Never raises. Refuses to run if the directory does not
    look like a Flet install. With ``dry_run`` it only measures what it *would*
    remove.
    """
    stats = {"dirs": 0, "files": 0, "bytes": 0, "errors": 0, "skipped": None}

    if install_dir is None:
        install_dir = _resolve_install_dir()
    if not install_dir or not os.path.isdir(install_dir):
        stats["skipped"] = "no install dir"
        return stats

    install_dir = os.path.abspath(install_dir)
    if not any(os.path.exists(os.path.join(install_dir, m)) for m in _FLET_MARKERS):
        stats["skipped"] = "not a Flet install dir"
        return stats

    for rel in LEGACY_DIRS:
        target = os.path.join(install_dir, *rel.split("/"))
        if not os.path.isdir(target) or not _within(install_dir, target):
            continue
        size = _dir_size(target)
        if dry_run:
            stats["dirs"] += 1
            stats["bytes"] += size
            continue
        try:
            shutil.rmtree(target)
            stats["dirs"] += 1
            stats["bytes"] += size
        except Exception as e:  # noqa: BLE001
            print(f"[legacy-cleanup] failed to remove dir {target}: {e}")
            stats["errors"] += 1

    for rel in LEGACY_FILES:
        target = os.path.join(install_dir, *rel.split("/"))
        if not os.path.isfile(target) or not _within(install_dir, target):
            continue
        try:
            size = os.path.getsize(target)
        except OSError:
            size = 0
        if dry_run:
            stats["files"] += 1
            stats["bytes"] += size
            continue
        try:
            os.remove(target)
            stats["files"] += 1
            stats["bytes"] += size
        except Exception as e:  # noqa: BLE001
            print(f"[legacy-cleanup] failed to remove file {target}: {e}")
            stats["errors"] += 1

    return stats


def maybe_cleanup_after_upgrade(previous_version) -> dict:
    """Run the cleanup iff upgrading from a pre-2.0.0 build (packaged Flet only)."""
    if not is_pre_2_0(previous_version):
        return {"skipped": "not a pre-2.0.0 upgrade"}
    stats = cleanup_legacy_files()
    try:
        from auto_updater import _log_update
        _log_update(f"legacy cleanup after upgrade from {previous_version}: {stats}")
    except Exception:
        pass
    print(f"[legacy-cleanup] from {previous_version}: {stats}")
    return stats


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else None
    dry = "--apply" not in sys.argv
    print(("DRY-RUN " if dry else "APPLYING ") + "cleanup on: " + str(d or "<auto>"))
    print(cleanup_legacy_files(install_dir=d, dry_run=dry))
