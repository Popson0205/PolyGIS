"""The background job: the feeds PolyGIS asks (aircraft, ships, Geohub forms) are asked on a schedule, so positions are
kept and alerts (and their WhatsApp messages) go out when no one has PolyGIS open. Pages with Live on still ask more
often; every ask claims the tracker first, so pages, this job and other copies of the server never ask the same feed
at once. A tracker is left out when it is paused, when its "background" setting is off, and (Geohub) when it has no
Geohub API key, since there is no one signed in to read the form as."""
import asyncio
import logging

from .config import settings
from .db import pool

log = logging.getLogger("feeds")
EVERY = {"adsb": 60, "ais": 120, "geohub": 60}       # seconds between background asks of one tracker
TICK_S = 15
AT_ONCE = 3                                          # trackers asked at the same time (AIS listens for some seconds)


async def due() -> list[dict]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT * FROM trackers WHERE active AND kind = ANY(%s) AND coalesce((config->>'background')::boolean, true) "
            "AND (kind <> 'geohub' OR coalesce(secret->>'geohub_key', '') <> '') "
            "AND (refreshed_at IS NULL OR refreshed_at < now() - make_interval(secs => CASE kind WHEN 'adsb' THEN %s WHEN 'ais' THEN %s ELSE %s END)) "
            "ORDER BY refreshed_at NULLS FIRST LIMIT 50", (list(EVERY), EVERY["adsb"], EVERY["ais"], EVERY["geohub"]))
        return await cur.fetchall()


async def once() -> int:
    """One round: every tracker that is due, a few at a time. Returns how many were asked."""
    from .routers.tracking import ask
    from .errors import Problem
    gate = asyncio.Semaphore(AT_ONCE)

    async def one(t):
        async with gate:
            try:
                await ask(t, (t["secret"] or {}).get("geohub_key"))
            except Problem:
                pass                                  # written on the tracker (last_error), and asked again after a rest
            except Exception:                         # noqa: BLE001 - one bad feed never stops the others
                log.exception("tracker %s could not be asked", t["id"])
    ts = await due()
    await asyncio.gather(*(one(t) for t in ts))
    return len(ts)


async def run() -> None:
    log.info("background feeds: on (aircraft every %s s, ships every %s s, Geohub forms every %s s)", EVERY["adsb"], EVERY["ais"], EVERY["geohub"])
    while True:
        try:
            await once()
        except asyncio.CancelledError:
            raise
        except Exception:                             # noqa: BLE001 - the database away for a moment: try again next tick
            log.exception("background feeds: a round failed")
        await asyncio.sleep(TICK_S)


def start() -> asyncio.Task | None:
    return asyncio.get_running_loop().create_task(run()) if settings.background_feeds else None
