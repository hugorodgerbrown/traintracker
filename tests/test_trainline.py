from __future__ import annotations

import re
from datetime import date

from traintracker import stations, trainline
from traintracker.config import TRAINLINE_LIVE_URL


def test_train_url_is_the_address_trainline_gives_the_train() -> None:
    # As linked from Trainline's Liverpool Street board on 30 September 2026.
    assert trainline.train_url(TRAINLINE_LIVE_URL, "C37268", date(2026, 9, 30), ["LST"]) == (
        "https://www.thetrainline.com/live/departures/london-liverpool-street/"
        "L2NhbGxpbmdQYXR0ZXJuL0MzNzI2OC8yMDI2LTA5LTMw"
    )


def test_station_slugs() -> None:
    assert trainline.station_slug("SRA") == "stratford-london"
    assert trainline.station_slug("kgx") == "london-kings-cross"
    assert trainline.station_slug("SWI") == "swindon-wilts"  # not what its name here gives
    assert trainline.station_slug("CMS") is None  # Cambridge South: no board there
    assert trainline.station_slug("ZZZ") is None


def test_train_url_is_titled_for_the_first_station_with_a_board() -> None:
    day = date(2026, 10, 2)
    url = trainline.train_url("https://example.test/live/", "P66671", day, ["CMS", "CBG", "LST"])
    assert url is not None and url.startswith("https://example.test/live/departures/cambridge/")
    assert trainline.train_url(TRAINLINE_LIVE_URL, "P66671", day, ["CMS"]) is None
    assert trainline.train_url(TRAINLINE_LIVE_URL, "P66671", day, []) is None


def test_slug_exceptions_are_for_stations_in_the_list() -> None:
    for crs, slug in trainline._exceptions().items():
        assert stations.by_crs(crs) is not None, crs
        assert slug is None or re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug), crs
