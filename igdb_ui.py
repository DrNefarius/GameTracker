"""
PySimpleGUI dialogs for the IGDB integration.

Exposes:
    - show_igdb_settings_dialog(parent_window): configure credentials / enable flag
    - show_match_dialog(game_name, candidates, parent_window): disambiguate a search
    - show_library_enrichment_dialog(...): batch enrich games without metadata
    - search_igdb_candidates / load_igdb_details: thread-safe network wrappers
      consumed by the unified Game Hub in game_hub.py

The previous standalone Game Details popup has been folded into the Game Hub
(see game_hub.py); the match dialog below still runs on the main thread,
while search_igdb_candidates / load_igdb_details are safe to call from a
worker thread.

All network I/O lives in igdb_integration.py; these dialogs are pure UI + thin
threading helpers. Long calls run on a background thread and post results back to
the dialog window via window.write_event_value() to keep PySimpleGUI responsive.
"""

import io
import os
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

import PySimpleGUI as sg

try:
    from PIL import Image as _PILImage
    try:
        _COVER_RESAMPLE = _PILImage.Resampling.LANCZOS  # Pillow >= 9.1
    except AttributeError:  # Pillow 9.0 fallback
        _COVER_RESAMPLE = _PILImage.LANCZOS
    _HAVE_PIL = True
except ImportError:
    _PILImage = None  # type: ignore[assignment]
    _COVER_RESAMPLE = None
    _HAVE_PIL = False

from config import load_config, save_config
from igdb_integration import (
    IGDB_CATEGORY_LABELS,
    IGDBAuthError,
    IGDBConfigError,
    IGDBError,
    IGDBNotFoundError,
    build_image_url,
    cover_cache_path,
    get_igdb_client,
    legacy_cover_cache_path,
    reset_igdb_client,
    sort_search_candidates,
)
from utilities import calculate_popup_center_location


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _run_async(target: Callable, *args, **kwargs) -> threading.Thread:
    """Start a daemon worker thread and return it. Small convenience wrapper."""
    t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t


def _format_seconds_short(seconds: Optional[int]) -> str:
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


def _parse_user_playtime_seconds(time_str: Optional[str]) -> int:
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


def _purge_legacy_jpeg(igdb_id: int) -> None:
    """Remove any pre-PNG cached JPEG for this id; tk can't render it anyway."""
    legacy = legacy_cover_cache_path(int(igdb_id))
    try:
        if os.path.exists(legacy):
            os.remove(legacy)
    except OSError:
        pass


def _cover_image_for(igdb: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return a local cover path if cached (auto-healing a missing PNG), else None.

    tkinter's PhotoImage only speaks PNG/GIF, so we normalize everything to PNG
    on disk. If the PNG is missing but we still have the IGDB image id, we
    download it now (small, synchronous HTTP call) so the popup renders with an
    image on the very first open after upgrading.
    """
    if not igdb:
        return None
    igdb_id = igdb.get("igdb_id")
    if not igdb_id:
        return None
    _purge_legacy_jpeg(int(igdb_id))
    path = cover_cache_path(int(igdb_id))
    if os.path.exists(path):
        return path
    # Try a best-effort on-demand download to upgrade older caches.
    return _ensure_cover_cached(igdb)


_COVER_DISPLAY_SIZE: Tuple[int, int] = (200, 280)


def _render_cover_fitted(cover_path: Optional[str],
                         size: Tuple[int, int] = _COVER_DISPLAY_SIZE
                         ) -> Optional[bytes]:
    """Return PNG bytes of the cover scaled to fit within `size` with its
    aspect ratio preserved.

    IGDB's `t_cover_big` ships at 264x374, which overflows our 200x280 slot
    when handed straight to Tk (tk's PhotoImage renders at native pixel
    dimensions and the widget stretches to match). Running the file through
    PIL first guarantees the pixel payload is never larger than the container,
    so the Details popup has a predictable fixed layout regardless of the
    underlying cover's resolution.

    Returns None if PIL isn't available or the image can't be decoded; the
    caller should then fall back to the "no cover available" placeholder.
    """
    if not cover_path or not _HAVE_PIL:
        return None
    try:
        with _PILImage.open(cover_path) as img:
            # Preserve transparency for PNGs; convert paletted/JPEG to RGBA.
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")
            img.thumbnail(size, _COVER_RESAMPLE)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        print(f"IGDB: cover resize failed for {cover_path}: {exc}")
        return None


def _ensure_cover_cached(igdb: Dict[str, Any]) -> Optional[str]:
    """Download the cover to the local cache if missing. Returns path or None."""
    igdb_id = igdb.get("igdb_id")
    image_id = igdb.get("cover_image_id")
    if not igdb_id or not image_id:
        return None
    _purge_legacy_jpeg(int(igdb_id))
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


# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------


def show_igdb_settings_dialog(parent_window=None) -> bool:
    """Let the user edit IGDB credentials. Returns True if settings were saved."""
    config = load_config()
    layout = [
        [sg.Text("IGDB integration uses a Twitch application. See IGDB_SETUP.md for steps.",
                 font=("Helvetica", 9, "italic"))],
        [sg.Checkbox("Enable IGDB integration", default=config.get("igdb_enabled", False),
                     key="-IGDB-ENABLED-")],
        [sg.Text("Client ID", size=(14, 1)),
         sg.Input(config.get("igdb_client_id", ""), key="-IGDB-CLIENT-ID-", size=(40, 1))],
        [sg.Text("Client Secret", size=(14, 1)),
         sg.Input(config.get("igdb_client_secret", ""), key="-IGDB-CLIENT-SECRET-",
                  size=(40, 1), password_char="*")],
        [sg.Checkbox("Auto-apply strong matches when fetching metadata",
                     default=config.get("igdb_auto_match_on_add", False),
                     key="-IGDB-AUTO-MATCH-",
                     tooltip="Skip the picker when a single candidate matches name + year")],
        [sg.Text("", key="-IGDB-TEST-STATUS-", size=(50, 2), text_color="gray")],
        [sg.Button("Test Connection", key="-IGDB-TEST-"),
         sg.Push(),
         sg.Button("Save"), sg.Button("Cancel")],
    ]

    location = calculate_popup_center_location(parent_window, 520, 260) if parent_window else None
    window = sg.Window("IGDB Settings", layout, modal=True, finalize=True,
                       icon="gameslisticon.ico", location=location)

    saved = False
    test_thread: Optional[threading.Thread] = None

    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, "Cancel"):
                break

            if event == "-IGDB-TEST-":
                client_id = values["-IGDB-CLIENT-ID-"].strip()
                client_secret = values["-IGDB-CLIENT-SECRET-"].strip()
                if not client_id or not client_secret:
                    window["-IGDB-TEST-STATUS-"].update(
                        "Enter both Client ID and Client Secret first.", text_color="orange red"
                    )
                    continue
                window["-IGDB-TEST-STATUS-"].update("Testing connection...", text_color="gray")
                window["-IGDB-TEST-"].update(disabled=True)

                def _do_test(win, cid, csec):
                    from igdb_integration import IGDBClient  # local import to avoid cycles
                    try:
                        IGDBClient(cid, csec).test_connection()
                        win.write_event_value("-IGDB-TEST-DONE-", ("ok", "Connection succeeded."))
                    except IGDBAuthError as e:
                        win.write_event_value("-IGDB-TEST-DONE-", ("err", f"Auth failed: {e}"))
                    except IGDBError as e:
                        win.write_event_value("-IGDB-TEST-DONE-", ("err", f"IGDB error: {e}"))
                    except Exception as e:  # noqa: BLE001
                        win.write_event_value("-IGDB-TEST-DONE-", ("err", f"Unexpected error: {e}"))

                test_thread = _run_async(_do_test, window, client_id, client_secret)
                continue

            if event == "-IGDB-TEST-DONE-":
                status, message = values[event]
                color = "dark green" if status == "ok" else "red"
                window["-IGDB-TEST-STATUS-"].update(message, text_color=color)
                window["-IGDB-TEST-"].update(disabled=False)
                continue

            if event == "Save":
                config["igdb_enabled"] = bool(values["-IGDB-ENABLED-"])
                config["igdb_client_id"] = values["-IGDB-CLIENT-ID-"].strip()
                config["igdb_client_secret"] = values["-IGDB-CLIENT-SECRET-"].strip()
                config["igdb_auto_match_on_add"] = bool(values["-IGDB-AUTO-MATCH-"])
                if save_config(config):
                    reset_igdb_client()
                    saved = True
                    break
                sg.popup_error("Failed to save settings.", title="Error")
    finally:
        window.close()
        # The test thread is a daemon and will exit with the process; nothing else to do.
        del test_thread

    return saved


# ---------------------------------------------------------------------------
# Match disambiguation dialog
# ---------------------------------------------------------------------------


def _format_candidate_row(cand: Dict[str, Any]) -> str:
    """Pretty single-line label for a listbox row.

    Non-main-game entries get a leading tag like '[Port]' or '[Remake]' so the
    user can distinguish the canonical release from ports, bundles, DLC, etc.
    Main games (category 0) and entries with an unknown category render
    without a tag to keep the common case uncluttered.
    """
    name = cand.get("name") or "Unknown"
    year = cand.get("year")
    platforms = ", ".join((cand.get("platforms") or [])[:3])
    category = cand.get("category")
    bits: List[str] = []
    if category is not None and int(category) != 0:
        tag = IGDB_CATEGORY_LABELS.get(int(category))
        if tag:
            bits.append(f"[{tag}]")
    bits.append(name)
    if year:
        bits.append(f"({year})")
    if platforms:
        bits.append(f"- {platforms}")
    return " ".join(bits)


_MATCH_EMPTY_HINT = "(no results - edit the title above and click Search)"


def show_match_dialog(game_name: str, candidates: List[Dict[str, Any]],
                      parent_window=None,
                      *,
                      user_release: Optional[str] = None,
                      user_platform: Optional[str] = None,
                      ) -> Optional[Dict[str, Any]]:
    """Ask the user to pick from a list of IGDB search candidates.

    The dialog carries its own editable search input (prefilled with
    `game_name`) so the user can look up an alternate title without cancelling
    the flow. This matters for regional releases where the local title differs
    from the IGDB canonical one (e.g. a German retail title vs. the English
    original).

    `user_release` (YYYY-MM-DD-ish) and `user_platform` are passed through to
    the re-search worker so that user-typed alternate-title searches stay
    confidence-ranked just like the initial query - the user shouldn't have
    to scroll past five remakes to find the original release they're looking
    for after editing the search title.

    Returns the chosen candidate dict, `{'_skip': True}` if the user explicitly
    marks the game as not-in-IGDB, or None if they cancel.
    """
    # Working copy of the current result set. Mutated on every re-search so the
    # listbox and the confirm button stay in sync with what's visible.
    current_candidates: List[Dict[str, Any]] = list(candidates or [])
    current_labels: List[str] = [_format_candidate_row(c) for c in current_candidates]
    list_values = current_labels if current_labels else [_MATCH_EMPTY_HINT]

    layout = [
        [sg.Text("Pick the IGDB entry that matches your game:",
                 font=("Helvetica", 10, "bold"))],
        [sg.Text("Search title:"),
         sg.Input(default_text=game_name or "", key="-MATCH-QUERY-", expand_x=True),
         sg.Button("Search", key="-MATCH-SEARCH-", bind_return_key=True)],
        [sg.Text("Tip: edit the title above to try a different spelling or "
                 "the English/original title.",
                 font=("Helvetica", 9, "italic"), text_color="#555555")],
        [sg.Listbox(values=list_values, key="-MATCH-LIST-", size=(70, 8),
                    enable_events=True, select_mode=sg.LISTBOX_SELECT_MODE_SINGLE)],
        [sg.Text("", key="-MATCH-DETAIL-", size=(70, 2))],
        [sg.Button("Use Selected", key="-MATCH-USE-", disabled=True),
         sg.Button("Not in IGDB (skip)", key="-MATCH-SKIP-"),
         sg.Push(),
         sg.Button("Cancel")],
    ]
    location = calculate_popup_center_location(parent_window, 640, 400) if parent_window else None
    window = sg.Window("Match IGDB Game", layout, modal=True, finalize=True,
                       icon="gameslisticon.ico", location=location)

    # If we opened with no initial results, hint the user toward the search box.
    if not current_candidates:
        window["-MATCH-DETAIL-"].update(
            "No initial matches. Edit the title above and click Search.")

    searching = False
    chosen: Optional[Dict[str, Any]] = None
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, "Cancel"):
                break

            # ---- Re-search with a user-edited query --------------------------
            if event == "-MATCH-SEARCH-":
                if searching:
                    continue
                query = (values.get("-MATCH-QUERY-") or "").strip()
                if not query:
                    window["-MATCH-DETAIL-"].update("Enter a title to search.")
                    continue
                searching = True
                window["-MATCH-SEARCH-"].update(disabled=True)
                window["-MATCH-USE-"].update(disabled=True)
                window["-MATCH-DETAIL-"].update(f"Searching IGDB for '{query}'...")
                window["-MATCH-LIST-"].update(values=["(searching...)"])

                def _worker(win, q, rel, plat):
                    win.write_event_value(
                        "-MATCH-SEARCH-DONE-",
                        search_igdb_candidates(
                            q, user_release=rel, user_platform=plat))
                _run_async(_worker, window, query, user_release, user_platform)
                continue

            if event == "-MATCH-SEARCH-DONE-":
                result = values[event] or {}
                searching = False
                window["-MATCH-SEARCH-"].update(disabled=False)
                if result.get("_error"):
                    current_candidates = []
                    current_labels = []
                    window["-MATCH-LIST-"].update(values=[_MATCH_EMPTY_HINT])
                    window["-MATCH-DETAIL-"].update(f"Error: {result['_error']}")
                    continue
                current_candidates = result.get("candidates") or []
                current_labels = [_format_candidate_row(c) for c in current_candidates]
                if current_labels:
                    window["-MATCH-LIST-"].update(values=current_labels)
                    window["-MATCH-DETAIL-"].update(
                        f"{len(current_labels)} result(s). Click one to select.")
                else:
                    window["-MATCH-LIST-"].update(values=[_MATCH_EMPTY_HINT])
                    window["-MATCH-DETAIL-"].update(
                        "No matches. Try a different title.")
                continue

            # ---- List selection / confirm ------------------------------------
            # The listbox may hold the empty-state hint instead of a real row;
            # validate the selection against `current_labels` before using it.
            if event == "-MATCH-LIST-":
                selection = values.get("-MATCH-LIST-") or []
                if not selection or not current_candidates:
                    continue
                label = selection[0]
                if label not in current_labels:
                    continue
                idx = current_labels.index(label)
                cand = current_candidates[idx]
                genres = ", ".join(cand.get("genres") or [])
                detail = f"Genres: {genres or '(none)'}"
                if cand.get("total_rating") is not None:
                    detail += f"   IGDB rating: {cand['total_rating']:.1f}"
                window["-MATCH-DETAIL-"].update(detail)
                window["-MATCH-USE-"].update(disabled=False)
                continue

            if event == "-MATCH-USE-":
                selection = values.get("-MATCH-LIST-") or []
                if not selection or not current_candidates:
                    continue
                label = selection[0]
                if label not in current_labels:
                    continue
                idx = current_labels.index(label)
                chosen = current_candidates[idx]
                break

            if event == "-MATCH-SKIP-":
                chosen = None
                window.close()
                return {"_skip": True}
    finally:
        window.close()
    return chosen


# ---------------------------------------------------------------------------
# Fetch metadata (business logic shared by single-game + batch flows)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Auto-match heuristics
# ---------------------------------------------------------------------------

# Common short-form platform names users type into the app, mapped to the
# canonical IGDB-style names. Used only for fuzzy matching in `_platforms_match`;
# we don't force the user to spell anything in particular.
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


def _platforms_match(user_platform: Optional[str], igdb_platforms: List[str]) -> bool:
    """True if the user's platform string plausibly refers to any IGDB platform
    for the candidate. Matching is case/punctuation-insensitive and falls back
    to an alias table for common short forms like 'SNES' or 'PS2'."""
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
        # Bidirectional substring check catches partial names in either direction
        # (e.g. user "super nintendo" vs. IGDB "Super Nintendo Entertainment System").
        if up_norm and (up_norm in ig_norm or ig_norm in up_norm):
            return True
    return False


# Category-based score adjustment: main games are preferred, ports/remasters
# are neutral, bundles and DLC are actively penalized so they only win when
# the rest of the signal overwhelmingly points at them.
_CATEGORY_SCORE: Dict[int, int] = {
    0: 10,    # Main game
    8: 0,     # Remake
    9: 0,     # Remaster
    4: -5,    # Standalone Expansion
    10: -5,   # Expanded Edition
    2: -5,    # Expansion
    11: -10,  # Port
    6: -20,   # Episode
    7: -20,   # Season
    3: -25,   # Bundle
    13: -25,  # Pack
    1: -30,   # DLC / Add-on
    12: -30,  # Fork
    5: -30,   # Mod
    14: -30,  # Update
}

# Thresholds for auto-pick. Tuned so "exact name + year + platform" on the
# main game easily clears them, while ambiguous cases fall through to the
# dialog.
_AUTO_MATCH_MIN_SCORE = 100
_AUTO_MATCH_MIN_MARGIN = 25


def _score_candidate(cand: Dict[str, Any], game_name: str,
                     user_year: Optional[int], user_platform: Optional[str]) -> int:
    """Compute a confidence score for a single IGDB candidate.

    The score composes four independent signals:
      - Name similarity (exact / substring).
      - Release year proximity within the user's stored date (±1 / ±3).
      - Platform overlap with the user's stored platform.
      - IGDB category (main game preferred, ports/bundles/DLC penalized).
    """
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
            # Big year gap is a negative signal on its own - a "1990" user
            # entry almost certainly isn't the 2019 remaster.
            score -= 10

    if user_platform and _platforms_match(user_platform, cand.get("platforms") or []):
        score += 50

    cat = cand.get("category")
    if cat is not None:
        try:
            score += _CATEGORY_SCORE.get(int(cat), 0)
        except (TypeError, ValueError):
            pass

    return score


def _parse_user_year(release_date: Optional[str]) -> Optional[int]:
    """Return the year part of a YYYY-MM-DD-ish string, or None if unparseable."""
    if not release_date or not isinstance(release_date, str) or len(release_date) < 4:
        return None
    try:
        return int(release_date[:4])
    except ValueError:
        return None


def rank_candidates_by_confidence(
    candidates: List[Dict[str, Any]],
    game_name: str,
    release_date: Optional[str],
    platform: Optional[str],
) -> List[Dict[str, Any]]:
    """Re-order IGDB search candidates by composite-confidence score.

    `sort_search_candidates` only knows about the IGDB `category` field, so a
    1990 SNES original and a 2020 remake of it both land in the "Main game"
    bucket and IGDB's raw relevance order decides which is shown first - that
    relevance often surfaces the newer, more popular release first, even
    though the user explicitly stored a 1990 release date for the entry.

    Given the user's known year and platform, this function reuses the same
    `_score_candidate` heuristic that powers `_pick_auto_match` to put the
    most likely match at the top of the list. Stable on score ties so the
    original IGDB / category order survives where signals are uninformative.

    No-op (returns the input order) if no context is provided, so callers
    that don't have year/platform handy don't need to special-case anything.
    """
    if not candidates:
        return candidates
    if not (game_name or release_date or platform):
        return candidates
    user_year = _parse_user_year(release_date)
    indexed = list(enumerate(candidates))
    indexed.sort(
        key=lambda it: (
            -_score_candidate(it[1], game_name, user_year, platform),
            it[0],
        )
    )
    return [c for _, c in indexed]


def _pick_auto_match(game_name: str, release_date: Optional[str],
                     platform: Optional[str],
                     candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pick the single best IGDB candidate for a user's game if confidence is high.

    Returns a candidate only when it satisfies both:
      - its absolute score meets `_AUTO_MATCH_MIN_SCORE`, and
      - it beats the runner-up by at least `_AUTO_MATCH_MIN_MARGIN`.

    Otherwise returns None, letting the caller fall back to the interactive
    match dialog. The `platform` argument is optional so legacy callers that
    don't have it handy (e.g. backfill scripts) still get useful auto-match
    behavior using name + year alone.
    """
    if not candidates:
        return None
    user_year = _parse_user_year(release_date)

    scored = [(_score_candidate(c, game_name, user_year, platform), c) for c in candidates]
    scored.sort(key=lambda it: it[0], reverse=True)

    top_score, top_cand = scored[0]
    if top_score < _AUTO_MATCH_MIN_SCORE:
        return None
    runner_up = scored[1][0] if len(scored) > 1 else (top_score - _AUTO_MATCH_MIN_MARGIN - 1)
    if top_score - runner_up < _AUTO_MATCH_MIN_MARGIN:
        return None
    return top_cand


def search_igdb_candidates(
    game_name: str,
    limit: int = 15,
    *,
    user_release: Optional[str] = None,
    user_platform: Optional[str] = None,
) -> Dict[str, Any]:
    """Network-only search wrapper safe to run on a worker thread.

    Returns {'candidates': [...]} on success or {'_error': 'message'} on failure.
    Results are already re-ordered so main games surface above ports/bundles;
    when `user_release` / `user_platform` are supplied, results are further
    re-ranked by composite confidence (name + year + platform) so the entry
    that best matches the user's stored data is shown first - critical when
    the same title spans multiple decades (1993 original vs. 2020 remake).
    Never touches Tk/PySimpleGUI, so it can run from any thread.
    """
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
    """Network-only details + cover download safe to run on a worker thread.

    Returns the normalized details dict on success or {'_error': 'message'} on
    failure. Never touches Tk/PySimpleGUI.
    """
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
        # Cover download also goes through the same thread-safe HTTP session.
        _ensure_cover_cached(details)
    return details


def _show_igdb_error(parent_window, message: str) -> None:
    location = calculate_popup_center_location(parent_window, 460, 160) if parent_window else None
    sg.popup(message, title="IGDB Error", icon="gameslisticon.ico", location=location)


# ---------------------------------------------------------------------------
# Library enrichment wizard
# ---------------------------------------------------------------------------


def show_library_enrichment_dialog(data_with_indices: List, on_games_updated: Callable,
                                   parent_window=None) -> None:
    """Walk every game missing IGDB metadata, prompting the user for ambiguous matches.

    `data_with_indices` is the same (index, row) structure used elsewhere in the app.
    `on_games_updated` is called once at the end with the count of games successfully
    enriched so the caller can persist + refresh the UI.
    """
    try:
        client = get_igdb_client()
    except IGDBConfigError as exc:
        _show_igdb_error(parent_window, str(exc))
        return

    targets = [
        (idx, row) for idx, row in data_with_indices
        if not (len(row) > 10 and isinstance(row[10], dict) and row[10].get("igdb_id"))
    ]
    if not targets:
        location = calculate_popup_center_location(parent_window, 400, 140) if parent_window else None
        sg.popup("Every game already has IGDB metadata.", title="Nothing to enrich",
                 icon="gameslisticon.ico", location=location)
        return

    config = load_config()
    allow_auto = bool(config.get("igdb_auto_match_on_add"))

    total = len(targets)
    layout = [
        [sg.Text(f"Enriching {total} game(s) from IGDB...", font=("Helvetica", 10, "bold"))],
        [sg.Text("", key="-ENRICH-CURRENT-", size=(60, 1))],
        [sg.ProgressBar(total, orientation="h", size=(50, 18), key="-ENRICH-BAR-")],
        [sg.Text("Ambiguous matches will be shown at the end for review.",
                 font=("Helvetica", 9, "italic"))],
        [sg.Button("Cancel", key="-ENRICH-CANCEL-")],
    ]
    location = calculate_popup_center_location(parent_window, 520, 200) if parent_window else None
    window = sg.Window("Enrich Library from IGDB", layout, modal=True, finalize=True,
                       icon="gameslisticon.ico", location=location)

    cancel_flag = threading.Event()
    ambiguous: List[Dict[str, Any]] = []
    updated = 0

    def _worker():
        nonlocal updated
        for i, (idx, row) in enumerate(targets):
            if cancel_flag.is_set():
                break
            name = row[0]
            release = row[1] if len(row) > 1 else None
            platform = row[2] if len(row) > 2 else None
            window.write_event_value("-ENRICH-PROGRESS-", (i, name))
            try:
                candidates = sort_search_candidates(
                    client.search_games(name, limit=15))
                # Apply the same confidence-based re-rank used in the
                # interactive picker so the auto-match heuristic, the
                # ambiguous-review picker, and the user's eyeballs all see
                # the same most-likely-match-first ordering.
                candidates = rank_candidates_by_confidence(
                    candidates, name, release, platform)
            except IGDBError as exc:
                print(f"IGDB enrich: search failed for {name}: {exc}")
                continue
            if not candidates:
                continue
            auto = _pick_auto_match(name, release, platform, candidates) if allow_auto else None
            if auto is None:
                # Skip for now; queue for end-of-run review.
                ambiguous.append({
                    "idx": idx, "name": name, "release": release,
                    "platform": platform, "candidates": candidates,
                })
                continue
            try:
                details = client.get_game_details(int(auto["id"]))
            except IGDBError as exc:
                print(f"IGDB enrich: details failed for {name}: {exc}")
                continue
            if details.get("cover_image_id"):
                _ensure_cover_cached(details)
            # Persist into the shared row in place.
            while len(row) <= 10:
                row.append(None)
            row[10] = details
            updated += 1
        window.write_event_value("-ENRICH-DONE-", updated)

    _run_async(_worker)

    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, "-ENRICH-CANCEL-"):
                cancel_flag.set()
                break
            if event == "-ENRICH-PROGRESS-":
                i, name = values[event]
                window["-ENRICH-CURRENT-"].update(f"[{i + 1}/{total}] {name}")
                window["-ENRICH-BAR-"].update(i + 1)
                continue
            if event == "-ENRICH-DONE-":
                window["-ENRICH-BAR-"].update(total)
                break
    finally:
        window.close()

    # Walk ambiguous results interactively. Crucially, we also offer this
    # review when the user cancelled mid-run: the network searches that
    # already completed produced ambiguous results, and there's no reason
    # to throw that work away just because the user wanted to stop the
    # remaining searches.
    if ambiguous:
        cancelled_search = cancel_flag.is_set()
        if cancelled_search:
            prompt = (
                f"{len(ambiguous)} game(s) need a manual match.\n"
                "(Searching was cancelled - these are the ambiguous results "
                "found before you stopped. You can still review them.)\n\n"
                "Review now?"
            )
        else:
            prompt = f"{len(ambiguous)} game(s) need a manual match. Review now?"
        location2 = calculate_popup_center_location(parent_window, 460, 200) if parent_window else None
        proceed = sg.popup_yes_no(
            prompt,
            title="Review Ambiguous Matches",
            icon="gameslisticon.ico",
            location=location2)
        if proceed == "Yes":
            for pending in ambiguous:
                chosen = show_match_dialog(
                    pending["name"], pending["candidates"], parent_window,
                    user_release=pending.get("release"),
                    user_platform=pending.get("platform"),
                )
                if chosen is None:
                    # User closed the picker without picking and without
                    # explicitly skipping - treat that as "I'm done
                    # reviewing" rather than "skip just this one".
                    break
                if chosen.get("_skip"):
                    continue
                try:
                    details = client.get_game_details(int(chosen["id"]))
                except IGDBError as exc:
                    print(f"IGDB enrich: details failed for {pending['name']}: {exc}")
                    continue
                if details.get("cover_image_id"):
                    _ensure_cover_cached(details)
                # Look up the row for this original index and update in place.
                for didx, drow in data_with_indices:
                    if didx == pending["idx"]:
                        while len(drow) <= 10:
                            drow.append(None)
                        drow[10] = details
                        updated += 1
                        break

    on_games_updated(updated)
