"""Tests for bin/npm-audit: high npm advisories fail, except the listed ones."""

import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "npm-audit"


def load() -> ModuleType:
    """Import the script, which has no .py suffix."""
    loader = importlib.machinery.SourceFileLoader("npm_audit", str(SCRIPT))
    spec = importlib.util.spec_from_loader("npm_audit", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def report(ghsa: str, severity: str = "high") -> dict:
    """An npm audit --json report with one advisory, reached through vitest."""
    advisory = {"name": "pkg", "severity": severity, "url": f"https://github.com/advisories/{ghsa}"}
    return {
        "vulnerabilities": {
            "pkg": {"severity": severity, "via": [advisory]},
            "vitest": {"severity": severity, "via": ["pkg"]},
        }
    }


def run(monkeypatch: pytest.MonkeyPatch, data: dict) -> int:
    """Run main() on a report given on stdin."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(data)))
    status: int = load().main(["npm-audit", "-"])
    return status


def test_an_excepted_advisory_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A listed advisory is reported as excepted, not failed."""
    ghsa = next(iter(load().EXCEPTIONS))
    assert run(monkeypatch, report(ghsa)) == 0


@pytest.mark.parametrize("severity", ["high", "critical"])
def test_any_other_high_advisory_fails(monkeypatch: pytest.MonkeyPatch, severity: str) -> None:
    """Anything high or critical outside the exceptions fails the audit."""
    assert run(monkeypatch, report("GHSA-xxxx-xxxx-xxxx", severity)) == 1


def test_moderate_advisories_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """The level stays at high, as npm audit --audit-level=high had it."""
    assert run(monkeypatch, report("GHSA-xxxx-xxxx-xxxx", "moderate")) == 0


def test_a_clean_report_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """No vulnerabilities, nothing fails."""
    assert run(monkeypatch, {"vulnerabilities": {}}) == 0
