"""The sign-in pages' stylesheet and icons are the public site's, byte for byte.

Until Titan 3/4 moves the site into Django, ``static/`` holds copies of the
files production serves from ``src/traintracker/site/``. This keeps the two
from drifting apart: change the site's file and copy it.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "src" / "traintracker" / "site"


@pytest.mark.parametrize(
    ("copy", "original"),
    [
        ("css/site.css", "site.css"),
        ("icons/icon.svg", "icon.svg"),
        ("icons/icon-32.png", "icon-32.png"),
        ("icons/icon-180.png", "icon-180.png"),
    ],
)
def test_static_copy_matches_the_site(copy: str, original: str) -> None:
    """``static/<copy>`` is the same file as the site's ``<original>``."""
    assert (ROOT / "static" / copy).read_bytes() == (SITE / original).read_bytes()
