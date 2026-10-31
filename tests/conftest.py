"""Ordinary offer tests never call the live Spanish name resolver."""
from unittest.mock import AsyncMock, patch
import pytest


@pytest.fixture(autouse=True)
def mock_offer_name_network():
    with patch('src.services.offer_name_resolver._fetch_exact_spanish_card', AsyncMock(return_value=None)):
        yield


@pytest.fixture(autouse=True)
def dev_auth_fallbacks(monkeypatch):
    """Endpoint tests act as a user through X-User-Id, as in local development.
    Tests of the production behaviour turn it off (see test_auth.py)."""
    from src.core.config import settings
    monkeypatch.setattr(settings, "ALLOW_INSECURE_DEV_AUTH", True)
