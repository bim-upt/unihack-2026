"""One shared, incremental store of bus positions (telemetry), used by every page.

A bus's window is downloaded once; after that each update only asks for what came in since the newest position
held (GET /buses/{id}/telemetry?from=...), and old positions are dropped. So the map's trail (every second),
the Devices card, Line health and Sightings (every 30 s) read the same rows instead of each re-downloading them.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from ..api import ApiClient, fetch_many, get_telemetry_history
from ..api.models import Telemetry

KEEP = timedelta(hours=3, minutes=15)   # the longest window a page asks for (Line health: 3 h), plus margin
OVERLAP = timedelta(seconds=2)          # re-ask a little before the newest held, so nothing is missed
PAGE = 1000                             # the most the server gives in one call
MAX_PAGES = 6                           # a window is paged back at most this far (the server keeps 5000 a bus)


def _download(api: ApiClient, bus_id: int, since: datetime, whole: bool) -> list[Telemetry]:
    """Positions received since `since`. New rows (`whole` False) fit in one call; a window loaded for the first
    time is paged backwards (newest first, `to` = the oldest row so far) until it's all there."""
    rows = get_telemetry_history(api, bus_id, start=since, limit=PAGE) or []
    pages = 1
    while whole and len(rows) >= PAGE * pages and pages < MAX_PAGES:
        oldest = min(t.received_at for t in rows if t.received_at)
        more = get_telemetry_history(api, bus_id, start=since, end=oldest, limit=PAGE) or []
        pages += 1
        if not more or min(t.received_at for t in more if t.received_at) >= oldest:
            break   # nothing older came back
        rows += more
    return rows


def _key(t: Telemetry) -> tuple:
    return (t.received_at, t.device_id, t.seq, t.ts)


class _Bus:
    __slots__ = ("rows", "covered_from", "fetched_at")

    def __init__(self) -> None:
        self.rows: dict[tuple, Telemetry] = {}
        self.covered_from: datetime | None = None   # everything since then is held
        self.fetched_at = 0.0                       # time.monotonic() of the last call


class HistoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buses: dict[int, _Bus] = {}

    def get(self, api: ApiClient, bus_ids, start: datetime, max_age_s: float
            ) -> dict[int, tuple[list[Telemetry], str | None]]:
        """bus id -> (positions received since `start`, newest first; error). A bus is asked again only if its
        rows are older than `max_age_s`, and then only for what's new (or for an earlier `start`)."""
        now_mono = time.monotonic()
        plans: dict[int, datetime] = {}
        with self._lock:
            for bid in bus_ids:
                b = self._buses.setdefault(bid, _Bus())
                if b.covered_from is None or b.covered_from > start:
                    plans[bid] = start                                   # the window isn't held yet: all of it
                elif now_mono - b.fetched_at >= max_age_s:
                    newest = max((t.received_at for t in b.rows.values()), default=None)
                    plans[bid] = (newest - OVERLAP) if newest else start  # only what came in since
        backfill = {bid for bid in plans if self._buses[bid].covered_from is None
                    or self._buses[bid].covered_from > start}
        got = fetch_many(lambda bid: _download(api, bid, plans[bid], whole=bid in backfill), plans)

        cut = datetime.now(timezone.utc) - KEEP
        out: dict[int, tuple[list[Telemetry], str | None]] = {}
        with self._lock:
            for bid in bus_ids:
                b = self._buses[bid]
                rows, err = got.get(bid, (None, None))
                if bid in plans and err is None:
                    for t in rows or []:
                        if t.received_at:
                            b.rows[_key(t)] = t
                    if b.covered_from is None or plans[bid] < b.covered_from:
                        b.covered_from = plans[bid]   # _download paged back to it (or to the server's oldest)
                    b.fetched_at = now_mono
                for k in [k for k, t in b.rows.items() if t.received_at < cut]:
                    del b.rows[k]
                if b.covered_from is not None and b.covered_from < cut:
                    b.covered_from = cut
                held = sorted((t for t in b.rows.values() if t.received_at >= start),
                              key=lambda t: t.received_at, reverse=True)
                out[bid] = (held, err)
        return out
