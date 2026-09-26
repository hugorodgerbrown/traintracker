from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Never pick up a developer's real .env (it could trigger a live download).
    monkeypatch.setenv("TRAINTRACKER_NO_DOTENV", "1")
