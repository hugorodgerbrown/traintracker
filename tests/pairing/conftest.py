"""Fixtures for the TV app's pairing: production's site address and fresh rate limits."""

from typing import Any

import pytest
from django.core.cache import cache

from apps.public import views
from tests.public.fakes import BASE, FIELDS


@pytest.fixture(autouse=True)
def _site(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test on the site's own address, with fresh rate-limit counters."""
    cache.clear()
    settings.SITE_URL = BASE
    monkeypatch.setattr(views, "fields", lambda: FIELDS)
