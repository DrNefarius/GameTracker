"""Flet IGDB match-picker + metadata fetch/enrich flows (Phase 3F).

Ports the interactive parts of the legacy ``igdb_ui`` (which imports PySimpleGUI)
onto Flet, over the GUI-free ``core.igdb_logic`` (search / ranking / auto-match /
details / cover caching). Used by:

  * the **Game Hub** "Fetch metadata" / "Change match" / "Re-fetch" actions, and
  * the toolbar **Enrich Library from IGDB** wizard.

The match-picker (``open_match_picker``) shows IGDB search candidates for a game,
lets the user re-search under a different title, and applies the chosen entry's
full details (incl. cover download) onto the game's row[10]. All network calls
run on daemon threads; results are marshalled back onto the Flet loop via
``page.run_task``.
"""

import threading

import flet as ft

from core import igdb_logic

try:
    from igdb_integration import IGDB_CATEGORY_LABELS
except Exception:  # pragma: no cover - defensive
    IGDB_CATEGORY_LABELS = {}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run_on(page, coro_fn, *args):
    try:
        page.run_task(coro_fn, *args)
    except Exception:
        pass


def _snack(page, message, error=False):
    try:
        bar = ft.SnackBar(content=ft.Text(message),
                          bgcolor=ft.Colors.ERROR if error else None, duration=3000)
        page.overlay.append(bar)
        bar.open = True
        page.update()
    except Exception:
        pass


def format_candidate_label(cand):
    """Single-line label for a candidate: '[Tag] Name (Year) - plat1, plat2'."""
    name = cand.get("name") or "Unknown"
    year = cand.get("year")
    platforms = ", ".join((cand.get("platforms") or [])[:3])
    category = cand.get("category")
    bits = []
    if category is not None:
        try:
            if int(category) != 0:
                tag = IGDB_CATEGORY_LABELS.get(int(category))
                if tag:
                    bits.append(f"[{tag}]")
        except (TypeError, ValueError):
            pass
    bits.append(name)
    if year:
        bits.append(f"({year})")
    if platforms:
        bits.append(f"- {platforms}")
    return " ".join(bits)


def _candidate_sublabel(cand):
    genres = ", ".join(cand.get("genres") or [])
    parts = []
    if genres:
        parts.append(genres)
    if cand.get("total_rating") is not None:
        try:
            parts.append(f"IGDB {float(cand['total_rating']):.0f}")
        except (TypeError, ValueError):
            pass
    return "  ·  ".join(parts)


def _apply_details_to_row(service, orig_idx, details):
    """Write IGDB details into row[10] and persist."""
    row = list(service.get_game(orig_idx) or [])
    while len(row) <= 10:
        row.append(None)
    row[10] = details
    service.update_game(orig_idx, row)
    service.save()


# --------------------------------------------------------------------------- #
# Match picker
# --------------------------------------------------------------------------- #
def open_match_picker(page, service, orig_idx, on_done=None, initial_query=None):
    """Open the IGDB match picker for the game at ``orig_idx``.

    Searches IGDB for the game's title (confidence-ranked by its stored release
    year + platform), lets the user pick / re-search / mark as not-in-IGDB, and
    on "Use selected" downloads the full details + cover and writes them to the
    row. Calls ``on_done()`` after a successful apply or a skip (so the caller
    can refresh). Cancel just closes.
    """
    row = service.get_game(orig_idx) or []
    game_name = row[0] if row else ""
    release = row[1] if len(row) > 1 else None
    platform = row[2] if len(row) > 2 else None

    state = {"candidates": [], "selected": None, "searching": False}

    query = ft.TextField(label="Search title", value=initial_query or game_name or "",
                         expand=True, dense=True)
    status = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    results = ft.ListView(spacing=2, padding=ft.Padding(4, 4, 4, 4))
    use_btn = ft.Button("Use selected", icon=ft.Icons.CHECK, disabled=True)

    def _set_status(msg):
        status.value = msg
        try:
            page.update()
        except Exception:
            pass

    def _row_item(cand, idx):
        selected = (idx == state["selected"])
        sub = _candidate_sublabel(cand)
        lines = [ft.Text(format_candidate_label(cand), size=13,
                         weight=ft.FontWeight.W_600 if selected else None,
                         color=ft.Colors.PRIMARY if selected else None)]
        if sub:
            lines.append(ft.Text(sub, size=11, color=ft.Colors.ON_SURFACE_VARIANT))
        return ft.Container(
            content=ft.Column(lines, spacing=1, tight=True),
            on_click=lambda e, i=idx: _select(i),
            padding=ft.Padding(10, 6, 10, 6), border_radius=6,
            bgcolor=ft.Colors.with_opacity(0.14, ft.Colors.PRIMARY) if selected else None,
        )

    def _render_results():
        cands = state["candidates"]
        if cands:
            results.controls = [_row_item(c, i) for i, c in enumerate(cands)]
        else:
            results.controls = [ft.Container(
                content=ft.Text("(no results — edit the title above and Search)",
                                italic=True, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                padding=ft.Padding(10, 6, 10, 6))]
        try:
            page.update()
        except Exception:
            pass

    def _select(i):
        state["selected"] = i
        use_btn.disabled = False
        _render_results()

    def _do_search(_=None):
        if state["searching"]:
            return
        q = (query.value or "").strip()
        if not q:
            _set_status("Enter a title to search.")
            return
        state["searching"] = True
        state["selected"] = None
        use_btn.disabled = True
        _set_status(f"Searching IGDB for '{q}'…")

        def _worker():
            result = igdb_logic.search_igdb_candidates(
                q, user_release=release, user_platform=platform)

            async def _after():
                state["searching"] = False
                if result.get("_error"):
                    state["candidates"] = []
                    _render_results()
                    _set_status(f"Error: {result['_error']}")
                    return
                state["candidates"] = result.get("candidates") or []
                _render_results()
                _set_status(f"{len(state['candidates'])} result(s). Click one to select."
                            if state["candidates"] else "No matches. Try a different title.")
            _run_on(page, _after)

        threading.Thread(target=_worker, name="igdb-search", daemon=True).start()

    def _on_use(_):
        sel = state["selected"]
        cands = state["candidates"]
        if sel is None or sel >= len(cands):
            return
        cand = cands[sel]
        igdb_id = cand.get("id")
        if not igdb_id:
            _set_status("That candidate has no IGDB id.")
            return
        use_btn.disabled = True
        _set_status("Fetching details…")

        def _worker():
            details = igdb_logic.load_igdb_details(int(igdb_id))

            async def _after():
                if details.get("_error"):
                    _set_status(f"Error: {details['_error']}")
                    use_btn.disabled = False
                    page.update()
                    return
                _apply_details_to_row(service, orig_idx, details)
                page.pop_dialog()
                _snack(page, f"Applied IGDB metadata: {details.get('name', game_name)}")
                if on_done:
                    on_done()
            _run_on(page, _after)

        threading.Thread(target=_worker, name="igdb-details", daemon=True).start()

    def _on_skip(_):
        # Mark as intentionally not-in-IGDB: store a sentinel so enrichment skips it.
        _apply_details_to_row(service, orig_idx, {"_skipped": True})
        page.pop_dialog()
        _snack(page, "Marked as not in IGDB.")
        if on_done:
            on_done()

    use_btn.on_click = _on_use
    query.on_submit = _do_search

    content = ft.Column(
        [
            ft.Text("Pick the IGDB entry that matches your game:", size=13,
                    weight=ft.FontWeight.W_600),
            ft.Row([query, ft.Button("Search", icon=ft.Icons.SEARCH, on_click=_do_search)],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            ft.Text("Tip: edit the title to try a different spelling or the original title.",
                    size=11, italic=True, color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Container(results, height=300, border_radius=8,
                         bgcolor=ft.Colors.with_opacity(0.03, ft.Colors.ON_SURFACE)),
            status,
        ],
        tight=True, spacing=10,
    )

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Match IGDB game"),
        content=ft.Container(width=620, content=content),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
            ft.OutlinedButton("Not in IGDB (skip)", on_click=_on_skip),
            use_btn,
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    ))

    # Kick off the initial search automatically.
    _render_results()
    _do_search()


# --------------------------------------------------------------------------- #
# Re-fetch (refresh details for an already-matched game)
# --------------------------------------------------------------------------- #
def refetch_metadata(page, service, orig_idx, on_done=None):
    """Re-download details + cover for a game that already has an IGDB id."""
    row = service.get_game(orig_idx) or []
    igdb = row[10] if len(row) > 10 and isinstance(row[10], dict) else None
    igdb_id = igdb.get("igdb_id") if igdb else None
    if not igdb_id:
        # No id yet -> behave like Change match.
        open_match_picker(page, service, orig_idx, on_done=on_done)
        return

    _snack(page, "Re-fetching IGDB metadata…")

    def _worker():
        details = igdb_logic.load_igdb_details(int(igdb_id))

        async def _after():
            if details.get("_error"):
                _snack(page, f"Re-fetch failed: {details['_error']}", error=True)
                return
            _apply_details_to_row(service, orig_idx, details)
            _snack(page, f"Re-fetched: {details.get('name', row[0] if row else '')}")
            if on_done:
                on_done()
        _run_on(page, _after)

    threading.Thread(target=_worker, name="igdb-refetch", daemon=True).start()


# --------------------------------------------------------------------------- #
# Library enrichment wizard
# --------------------------------------------------------------------------- #
def open_enrich_library(page, service, on_done=None):
    """Batch-enrich every game lacking IGDB metadata.

    Searches each candidate game off-thread; auto-applies strong matches when
    ``igdb_auto_match_on_add`` is set, otherwise queues the game for an
    end-of-run interactive review using the same match-picker. Shows live
    progress with a cancel button.
    """
    try:
        from igdb_integration import get_igdb_client, IGDBConfigError
        get_igdb_client()  # surfaces a config error early
    except IGDBConfigError as exc:
        _info(page, "IGDB", str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        _info(page, "IGDB", f"IGDB unavailable: {exc}")
        return

    def _needs_enrich(row):
        meta = row[10] if len(row) > 10 else None
        if isinstance(meta, dict) and (meta.get("igdb_id") or meta.get("_skipped")):
            return False
        return True

    targets = [(idx, row) for idx, row in service.data if _needs_enrich(row)]
    if not targets:
        _info(page, "Nothing to enrich", "Every game already has IGDB metadata.")
        return

    allow_auto = bool(service.config.get("igdb_auto_match_on_add"))
    total = len(targets)
    cancel = threading.Event()
    state = {"updated": 0, "ambiguous": []}

    current = ft.Text("", size=13)
    bar = ft.ProgressBar(value=0)
    pct = ft.Text(f"0 / {total}", size=12, color=ft.Colors.ON_SURFACE_VARIANT)

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Enrich Library from IGDB"),
        content=ft.Container(width=480, content=ft.Column(
            [
                ft.Text(f"Enriching {total} game(s) from IGDB…", weight=ft.FontWeight.W_600),
                current, bar, pct,
                ft.Text("Ambiguous matches are reviewed at the end.",
                        size=11, italic=True, color=ft.Colors.ON_SURFACE_VARIANT),
            ],
            tight=True, spacing=12)),
        actions=[ft.TextButton("Cancel", on_click=lambda e: cancel.set())],
        actions_alignment=ft.MainAxisAlignment.END,
    ))

    def _progress(i, name):
        async def _u():
            current.value = f"[{i + 1}/{total}] {name}"
            bar.value = (i + 1) / total
            pct.value = f"{i + 1} / {total}"
            try:
                page.update()
            except Exception:
                pass
        _run_on(page, _u)

    def _worker():
        for i, (idx, row) in enumerate(targets):
            if cancel.is_set():
                break
            name = row[0]
            release = row[1] if len(row) > 1 else None
            platform = row[2] if len(row) > 2 else None
            _progress(i, name)

            result = igdb_logic.search_igdb_candidates(
                name, user_release=release, user_platform=platform)
            if result.get("_error"):
                continue
            candidates = result.get("candidates") or []
            if not candidates:
                continue

            auto = igdb_logic.pick_auto_match(name, release, platform, candidates) \
                if allow_auto else None
            if auto is None:
                state["ambiguous"].append({"idx": idx, "name": name})
                continue
            details = igdb_logic.load_igdb_details(int(auto["id"]))
            if details.get("_error"):
                continue
            _apply_details_to_row(service, idx, details)
            state["updated"] += 1

        async def _after():
            page.pop_dialog()
            updated = state["updated"]
            ambiguous = state["ambiguous"]
            if updated:
                _snack(page, f"Auto-enriched {updated} game(s).")
            if on_done:
                on_done()
            if ambiguous and not cancel.is_set():
                _offer_ambiguous_review(page, service, ambiguous, on_done)
            elif ambiguous and cancel.is_set():
                _offer_ambiguous_review(page, service, ambiguous, on_done, cancelled=True)
        _run_on(page, _after)

    threading.Thread(target=_worker, name="igdb-enrich", daemon=True).start()


def _offer_ambiguous_review(page, service, ambiguous, on_done, cancelled=False):
    """Prompt to review the games that need a manual match, one picker at a time."""
    note = (" (search was cancelled; these are what was found so far)"
            if cancelled else "")

    def _start(_):
        page.pop_dialog()
        _review_next(page, service, list(ambiguous), 0, on_done)

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Review ambiguous matches"),
        content=ft.Container(width=440, content=ft.Text(
            f"{len(ambiguous)} game(s) need a manual match{note}. Review now?")),
        actions=[
            ft.TextButton("Later", on_click=lambda e: page.pop_dialog()),
            ft.Button("Review now", icon=ft.Icons.FACT_CHECK, on_click=_start),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    ))


def _review_next(page, service, pending, i, on_done):
    """Open the match picker for pending[i]; chain to the next on completion."""
    if i >= len(pending):
        if on_done:
            on_done()
        return

    def _next():
        if on_done:
            on_done()
        _review_next(page, service, pending, i + 1, on_done)

    open_match_picker(page, service, pending[i]["idx"], on_done=_next)


def _info(page, title, message):
    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text(title),
        content=ft.Text(message),
        actions=[ft.TextButton("OK", on_click=lambda e: page.pop_dialog())],
        actions_alignment=ft.MainAxisAlignment.END,
    ))


# --------------------------------------------------------------------------- #
# Rescan game libraries (store manifests)
# --------------------------------------------------------------------------- #
def rescan_game_libraries(page, on_done=None):
    """Re-scan Steam/Epic/GOG manifests (off-thread), report the count."""
    _snack(page, "Rescanning game libraries…")

    def _worker():
        count = None
        err = None
        try:
            from process_watcher import get_watcher
            watcher = get_watcher()
            if watcher is None:
                from store_manifests import scan_all_stores
                from config import load_config, save_config
                idx = scan_all_stores()
                cfg = load_config()
                cfg["watcher_store_index"] = idx.to_serializable()
                save_config(cfg)
                count = len(idx.entries)
            else:
                watcher.rescan_stores()
                snap = watcher.get_state_snapshot() or {}
                count = snap.get("store_entries")
                if count is None:
                    from config import load_config
                    cfg = load_config()
                    count = len((cfg.get("watcher_store_index") or {}).get("entries", []))
        except Exception as exc:  # noqa: BLE001
            err = str(exc)

        async def _after():
            if err:
                _snack(page, f"Library rescan failed: {err}", error=True)
            else:
                _info(page, "Rescan Game Libraries",
                      f"Library rescan complete. Discovered {count} installed game(s).")
            if on_done:
                on_done()
        _run_on(page, _after)

    threading.Thread(target=_worker, name="store-rescan", daemon=True).start()
