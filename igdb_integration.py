"""
IGDB integration for the GamesList application.

Provides a thin client over the IGDB v4 API using the Twitch OAuth client-credentials
flow. Users supply their own Client ID + Client Secret via Options -> IGDB Settings;
tokens are cached to a file under the user config directory and refreshed lazily.

Public surface:
    - IGDBClient: singleton-ish class wrapping all HTTP interactions
    - IGDBError, IGDBAuthError, IGDBRateLimitError, IGDBNotFoundError, IGDBConfigError
    - get_igdb_client() / reset_igdb_client(): module-level accessor used by the UI
    - build_image_url(image_id, size) / cover_cache_path(igdb_id)

All network calls are blocking; callers running inside the GUI loop should invoke them
from a worker thread and post results back via window.write_event_value(...).
"""

import json
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from config import get_config_dir, load_config
from constants import (
    IGDB_API_BASE,
    IGDB_CACHE_SUBDIR,
    IGDB_COVER_SIZE,
    IGDB_IMAGE_BASE,
    IGDB_RATE_LIMIT_PER_SEC,
    IGDB_TOKEN_FILE,
    IGDB_TOKEN_URL,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class IGDBError(Exception):
    """Base class for IGDB integration errors."""


class IGDBConfigError(IGDBError):
    """Raised when IGDB credentials are missing or the integration is disabled."""


class IGDBAuthError(IGDBError):
    """Raised when the Twitch token request fails (bad credentials, etc.)."""


class IGDBRateLimitError(IGDBError):
    """Raised when IGDB responds with 429 after retries have been exhausted."""


class IGDBNotFoundError(IGDBError):
    """Raised when a lookup by id returns no rows."""


# ---------------------------------------------------------------------------
# IGDB 'category' enum -> human label. Values match IGDB API v4 docs.
# Used to distinguish main games from ports/bundles/DLCs in the match picker.
# ---------------------------------------------------------------------------

IGDB_CATEGORY_LABELS: Dict[int, str] = {
    0: "Main",
    1: "DLC",
    2: "Expansion",
    3: "Bundle",
    4: "Standalone Expansion",
    5: "Mod",
    6: "Episode",
    7: "Season",
    8: "Remake",
    9: "Remaster",
    10: "Expanded Edition",
    11: "Port",
    12: "Fork",
    13: "Pack",
    14: "Update",
}

# Ordering used when ranking search results: lower = shown first.
# Main game > remake/remaster > standalone/expanded > expansions > ports >
# episodes/seasons > bundles/packs > DLC > fork > mod > update. Stable sort
# preserves IGDB's raw relevance within each bucket.
_CATEGORY_RANK: Dict[Optional[int], int] = {
    0: 0,     # Main game
    8: 1,     # Remake
    9: 2,     # Remaster
    4: 3,     # Standalone Expansion
    10: 4,    # Expanded Edition
    2: 5,     # Expansion
    11: 6,    # Port
    6: 7,     # Episode
    7: 8,     # Season
    3: 9,     # Bundle
    13: 10,   # Pack
    1: 11,    # DLC / Add-on
    12: 12,   # Fork
    5: 13,    # Mod
    14: 14,   # Update
}


def sort_search_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort IGDB search results so canonical main games come first.

    The raw IGDB relevance order can bury the canonical main game beneath a
    stack of ports and bundles for popular franchises (e.g. "Super Mario
    World" returning the GBA port, an LCD handheld, and a Japan-only arcade
    entry before the 1990 SNES original). This stable sort re-orders by
    `_CATEGORY_RANK` while preserving IGDB's relevance ordering within each
    bucket.
    """
    def _key(cand: Dict[str, Any]) -> int:
        cat = cand.get("category")
        if cat is None:
            # Unknown category -> sit between ports and episodes so it's
            # neither promoted above main games nor buried with mods/updates.
            return 6
        return _CATEGORY_RANK.get(int(cat), 6)

    return sorted(candidates, key=_key)


# ---------------------------------------------------------------------------
# Paths / image helpers
# ---------------------------------------------------------------------------


def _cache_dir() -> str:
    """Return the absolute path to the IGDB cache dir, creating it if needed."""
    path = os.path.join(get_config_dir(), IGDB_CACHE_SUBDIR)
    os.makedirs(os.path.join(path, "covers"), exist_ok=True)
    return path


def cover_cache_path(igdb_id: int) -> str:
    """Absolute path to the on-disk cached cover image for an IGDB id.

    We cache as PNG because tkinter's built-in PhotoImage only supports
    PNG/GIF; JPEGs would need PIL. IGDB's image CDN serves any format based on
    the URL extension.
    """
    return os.path.join(_cache_dir(), "covers", f"{igdb_id}.png")


def legacy_cover_cache_path(igdb_id: int) -> str:
    """Return the pre-v1 JPEG cache path so we can clean up orphans."""
    return os.path.join(_cache_dir(), "covers", f"{igdb_id}.jpg")


def delete_cached_cover(igdb_id: Optional[int]) -> None:
    """Best-effort removal of every cached cover file for an IGDB id.

    Called when the user removes IGDB metadata from a game or when a
    Re-fetch / Change Match swaps in a new IGDB id, so the on-disk cache
    doesn't accumulate orphaned covers indefinitely. Silently ignores
    missing files and filesystem errors - the cache is purely derived
    state and can always be re-fetched on demand.
    """
    if not igdb_id:
        return
    for path in (cover_cache_path(int(igdb_id)),
                 legacy_cover_cache_path(int(igdb_id))):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


def build_image_url(image_id: str, size: str = IGDB_COVER_SIZE) -> str:
    """Build a full IGDB image URL from an image hash id and a size tag."""
    if not image_id:
        return ""
    return f"{IGDB_IMAGE_BASE}/{size}/{image_id}.png"


def _token_file() -> str:
    return os.path.join(get_config_dir(), IGDB_TOKEN_FILE)


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Simple token-bucket limiter: at most `rate` calls per second, shared across threads."""

    def __init__(self, rate_per_sec: int) -> None:
        self._rate = max(1, int(rate_per_sec))
        self._min_interval = 1.0 / self._rate
        self._lock = threading.Lock()
        self._last = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last)
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._last = now


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class IGDBClient:
    """Thin client over the IGDB v4 API.

    Instances are cheap to construct but hold the OAuth token cache in memory,
    so prefer reusing a single instance (see `get_igdb_client`).
    """

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = (client_id or "").strip()
        self._client_secret = (client_secret or "").strip()
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._lock = threading.Lock()
        self._limiter = _RateLimiter(IGDB_RATE_LIMIT_PER_SEC)
        self._session = requests.Session()

    # -- public --------------------------------------------------------------

    @property
    def has_credentials(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def test_connection(self) -> bool:
        """Force a fresh token + trivial search. Raises IGDBAuthError on failure."""
        self._token = None
        self._token_expires_at = 0.0
        self._ensure_token(force=True)
        # Smallest possible call just to confirm the token is accepted by IGDB too.
        self._post("games", 'fields name; limit 1;')
        return True

    def search_games(self, name: str, limit: int = 15) -> List[Dict[str, Any]]:
        """Return a list of candidate matches for a name. Never raises on no-match.

        The default limit is deliberately generous (15) because popular titles
        have many IGDB entries (ports, bundles, Virtual Console re-releases,
        handheld LCD editions, etc.) and the canonical "main game" entry can
        rank below a handful of ports in raw relevance order. Callers that
        want fewer rows can pass an explicit `limit`.
        """
        name = (name or "").strip()
        if not name:
            return []
        # Escape embedded quotes so the IGDB query string stays valid.
        safe_name = name.replace('"', '\\"')
        body = (
            f'search "{safe_name}"; '
            f'fields id,name,slug,category,first_release_date,cover.image_id,'
            f'platforms.name,genres.name,total_rating,total_rating_count; '
            f'limit {max(1, int(limit))};'
        )
        rows = self._post("games", body)
        return [self._normalize_search_row(r) for r in rows]

    def get_game_details(self, igdb_id: int) -> Dict[str, Any]:
        """Fetch full details (+ time_to_beat) for a known IGDB id, normalized for storage."""
        if not igdb_id:
            raise IGDBNotFoundError("igdb_id is required")
        body = (
            f"fields id,name,slug,summary,first_release_date,"
            f"cover.image_id,genres.name,platforms.name,"
            f"total_rating,total_rating_count,aggregated_rating,aggregated_rating_count;"
            f" where id = {int(igdb_id)};"
        )
        rows = self._post("games", body)
        if not rows:
            raise IGDBNotFoundError(f"IGDB id {igdb_id} not found")
        raw = rows[0]
        ttb = self._fetch_time_to_beat(int(igdb_id))
        return self._normalize_details(raw, ttb)

    def download_cover(self, image_id: str, dest_path: str, size: str = IGDB_COVER_SIZE) -> bool:
        """Download a cover by image_id to dest_path. Returns True on success.

        The file is written atomically and we verify the PNG magic bytes before
        committing so that a malformed payload (e.g. an HTML error page served
        with 200) never lands in the cache as a .png tk can't decode.
        """
        if not image_id:
            return False
        url = build_image_url(image_id, size)
        try:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            self._limiter.acquire()
            resp = self._session.get(url, timeout=20)
            if resp.status_code != 200 or not resp.content:
                return False
            # PNG signature: \x89PNG\r\n\x1a\n
            if not resp.content.startswith(b"\x89PNG\r\n\x1a\n"):
                print(f"IGDB: cover payload for {image_id} is not a PNG "
                      f"(content-type={resp.headers.get('Content-Type')})")
                return False
            tmp = f"{dest_path}.tmp"
            with open(tmp, "wb") as f:
                f.write(resp.content)
            os.replace(tmp, dest_path)
            return True
        except Exception as exc:
            print(f"IGDB: cover download failed for {image_id}: {exc}")
            return False

    # -- internals -----------------------------------------------------------

    def _ensure_token(self, force: bool = False) -> str:
        """Return a valid bearer token, refreshing via Twitch OAuth when necessary."""
        if not self.has_credentials:
            raise IGDBConfigError("IGDB credentials are not configured")

        with self._lock:
            if not force and self._token and time.time() < self._token_expires_at - 60:
                return self._token

            # Try reusing a cached token on disk before hitting Twitch again.
            if not force:
                cached = self._load_cached_token()
                if cached and cached.get("client_id") == self._client_id:
                    expires_at = float(cached.get("expires_at", 0))
                    if time.time() < expires_at - 60:
                        self._token = cached.get("access_token")
                        self._token_expires_at = expires_at
                        return self._token

            try:
                resp = requests.post(
                    IGDB_TOKEN_URL,
                    params={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "grant_type": "client_credentials",
                    },
                    timeout=15,
                )
            except requests.RequestException as exc:
                raise IGDBAuthError(f"Network error reaching Twitch OAuth: {exc}") from exc

            if resp.status_code != 200:
                raise IGDBAuthError(
                    f"Twitch OAuth failed ({resp.status_code}): {resp.text[:200]}"
                )
            data = resp.json()
            token = data.get("access_token")
            expires_in = int(data.get("expires_in", 0))
            if not token:
                raise IGDBAuthError("Twitch OAuth did not return an access_token")

            self._token = token
            self._token_expires_at = time.time() + expires_in
            self._save_cached_token()
            return self._token

    def _headers(self) -> Dict[str, str]:
        return {
            "Client-ID": self._client_id,
            "Authorization": f"Bearer {self._ensure_token()}",
            "Accept": "application/json",
        }

    def _post(self, endpoint: str, body: str, _retry: int = 0) -> List[Dict[str, Any]]:
        """POST a raw IGDB query body, returning the JSON rows list."""
        url = f"{IGDB_API_BASE}/{endpoint}"
        self._limiter.acquire()
        try:
            resp = self._session.post(
                url, data=body.encode("utf-8"), headers=self._headers(), timeout=20
            )
        except requests.RequestException as exc:
            raise IGDBError(f"Network error calling {endpoint}: {exc}") from exc

        if resp.status_code == 401 and _retry == 0:
            # Token was rejected; force refresh and retry once.
            self._ensure_token(force=True)
            return self._post(endpoint, body, _retry=_retry + 1)

        if resp.status_code == 429:
            if _retry < 3:
                # Exponential backoff keyed off the attempt.
                time.sleep(0.5 * (2 ** _retry))
                return self._post(endpoint, body, _retry=_retry + 1)
            raise IGDBRateLimitError("IGDB rate limit exceeded")

        if resp.status_code >= 500 and _retry < 2:
            time.sleep(0.5 * (2 ** _retry))
            return self._post(endpoint, body, _retry=_retry + 1)

        if resp.status_code != 200:
            raise IGDBError(
                f"IGDB {endpoint} failed ({resp.status_code}): {resp.text[:200]}"
            )

        try:
            return resp.json()
        except ValueError as exc:
            raise IGDBError(f"IGDB {endpoint} returned invalid JSON: {exc}") from exc

    def _fetch_time_to_beat(self, igdb_id: int) -> Optional[Dict[str, Optional[int]]]:
        """Fetch time_to_beat (if any) and return a dict in seconds or None."""
        try:
            rows = self._post(
                "game_time_to_beats",
                f"fields game_id,hastily,normally,completely,count;"
                f" where game_id = {int(igdb_id)};",
            )
        except IGDBError as exc:
            print(f"IGDB: time_to_beat lookup failed for {igdb_id}: {exc}")
            return None
        if not rows:
            return None
        row = rows[0]
        return {
            "hastily": row.get("hastily"),
            "normally": row.get("normally"),
            "completely": row.get("completely"),
            "count": row.get("count"),
        }

    # -- normalization -------------------------------------------------------

    @staticmethod
    def _normalize_search_row(row: Dict[str, Any]) -> Dict[str, Any]:
        cover = row.get("cover") or {}
        cover_image_id = cover.get("image_id") if isinstance(cover, dict) else None
        platforms = [p.get("name") for p in row.get("platforms", []) if isinstance(p, dict) and p.get("name")]
        genres = [g.get("name") for g in row.get("genres", []) if isinstance(g, dict) and g.get("name")]
        first_release_ts = row.get("first_release_date")
        year = None
        if isinstance(first_release_ts, (int, float)) and first_release_ts > 0:
            try:
                year = datetime.utcfromtimestamp(int(first_release_ts)).year
            except (OverflowError, OSError, ValueError):
                year = None
        return {
            "id": row.get("id"),
            "name": row.get("name") or "",
            "slug": row.get("slug"),
            "year": year,
            "platforms": platforms,
            "genres": genres,
            "cover_image_id": cover_image_id,
            "total_rating": row.get("total_rating"),
            "total_rating_count": row.get("total_rating_count"),
            "category": row.get("category"),
        }

    @staticmethod
    def _normalize_details(raw: Dict[str, Any], ttb: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        cover = raw.get("cover") or {}
        cover_image_id = cover.get("image_id") if isinstance(cover, dict) else None
        genres = [g.get("name") for g in raw.get("genres", []) if isinstance(g, dict) and g.get("name")]
        platforms = [p.get("name") for p in raw.get("platforms", []) if isinstance(p, dict) and p.get("name")]

        # Prefer aggregated (critic) rating; fall back to total (user + critic blend).
        agg_rating = raw.get("aggregated_rating")
        agg_count = raw.get("aggregated_rating_count")
        if agg_rating is None:
            agg_rating = raw.get("total_rating")
            agg_count = raw.get("total_rating_count")

        first_release_ts = raw.get("first_release_date")
        release_date_str = None
        if isinstance(first_release_ts, (int, float)) and first_release_ts > 0:
            try:
                release_date_str = datetime.utcfromtimestamp(int(first_release_ts)).strftime("%Y-%m-%d")
            except (OverflowError, OSError, ValueError):
                release_date_str = None

        return {
            "igdb_id": raw.get("id"),
            "slug": raw.get("slug"),
            "name": raw.get("name"),
            "cover_image_id": cover_image_id,
            "cover_url": build_image_url(cover_image_id) if cover_image_id else None,
            "cover_cache": f"covers/{raw.get('id')}.png" if raw.get("id") else None,
            "genres": genres,
            "platforms": platforms,
            "summary": raw.get("summary") or "",
            "aggregated_rating": round(float(agg_rating), 1) if agg_rating is not None else None,
            "aggregated_rating_count": int(agg_count) if agg_count is not None else None,
            "release_date": release_date_str,
            "time_to_beat": ttb,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }

    # -- token cache ---------------------------------------------------------

    def _load_cached_token(self) -> Optional[Dict[str, Any]]:
        path = _token_file()
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _save_cached_token(self) -> None:
        if not self._token:
            return
        path = _token_file()
        payload = {
            "client_id": self._client_id,
            "access_token": self._token,
            "expires_at": self._token_expires_at,
        }
        try:
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, path)
        except OSError as exc:
            print(f"IGDB: failed to cache token: {exc}")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_client_lock = threading.Lock()
_client_instance: Optional[IGDBClient] = None
_client_creds: Optional[tuple] = None


def get_igdb_client() -> IGDBClient:
    """Return a shared IGDBClient built from the current saved config.

    Raises IGDBConfigError when the integration is disabled or credentials are blank.
    """
    global _client_instance, _client_creds

    config = load_config()
    if not config.get("igdb_enabled"):
        raise IGDBConfigError(
            "IGDB integration is disabled. Enable it in Options -> IGDB Settings."
        )
    client_id = (config.get("igdb_client_id") or "").strip()
    client_secret = (config.get("igdb_client_secret") or "").strip()
    if not client_id or not client_secret:
        raise IGDBConfigError(
            "IGDB credentials are missing. Set them in Options -> IGDB Settings."
        )

    with _client_lock:
        creds = (client_id, client_secret)
        if _client_instance is None or _client_creds != creds:
            _client_instance = IGDBClient(client_id, client_secret)
            _client_creds = creds
        return _client_instance


def reset_igdb_client() -> None:
    """Force the next get_igdb_client() call to rebuild (e.g. after settings change)."""
    global _client_instance, _client_creds
    with _client_lock:
        _client_instance = None
        _client_creds = None
