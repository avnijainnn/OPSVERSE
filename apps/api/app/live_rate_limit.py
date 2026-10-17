"""Small per-process guard for expensive authenticated analysis actions."""
from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from fastapi import HTTPException

_lock = Lock()
_calls: dict[tuple[str, str, str], deque[float]] = defaultdict(deque)
WINDOW_SECONDS = 60


def check_analysis_budget(user_id: str, workspace_id: str, action: str, max_calls: int = 12) -> None:
    now = monotonic()
    key = (user_id, workspace_id, action)
    with _lock:
        calls = _calls[key]
        while calls and now - calls[0] >= WINDOW_SECONDS:
            calls.popleft()
        if len(calls) >= max_calls:
            retry = max(1, int(WINDOW_SECONDS - (now - calls[0]) + 1))
            raise HTTPException(429, "Analysis rate limit reached; retry shortly",
                                headers={"Retry-After": str(retry)})
        calls.append(now)
        if len(_calls) > 5000:
            for old_key in list(_calls):
                if not _calls[old_key] or now - _calls[old_key][-1] >= WINDOW_SECONDS:
                    del _calls[old_key]
