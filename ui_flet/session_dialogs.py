"""Session + feedback dialogs for the Flet UI.

Flet port of the legacy ``session_ui.show_session_feedback_popup`` and
``session_ui.show_manual_session_popup`` (which import PySimpleGUI). Nothing
here imports a GUI toolkit beyond Flet, and the validation / feedback-building
logic is split into dependency-free helpers so it can be unit-tested without a
running ``Page``.

Data shapes produced (mirroring the legacy structures):

    session = {
        'start':    ISO datetime str,
        'end':      ISO datetime str,
        'duration': 'HH:MM:SS',
        'pauses':   [],
        'feedback': {...}        # only when feedback was added
    }

    feedback = {
        'text':      str,
        'timestamp': ISO datetime str,
        'rating': {              # only when a rating was enabled
            'stars':     int (1-5),
            'tags':      [str],
            'timestamp': ISO datetime str,
        },
    }
"""

from datetime import datetime, date, timedelta

import flet as ft

from constants import (
    RATING_TAGS,
    NEGATIVE_TAGS,
    NEUTRAL_TAGS,
    POSITIVE_TAGS,
)
from utilities import format_timedelta_with_seconds
from session_data import (
    add_manual_session_to_game,
    get_game_sessions,
    get_status_history,
    delete_session_from_game,
)


def _safe_update(control):
    """Call ``control.update()`` only if it is mounted.

    A control's ``.page`` *raises* (it does not return None) until the control
    has been added to the page, so a plain truthiness check is not safe inside
    constructors / pre-mount refresh calls.
    """
    try:
        if control.page is not None:
            control.update()
    except (RuntimeError, AssertionError):
        pass


# --------------------------------------------------------------------------- #
# Dependency-free validation (testable without a Page)
# --------------------------------------------------------------------------- #
def validate_manual_session(start_date, start_time, end_date, end_time):
    """Validate manual-session inputs and build the session dict.

    Mirrors the rules in ``session_ui.show_manual_session_popup``:
      * all four fields are required,
      * dates are ``YYYY-MM-DD`` and times are ``HH:MM``,
      * the end must be strictly after the start.

    Returns ``(errors, session_or_None)`` where ``errors`` is a list of
    human-readable strings (empty == valid). On success ``session`` is a dict
    with ``start``/``end`` ISO strings, ``duration`` as ``HH:MM:SS`` and an
    empty ``pauses`` list (feedback is attached by the caller, if any).
    """
    errors = []

    start_date = (start_date or "").strip()
    start_time = (start_time or "").strip()
    end_date = (end_date or "").strip()
    end_time = (end_time or "").strip()

    if not all([start_date, start_time, end_date, end_time]):
        errors.append("All date and time fields are required")
        return errors, None

    start_dt = end_dt = None
    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        st = datetime.strptime(start_time, "%H:%M").time()
        start_dt = datetime.combine(sd, st)
    except ValueError:
        errors.append("Start date/time must be YYYY-MM-DD and HH:MM")

    try:
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
        et = datetime.strptime(end_time, "%H:%M").time()
        end_dt = datetime.combine(ed, et)
    except ValueError:
        errors.append("End date/time must be YYYY-MM-DD and HH:MM")

    if errors:
        return errors, None

    if end_dt <= start_dt:
        errors.append("End time must be after start time")
        return errors, None

    duration = format_timedelta_with_seconds(end_dt - start_dt)
    session = {
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "duration": duration,
        "pauses": [],
    }
    return errors, session


# --------------------------------------------------------------------------- #
# Feedback content builder (testable: returns control + getter)
# --------------------------------------------------------------------------- #
def _build_feedback_content(existing=None):
    """Build the feedback dialog body.

    Returns ``(control, get_feedback_fn)`` where ``control`` is a Flet Control
    to drop into a dialog, and ``get_feedback_fn()`` returns a feedback dict
    matching the legacy structure (or ``None`` if neither notes nor a rating
    were provided).

    ``existing`` is an optional feedback dict to pre-fill from when editing.
    """
    existing = existing or {}
    existing_text = existing.get("text", "") or ""
    existing_rating = existing.get("rating")
    has_rating = existing_rating is not None
    initial_stars = (existing_rating or {}).get("stars", 3) or 3
    initial_tags = set((existing_rating or {}).get("tags", []) or [])

    # --- notes ---------------------------------------------------------- #
    notes_field = ft.TextField(
        label="Session thoughts / notes",
        value=existing_text,
        multiline=True,
        min_lines=4,
        max_lines=8,
    )

    # --- star rating (Row of toggle IconButtons) ------------------------ #
    star_state = {"value": int(initial_stars)}
    star_buttons = []

    def _refresh_stars():
        for i, btn in enumerate(star_buttons):
            filled = (i + 1) <= star_state["value"]
            btn.icon = ft.Icons.STAR if filled else ft.Icons.STAR_BORDER
            btn.icon_color = ft.Colors.AMBER if filled else ft.Colors.GREY
            # Guard: a control's .page raises until it is mounted.
            _safe_update(btn)

    def _make_star_handler(index):
        def _handler(_):
            star_state["value"] = index + 1
            _refresh_stars()
        return _handler

    for i in range(5):
        b = ft.IconButton(
            icon=ft.Icons.STAR_BORDER,
            icon_color=ft.Colors.GREY,
            icon_size=28,
            tooltip=f"{i + 1} star{'s' if i else ''}",
            on_click=_make_star_handler(i),
        )
        star_buttons.append(b)

    stars_row = ft.Row(star_buttons, spacing=0, tight=True)
    stars_label = ft.Text("Stars (1-5):")

    # --- tag chips ------------------------------------------------------ #
    tag_chips = {}  # tag -> ft.Chip

    def _make_chip(tag):
        chip = ft.Chip(
            label=ft.Text(tag),
            selected=tag in initial_tags,
            on_select=lambda _: None,  # selection state is read on Save
        )
        tag_chips[tag] = chip
        return chip

    def _tag_group(title, tags):
        return ft.Column(
            [
                ft.Text(title, weight=ft.FontWeight.BOLD, size=12),
                ft.Row([_make_chip(t) for t in tags], wrap=True, spacing=4, run_spacing=4),
            ],
            tight=True,
            spacing=4,
        )

    rating_section = ft.Column(
        [
            ft.Row([stars_label, stars_row], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text("Tags (optional):"),
            _tag_group("Negative", NEGATIVE_TAGS),
            _tag_group("Neutral", NEUTRAL_TAGS),
            _tag_group("Positive", POSITIVE_TAGS),
        ],
        tight=True,
        spacing=8,
        visible=has_rating,
    )

    # --- enable-rating toggle ------------------------------------------- #
    enable_rating = ft.Checkbox(label="Rate this session", value=has_rating)

    def _on_toggle_rating(_):
        rating_section.visible = enable_rating.value
        _safe_update(rating_section)

    enable_rating.on_change = _on_toggle_rating

    # initialise the star glyphs to the starting value
    _refresh_stars()

    content = ft.Column(
        [
            notes_field,
            enable_rating,
            rating_section,
        ],
        tight=True,
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
    )

    def get_feedback():
        """Return a feedback dict, or None when no notes and no rating."""
        text = (notes_field.value or "").strip()
        feedback = {
            "text": text,
            "timestamp": datetime.now().isoformat(),
        }
        if enable_rating.value:
            tags = [t for t in RATING_TAGS if tag_chips.get(t) and tag_chips[t].selected]
            feedback["rating"] = {
                "stars": int(star_state["value"]),
                "tags": tags,
                "timestamp": datetime.now().isoformat(),
            }
        # Nothing meaningful captured.
        if not feedback["text"] and "rating" not in feedback:
            return None
        return feedback

    return content, get_feedback


# --------------------------------------------------------------------------- #
# Public: feedback dialog
# --------------------------------------------------------------------------- #
def open_feedback_dialog(page, existing=None, on_result=None):
    """Open the session-feedback dialog.

    On Save calls ``on_result(feedback_dict)`` (which may be ``None`` if no
    notes/rating were entered); on Cancel calls ``on_result(None)``.
    """
    is_edit = existing is not None
    content, get_feedback = _build_feedback_content(existing)

    def _close():
        page.pop_dialog()

    def _on_save(_):
        feedback = get_feedback()
        _close()
        if on_result:
            on_result(feedback)

    def _on_cancel(_):
        _close()
        if on_result:
            on_result(None)

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(f"{'Edit' if is_edit else 'Add'} Session Feedback"),
        content=ft.Container(width=520, content=content),
        actions=[
            ft.TextButton("Cancel", on_click=_on_cancel),
            ft.ElevatedButton("Save", icon=ft.Icons.SAVE, on_click=_on_save),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dialog)
    return dialog


# --------------------------------------------------------------------------- #
# Public: manual session dialog
# --------------------------------------------------------------------------- #
def open_manual_session_dialog(page, service, game_name, on_saved=None):
    """Open the "Add manual session" dialog for ``game_name``.

    Two entry methods (mirroring the legacy popup):
      * Method 1: start date/time + end date/time, OR
      * Method 2: a duration (HH:MM) + an end date/time, which back-fills the
        start fields of Method 1.

    On Save the session is validated, attached to the game via
    ``add_manual_session_to_game`` (which also bumps total time + last-played),
    the library is persisted with ``service.save()`` and ``on_saved()`` is
    called.
    """
    today = date.today().strftime("%Y-%m-%d")

    start_date = ft.TextField(label="Start date", value=today, hint_text="YYYY-MM-DD", width=180)
    start_time = ft.TextField(label="Start time", value="19:00", hint_text="HH:MM", width=120)
    end_date = ft.TextField(label="End date", value=today, hint_text="YYYY-MM-DD", width=180)
    end_time = ft.TextField(label="End time", value="21:00", hint_text="HH:MM", width=120)

    # Method 2 helpers: duration + end -> back-fill start fields.
    duration_field = ft.TextField(label="Duration", hint_text="HH:MM", width=120)
    calc_start = ft.Text("", italic=True, size=12, color=ft.Colors.SECONDARY)

    def _recalc_start(_=None):
        dur = (duration_field.value or "").strip()
        ed = (end_date.value or "").strip()
        et = (end_time.value or "").strip()
        if not (dur and ed and et):
            calc_start.value = ""
            _safe_update(calc_start)
            return
        try:
            parts = dur.split(":")
            if len(parts) != 2:
                raise ValueError
            hh, mm = int(parts[0]), int(parts[1])
            end_dt = datetime.combine(
                datetime.strptime(ed, "%Y-%m-%d").date(),
                datetime.strptime(et, "%H:%M").time(),
            )
            start_dt = end_dt - timedelta(hours=hh, minutes=mm)
            calc_start.value = f"Calculated start: {start_dt.strftime('%Y-%m-%d %H:%M')}"
            # Back-fill the Method 1 fields so Save uses the unified path.
            start_date.value = start_dt.strftime("%Y-%m-%d")
            start_time.value = start_dt.strftime("%H:%M")
            end_date.value = ed
            end_time.value = et
        except (ValueError, TypeError):
            calc_start.value = "Invalid duration / end input"
        if calc_start.page is not None:
            page.update()

    duration_field.on_change = _recalc_start

    error_text = ft.Text("", color=ft.Colors.ERROR, visible=False)

    # Feedback captured via the feedback dialog.
    feedback_holder = {"feedback": None}
    feedback_status = ft.Text("No feedback added", italic=True, size=12, color=ft.Colors.SECONDARY)

    def _refresh_feedback_status():
        fb = feedback_holder["feedback"]
        if fb is None:
            feedback_status.value = "No feedback added"
        else:
            bits = []
            if fb.get("text"):
                bits.append("notes")
            if fb.get("rating"):
                bits.append(f"{fb['rating'].get('stars', 0)}★")
            feedback_status.value = "Feedback added" + (f" ({', '.join(bits)})" if bits else "")
        if feedback_status.page is not None:
            feedback_status.update()

    def _on_add_feedback(_):
        def _result(fb):
            # Re-open this dialog after the feedback dialog closes.
            feedback_holder["feedback"] = fb
            page.show_dialog(dialog)
            _refresh_feedback_status()

        # Close the manual-session dialog first, then open the feedback one.
        page.pop_dialog()
        open_feedback_dialog(page, existing=feedback_holder["feedback"], on_result=_result)

    def _on_save(_):
        errors, session = validate_manual_session(
            start_date.value, start_time.value, end_date.value, end_time.value
        )
        if errors:
            error_text.value = "\n".join(errors)
            error_text.visible = True
            page.update()
            return

        if feedback_holder["feedback"] is not None:
            session["feedback"] = feedback_holder["feedback"]

        add_manual_session_to_game(game_name, session, service.data)
        service.save()
        page.pop_dialog()
        if on_saved:
            on_saved()

    method1 = ft.Column(
        [
            ft.Text("Method 1: Start + End times", weight=ft.FontWeight.BOLD),
            ft.Row([start_date, start_time], spacing=8),
            ft.Row([end_date, end_time], spacing=8),
        ],
        tight=True,
        spacing=8,
    )
    method2 = ft.Column(
        [
            ft.Text("Method 2: Duration + End time", weight=ft.FontWeight.BOLD),
            ft.Text("Fills the start fields above from a duration.", size=11, italic=True),
            ft.Row([duration_field, end_date, end_time], spacing=8, wrap=True),
            calc_start,
        ],
        tight=True,
        spacing=8,
    )

    content = ft.Column(
        [
            method1,
            ft.Divider(),
            method2,
            ft.Divider(),
            ft.Row(
                [
                    ft.OutlinedButton(
                        "Add feedback",
                        icon=ft.Icons.RATE_REVIEW,
                        on_click=_on_add_feedback,
                    ),
                    feedback_status,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
            ),
            error_text,
        ],
        tight=True,
        spacing=14,
        scroll=ft.ScrollMode.AUTO,
    )

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(f"Add Manual Session for {game_name}"),
        content=ft.Container(width=560, content=content),
        actions=[
            ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
            ft.ElevatedButton("Add Session", icon=ft.Icons.ADD, on_click=_on_save),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dialog)
    return dialog


# --------------------------------------------------------------------------- #
# Public: per-session actions (view / edit feedback / delete) - mirrors the
# legacy session-table click popup.
# --------------------------------------------------------------------------- #
def open_session_actions_dialog(page, service, game_name, session, on_done=None):
    """Manage one session: view its feedback, edit/remove feedback, or delete it.

    Designed to never stack two dialogs at once (Flet 0.85 shows one at a time):
    every terminal path pops its own dialog and then calls ``on_done()`` exactly
    once, so the caller (e.g. the Game Hub) can restore its own view afterwards.
    ``session`` is the live dict inside the game's session list (mutated in place
    for feedback edits); deletion is by identity via ``delete_session_from_game``.
    """
    def _finish():
        if on_done:
            on_done()

    fb = session.get("feedback") or {}
    rating = fb.get("rating") or {}
    stars = rating.get("stars")
    star_str = ("★" * int(stars) + "☆" * (5 - int(stars))) if stars else ""

    info = [
        ft.Text(f"Start: {session.get('start', '—')}", size=13),
        ft.Text(f"Duration: {session.get('duration', '00:00:00')}", size=13),
    ]
    if star_str:
        info.append(ft.Text(f"Rating: {star_str}", size=13))
    if rating.get("tags"):
        info.append(ft.Text("Tags: " + ", ".join(rating["tags"]), size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT))
    info.append(ft.Text("Feedback: " + (fb.get("text") or "—"), size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT))

    def _edit_feedback(_):
        page.pop_dialog()

        def _res(new_fb):
            if new_fb is not None:
                session["feedback"] = new_fb
                service.save()
            _finish()

        open_feedback_dialog(page, existing=(fb or None), on_result=_res)

    def _remove_feedback(_):
        session.pop("feedback", None)
        service.save()
        page.pop_dialog()
        _finish()

    def _delete_session(_):
        page.pop_dialog()
        sessions = get_game_sessions(service.data, game_name) or []
        index = next((i for i, s in enumerate(sessions) if s is session), None)

        def _confirm(_):
            page.pop_dialog()
            if index is not None:
                delete_session_from_game(game_name, index, service.data)
                service.save()
            _finish()

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Delete session"),
            content=ft.Text("Delete this session? This also reduces the game's "
                            "recorded total play time."),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: (page.pop_dialog(), _finish())),
                ft.ElevatedButton("Delete", icon=ft.Icons.DELETE_OUTLINE,
                                  color=ft.Colors.WHITE, bgcolor=ft.Colors.RED,
                                  on_click=_confirm),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    buttons = [ft.ElevatedButton("Edit feedback", icon=ft.Icons.EDIT,
                                 on_click=_edit_feedback)]
    if fb:
        buttons.append(ft.OutlinedButton("Remove feedback", icon=ft.Icons.CLEAR,
                                         on_click=_remove_feedback))
    buttons.append(ft.OutlinedButton("Delete session", icon=ft.Icons.DELETE_OUTLINE,
                                     on_click=_delete_session))

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text("Session"),
        content=ft.Container(width=480, content=ft.Column(
            [*info, ft.Divider(), ft.Row(buttons, wrap=True, spacing=8)],
            tight=True, spacing=8)),
        actions=[ft.TextButton("Close", on_click=lambda e: (page.pop_dialog(), _finish()))],
        actions_alignment=ft.MainAxisAlignment.END,
    ))


# --------------------------------------------------------------------------- #
# Public: read-only activity-log journal (all notes + status changes)
# --------------------------------------------------------------------------- #
def open_activity_log_dialog(page, service, game_name):
    """Show a chronological, journal-style log of a game's sessions (with their
    notes/ratings) interleaved with status changes - readable like a diary."""
    sessions = get_game_sessions(service.data, game_name) or []
    history = get_status_history(service.data, game_name) or []

    def _parse(ts):
        try:
            return datetime.fromisoformat(ts) if ts else None
        except (ValueError, TypeError):
            return None

    entries = []  # (sort_dt, control)

    for s in sessions:
        dt = _parse(s.get("start"))
        when = dt.strftime("%Y-%m-%d %H:%M") if dt else (s.get("start") or "—")
        fb = s.get("feedback") or {}
        rating = fb.get("rating") or {}
        stars = rating.get("stars")
        star_str = ("★" * int(stars) + "☆" * (5 - int(stars))) if stars else ""
        lines = [ft.Text(f"{when}  ·  played {s.get('duration', '00:00:00')}",
                         weight=ft.FontWeight.W_600, size=13, selectable=True)]
        meta = []
        if star_str:
            meta.append(star_str)
        if rating.get("tags"):
            meta.append(", ".join(rating["tags"]))
        if meta:
            lines.append(ft.Text("  ·  ".join(meta), size=12,
                                 color=ft.Colors.ON_SURFACE_VARIANT, selectable=True))
        if fb.get("text"):
            lines.append(ft.Text(fb["text"], size=12, selectable=True))
        entries.append((dt or datetime.min,
                        ft.Container(content=ft.Column(lines, spacing=2, tight=True),
                                     padding=ft.Padding(10, 8, 10, 8), border_radius=8,
                                     bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.ON_SURFACE))))

    for c in history:
        dt = _parse(c.get("timestamp"))
        when = dt.strftime("%Y-%m-%d %H:%M") if dt else (c.get("timestamp") or "—")
        entries.append((dt or datetime.min,
                        ft.Row([ft.Icon(ft.Icons.SWAP_HORIZ, size=16,
                                        color=ft.Colors.ON_SURFACE_VARIANT),
                                ft.Text(f"{when}  ·  status: {c.get('from') or '—'} → "
                                        f"{c.get('to') or '—'}", size=12, selectable=True)],
                               spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)))

    entries.sort(key=lambda e: e[0])
    body_controls = [e[1] for e in entries] or [
        ft.Text("No activity recorded yet.", color=ft.Colors.ON_SURFACE_VARIANT)
    ]

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text(f"Activity log — {game_name}"),
        content=ft.Container(width=600, height=460,
                             content=ft.Column(body_controls, spacing=8,
                                               scroll=ft.ScrollMode.AUTO, tight=True)),
        actions=[ft.TextButton("Close", on_click=lambda e: page.pop_dialog())],
        actions_alignment=ft.MainAxisAlignment.END,
    ))
