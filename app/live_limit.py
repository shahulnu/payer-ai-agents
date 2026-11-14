"""A per-IP daily cap on live (billed) model runs. In memory, so it resets when the service restarts;
a deployment with several instances would keep the counts in a shared store instead."""

from datetime import date
from threading import Lock

from app import settings

_counts: dict[tuple[str, date], int] = {}
_lock = Lock()


def take(ip: str) -> bool:
    """Count one live run for this IP today. Returns False when the IP is over its limit."""
    key = (ip, date.today())
    with _lock:
        used = _counts.get(key, 0)
        if used >= settings.DAILY_LIMIT_PER_IP:
            return False
        _counts[key] = used + 1
        return True
