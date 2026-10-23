"""Per-workspace analysis limits and authentication abuse controls.

Production deployments should set REDIS_URL and AUTH_REQUIRE_DURABLE_LIMITS=true.
If Redis is unavailable, the production configuration fails closed.
"""
from __future__ import annotations

import hashlib
import os
from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from fastapi import HTTPException

_lock = Lock()
_calls: dict[tuple[str, ...], deque[float]] = defaultdict(deque)
WINDOW_SECONDS = 60


def _allow_local(key: tuple[str, ...], limit: int, seconds: int, message: str) -> None:
    now = monotonic()
    with _lock:
        calls = _calls[key]
        while calls and now - calls[0] >= seconds:
            calls.popleft()
        if len(calls) >= limit:
            retry = max(1, int(seconds - (now - calls[0]) + 1))
            raise HTTPException(429, message, headers={"Retry-After": str(retry)})
        calls.append(now)
        if len(_calls) > 5000:
            for old_key in list(_calls):
                if not _calls[old_key] or now - _calls[old_key][-1] >= seconds:
                    del _calls[old_key]


# A single EVAL makes the counter and expiry operation atomic across API workers.
_REDIS_LIMIT_SCRIPT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {n, redis.call('TTL', KEYS[1])}
"""


def _allow(key: tuple[str, ...], limit: int, seconds: int, message: str) -> None:
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        try:
            from redis import Redis
            client = Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
            namespaced = ":".join(key)
            digest = hashlib.sha256(namespaced.encode()).hexdigest()
            count, ttl = client.eval(_REDIS_LIMIT_SCRIPT, 1, "opsverse:rl:" + digest, seconds)
            if int(count) > limit:
                raise HTTPException(429, message, headers={"Retry-After": str(max(1, int(ttl)))})
            return
        except HTTPException:
            raise
        except Exception as exc:
            # Never silently allow costly operations when the configured store is down.
            raise HTTPException(503, "Request limiter temporarily unavailable") from exc
    if os.getenv("AUTH_REQUIRE_DURABLE_LIMITS", "false").lower() == "true":
        raise HTTPException(503, "Durable request limiting is required")
    _allow_local(key, limit, seconds, message)


def check_analysis_budget(user_id: str, workspace_id: str, action: str, max_calls: int = 12) -> None:
    _allow(("analysis", user_id, workspace_id, action),
           max_calls, WINDOW_SECONDS, "Analysis rate limit reached; retry shortly")


def check_auth_budget(email: str, client_id: str, action: str) -> None:
    """Separate IP and account budgets, backed by Redis when configured.

    Set stricter limits on a deployed environment; keep generous local defaults
    so test fixtures and local onboarding are not artificially blocked.
    """
    if action not in {"login", "register"}:
        raise ValueError("Unsupported authentication action")
    upper = action.upper()
    ip_default = "120" if action == "login" else "100"
    account_default = "50" if action == "login" else "100"
    ip_limit = int(os.getenv(f"AUTH_{upper}_IP_LIMIT", ip_default))
    account_limit = int(os.getenv(f"AUTH_{upper}_ACCOUNT_LIMIT", account_default))
    if ip_limit < 1 or account_limit < 1:
        raise HTTPException(503, "Authentication limits are misconfigured")
    _allow(("auth", action, "ip", client_id), ip_limit, 3600,
           "Too many authentication attempts")
    _allow(("auth", action, "account", email.lower()), account_limit, 3600,
           "Too many authentication attempts")
