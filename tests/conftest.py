"""Shared test fixtures and configuration."""

import pytest
from _pytest.monkeypatch import MonkeyPatch


@pytest.fixture(scope="session")
def monkeypatch_session():
    """Session-scoped monkeypatch fixture."""
    m = MonkeyPatch()
    yield m
    m.undo()


@pytest.fixture(scope="session", autouse=True)
def set_app_api_key(monkeypatch_session):
    """Set APP_API_KEY for all tests in the session."""
    monkeypatch_session.setenv("APP_API_KEY", "test-key-007")


@pytest.fixture(autouse=True)
def reset_rate_limit_state():
    """Reset rate limit windows before each test for isolation."""
    from app.middleware.rate_limit import reset_rate_limit_windows

    reset_rate_limit_windows()
    yield
    reset_rate_limit_windows()
