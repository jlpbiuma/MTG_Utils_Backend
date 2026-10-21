"""Ordinary offer tests never call the live Spanish name resolver."""
from unittest.mock import AsyncMock, patch
import pytest


@pytest.fixture(autouse=True)
def mock_offer_name_network():
    with patch('src.services.offer_name_resolver._fetch_exact_spanish_card', AsyncMock(return_value=None)):
        yield
