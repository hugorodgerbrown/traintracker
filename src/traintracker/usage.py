"""How much of the Darwin allowance has been used.

Free Darwin access covers 5 million requests per four-week railway period, and
one server key serves every user, so the count has to be visible before it
becomes a problem. Railway periods are not simple to derive (the first and last
of the year vary in length), so usage is measured over a rolling 28 days, which
is never less strict than the period it overlaps.

Requests are counted per Rail Data Marketplace product and compared with the
allowance as a total. If the allowance turns out to be per product, that
overstates usage, which is the safe direction.

Counts are kept in Postgres, in a schema of their own: the timetable schema is
replaced on every refresh, and the auth schema only exists under serve-http.
They gather in memory and are written in batches, so counting costs a tool call
nothing, and a failed write never fails one.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import psycopg
from psycopg import sql

from traintracker.config import SCHEMA_NAME, UK_TZ

log = logging.getLogger(__name__)

LIMIT = 5_000_000
WINDOW_DAYS = 28
KEEP_DAYS = 60  # rows older than this are deleted; the window needs 28
FLUSH_AFTER = 50  # requests waiting to be written
FLUSH_SECONDS = 60.0
WARN_AT = (70, 90)  # percent of the allowance


@dataclass(frozen=True)
class Usage:
    requests: int
    by_product: dict[str, int] = field(default_factory=dict)

    @property
    def percent(self) -> float:
        return round(100 * self.requests / LIMIT, 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "window": f"rolling {WINDOW_DAYS} days",
            "requests": self.requests,
            "by_product": dict(sorted(self.by_product.items())),
            "limit": LIMIT,
            "percent_used": self.percent,
        }

    def line(self) -> str:
        return (
            f"{self.requests:,} request{'' if self.requests == 1 else 's'} in the last "
            f"{WINDOW_DAYS} days, "
            f"{self.percent}% of {LIMIT:,}"
        )


def _today() -> date:
    return datetime.now(UK_TZ).date()


class DarwinUsage:
    def __init__(
        self,
        dsn: str,
        schema: str,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = _today,
    ) -> None:
        if not SCHEMA_NAME.fullmatch(schema):
            raise ValueError(f"Invalid usage schema name {schema!r}.")
        self.dsn = dsn
        self.schema = sql.Identifier(schema)
        self.clock = clock
        self.today = today
        self._pending: Counter[tuple[date, str]] = Counter()
        self._flushed_at = clock()
        self._ready = False  # the table is known to exist
        self._warned: set[int] = set()
        self._lock = asyncio.Lock()

    def count(self, product: str) -> None:
        """Note one request sent to Darwin. Cheap: nothing is written here."""
        self._pending[(self.today(), product)] += 1

    def read_pending(self) -> int:
        """Requests counted and not yet written."""
        return sum(self._pending.values())

    @property
    def due(self) -> bool:
        waiting = self.read_pending()
        if not waiting:
            return False
        return waiting >= FLUSH_AFTER or self.clock() - self._flushed_at >= FLUSH_SECONDS

    async def flush(self, *, force: bool = False) -> None:
        """Write the waiting counts if it is time to, or if `force`d (shutdown).

        A failure is logged and the counts wait for the next flush: usage
        figures are not worth failing a tool call for.
        """
        if not (self.due or (force and self._pending)):
            return
        async with self._lock:
            batch, self._pending = self._pending, Counter()
            self._flushed_at = self.clock()
            try:
                usage = await asyncio.to_thread(self._write, batch)
            except psycopg.Error as exc:
                self._pending.update(batch)
                log.warning("Darwin usage not recorded (will retry): %s", exc)
                return
        self._warn(usage)

    def _write(self, batch: Counter[tuple[date, str]]) -> Usage:
        with psycopg.connect(self.dsn, connect_timeout=10) as con:
            if not self._ready:
                con.execute(
                    sql.SQL(
                        "CREATE SCHEMA IF NOT EXISTS {s}; "
                        "CREATE TABLE IF NOT EXISTS {s}.darwin_requests ("
                        "day DATE NOT NULL, product TEXT NOT NULL, requests BIGINT NOT NULL, "
                        "PRIMARY KEY (day, product))"
                    ).format(s=self.schema)
                )
                self._ready = True
            for (day, product), requests in batch.items():
                con.execute(
                    sql.SQL(
                        "INSERT INTO {s}.darwin_requests (day, product, requests) "
                        "VALUES (%s, %s, %s) ON CONFLICT (day, product) "
                        "DO UPDATE SET requests = darwin_requests.requests + EXCLUDED.requests"
                    ).format(s=self.schema),
                    (day, product, requests),
                )
            con.execute(
                sql.SQL("DELETE FROM {s}.darwin_requests WHERE day < %s").format(s=self.schema),
                (self.today() - timedelta(days=KEEP_DAYS),),
            )
            return self._stored(con)

    def _stored(self, con: psycopg.Connection[Any]) -> Usage:
        rows = con.execute(
            sql.SQL(
                "SELECT product, sum(requests)::bigint FROM {s}.darwin_requests "
                "WHERE day > %s GROUP BY product"
            ).format(s=self.schema),
            (self.today() - timedelta(days=WINDOW_DAYS),),
        ).fetchall()
        by_product = {product: int(requests) for product, requests in rows}
        return Usage(sum(by_product.values()), by_product)

    def read(self) -> Usage:
        """Usage over the window: what is stored, plus what is waiting to be."""
        try:
            with psycopg.connect(self.dsn, connect_timeout=10) as con:
                stored = self._stored(con)
        except (psycopg.errors.UndefinedTable, psycopg.errors.InvalidSchemaName):
            stored = Usage(0)  # nothing has been counted yet
        by_product = Counter(stored.by_product)
        start = self.today() - timedelta(days=WINDOW_DAYS)
        for (day, product), requests in self._pending.items():
            if day > start:
                by_product[product] += requests
        return Usage(sum(by_product.values()), dict(by_product))

    def _warn(self, usage: Usage) -> None:
        """Warn once as usage passes each mark; again only if it falls and returns."""
        for mark in WARN_AT:
            if usage.percent < mark:
                self._warned.discard(mark)
            elif mark not in self._warned:
                self._warned.add(mark)
                log.warning("Darwin usage has passed %d%% of the allowance: %s", mark, usage.line())
