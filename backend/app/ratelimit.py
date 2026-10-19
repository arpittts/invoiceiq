"""Rate limits per user and per IP address (blueprint section 18).

A sliding window in memory: the API runs as a single instance on the free host,
so there is nothing to share between processes. A restart forgets the counts,
which is fine for limits measured in minutes; the daily LLM cap, which must
survive restarts, lives in the database (app/services/budget.py).

The client's IP comes from request.client: uvicorn runs with --proxy-headers so
that, behind the host's proxy, this is the visitor's address.
"""

import math
import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status

from app.config import get_settings
from app.deps import CurrentUser

# scope -> (requests, window in seconds)
PER_IP = {
    "login": (10, 60),
    "register": (5, 3600),
    "upload": (60, 3600),
    "ask": (60, 3600),
    "demo": (10, 3600),
}
PER_USER = {
    "upload": (30, 3600),
    "ask": (30, 3600),
    "demo": (5, 3600),
    "reprocess": (20, 3600),
}


class SlidingWindow:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: int) -> float | None:
        """Count a request. Returns None if allowed, else seconds until the next slot."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= limit:
                return hits[0] + window - now
            hits.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindow()


def _check(key: str, limit: int, window: int) -> None:
    if not get_settings().rate_limits_enabled:
        return
    wait = limiter.hit(key, limit, window)
    if wait is not None:
        seconds = max(1, math.ceil(wait))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many requests. Please try again in {seconds} seconds.",
            headers={"Retry-After": str(seconds)},
        )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def limit_ip(scope: str):
    """Dependency: limit requests to `scope` per client IP."""

    def dependency(request: Request) -> None:
        _check(f"{scope}:ip:{_client_ip(request)}", *PER_IP[scope])

    return Depends(dependency)


def limit_user(scope: str):
    """Dependency: limit `scope` per signed-in user, and per IP when PER_IP has it."""

    def dependency(request: Request, user: CurrentUser) -> None:
        if scope in PER_IP:
            _check(f"{scope}:ip:{_client_ip(request)}", *PER_IP[scope])
        _check(f"{scope}:user:{user.id}", *PER_USER[scope])

    return Depends(dependency)
