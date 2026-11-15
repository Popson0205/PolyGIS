"""Live tracking: reading positions from the open formats and feeds, and keeping them.

Own devices and apps send to a tracker's address (no account needed; the address holds a secret token):
  * Traccar Client and OsmAnd (the OsmAnd protocol: ?id=&lat=&lon=&timestamp=&speed=&bearing=&altitude=, speed in knots);
  * OwnTracks in HTTP mode (its JSON location messages);
  * a Traccar server forwarding positions (forward.url with forward.json = true);
  * any system posting JSON ({"device_id", "lat", "lon", "time", "speed" (km/h), "course", …}, one or a list) or GeoJSON.
Feeds PolyGIS asks, over an area:
  * aircraft by ADS-B, from adsb.lol (open data, ODbL; no key) or the OpenSky Network;
  * ships by AIS, from aisstream.io (a free API key);
  * a Geohub form's submissions (patrol check-ins, sightings, reports), as the person signed in."""
import asyncio
import json
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from .errors import Problem

KNOT_KMH = 1.852
TRANSPORT: httpx.AsyncBaseTransport | None = None      # the tests answer for adsb.lol and OpenSky
WS_CONNECT = None                                      # the tests stand in for aisstream.io
KINDS = ("device", "adsb", "ais", "geohub")
# how each kind is drawn: the military symbol of its points (unknown aircraft and ships; own forces)
DEFAULT_SIDC = {"device": "SFGPU----------", "adsb": "SUAPC----------", "ais": "SUSPX----------", "geohub": "SFGPU----------"}
# the least time between two asks of a feed, in seconds (to be a good neighbour to free services)
MIN_REFRESH = {"adsb": 5, "ais": 20, "geohub": 15}
BACKOFF_S = 120                                         # after a feed fails, it is not asked again for this long


@dataclass
class Pos:
    device: str
    lng: float
    lat: float
    ts: datetime
    name: str | None = None
    speed_kmh: float | None = None
    course: float | None = None
    altitude: float | None = None
    props: dict = field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _num(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _time(v) -> datetime:
    """A time from epoch seconds or milliseconds, or ISO text; now when it is missing or unreadable."""
    if v in (None, ""):
        return _now()
    n = _num(v)
    if n is not None:
        try:
            return datetime.fromtimestamp(n / 1000 if n > 1e11 else n, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return _now()
    s = str(v).strip().replace(" UTC", "").replace("Z", "+00:00")
    for cut in (s, s[:32]):
        try:
            t = datetime.fromisoformat(cut)
            return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # aisstream.io's "2024-05-01 10:20:30.123456789 +0000"
    try:
        head, _, tz = s.rpartition(" ")
        return datetime.fromisoformat(head[:26]).replace(tzinfo=timezone.utc)
    except ValueError:
        return _now()


def _ok(p: Pos) -> bool:
    return (-90 <= p.lat <= 90 and -180 <= p.lng <= 180 and not (p.lat == 0 and p.lng == 0)
            and p.ts <= _now() + timedelta(hours=1) and bool(p.device))


def _first(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


SKIP = {"device_id", "deviceid", "device", "id", "imei", "uniqueid", "lat", "latitude", "lon", "lng", "longitude", "time", "timestamp", "ts",
        "fixtime", "devicetime", "speed", "speed_kmh", "speed_knots", "course", "heading", "bearing", "altitude", "alt", "name", "valid"}


def _generic(d: dict, device_hint: str | None = None) -> Pos | None:
    lat, lng = _num(_first(d, "lat", "latitude")), _num(_first(d, "lon", "lng", "longitude"))
    dev = _first(d, "device_id", "deviceid", "device", "id", "imei", "uniqueId", "uniqueid") or device_hint
    if lat is None or lng is None or dev is None:
        return None
    sp = _num(d.get("speed_kmh"))
    if sp is None and _num(d.get("speed_knots")) is not None:
        sp = _num(d.get("speed_knots")) * KNOT_KMH
    if sp is None:
        sp = _num(d.get("speed"))
    extra = {k: v for k, v in d.items() if k.lower() not in SKIP and isinstance(v, (str, int, float, bool)) and len(str(v)) < 500}
    return Pos(str(dev)[:100], lng, lat, _time(_first(d, "time", "timestamp", "ts", "fixTime", "deviceTime")), _first(d, "name"),
               sp, _num(_first(d, "course", "heading", "bearing")), _num(_first(d, "altitude", "alt")), dict(list(extra.items())[:30]))


def parse(query: dict, body, headers: dict) -> list[Pos]:
    """The positions in one request, whichever of the formats above it is in."""
    out: list[Pos] = []
    q = {k.lower(): v for k, v in query.items()}
    if ("lat" in q and ("lon" in q or "lng" in q)) and ("id" in q or "deviceid" in q):
        # OsmAnd protocol (Traccar Client, OsmAnd): speed in knots
        # speed: knots (Traccar Client), or say the unit: speed_ms (the OsmAnd app's {5}) or speed_kmh
        sp = _num(q.get("speed"))
        sp = sp * KNOT_KMH if sp is not None else _num(q.get("speed_kmh"))
        if sp is None and _num(q.get("speed_ms")) is not None:
            sp = _num(q.get("speed_ms")) * 3.6
        extra = {k: v for k, v in q.items() if k in ("accuracy", "batt", "battery", "charge", "hdop", "motion", "event", "activity")}
        out.append(Pos(str(q.get("id") or q.get("deviceid"))[:100], float(_num(q.get("lon", q.get("lng"))) or 0), float(_num(q.get("lat")) or 0),
                       _time(q.get("timestamp")), q.get("name"), sp,
                       _num(q.get("bearing", q.get("heading"))), _num(q.get("altitude")), extra))
    items = body if isinstance(body, list) else [body] if isinstance(body, dict) else []
    hdev = headers.get("x-limit-d")
    huser = headers.get("x-limit-u")
    for d in items:
        if not isinstance(d, dict):
            continue
        if d.get("_type") == "location":                                       # OwnTracks
            dev = hdev or d.get("tid") or d.get("topic") or "owntracks"
            out.append(Pos(f"{huser}/{dev}" if huser else str(dev), float(_num(d.get("lon")) or 0), float(_num(d.get("lat")) or 0), _time(d.get("tst")),
                           d.get("tid") or dev, _num(d.get("vel")), _num(d.get("cog")), _num(d.get("alt")),
                           {k: d[k] for k in ("acc", "batt", "conn", "t") if k in d}))
        elif isinstance(d.get("position"), dict):                               # Traccar server forwarding
            p, dv = d["position"], d.get("device") or {}
            sp = _num(p.get("speed"))
            attrs = {k: v for k, v in (p.get("attributes") or {}).items() if isinstance(v, (str, int, float, bool))}
            out.append(Pos(str(dv.get("uniqueId") or p.get("deviceId") or dv.get("id")), float(_num(p.get("longitude")) or 0),
                           float(_num(p.get("latitude")) or 0), _time(p.get("fixTime") or p.get("deviceTime")), dv.get("name"),
                           sp * KNOT_KMH if sp is not None else None, _num(p.get("course")), _num(p.get("altitude")), dict(list(attrs.items())[:20])))
        elif d.get("type") in ("Feature", "FeatureCollection"):                 # GeoJSON
            for f in (d.get("features") or []) if d["type"] == "FeatureCollection" else [d]:
                g = f.get("geometry") or {}
                if g.get("type") != "Point":
                    continue
                pr = dict(f.get("properties") or {})
                pr.setdefault("lon", g["coordinates"][0]); pr.setdefault("lat", g["coordinates"][1])
                if f.get("id") is not None:
                    pr.setdefault("device_id", f["id"])
                p = _generic(pr)
                if p:
                    out.append(p)
        else:
            p = _generic(d, hdev)
            if p:
                out.append(p)
    good = [p for p in out if _ok(p)]
    if items and not good and not out:
        raise Problem(422, "No positions found: send lat, lon and a device id (see the tracker's setup notes).", code="no_positions")
    return good


# ---------- feeds ----------
def _bbox(cfg: dict) -> tuple[float, float, float, float]:
    b = cfg.get("bbox")
    try:
        w, s, e, n = (float(x) for x in b)
    except (TypeError, ValueError):
        raise Problem(422, "This feed needs an area: set it to the map view.", code="validation") from None
    return w, s, e, n


FALLBACK_NOTE = ("The OpenSky Network did not answer this server (it refuses many cloud hosts, Railway's among them): "
                 "adsb.lol was used instead. Choose adsb.lol as the source to stop seeing this.")


async def adsb(cfg: dict, secret: dict) -> tuple[list[Pos], str | None]:
    """Aircraft over the area, from adsb.lol (default) or the OpenSky Network; when OpenSky cannot be reached, from adsb.lol
    instead, with a note saying so."""
    if (cfg.get("source") or "adsb.lol") == "opensky":
        try:
            return await _adsb(cfg, secret, "opensky"), None
        except (httpx.HTTPError, ValueError):
            return await _adsb(cfg, secret, "adsb.lol"), FALLBACK_NOTE
    return await _adsb(cfg, secret, "adsb.lol"), None


async def _adsb(cfg: dict, secret: dict, source: str) -> list[Pos]:
    w, s, e, n = _bbox(cfg)
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=8), transport=TRANSPORT, headers={"User-Agent": "PolyGIS tracking"}) as c:
        if source == "opensky":
            auth = (secret["user"], secret["password"]) if secret.get("user") and secret.get("password") else None
            r = await c.get("https://opensky-network.org/api/states/all", params={"lamin": s, "lomin": w, "lamax": n, "lomax": e}, auth=auth)
            r.raise_for_status()
            out = []
            for st in r.json().get("states") or []:
                if st[5] is None or st[6] is None:
                    continue
                out.append(Pos(st[0], st[5], st[6], _time(st[3] or st[4]), (st[1] or "").strip() or st[0],
                               st[9] * 3.6 if st[9] is not None else None, st[10], st[7] if st[7] is not None else st[13],
                               {"callsign": (st[1] or "").strip(), "country": st[2], "on_ground": st[8], "squawk": st[14]}))
            return out
        lat, lng = (s + n) / 2, (w + e) / 2
        half = max(abs(n - s) / 2 * 60, abs(e - w) / 2 * 60 * math.cos(math.radians(lat)))         # nautical miles to the edge
        r = await c.get(f"https://api.adsb.lol/v2/point/{lat:.5f}/{lng:.5f}/{min(250, max(1, math.ceil(half * 1.42)))}")
        r.raise_for_status()
        now = _now()
        out = []
        for a in r.json().get("ac") or []:
            if a.get("lat") is None or a.get("lon") is None or not (s <= a["lat"] <= n and w <= a["lon"] <= e):
                continue
            alt = a.get("alt_geom") if isinstance(a.get("alt_geom"), (int, float)) else a.get("alt_baro")
            alt_m = alt * 0.3048 if isinstance(alt, (int, float)) else 0.0 if alt == "ground" else None
            gs = _num(a.get("gs"))
            call = (a.get("flight") or "").strip()
            out.append(Pos(a["hex"], a["lon"], a["lat"], now - timedelta(seconds=float(a.get("seen_pos") or 0)), call or a.get("r") or a["hex"],
                           gs * KNOT_KMH if gs is not None else None, _num(a.get("track")), alt_m,
                           {"callsign": call, "registration": a.get("r"), "type": a.get("t"), "squawk": a.get("squawk"),
                            "category": a.get("category"), "emergency": a.get("emergency") if a.get("emergency") not in (None, "none") else None}))
        return out


async def ais(cfg: dict, secret: dict, seconds: float = 8.0) -> list[Pos]:
    """Ships over the area from aisstream.io: listen for a few seconds and keep the latest report of each."""
    key = secret.get("api_key")
    if not key:
        raise Problem(422, "AIS needs an aisstream.io API key (free at aisstream.io): add it to the tracker.", code="no_key")
    w, s, e, n = _bbox(cfg)
    sub = {"APIKey": key, "BoundingBoxes": [[[s, w], [n, e]]], "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport", "ShipStaticData"]}
    connect = WS_CONNECT
    if connect is None:
        import websockets
        connect = websockets.connect
    ships: dict[str, Pos] = {}
    names: dict[str, dict] = {}
    try:
        async with connect("wss://stream.aisstream.io/v0/stream", open_timeout=10) as ws:
            await ws.send(json.dumps(sub))
            loop = asyncio.get_running_loop()
            end = loop.time() + seconds
            while (left := end - loop.time()) > 0:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=left)
                except asyncio.TimeoutError:
                    break
                m = json.loads(raw)
                if m.get("error"):
                    raise Problem(502, f"aisstream.io: {m['error']}", code="feed")
                meta, kind = m.get("MetaData") or {}, m.get("MessageType")
                mmsi = str(meta.get("MMSI") or "")
                if not mmsi:
                    continue
                body = (m.get("Message") or {}).get(kind) or {}
                if kind == "ShipStaticData":
                    names[mmsi] = {"callsign": (body.get("CallSign") or "").strip(), "ship_type": body.get("Type"),
                                   "destination": (body.get("Destination") or "").strip(), "imo": body.get("ImoNumber")}
                    continue
                lat, lng = _num(meta.get("latitude")), _num(meta.get("longitude"))
                if lat is None or lng is None:
                    continue
                sog = _num(body.get("Sog"))
                hdg = _num(body.get("TrueHeading"))
                ships[mmsi] = Pos(mmsi, lng, lat, _time(meta.get("time_utc")), (meta.get("ShipName") or "").strip() or mmsi,
                                  sog * KNOT_KMH if sog is not None and sog < 102.3 else None, _num(body.get("Cog")), None,
                                  {"mmsi": mmsi, "heading": hdg if hdg is not None and hdg < 511 else None, "nav_status": body.get("NavigationalStatus")})
    except Problem:
        raise
    except Exception as ex:  # noqa: BLE001  (a refused key, a dropped line: said plainly)
        raise Problem(502, f"aisstream.io could not be reached: {ex}", code="feed") from None
    for k, p in ships.items():
        p.props.update({kk: v for kk, v in names.get(k, {}).items() if v not in (None, "")})
    return list(ships.values())


def geohub_positions(page_features: list[dict]) -> list[Pos]:
    """Geohub form submissions as positions: where each was made, by whom and when."""
    import shapely
    import shapely.geometry
    out = []
    for f in page_features:
        try:
            g = shapely.geometry.shape(f["geometry"])
        except Exception:  # noqa: BLE001
            continue
        if g.is_empty:
            continue
        pt = g if g.geom_type == "Point" else g.representative_point()
        who = f.get("created_by_name") or "Unknown"
        props = {k: (", ".join(map(str, v)) if isinstance(v, list) else v) for k, v in (f.get("properties") or {}).items()
                 if isinstance(v, (str, int, float, bool, list))}
        out.append(Pos(str(who)[:100], pt.x, pt.y, _time(f.get("created_at")), who, None, None, None,
                       {**dict(list(props.items())[:30]), "status": f.get("status"), "report_id": str(f.get("id"))}))
    return out


def prune_now() -> bool:
    """Old positions are cleared now and then, not on every report."""
    return random.random() < 0.02
