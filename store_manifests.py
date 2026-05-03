"""
Read-only scanners for installed-game manifests from PC storefronts.

Public surface:
    scan_all_stores() -> StoreIndex
    StoreIndex.lookup_by_path(exe_path)  -> Optional[ManifestEntry]
    StoreIndex.lookup_steam_appid(appid) -> Optional[ManifestEntry]
    StoreIndex.install_roots()           -> List[str]

The scanners are best-effort: any individual store failing to parse should
NEVER raise out of `scan_all_stores`. Errors are logged and the affected store
is simply absent from the index.

Currently implemented stores: Steam, Epic, GOG (registry + Galaxy DB).
Other launchers (EA, Ubisoft, Battle.net) can be added later without changing
the public surface.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from watcher_log import manifest_logger

_log = manifest_logger()


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class ManifestEntry:
    """A single installed game discovered via a storefront's manifest."""
    name: str
    install_dir: str
    store: str                   # 'steam' | 'epic' | 'gog'
    store_id: Optional[str] = None
    executables: List[str] = field(default_factory=list)


@dataclass
class StoreIndex:
    """Aggregated, normalized result of scanning all known stores."""
    entries: List[ManifestEntry] = field(default_factory=list)
    # Pre-computed maps for fast resolver lookups.
    by_install_dir: Dict[str, ManifestEntry] = field(default_factory=dict)
    by_steam_appid: Dict[str, ManifestEntry] = field(default_factory=dict)

    def install_roots(self) -> List[str]:
        """Distinct, normalized install-dir prefixes (with trailing sep)."""
        seen = set()
        out: List[str] = []
        for e in self.entries:
            if not e.install_dir:
                continue
            d = _normpath_with_sep(e.install_dir)
            if d not in seen:
                seen.add(d)
                out.append(d)
        return out

    def lookup_by_path(self, exe_path: str) -> Optional[ManifestEntry]:
        if not exe_path:
            return None
        p = _normpath(exe_path)
        for d, entry in self.by_install_dir.items():
            if p.startswith(d):
                return entry
        return None

    def lookup_steam_appid(self, appid: str) -> Optional[ManifestEntry]:
        return self.by_steam_appid.get(str(appid))

    def to_serializable(self) -> dict:
        return {
            'entries': [e.__dict__ for e in self.entries],
        }

    @classmethod
    def from_serializable(cls, data: dict) -> "StoreIndex":
        entries = [ManifestEntry(**e) for e in data.get('entries', [])]
        idx = cls(entries=entries)
        idx._rebuild_maps()
        return idx

    def _rebuild_maps(self) -> None:
        self.by_install_dir = {}
        self.by_steam_appid = {}
        for e in self.entries:
            if e.install_dir:
                self.by_install_dir[_normpath_with_sep(e.install_dir)] = e
            if e.store == 'steam' and e.store_id:
                self.by_steam_appid[str(e.store_id)] = e


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def scan_all_stores() -> StoreIndex:
    """Run every store scanner and return the combined index.

    Errors are logged but never propagated. Always returns an index
    (possibly empty) so callers don't need defensive try/except.
    """
    entries: List[ManifestEntry] = []

    if sys.platform == 'win32':
        for fn in (_scan_steam, _scan_epic, _scan_gog):
            try:
                got = fn() or []
                _log.debug("%s -> %d entries", fn.__name__, len(got))
                entries.extend(got)
            except Exception as exc:  # noqa: BLE001
                _log.error("%s failed: %s", fn.__name__, exc, exc_info=True)
    else:
        _log.debug("non-Windows platform; store manifest scan skipped")

    idx = StoreIndex(entries=entries)
    idx._rebuild_maps()
    _log.info("scan_all_stores complete: %d total entries across %d roots",
              len(entries), len(idx.install_roots()))
    return idx


# ---------------------------------------------------------------------------
# Steam
# ---------------------------------------------------------------------------


def _scan_steam() -> List[ManifestEntry]:
    """Discover Steam libraries via libraryfolders.vdf, then read each appmanifest."""
    if sys.platform != 'win32':
        return []

    steam_root = _find_steam_root()
    if not steam_root:
        return []

    library_paths: List[str] = []
    primary_steamapps = os.path.join(steam_root, 'steamapps')
    if os.path.isdir(primary_steamapps):
        library_paths.append(primary_steamapps)

    libfolders = os.path.join(primary_steamapps, 'libraryfolders.vdf')
    if os.path.exists(libfolders):
        try:
            with open(libfolders, 'r', encoding='utf-8', errors='replace') as fh:
                text = fh.read()
            # libraryfolders.vdf has multiple "path" keys, one per library.
            for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                p = m.group(1).replace('\\\\', '\\')
                p = os.path.join(p, 'steamapps')
                if os.path.isdir(p) and p not in library_paths:
                    library_paths.append(p)
        except Exception as exc:  # noqa: BLE001
            _log.warning("failed to parse libraryfolders.vdf: %s", exc)

    entries: List[ManifestEntry] = []
    for steamapps in library_paths:
        try:
            for fname in os.listdir(steamapps):
                if not (fname.startswith('appmanifest_') and fname.endswith('.acf')):
                    continue
                try:
                    entry = _parse_steam_appmanifest(
                        os.path.join(steamapps, fname),
                        os.path.join(steamapps, 'common'),
                    )
                except Exception as exc:  # noqa: BLE001
                    _log.warning("steam manifest %s failed: %s", fname, exc)
                    continue
                if entry:
                    entries.append(entry)
        except Exception as exc:  # noqa: BLE001
            _log.warning("cannot list %s: %s", steamapps, exc)
    _log.debug("steam scan: %d entries from %d libraries",
               len(entries), len(library_paths))
    return entries


def _find_steam_root() -> Optional[str]:
    """Look up Steam install root from registry, falling back to defaults."""
    try:
        import winreg  # type: ignore
        for hive, key in (
            (winreg.HKEY_CURRENT_USER, r"Software\\Valve\\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\WOW6432Node\\Valve\\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\Valve\\Steam"),
        ):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for value_name in ('SteamPath', 'InstallPath'):
                        try:
                            val, _ = winreg.QueryValueEx(k, value_name)
                            if val and os.path.isdir(val):
                                return os.path.normpath(val)
                        except FileNotFoundError:
                            continue
            except FileNotFoundError:
                continue
    except Exception:
        pass

    for guess in (
        r"C:\\Program Files (x86)\\Steam",
        r"C:\\Program Files\\Steam",
    ):
        if os.path.isdir(guess):
            return guess
    return None


_ACF_KV_RE = re.compile(r'"([^"]+)"\s+"([^"]*)"')


def _parse_steam_appmanifest(acf_path: str, common_dir: str) -> Optional[ManifestEntry]:
    with open(acf_path, 'r', encoding='utf-8', errors='replace') as fh:
        text = fh.read()
    kv = dict(_ACF_KV_RE.findall(text))
    appid = kv.get('appid')
    name = kv.get('name')
    installdir_rel = kv.get('installdir')
    if not (appid and name and installdir_rel):
        return None
    install_dir = os.path.join(common_dir, installdir_rel)
    return ManifestEntry(
        name=name,
        install_dir=install_dir,
        store='steam',
        store_id=appid,
    )


# ---------------------------------------------------------------------------
# Epic
# ---------------------------------------------------------------------------


def _scan_epic() -> List[ManifestEntry]:
    if sys.platform != 'win32':
        return []
    program_data = os.environ.get('ProgramData', r'C:\\ProgramData')
    manifest_dir = os.path.join(
        program_data, 'Epic', 'EpicGamesLauncher', 'Data', 'Manifests'
    )
    if not os.path.isdir(manifest_dir):
        return []
    out: List[ManifestEntry] = []
    for fname in os.listdir(manifest_dir):
        if not fname.endswith('.item'):
            continue
        try:
            with open(os.path.join(manifest_dir, fname), 'r',
                      encoding='utf-8', errors='replace') as fh:
                data = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            _log.warning("epic manifest %s failed: %s", fname, exc)
            continue
        name = data.get('DisplayName')
        install_loc = data.get('InstallLocation')
        if not (name and install_loc):
            continue
        exe = data.get('LaunchExecutable')
        executables = [os.path.join(install_loc, exe)] if exe else []
        out.append(ManifestEntry(
            name=name,
            install_dir=install_loc,
            store='epic',
            store_id=data.get('AppName'),
            executables=executables,
        ))
    return out


# ---------------------------------------------------------------------------
# GOG (registry + optional Galaxy DB)
# ---------------------------------------------------------------------------


def _scan_gog() -> List[ManifestEntry]:
    if sys.platform != 'win32':
        return []
    by_id: Dict[str, ManifestEntry] = {}

    # Pass 1: registry (cheap, always works when Galaxy is installed).
    try:
        import winreg  # type: ignore
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for root in (
                r"SOFTWARE\\WOW6432Node\\GOG.com\\Games",
                r"SOFTWARE\\GOG.com\\Games",
            ):
                try:
                    with winreg.OpenKey(hive, root) as k:
                        i = 0
                        while True:
                            try:
                                game_id = winreg.EnumKey(k, i)
                                i += 1
                            except OSError:
                                break
                            try:
                                with winreg.OpenKey(k, game_id) as gk:
                                    name = _reg_get(gk, 'gameName')
                                    path = _reg_get(gk, 'path')
                                    exe = _reg_get(gk, 'exe')
                                    if name and path:
                                        executables = [exe] if exe else []
                                        by_id[game_id] = ManifestEntry(
                                            name=name,
                                            install_dir=path,
                                            store='gog',
                                            store_id=game_id,
                                            executables=executables,
                                        )
                            except OSError:
                                continue
                except FileNotFoundError:
                    continue
    except Exception as exc:  # noqa: BLE001
        _log.warning("GOG registry scan failed: %s", exc)

    # Pass 2: Galaxy DB - augment, don't overwrite (registry tends to be canonical).
    program_data = os.environ.get('ProgramData', r'C:\\ProgramData')
    db_path = os.path.join(program_data, 'GOG.com', 'Galaxy', 'storage', 'galaxy-2.0.db')
    if os.path.exists(db_path):
        try:
            uri = f"file:{db_path}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=2.0) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT productId, installationPath
                      FROM InstalledBaseProducts
                """)
                rows = cur.fetchall()
                # Look up titles in a separate table that may or may not exist
                # depending on Galaxy version; if missing, fall back gracefully.
                try:
                    cur.execute("SELECT productId, title FROM ProductPurchaseDates")
                    title_map = {str(r[0]): r[1] for r in cur.fetchall() if r[1]}
                except sqlite3.Error:
                    title_map = {}
                for pid, install_path in rows:
                    pid_s = str(pid)
                    if not install_path:
                        continue
                    name = title_map.get(pid_s)
                    if pid_s not in by_id:
                        by_id[pid_s] = ManifestEntry(
                            name=name or os.path.basename(install_path) or pid_s,
                            install_dir=install_path,
                            store='gog',
                            store_id=pid_s,
                        )
        except Exception as exc:  # noqa: BLE001
            _log.warning("GOG Galaxy DB scan failed: %s", exc)

    return list(by_id.values())


def _reg_get(key, value_name: str) -> Optional[str]:
    try:
        import winreg  # type: ignore
        val, _ = winreg.QueryValueEx(key, value_name)
        return val if isinstance(val, str) else None
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _normpath(p: str) -> str:
    try:
        return os.path.normcase(os.path.normpath(p))
    except Exception:
        return p


def _normpath_with_sep(p: str) -> str:
    n = _normpath(p)
    if not n.endswith(os.sep):
        n += os.sep
    return n
