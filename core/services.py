"""GameLibraryService - the UI-agnostic facade over the data layer.

Holds the in-memory library and current file path, and exposes load / save /
CRUD operations. The in-memory shape matches the rest of the codebase:

    data = list of (orig_idx, row)
    row  = [name, release_date, platform, time_played, status, owned,
            last_played, sessions, status_history, rating, igdb]

``orig_idx`` is an in-session correlation key only; ``save_to_gmd`` re-enumerates
on write and ``load_from_gmd`` re-assigns indices on read, so any unique int is
fine for newly added rows.

This module must not import any GUI toolkit.
"""

import os
from datetime import datetime

from config import load_config, save_config
from constants import _DEBUG, STATUS_PENDING, VALID_STATUSES
from data_management import (
    load_from_gmd,
    save_to_gmd,
    save_data,
    convert_excel_to_gmd,
)
from session_data import migrate_all_game_sessions


def _sort_key(item):
    """Mirror main.main(): unknown release dates ('-') sort last, else by date string."""
    release = item[1][1]
    return (release == "-", release)


class GameLibraryService:
    def __init__(self):
        self.config = load_config()
        self.data = []          # list of (orig_idx, row)
        self.filename = None
        self._next_idx = 0

    # ------------------------------------------------------------------ #
    # internal helpers
    # ------------------------------------------------------------------ #
    def _reset_index_counter(self):
        self._next_idx = max((idx for idx, _ in self.data), default=-1) + 1

    def _apply_loaded(self, data, needs_migration, filename):
        """Run optional migration, sort, store, and refresh the index counter."""
        if data and needs_migration:
            # Best-effort: tolerate a migration failure rather than block loading
            # a file. (migrate_all_game_sessions lives in the GUI-free session_data.)
            try:
                data = migrate_all_game_sessions(data)
                save_data(data, filename)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"GameLibraryService: migration skipped ({exc})")
        self.data = sorted(data, key=_sort_key)
        self.filename = filename
        self._reset_index_counter()

    # ------------------------------------------------------------------ #
    # bootstrap / persistence
    # ------------------------------------------------------------------ #
    def bootstrap(self):
        """Resolve the last-used (or default) .gmd file and load it.

        Mirrors the startup logic in the legacy ``main.main()``.
        """
        last_file = self.config.get("last_file")
        default_dir = self.config.get("default_save_dir", os.path.expanduser("~"))

        if not last_file or not os.path.exists(last_file):
            fn = os.path.join(default_dir, "games_debug.gmd" if _DEBUG else "games.gmd")
        else:
            fn = last_file

        try:
            data, needs_migration = load_from_gmd(fn)
        except FileNotFoundError:
            data, needs_migration = [], False
            save_to_gmd(data, fn)
            self.config["last_file"] = fn
            save_config(self.config)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"GameLibraryService.bootstrap: load failed ({exc}); starting empty")
            data, needs_migration = [], False

        self._apply_loaded(data, needs_migration, fn)
        return self.data

    def open_path(self, path):
        """Load a user-chosen .gmd file and make it the current file."""
        data, needs_migration = load_from_gmd(path)
        self._apply_loaded(data, needs_migration, path)
        self.config["last_file"] = path
        save_config(self.config)
        return self.data

    def import_excel(self, excel_path):
        """Convert an .xlsx library to a sibling .gmd, then load it."""
        gmd_path = os.path.splitext(excel_path)[0] + ".gmd"
        # convert_excel_to_gmd writes the .gmd (7-element rows); re-load it so the
        # rows get normalised to the full 11-element shape by load_from_gmd.
        if not convert_excel_to_gmd(excel_path, gmd_path):
            raise RuntimeError("Excel conversion failed")
        return self.open_path(gmd_path)

    def save(self):
        """Persist the full library to the current file. Returns True on success."""
        if not self.filename:
            return False
        return save_data(self.data, self.filename)

    def save_as(self, path):
        if not path.lower().endswith(".gmd"):
            path += ".gmd"
        self.filename = path
        return self.save()

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #
    def get_game(self, orig_idx):
        for idx, row in self.data:
            if idx == orig_idx:
                return row
        return None

    def add_game(self, row):
        idx = self._next_idx
        self._next_idx += 1
        self.data.append((idx, row))
        self.data = sorted(self.data, key=_sort_key)
        return idx

    def update_game(self, orig_idx, new_row):
        for i, (idx, _) in enumerate(self.data):
            if idx == orig_idx:
                self.data[i] = (idx, new_row)
                self.data = sorted(self.data, key=_sort_key)
                return True
        return False

    def delete_game(self, orig_idx):
        before = len(self.data)
        self.data = [(idx, row) for idx, row in self.data if idx != orig_idx]
        return len(self.data) != before

    def set_status(self, orig_idx, new_status):
        """Quick status change: update status, append a status-history entry, save.

        Returns True if the status actually changed.
        """
        row = self.get_game(orig_idx)
        if not row or len(row) <= 4:
            return False
        old_status = row[4]
        if new_status == old_status or new_status not in VALID_STATUSES:
            return False
        new_row = list(row)
        while len(new_row) <= 8:
            new_row.append(None)
        history = list(new_row[8]) if isinstance(new_row[8], list) else []
        history.append({"from": old_status, "to": new_status,
                        "timestamp": datetime.now().isoformat()})
        new_row[8] = history
        new_row[4] = new_status
        self.update_game(orig_idx, new_row)
        self.save()
        return True


def new_game_row(name, release, platform, time_value, status, owned):
    """Build a fresh 11-element game row with an initial status-history entry."""
    if status not in VALID_STATUSES:
        status = STATUS_PENDING
    return [
        name,
        release or "-",
        platform,
        time_value or None,
        status,
        "✅" if owned else "",
        None,  # last_played
        [],    # sessions
        [{"from": None, "to": status, "timestamp": datetime.now().isoformat()}],
        None,  # rating
        None,  # igdb
    ]


def edited_game_row(existing, name, release, platform, time_value, status, owned):
    """Return an updated row, preserving sessions / history / rating / igdb.

    Appends a status-history entry when the status changed.
    """
    old_status = existing[4] if len(existing) > 4 else None
    sessions = existing[7] if len(existing) > 7 and existing[7] else []
    history = list(existing[8]) if len(existing) > 8 and existing[8] else []
    rating = existing[9] if len(existing) > 9 else None
    igdb = existing[10] if len(existing) > 10 else None

    if status not in VALID_STATUSES:
        status = STATUS_PENDING
    if status != old_status:
        history.append(
            {"from": old_status, "to": status, "timestamp": datetime.now().isoformat()}
        )

    return [
        name,
        release or "-",
        platform,
        time_value or None,
        status,
        "✅" if owned else "",
        existing[6] if len(existing) > 6 else None,  # preserve last_played
        sessions,
        history,
        rating,
        igdb,
    ]
