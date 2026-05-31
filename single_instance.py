"""Single-instance guard for the GameTracker app.

Cross-platform (Windows + Linux/macOS) prevention of a second running copy, with
two independent layers:

  * **Prevention** - an exclusive, non-blocking lock on a per-user lock file
    (``<config dir>/gametracker.lock``), held for the process lifetime.
    ``msvcrt.locking`` on Windows, ``fcntl.flock`` elsewhere. The OS drops the
    lock the instant the holding process dies, so there is no stale-lock problem
    after a crash, and a file lock is immune to the TCP port exclusions Windows
    reserves in the dynamic range (a socket-only guard silently degrades to *no*
    protection on machines with Hyper-V/WSL port reservations).

  * **Activation** (best-effort) - the holder also tries to listen on a fixed
    loopback port; a blocked launch pings it so the running instance brings its
    (possibly tray-hidden) window to the foreground. If that port can't be bound
    (excluded/in use), activation is simply unavailable - prevention still works.

Usage (entry point, before starting the UI)::

    from single_instance import try_acquire
    if try_acquire() is None:
        print("GameTracker is already running.")
        sys.exit(0)
    ...
    # later, once the UI page exists, wire activation:
    inst = get_instance()
    if inst is not None:
        inst.on_activate = bring_window_to_front
"""

from __future__ import annotations

import os
import socket
import threading
from typing import Callable, Optional

# Best-effort activation channel. Loopback-only (no firewall prompt, unreachable
# off-box). Kept in the *registered* range (< 49152) to dodge the dynamic-range
# port exclusions Windows hands to Hyper-V/WSL. Prevention does not depend on it.
_HOST = "127.0.0.1"
_PORT = 48219
_MAGIC = b"GAMETRACKER-SINGLE-INSTANCE-V1"
_LOCK_FILENAME = "gametracker.lock"

_INSTANCE: Optional["SingleInstance"] = None


def _lock_path() -> str:
    """Per-user lock file path (same dir as config.json), temp dir as fallback."""
    try:
        from config import get_config_dir
        return os.path.join(get_config_dir(), _LOCK_FILENAME)
    except Exception:  # noqa: BLE001
        import tempfile
        return os.path.join(tempfile.gettempdir(), _LOCK_FILENAME)


def _try_lock(fh) -> bool:
    """Take an exclusive, non-blocking lock on ``fh``. Returns True on success."""
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _try_bind_activation() -> Optional[socket.socket]:
    """Bind+listen the activation port, or return None if unavailable."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # NB: deliberately NO SO_REUSEADDR - on Windows it would let a second
        # process share the port.
        s.bind((_HOST, _PORT))
        s.listen(8)
        return s
    except OSError:
        try:
            s.close()
        except OSError:
            pass
        return None


class SingleInstance:
    """Holds the lock file handle (+ optional activation listener).

    ``on_activate`` is an optional zero-arg callable invoked (on the listener
    thread) whenever a later launch pings this instance. The UI sets it to a
    "bring my window to the foreground" action; it must marshal onto its own
    event loop itself (the callback runs off-thread).
    """

    def __init__(self, lock_fh, sock: Optional[socket.socket]):
        self._lock_fh = lock_fh
        self._sock = sock
        self.on_activate: Optional[Callable[[], None]] = None
        self._thread: Optional[threading.Thread] = None
        if sock is not None:
            self._thread = threading.Thread(
                target=self._serve, name="single-instance", daemon=True)
            self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return  # socket closed -> stop serving
            try:
                conn.settimeout(1.0)
                if conn.recv(len(_MAGIC)) == _MAGIC:
                    # Ack first so the pinger can confirm it's really us, then
                    # surface the window.
                    try:
                        conn.sendall(_MAGIC)
                    except OSError:
                        pass
                    cb = self.on_activate
                    if cb is not None:
                        try:
                            cb()
                        except Exception:  # noqa: BLE001
                            pass
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def release(self) -> None:
        """Drop the activation listener and the lock (the OS would do this on
        process death anyway; useful for tests / clean shutdown)."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._lock_fh is not None:
            try:
                self._lock_fh.close()  # closing releases the OS lock
            except OSError:
                pass
            self._lock_fh = None


def _ping_existing() -> bool:
    """Ask the running instance to activate. Best-effort; returns True only on a
    valid handshake (so a foreign port squatter isn't mistaken for us)."""
    try:
        with socket.create_connection((_HOST, _PORT), timeout=1.0) as c:
            c.sendall(_MAGIC)
            c.settimeout(1.0)
            return c.recv(len(_MAGIC)) == _MAGIC
    except OSError:
        return False


def try_acquire() -> Optional[SingleInstance]:
    """Acquire the single-instance lock.

    Returns:
      * a :class:`SingleInstance` when this process is the sole instance (keep
        the returned object alive for the process lifetime), OR when the lock
        file can't be opened at all (a degraded guard, so a filesystem quirk
        never traps the user out of their own app);
      * ``None`` when another GameTracker instance already holds the lock (it
        was pinged to come to the foreground - the caller should exit).
    """
    global _INSTANCE
    try:
        fh = open(_lock_path(), "a+")
    except OSError:
        # Can't open the lock file -> don't block the user; run unguarded.
        _INSTANCE = SingleInstance(None, _try_bind_activation())
        return _INSTANCE

    if not _try_lock(fh):
        # Someone else holds it -> a real second instance. Nudge it forward.
        try:
            fh.close()
        except OSError:
            pass
        _ping_existing()  # best-effort
        return None

    _INSTANCE = SingleInstance(fh, _try_bind_activation())
    return _INSTANCE


def get_instance() -> Optional[SingleInstance]:
    """Return the acquired guard (set by :func:`try_acquire`), or ``None``."""
    return _INSTANCE
