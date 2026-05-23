"""
Normalize pause records from manual timer sessions and the process watcher.

Manual timer pauses (integrated):
  paused_at, resumed_at, pause_duration, incomplete

Process watcher pauses:
  start, end, reason, resume_reason
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

PauseInterval = Tuple[datetime, datetime]


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _parse_hms_duration(value: str) -> Optional[timedelta]:
    if not value:
        return None
    try:
        parts = value.strip().split(":")
        if len(parts) != 3:
            return None
        hours, minutes, seconds = (int(p) for p in parts)
        return timedelta(hours=hours, minutes=minutes, seconds=seconds)
    except (ValueError, TypeError):
        return None


def pause_interval(pause: Dict) -> Optional[PauseInterval]:
    """Return (start, end) for a single pause dict, or None if not drawable."""
    if not isinstance(pause, dict):
        return None

    start = _parse_iso(pause.get("paused_at") or pause.get("start"))
    if start is None:
        return None

    end = _parse_iso(pause.get("resumed_at") or pause.get("end"))
    if end is None and pause.get("pause_duration"):
        duration = _parse_hms_duration(pause["pause_duration"])
        if duration is not None:
            end = start + duration

    if end is None or end <= start:
        return None
    return start, end


def pause_duration_timedelta(pause: Dict) -> timedelta:
    interval = pause_interval(pause)
    if interval is None:
        return timedelta()
    return interval[1] - interval[0]


def session_pause_periods(session: Dict) -> List[Dict]:
    """Pause intervals as {start, end, duration} with duration in minutes (heatmap)."""
    periods: List[Dict] = []
    for pause in session.get("pauses") or []:
        interval = pause_interval(pause)
        if interval is None:
            continue
        start, end = interval
        periods.append({
            "start": start,
            "end": end,
            "duration": (end - start).total_seconds() / 60,
        })
    return periods


def total_session_pause_timedelta(session: Dict) -> timedelta:
    total = timedelta()
    for pause in session.get("pauses") or []:
        total += pause_duration_timedelta(pause)
    return total


def normalize_pause_to_integrated(pause: Dict) -> Dict:
    """Convert a watcher-style pause to manual integrated fields (for migration)."""
    if not isinstance(pause, dict):
        return pause
    if "paused_at" in pause and "resumed_at" in pause:
        return pause

    interval = pause_interval(pause)
    if interval is None:
        return pause

    start, end = interval
    delta = end - start
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    out = {
        "paused_at": start.isoformat(),
        "resumed_at": end.isoformat(),
        "pause_duration": f"{hours:02d}:{minutes:02d}:{seconds:02d}",
    }
    if pause.get("reason"):
        out["reason"] = pause["reason"]
    if pause.get("resume_reason"):
        out["resume_reason"] = pause["resume_reason"]
    if pause.get("incomplete"):
        out["incomplete"] = True
    return out


def normalize_session_pauses(session: Dict) -> Dict:
    """Return session with pauses converted to integrated manual shape when needed."""
    pauses = session.get("pauses")
    if not pauses:
        return session

    normalized: List[Dict] = []
    changed = False
    for pause in pauses:
        if isinstance(pause, dict) and "start" in pause and "paused_at" not in pause:
            normalized.append(normalize_pause_to_integrated(pause))
            changed = True
        else:
            normalized.append(pause)

    if not changed:
        return session
    out = dict(session)
    out["pauses"] = normalized
    return out
