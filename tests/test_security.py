"""API-key authentication dependency tests."""
import pytest
from fastapi import HTTPException

from app import security


class _Settings:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key


def test_auth_disabled_when_key_unset(monkeypatch):
    monkeypatch.setattr(security, "get_settings", lambda: _Settings(""))
    assert security.require_api_key(None) is None
    assert security.require_api_key("anything") is None


def test_auth_accepts_matching_key(monkeypatch):
    monkeypatch.setattr(security, "get_settings", lambda: _Settings("secret"))
    assert security.require_api_key("secret") is None


@pytest.mark.parametrize("supplied", [None, "wrong"])
def test_auth_rejects_missing_or_wrong_key(monkeypatch, supplied):
    monkeypatch.setattr(security, "get_settings", lambda: _Settings("secret"))
    with pytest.raises(HTTPException) as exc:
        security.require_api_key(supplied)
    assert exc.value.status_code == 401
