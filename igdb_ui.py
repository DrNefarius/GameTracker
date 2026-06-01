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

# The IGDB business logic (search / ranking / auto-match / details / cover
# caching) lives in the GUI-free ``core.igdb_logic`` so both this legacy
# PySimpleGUI UI and the Flet UI share one implementation. Re-imported here
# under their historical (underscore-prefixed) names for backwards
# compatibility with existing callers (game_hub.py, this module's dialogs).
from core.igdb_logic import (  # noqa: F401
    cover_image_for as _cover_image_for,
    ensure_cover_cached as _ensure_cover_cached,
    format_seconds_short as _format_seconds_short,
    load_igdb_details,
    parse_user_playtime_seconds as _parse_user_playtime_seconds,
    parse_user_year as _parse_user_year,
    pick_auto_match as _pick_auto_match,
    platforms_match as _platforms_match,
    purge_legacy_jpeg as _purge_legacy_jpeg,
    rank_candidates_by_confidence,
    score_candidate as _score_candidate,
    search_igdb_candidates,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _run_async(target: Callable, *args, **kwargs) -> threading.Thread:
    """Start a daemon worker thread and return it. Small convenience wrapper."""
    t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t


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
