"""Limits and workspace authorization guard regression tests."""
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.live_rate_limit import _calls, check_analysis_budget, check_auth_budget


def test_auth_attempt_limit_per_account(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AUTH_REQUIRE_DURABLE_LIMITS", "false")
    monkeypatch.setenv("AUTH_LOGIN_ACCOUNT_LIMIT", "2")
    monkeypatch.setenv("AUTH_LOGIN_IP_LIMIT", "10")
    email = f"test-{uuid4().hex}@example.test"
    _calls.clear()
    check_auth_budget(email, "testclient", "login")
    check_auth_budget(email, "testclient", "login")
    with pytest.raises(HTTPException) as error:
        check_auth_budget(email, "testclient", "login")
    assert error.value.status_code == 429
    _calls.clear()


def test_fail_closed_when_redis_is_required(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AUTH_REQUIRE_DURABLE_LIMITS", "true")
    with pytest.raises(HTTPException) as error:
        check_analysis_budget("test-user", "test-workspace", "compare")
    assert error.value.status_code == 503
