"""
Centralized logging for the process-watcher subsystem.

Why a dedicated logger instead of the project's stdout `print()` style:
- Watcher debug output is noisy (per-tick decisions, candidate ages, fuzzy
  scores) and you want it isolated from the main app's stdout.
- A rotating file means you can launch a game, hit "Open log folder", and
  read exactly what happened, even if the app was minimized to tray.
- Per-module child loggers (`watcher.resolver`, `watcher.state`,
  `watcher.manifests`, etc.) make it trivial to grep "why didn't X track?".

Public API:
    init_watcher_logging()      idempotent; called once early in main.py
    get_logger(name)            -> logging.Logger (child of 'watcher')
    set_level(level_name)       update the file/console handler level
    get_log_path()              path of the active rotating log file
    get_log_dir()               directory containing the log files
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
from typing import Optional

from config import get_config_dir, load_config


_ROOT_LOGGER_NAME = 'watcher'
_LOG_FILENAME = 'watcher.log'
_MAX_BYTES = 2 * 1024 * 1024     # 2 MiB per file
_BACKUP_COUNT = 3                # plus 3 rotated backups -> ~8 MiB max on disk

_init_lock = threading.Lock()
_initialized = False
_file_handler: Optional[logging.Handler] = None
_console_handler: Optional[logging.Handler] = None


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def get_log_dir() -> str:
    d = os.path.join(get_config_dir(), 'logs')
    os.makedirs(d, exist_ok=True)
    return d


def get_log_path() -> str:
    return os.path.join(get_log_dir(), _LOG_FILENAME)


# ---------------------------------------------------------------------------
# Init / level mgmt
# ---------------------------------------------------------------------------


def _resolve_level(level: object) -> int:
    """Accepts an int, a level name, or None. Defaults to INFO."""
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        l = level.strip().upper()
        if l in ('TRACE',):
            return logging.DEBUG
        return logging.getLevelName(l) if hasattr(logging, l) else logging.INFO
    return logging.INFO


def init_watcher_logging(level: object = None) -> logging.Logger:
    """Idempotent init. Reads `watcher_log_level` from config when level is None."""
    global _initialized, _file_handler, _console_handler
    with _init_lock:
        root = logging.getLogger(_ROOT_LOGGER_NAME)
        # Don't propagate to the root logger; this keeps watcher output out of
        # other handlers a downstream caller might attach later.
        root.propagate = False

        if level is None:
            try:
                level = load_config().get('watcher_log_level', 'INFO')
            except Exception:
                level = 'INFO'
        lvl = _resolve_level(level)
        root.setLevel(lvl)

        if _initialized:
            # Update existing handler levels for runtime changes.
            if _file_handler is not None:
                _file_handler.setLevel(lvl)
            if _console_handler is not None:
                _console_handler.setLevel(lvl)
            return root

        fmt = logging.Formatter(
            fmt='%(asctime)s %(levelname)-7s %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )

        # Rotating file handler.
        try:
            log_path = get_log_path()
            fh = logging.handlers.RotatingFileHandler(
                log_path,
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding='utf-8',
                delay=True,
            )
            fh.setLevel(lvl)
            fh.setFormatter(fmt)
            root.addHandler(fh)
            _file_handler = fh
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[watcher_log] failed to attach file handler: {exc}\n")

        # Console handler so you still see watcher output when running from a
        # terminal. Stderr is used so it doesn't intermix with stdout-bound
        # tooling like image-data dumps.
        try:
            ch = logging.StreamHandler(stream=sys.stderr)
            ch.setLevel(lvl)
            ch.setFormatter(fmt)
            root.addHandler(ch)
            _console_handler = ch
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[watcher_log] failed to attach console handler: {exc}\n")

        _initialized = True
        root.info("watcher logging initialized at %s (level=%s)",
                  get_log_path(), logging.getLevelName(lvl))
        return root


def get_logger(name: str) -> logging.Logger:
    """Return a child logger of 'watcher' (e.g. 'watcher.resolver')."""
    if not _initialized:
        init_watcher_logging()
    if name.startswith(_ROOT_LOGGER_NAME + '.') or name == _ROOT_LOGGER_NAME:
        return logging.getLogger(name)
    return logging.getLogger(f'{_ROOT_LOGGER_NAME}.{name}')


def set_level(level: object) -> None:
    """Runtime level change (no restart needed)."""
    if not _initialized:
        init_watcher_logging(level)
        return
    lvl = _resolve_level(level)
    logging.getLogger(_ROOT_LOGGER_NAME).setLevel(lvl)
    if _file_handler is not None:
        _file_handler.setLevel(lvl)
    if _console_handler is not None:
        _console_handler.setLevel(lvl)
    logging.getLogger(_ROOT_LOGGER_NAME).info(
        "log level changed to %s", logging.getLevelName(lvl))


# Convenience module-level loggers used widely enough to be worth pre-binding.
def core_logger() -> logging.Logger:
    return get_logger('core')


def resolver_logger() -> logging.Logger:
    return get_logger('resolver')


def state_logger() -> logging.Logger:
    return get_logger('state')


def manifest_logger() -> logging.Logger:
    return get_logger('manifests')


def toast_logger() -> logging.Logger:
    return get_logger('toast')


def tray_logger() -> logging.Logger:
    return get_logger('tray')


def bridge_logger() -> logging.Logger:
    return get_logger('bridge')


def idle_logger() -> logging.Logger:
    return get_logger('idle')
