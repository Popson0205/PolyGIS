"""The live pictures of the cameras, kept in memory: the latest picture of each, and a way to wait for the next.

A phone sends a JPEG every fraction of a second; whoever watches gets them as they come (MJPEG, which browsers play in
an <img>). Nothing is written to the database but the camera's position and when it last sent, so watching holds no
database connection. One copy of the server keeps the pictures; with several copies, the phone and the watchers would
need to reach the same one."""
import asyncio
import time
from dataclasses import dataclass, field

MAX_BYTES = 600_000          # one picture
LIVE_S = 15                  # without a picture for this long, a camera is not live


@dataclass
class Feed:
    frame: bytes = b""
    at: float = 0.0
    seq: int = 0
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)


_feeds: dict[str, Feed] = {}


def feed(cid: str) -> Feed:
    f = _feeds.get(cid)
    if f is None:
        f = _feeds[cid] = Feed()
    return f


async def put(cid: str, frame: bytes) -> int:
    f = feed(cid)
    async with f.cond:
        f.frame, f.at, f.seq = frame, time.time(), f.seq + 1
        f.cond.notify_all()
    return f.seq


def latest(cid: str) -> tuple[bytes, float] | None:
    f = _feeds.get(cid)
    return (f.frame, f.at) if f and f.frame else None


def live(cid: str) -> bool:
    f = _feeds.get(cid)
    return bool(f and f.frame and time.time() - f.at < LIVE_S)


async def next_frame(cid: str, after: int, timeout: float = 10.0) -> tuple[bytes, int] | None:
    """The first picture after `after`, or None when none came within `timeout` seconds."""
    f = feed(cid)
    async with f.cond:
        if f.seq <= after:
            try:
                await asyncio.wait_for(f.cond.wait_for(lambda: f.seq > after), timeout)
            except asyncio.TimeoutError:
                return None
        return f.frame, f.seq


def drop(cid: str) -> None:
    _feeds.pop(cid, None)
