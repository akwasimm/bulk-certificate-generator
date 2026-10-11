"""API-key authentication.

A single shared key is compared against the ``X-API-Key`` header. When
``API_KEY`` is empty the check is disabled, which keeps local runs and the test
suite frictionless; production sets ``API_KEY`` to require the header.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from .config import get_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: str | None = Depends(_api_key_header)) -> None:
    expected = get_settings().api_key
    if expected and api_key != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key.")
