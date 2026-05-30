"""Pure rating calculations (no GUI imports).

These are GUI-free copies of the logic in the legacy ``ratings.py`` (which can't
be imported by the new UI because it does ``import PySimpleGUI`` at module top).
Phase 5 cleanup can collapse the duplication by having ``ratings.py`` re-export
from here.
"""

from collections import Counter

from constants import STAR_FILLED, STAR_EMPTY


def format_rating(rating):
    """Format a rating dict as a 1-5 star string ('' when missing)."""
    if not rating:
        return ""
    try:
        stars = int(rating.get("stars", 0))
        stars = max(0, min(5, stars))
        return STAR_FILLED * stars + STAR_EMPTY * (5 - stars)
    except (ValueError, TypeError, AttributeError):
        return ""


def calculate_session_rating_average(sessions):
    """Duration-weighted average of session star ratings (None if unrated).

    Falls back to an unweighted average when no rated session has a parseable
    duration, so a rated library never silently reports 'no rating'.
    """
    rated = [
        s for s in sessions
        if s.get("feedback") and s["feedback"].get("rating") is not None
    ]
    if not rated:
        return None

    total_weight = 0.0
    weighted_sum = 0.0
    unweighted = []
    for session in rated:
        try:
            stars = session["feedback"]["rating"].get("stars", 0)
            unweighted.append(stars)
            duration = session.get("duration")
            if isinstance(duration, str):
                parts = duration.split(":")
                if len(parts) == 3:
                    h, m, s = map(int, parts)
                    minutes = h * 60 + m + s / 60
                    weight = max(1, minutes / 30)
                    weighted_sum += stars * weight
                    total_weight += weight
        except (ValueError, TypeError, AttributeError):
            continue

    if total_weight > 0:
        return round(weighted_sum / total_weight, 1)
    if unweighted:
        return round(sum(unweighted) / len(unweighted), 1)
    return None


def get_session_rating_summary(sessions):
    """Summary of session ratings: avg stars, exact avg, count, top-5 tags."""
    rated = [
        s for s in sessions
        if s.get("feedback") and s["feedback"].get("rating") is not None
    ]
    if not rated:
        return None

    average = calculate_session_rating_average(sessions)
    if average is None:
        return None

    tags = []
    for session in rated:
        try:
            tags.extend(session["feedback"]["rating"].get("tags", []) or [])
        except (ValueError, TypeError, AttributeError):
            continue

    return {
        "average_stars": int(round(average)),
        "exact_average": average,
        "total_rated_sessions": len(rated),
        "most_common_tags": [t for t, _ in Counter(tags).most_common(5)],
    }
