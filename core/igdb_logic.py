"""GUI-free IGDB business logic: search, confidence ranking, auto-match, details.

These helpers were previously defined inside ``igdb_ui.py`` (which imports
PySimpleGUI, so the Flet UI couldn't reuse them). They are pure logic + network
wrappers over ``igdb_integration`` — no GUI toolkit — so both the legacy
PySimpleGUI dialogs and the new Flet UI can share one implementation. ``igdb_ui``
now re-imports everything here for backwards compatibility.

Thread-safety: ``search_igdb_candidates`` / ``load_igdb_details`` /
``ensure_cover_cached`` perform blocking network I/O and never touch any GUI, so
they are safe to call from a worker thread.
"""

import os
from typing import Any, Dict, List, Optional

from igdb_integration import (
    IGDBConfigError,
    IGDBError,
    IGDBNotFoundError,
    cover_cache_path,
    get_igdb_client,
    legacy_cover_cache_path,
    sort_search_candidates,
)


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def format_seconds_short(seconds: Optional[int]) -> str:
    """Render an IGDB time_to_beat value (seconds) as e.g. '45h' or '1h 30m'."""
    if not seconds or seconds <= 0:
        return "--"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours >= 5:
        return f"{hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def parse_user_playtime_seconds(time_str: Optional[str]) -> int:
    """Convert the app's HH:MM:SS time_played field to integer seconds."""
    if not time_str or not isinstance(time_str, str):
        return 0
    parts = time_str.split(":")
    try:
        if len(parts) == 3:
            h, m, s = (int(p) for p in parts)
            return h * 3600 + m * 60 + s
        if len(parts) == 2:
            h, m = (int(p) for p in parts)
            return h * 3600 + m * 60
    except ValueError:
        return 0
    return 0


def parse_user_year(release_date: Optional[str]) -> Optional[int]:
    """Return the year part of a YYYY-MM-DD-ish string, or None if unparseable."""
    if not release_date or not isinstance(release_date, str) or len(release_date) < 4:
        return None
    try:
        return int(release_date[:4])
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Cover cache (network-capable, GUI-free)
# --------------------------------------------------------------------------- #
def purge_legacy_jpeg(igdb_id: int) -> None:
    """Remove any pre-PNG cached JPEG for this id; tk can't render it anyway."""
    legacy = legacy_cover_cache_path(int(igdb_id))
    try:
        if os.path.exists(legacy):
            os.remove(legacy)
    except OSError:
        pass


def ensure_cover_cached(igdb: Dict[str, Any]) -> Optional[str]:
    """Download the cover to the local cache if missing. Returns path or None."""
    igdb_id = igdb.get("igdb_id")
    image_id = igdb.get("cover_image_id")
    if not igdb_id or not image_id:
        return None
    purge_legacy_jpeg(int(igdb_id))
    path = cover_cache_path(int(igdb_id))
    if os.path.exists(path):
        return path
    try:
        client = get_igdb_client()
    except IGDBConfigError:
        return None
    if client.download_cover(image_id, path):
        return path
    return None


def cover_image_for(igdb: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return a local cover path if cached (auto-healing a missing PNG), else None."""
    if not igdb:
        return None
    igdb_id = igdb.get("igdb_id")
    if not igdb_id:
        return None
    purge_legacy_jpeg(int(igdb_id))
    path = cover_cache_path(int(igdb_id))
    if os.path.exists(path):
        return path
    return ensure_cover_cached(igdb)


# --------------------------------------------------------------------------- #
# Platform matching
# --------------------------------------------------------------------------- #
_PLATFORM_ALIASES: Dict[str, List[str]] = {
    "snes": ["super nintendo entertainment system", "super nintendo", "super famicom", "sfc"],
    "nes": ["nintendo entertainment system", "famicom", "fc"],
    "n64": ["nintendo 64"],
    "gc": ["gamecube", "nintendo gamecube"],
    "gb": ["game boy"],
    "gbc": ["game boy color"],
    "gba": ["game boy advance"],
    "nds": ["nintendo ds"],
    "ds": ["nintendo ds"],
    "3ds": ["nintendo 3ds"],
    "switch": ["nintendo switch", "ns"],
    "wii": ["nintendo wii"],
    "wiiu": ["nintendo wii u", "wii u"],
    "ps1": ["playstation", "psx"],
    "ps2": ["playstation 2"],
    "ps3": ["playstation 3"],
    "ps4": ["playstation 4"],
    "ps5": ["playstation 5"],
    "psp": ["playstation portable"],
    "psv": ["playstation vita", "ps vita", "psvita"],
    "xbox": ["xbox"],
    "x360": ["xbox 360"],
    "xone": ["xbox one"],
    "xsx": ["xbox series x", "xbox series x|s", "xbox series"],
    "xss": ["xbox series s", "xbox series"],
    "pc": ["pc (microsoft windows)", "microsoft windows", "windows"],
    "mac": ["mac", "macos", "os x"],
    "linux": ["linux"],
    "md": ["mega drive", "sega mega drive", "genesis", "sega genesis"],
    "saturn": ["sega saturn"],
    "dc": ["dreamcast", "sega dreamcast"],
    "arcade": ["arcade"],
}


def _norm_platform(text: str) -> str:
    """Lowercase + strip non-alphanumerics for loose platform comparison."""
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def platforms_match(user_platform: Optional[str], igdb_platforms: List[str]) -> bool:
    """True if the user's platform string plausibly refers to any IGDB platform."""
    if not user_platform or not igdb_platforms:
        return False
    up_raw = user_platform.strip().lower()
    up_norm = _norm_platform(up_raw)
    if not up_norm:
        return False
    candidates = {up_raw, up_norm}
    for key, aliases in _PLATFORM_ALIASES.items():
        if up_norm == key or up_norm in {_norm_platform(a) for a in aliases}:
            candidates.add(key)
            candidates.update(aliases)
            candidates.update(_norm_platform(a) for a in aliases)
    for ig in igdb_platforms:
        ig_raw = (ig or "").strip().lower()
        ig_norm = _norm_platform(ig_raw)
        if not ig_norm:
            continue
        if ig_raw in candidates or ig_norm in candidates:
            return True
        if up_norm and (up_norm in ig_norm or ig_norm in up_norm):
            return True
    return False


# --------------------------------------------------------------------------- #
# Confidence scoring / ranking / auto-match
# --------------------------------------------------------------------------- #
_CATEGORY_SCORE: Dict[int, int] = {
    0: 10, 8: 0, 9: 0, 4: -5, 10: -5, 2: -5, 11: -10, 6: -20,
    7: -20, 3: -25, 13: -25, 1: -30, 12: -30, 5: -30, 14: -30,
}

_AUTO_MATCH_MIN_SCORE = 100
_AUTO_MATCH_MIN_MARGIN = 25


def score_candidate(cand: Dict[str, Any], game_name: str,
                    user_year: Optional[int], user_platform: Optional[str]) -> int:
    """Composite confidence score for a single IGDB candidate (name/year/platform/category)."""
    score = 0
    clean_user = (game_name or "").strip().lower()
    clean_cand = (cand.get("name") or "").strip().lower()
    if clean_user and clean_cand:
        if clean_user == clean_cand:
            score += 100
        elif clean_user in clean_cand or clean_cand in clean_user:
            score += 40

    cand_year = cand.get("year")
    if user_year and cand_year:
        diff = abs(int(cand_year) - int(user_year))
        if diff == 0:
            score += 40
        elif diff == 1:
            score += 30
        elif diff <= 3:
            score += 10
        else:
            score -= 10

    if user_platform and platforms_match(user_platform, cand.get("platforms") or []):
        score += 50

    cat = cand.get("category")
    if cat is not None:
        try:
            score += _CATEGORY_SCORE.get(int(cat), 0)
        except (TypeError, ValueError):
            pass

    return score


def rank_candidates_by_confidence(
    candidates: List[Dict[str, Any]],
    game_name: str,
    release_date: Optional[str],
    platform: Optional[str],
) -> List[Dict[str, Any]]:
    """Re-order IGDB search candidates by composite-confidence score (stable on ties)."""
    if not candidates:
        return candidates
    if not (game_name or release_date or platform):
        return candidates
    user_year = parse_user_year(release_date)
    indexed = list(enumerate(candidates))
    indexed.sort(
        key=lambda it: (-score_candidate(it[1], game_name, user_year, platform), it[0])
    )
    return [c for _, c in indexed]


def pick_auto_match(game_name: str, release_date: Optional[str],
                    platform: Optional[str],
                    candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pick the single best IGDB candidate if confidence + margin are high enough."""
    if not candidates:
        return None
    user_year = parse_user_year(release_date)
    scored = [(score_candidate(c, game_name, user_year, platform), c) for c in candidates]
    scored.sort(key=lambda it: it[0], reverse=True)
    top_score, top_cand = scored[0]
    if top_score < _AUTO_MATCH_MIN_SCORE:
        return None
    runner_up = scored[1][0] if len(scored) > 1 else (top_score - _AUTO_MATCH_MIN_MARGIN - 1)
    if top_score - runner_up < _AUTO_MATCH_MIN_MARGIN:
        return None
    return top_cand


# --------------------------------------------------------------------------- #
# Network wrappers (worker-thread safe)
# --------------------------------------------------------------------------- #
def search_igdb_candidates(
    game_name: str,
    limit: int = 15,
    *,
    user_release: Optional[str] = None,
    user_platform: Optional[str] = None,
) -> Dict[str, Any]:
    """Network-only search wrapper. Returns {'candidates': [...]} or {'_error': msg}."""
    try:
        client = get_igdb_client()
    except IGDBConfigError as exc:
        return {"_error": str(exc)}
    try:
        raw = client.search_games(game_name, limit=limit)
        ordered = sort_search_candidates(raw)
        ordered = rank_candidates_by_confidence(
            ordered, game_name, user_release, user_platform)
        return {"candidates": ordered}
    except IGDBError as exc:
        return {"_error": f"IGDB search failed: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"Unexpected error during search: {exc}"}


def load_igdb_details(igdb_id: int) -> Dict[str, Any]:
    """Network-only details + cover download. Returns details dict or {'_error': msg}."""
    try:
        client = get_igdb_client()
    except IGDBConfigError as exc:
        return {"_error": str(exc)}
    try:
        details = client.get_game_details(int(igdb_id))
    except IGDBNotFoundError:
        return {"_error": f"IGDB entry {igdb_id} no longer exists."}
    except IGDBError as exc:
        return {"_error": f"IGDB lookup failed: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"Unexpected error during details lookup: {exc}"}

    if details.get("cover_image_id"):
        ensure_cover_cached(details)
    return details
