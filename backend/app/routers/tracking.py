"""Live tracking: trackers (feeds of positions), the address devices send to, asking the ADS-B, AIS and Geohub feeds, the
live picture (latest positions and trails), geofence alerts, and tracks kept as a layer."""
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

import anyio
import httpx
from fastapi import APIRouter, Depends, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import identity, milcoords as mc, tracking
from ..audit import audit
from ..config import settings
from ..db import get_conn, pool
from ..errors import Problem
from ..geoestate_id import GeoEstateIDError
from ..identity import need
from .layers import _next_position, _touch, layer_of
from .projects import project_of

router = APIRouter(tags=["tracking"])
EDIT = need("editor", write=True)
FIELDS = [{"name": n, "type": t} for n, t in (("device", "string"), ("name", "string"), ("time", "string"), ("speed_kmh", "number"),
                                              ("course", "number"), ("altitude", "number"), ("sidc", "string"), ("designation", "string"),
                                              ("tracker", "string"), ("details", "string"))]
KIND_NAMES = {"device": "Devices and apps", "adsb": "Aircraft (ADS-B)", "ais": "Ships (AIS)", "geohub": "Geohub form"}


def _public(t: dict) -> dict:
    t = dict(t)
    t["secret_set"] = sorted(k for k, v in (t.pop("secret", None) or {}).items() if v)
    t["kind_name"] = KIND_NAMES.get(t["kind"], t["kind"])
    if t["kind"] == "device":
        t["ingest_url"] = f"{settings.app_url}{settings.api_prefix}/track/{t['token']}"
    else:
        t.pop("token", None)
    return t


async def tracker_of(conn, tid, user) -> dict:
    cur = await conn.execute("SELECT * FROM trackers WHERE id = %s AND org_id = %s", (tid, user["org_id"]))
    t = await cur.fetchone()
    if not t:
        raise Problem(404, "That tracker was not found.", code="not_found")
    await project_of(conn, t["project_id"], user)
    return t


class TrackerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["device", "adsb", "ais", "geohub"]
    config: dict = Field(default_factory=dict, description="bbox [w,s,e,n] (ADS-B, AIS); source adsb.lol|opensky; form_id (Geohub); "
                                                           "geofence_layer_id; keep_days; trail_minutes")
    secret: dict = Field(default_factory=dict, description="api_key (aisstream.io); user, password (OpenSky)")


class TrackerPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    config: dict | None = None
    secret: dict | None = None
    active: bool | None = None


def _config_ok(kind: str, cfg: dict) -> dict:
    out = {k: v for k, v in cfg.items() if k in ("bbox", "source", "form_id", "form_label", "geofence_layer_id", "geofence_field", "keep_days",
                                                    "trail_minutes", "after")}
    if kind in ("adsb", "ais"):
        try:
            w, s, e, n = (float(x) for x in out.get("bbox") or [])
        except (TypeError, ValueError):
            raise Problem(422, "Give the area the feed covers: [west, south, east, north].", code="validation", fields={"config.bbox": ["missing"]}) from None
        if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
            raise Problem(422, "That area is not a valid [west, south, east, north].", code="validation", fields={"config.bbox": ["invalid"]})
        if (e - w) * (n - s) > 400:
            raise Problem(422, "That area is too large for a live feed (at most about 20° × 20°): zoom in.", code="validation", fields={"config.bbox": ["too large"]})
        out["bbox"] = [w, s, e, n]
    if kind == "adsb" and out.get("source", "adsb.lol") not in ("adsb.lol", "opensky"):
        raise Problem(422, "The ADS-B source is adsb.lol or opensky.", code="validation")
    if kind == "geohub" and not out.get("form_id"):
        raise Problem(422, "Choose the Geohub form.", code="validation", fields={"config.form_id": ["missing"]})
    out["keep_days"] = max(1, min(365, int(out.get("keep_days") or 7)))
    out["trail_minutes"] = max(0, min(24 * 60, int(out.get("trail_minutes") or 60)))
    return out


@router.get("/projects/{project_id}/trackers", summary="The project's trackers")
async def listing(project_id: UUID, user=Depends(need()), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    cur = await conn.execute("SELECT t.*, (SELECT count(*) FROM track_devices d WHERE d.tracker_id = t.id) AS devices "
                             "FROM trackers t WHERE project_id = %s ORDER BY created_at", (project_id,))
    return [_public(t) for t in await cur.fetchall()]


@router.post("/projects/{project_id}/trackers", status_code=201, summary="Add a tracker, with a layer of the latest positions")
async def create(project_id: UUID, body: TrackerIn, user=Depends(EDIT), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    cfg = _config_ok(body.kind, body.config)
    if cfg.get("geofence_layer_id"):
        g = await layer_of(conn, cfg["geofence_layer_id"], user)
        if g["geometry_type"] != "Polygon":
            raise Problem(422, "Geofences are polygons.", code="validation")
    pos = await _next_position(conn, project_id)
    style = {"type": "milsymbol", "color": "#2f6db5", "stroke": "#ffffff", "width": 1, "size": 6, "opacity": 1,
             "mil": {"sidc": tracking.DEFAULT_SIDC[body.kind], "sidc_field": "sidc", "designation_field": "designation", "higher_field": None, "size": 30}}
    async with conn.transaction():
        cur = await conn.execute(
            "INSERT INTO layers (project_id, org_id, name, geometry_type, fields, style, position, source, created_by) "
            "VALUES (%s, %s, %s, 'Point', %s, %s, %s, %s, %s) RETURNING id",
            (project_id, user["org_id"], body.name.strip()[:200], Jsonb(FIELDS), Jsonb(style), pos, Jsonb({"tracking": body.kind}), user["id"]))
        lid = (await cur.fetchone())["id"]
        cur = await conn.execute(
            "INSERT INTO trackers (project_id, org_id, name, kind, token, config, secret, layer_id, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (project_id, user["org_id"], body.name.strip(), body.kind, secrets.token_urlsafe(24), Jsonb(cfg),
             Jsonb({k: str(v) for k, v in body.secret.items() if v}), lid, user["id"]))
        t = await cur.fetchone()
    await audit(conn, user, "tracker.create", "tracker", t["id"], name=t["name"], kind=t["kind"])
    return {**_public(t), "devices": 0, "layer": await layer_of(conn, lid, user)}


@router.patch("/trackers/{tracker_id}", summary="Rename a tracker, change its area, feed, geofences or keys, or pause it")
async def update(tracker_id: UUID, body: TrackerPatch, user=Depends(EDIT), conn=Depends(get_conn)):
    t = await tracker_of(conn, tracker_id, user)
    sets, vals = [], []
    if body.name is not None:
        sets.append("name = %s"); vals.append(body.name.strip())
    if body.config is not None:
        cfg = _config_ok(t["kind"], {**t["config"], **body.config})
        if cfg.get("geofence_layer_id"):
            g = await layer_of(conn, cfg["geofence_layer_id"], user)
            if g["geometry_type"] != "Polygon":
                raise Problem(422, "Geofences are polygons.", code="validation")
        sets.append("config = %s"); vals.append(Jsonb(cfg))
    if body.secret is not None:
        merged = {**t["secret"], **{k: str(v) for k, v in body.secret.items() if v is not None}}
        sets.append("secret = %s"); vals.append(Jsonb({k: v for k, v in merged.items() if v}))
    if body.active is not None:
        sets.append("active = %s"); vals.append(body.active)
    if sets:
        cur = await conn.execute(f"UPDATE trackers SET {', '.join(sets)}, updated_at = now() WHERE id = %s RETURNING *", (*vals, tracker_id))
        t = await cur.fetchone()
    return _public(t)


@router.post("/trackers/{tracker_id}/token", summary="A new sending address (the old one stops working)")
async def new_token(tracker_id: UUID, user=Depends(EDIT), conn=Depends(get_conn)):
    await tracker_of(conn, tracker_id, user)
    cur = await conn.execute("UPDATE trackers SET token = %s, updated_at = now() WHERE id = %s RETURNING *", (secrets.token_urlsafe(24), tracker_id))
    return _public(await cur.fetchone())


@router.delete("/trackers/{tracker_id}", status_code=204, summary="Delete a tracker and its positions (its layer stays)")
async def delete(tracker_id: UUID, user=Depends(EDIT), conn=Depends(get_conn)):
    t = await tracker_of(conn, tracker_id, user)
    await conn.execute("DELETE FROM trackers WHERE id = %s", (tracker_id,))
    await audit(conn, user, "tracker.delete", "tracker", tracker_id, name=t["name"])
    return Response(status_code=204)


# ---------- keeping positions ----------
async def ingest(conn, t: dict, positions: list[tracking.Pos]) -> int:
    """Keep positions: each in the history, the latest of each device in the tracker's layer, and alerts on geofences."""
    if not positions:
        return 0
    cfg = t["config"] or {}
    fence = cfg.get("geofence_layer_id")
    fence_field = cfg.get("geofence_field") or "name"
    kept = 0
    latest: dict[str, tracking.Pos] = {}
    for p in sorted(positions, key=lambda x: x.ts):
        cur = await conn.execute(
            "INSERT INTO track_points (tracker_id, device, ts, geom, speed_kmh, course, altitude, props) "
            "SELECT %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, %s "
            "WHERE NOT EXISTS (SELECT 1 FROM track_points WHERE tracker_id = %s AND device = %s AND ts = %s) RETURNING id",
            (t["id"], p.device, p.ts, p.lng, p.lat, p.speed_kmh, p.course, p.altitude, Jsonb(p.props), t["id"], p.device, p.ts))
        if not await cur.fetchone():
            continue                                              # already had it (a feed asked twice)
        kept += 1
        latest[p.device] = p
        if fence:
            cur = await conn.execute(
                "SELECT coalesce(nullif(props->>%s, ''), id::text) AS z FROM features WHERE layer_id = layer_data(%s) "
                "AND ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))", (fence_field, fence, p.lng, p.lat))
            now_in = sorted({r["z"] for r in await cur.fetchall()})
            cur = await conn.execute("SELECT zones FROM track_devices WHERE tracker_id = %s AND device = %s", (t["id"], p.device))
            row = await cur.fetchone()
            was = set(row["zones"]) if row else set()
            for kind, zones in (("enter", set(now_in) - was), ("exit", was - set(now_in))):
                for z in sorted(zones):
                    await conn.execute("INSERT INTO track_alerts (tracker_id, project_id, device, name, kind, zone, ts, geom) "
                                       "VALUES (%s, %s, %s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
                                       (t["id"], t["project_id"], p.device, p.name or p.device, kind, z, p.ts, p.lng, p.lat))
            await conn.execute("INSERT INTO track_devices (tracker_id, device, name, zones) VALUES (%s, %s, %s, %s) "
                               "ON CONFLICT (tracker_id, device) DO UPDATE SET zones = EXCLUDED.zones", (t["id"], p.device, p.name, now_in))
    # the layer: one point per device, where it last was (its symbol and designation kept if someone changed them)
    for dev, p in latest.items():
        cur = await conn.execute("SELECT feature_id, last_ts FROM track_devices WHERE tracker_id = %s AND device = %s", (t["id"], dev))
        d = await cur.fetchone()
        if d and d["last_ts"] and d["last_ts"] >= p.ts and d["feature_id"]:
            continue
        details = "; ".join(f"{k}: {v}" for k, v in p.props.items() if v not in (None, "") and k not in ("status",))[:500]
        props = {"device": dev, "name": p.name or dev, "time": p.ts.isoformat(), "speed_kmh": None if p.speed_kmh is None else round(p.speed_kmh, 1),
                 "course": None if p.course is None else round(p.course, 1), "altitude": None if p.altitude is None else round(p.altitude, 1),
                 "tracker": t["name"], "details": details or None}
        fid = d["feature_id"] if d else None
        if fid and t["layer_id"]:
            cur = await conn.execute("UPDATE features SET geom = ST_SetSRID(ST_MakePoint(%s, %s), 4326), props = props || %s, updated_at = now() "
                                     "WHERE id = %s AND layer_id = layer_data(%s) RETURNING id", (p.lng, p.lat, Jsonb(props), fid, t["layer_id"]))
            if not await cur.fetchone():
                fid = None                                        # its point was deleted from the layer: make it again
        if not fid and t["layer_id"]:
            cur = await conn.execute("INSERT INTO features (layer_id, geom, props) VALUES (layer_data(%s), ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s) RETURNING id",
                                     (t["layer_id"], p.lng, p.lat, Jsonb({**props, "designation": p.name or dev})))
            fid = (await cur.fetchone())["id"]
        await conn.execute("INSERT INTO track_devices (tracker_id, device, name, feature_id, last_ts) VALUES (%s, %s, %s, %s, %s) "
                           "ON CONFLICT (tracker_id, device) DO UPDATE SET name = EXCLUDED.name, feature_id = EXCLUDED.feature_id, last_ts = EXCLUDED.last_ts",
                           (t["id"], dev, p.name or dev, fid, p.ts))
    if kept:
        await conn.execute("UPDATE trackers SET last_at = greatest(coalesce(last_at, 'epoch'), %s), last_error = NULL WHERE id = %s",
                           (max(p.ts for p in latest.values()), t["id"]))
        if t["layer_id"]:
            await conn.execute("UPDATE layers SET updated_at = clock_timestamp() WHERE id = %s", (t["layer_id"],))
    if tracking.prune_now():
        await conn.execute("DELETE FROM track_points WHERE tracker_id = %s AND ts < now() - make_interval(days => %s)", (t["id"], cfg.get("keep_days", 7)))
    return kept


@router.api_route("/track/{token}", methods=["GET", "POST"], summary="Where devices and apps send positions (OsmAnd / Traccar Client, OwnTracks, "
                  "Traccar forwarding, JSON or GeoJSON)", include_in_schema=True)
async def receive(token: str, request: Request, conn=Depends(get_conn)):
    cur = await conn.execute("SELECT * FROM trackers WHERE token = %s AND kind = 'device'", (token,))
    t = await cur.fetchone()
    if not t:
        raise Problem(404, "No tracker has that address.", code="not_found")
    if not t["active"]:
        raise Problem(403, "This tracker is paused.", code="paused")
    raw = await request.body()
    if len(raw) > 1_000_000:
        raise Problem(413, "Send at most 1 MB at a time.", code="too_large")
    body = None
    if raw.strip():
        try:
            body = json.loads(raw)
        except ValueError:
            # OsmAnd may send its fields as a form body
            from urllib.parse import parse_qsl
            body = None
            extra = dict(parse_qsl(raw.decode("utf-8", "replace")))
            if extra:
                q = {**dict(request.query_params), **extra}
                n = await ingest(conn, t, tracking.parse(q, None, {}))
                return {"received": n}
    positions = tracking.parse(dict(request.query_params), body, {k.lower(): v for k, v in request.headers.items()})
    n = await ingest(conn, t, positions)
    if body is not None and isinstance(body, (dict, list)) and isinstance(body, dict) and body.get("_type") is not None:
        return []                                                  # OwnTracks expects a JSON list back
    return {"received": n}


# ---------- feeds PolyGIS asks ----------
def _token(request: Request) -> str | None:
    return getattr(request.state, "at", None) or request.cookies.get(identity.AT)


@router.post("/trackers/{tracker_id}/refresh", summary="Ask the tracker's feed for the latest positions (ADS-B, AIS, Geohub)")
async def refresh(tracker_id: UUID, request: Request, user=Depends(need())):
    """No database connection is held while the feed is asked (AIS listens for some seconds, and the pool is small):
    one short connection reads the tracker, another keeps what came back."""
    async with pool.connection() as conn:
        t = await tracker_of(conn, tracker_id, user)
        if t["kind"] == "device":
            return {"received": 0, "note": "Devices send their positions themselves."}
        if not t["active"]:
            return {"received": 0, "note": "Paused."}
        now = datetime.now(timezone.utc)
        if t["refreshed_at"] and (now - t["refreshed_at"]).total_seconds() < tracking.MIN_REFRESH[t["kind"]]:
            return {"received": 0, "note": "Asked a moment ago."}
        # claimed for this ask, so that other open pages do not ask the feed at the same time
        cur = await conn.execute("UPDATE trackers SET refreshed_at = now() WHERE id = %s AND (refreshed_at IS NULL OR refreshed_at < now() - make_interval(secs => %s)) "
                                 "RETURNING id", (tracker_id, tracking.MIN_REFRESH[t["kind"]]))
        if not await cur.fetchone():
            return {"received": 0, "note": "Asked a moment ago."}
    cfg, sec = t["config"] or {}, t["secret"] or {}
    after = None
    try:
        if t["kind"] == "adsb":
            positions = await tracking.adsb(cfg, sec)
        elif t["kind"] == "ais":
            positions = await tracking.ais(cfg, sec)
        else:
            token = _token(request)
            if not token:
                raise Problem(401, "Sign in again to read Geohub.", code="unauthenticated")
            feats, after = [], int(cfg.get("after") or 0)
            g = identity.client()
            for _ in range(10):
                try:
                    page = await anyio.to_thread.run_sync(lambda: g.data(token, f"forms/{cfg['form_id']}/features", {"limit": 500, "after": after}))
                except GeoEstateIDError as e:
                    raise Problem(404 if e.status == 404 else 502, f"Geohub: {e}", code=e.code or "geohub") from None
                got = page.get("features") or []
                feats += got
                if got:
                    after = max(after, max(int(f.get("seq") or 0) for f in got))
                if not page.get("next"):
                    break
            positions = tracking.geohub_positions(feats)
    except (Problem, httpx.HTTPError) as e:
        why = e.detail if isinstance(e, Problem) else f"The feed could not be reached ({e.__class__.__name__}). Try again in a moment."
        async with pool.connection() as conn:
            await conn.execute("UPDATE trackers SET last_error = %s WHERE id = %s", (why, tracker_id))
        if isinstance(e, Problem):
            raise
        raise Problem(502, why, code="feed") from None
    async with pool.connection() as conn:
        if after is not None:
            await conn.execute("UPDATE trackers SET config = jsonb_set(config, '{after}', to_jsonb(%s::int)) WHERE id = %s", (after, tracker_id))
        n = await ingest(conn, t, positions)
    return {"received": n, "seen": len(positions)}


# ---------- the live picture ----------
@router.get("/projects/{project_id}/tracking/live", summary="The latest position of every tracked device, recent trails, and unseen alerts")
async def live(project_id: UUID, user=Depends(need()), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    cur = await conn.execute(
        "SELECT t.id AS tracker_id, t.name AS tracker, t.kind, t.config, d.device, d.name, p.ts, ST_X(p.geom) AS x, ST_Y(p.geom) AS y, p.speed_kmh, "
        "p.course, p.altitude, p.props, f.props AS fprops FROM trackers t JOIN track_devices d ON d.tracker_id = t.id "
        "JOIN LATERAL (SELECT * FROM track_points q WHERE q.tracker_id = t.id AND q.device = d.device ORDER BY ts DESC LIMIT 1) p ON true "
        "LEFT JOIN features f ON f.id = d.feature_id WHERE t.project_id = %s AND t.active", (project_id,))
    now = datetime.now(timezone.utc)
    pts = []
    for r in await cur.fetchall():
        fp = r["fprops"] or {}
        pts.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["x"], r["y"]]},
                    "properties": {"tracker_id": str(r["tracker_id"]), "tracker": r["tracker"], "kind": r["kind"], "device": r["device"],
                                   "name": r["name"] or r["device"], "time": r["ts"].isoformat(), "age_s": int((now - r["ts"]).total_seconds()),
                                   "speed_kmh": r["speed_kmh"], "course": r["course"], "altitude": r["altitude"],
                                   "sidc": fp.get("sidc") or tracking.DEFAULT_SIDC[r["kind"]], "designation": fp.get("designation") or r["name"] or r["device"],
                                   "mgrs": mc.write("usng", r["x"], r["y"]), **{k: v for k, v in (r["props"] or {}).items() if k not in ("name",)}}})
    cur = await conn.execute(
        "SELECT t.id AS tracker_id, t.kind, p.device, ST_AsGeoJSON(ST_MakeLine(p.geom ORDER BY p.ts))::jsonb AS g, count(*) AS n "
        "FROM trackers t JOIN track_points p ON p.tracker_id = t.id "
        "WHERE t.project_id = %s AND t.active AND p.ts > now() - make_interval(mins => coalesce((t.config->>'trail_minutes')::int, 60)) "
        "GROUP BY t.id, t.kind, p.device HAVING count(*) > 1", (project_id,))
    trails = [{"type": "Feature", "geometry": r["g"], "properties": {"tracker_id": str(r["tracker_id"]), "kind": r["kind"], "device": r["device"], "points": r["n"]}}
              for r in await cur.fetchall()]
    cur = await conn.execute("SELECT a.id, a.tracker_id, t.name AS tracker, a.device, a.name, a.kind, a.zone, a.ts, ST_X(a.geom) AS x, ST_Y(a.geom) AS y "
                             "FROM track_alerts a JOIN trackers t ON t.id = a.tracker_id WHERE a.project_id = %s AND NOT a.seen ORDER BY a.ts DESC LIMIT 100",
                             (project_id,))
    alerts = [{**r, "tracker_id": str(r["tracker_id"]), "ts": r["ts"].isoformat()} for r in await cur.fetchall()]
    return {"positions": {"type": "FeatureCollection", "features": pts}, "trails": {"type": "FeatureCollection", "features": trails},
            "alerts": alerts, "at": now.isoformat()}


@router.post("/projects/{project_id}/tracking/alerts/seen", summary="Mark the geofence alerts as seen")
async def alerts_seen(project_id: UUID, user=Depends(need()), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    await conn.execute("UPDATE track_alerts SET seen = true WHERE project_id = %s AND NOT seen", (project_id,))
    return {"ok": True}


class HistoryIn(BaseModel):
    since: datetime | None = Field(None, description="From (default: a day ago)")
    until: datetime | None = None
    name: str | None = Field(None, max_length=200)


@router.post("/trackers/{tracker_id}/history", status_code=201, summary="The tracks of a tracker's devices over a time, as a line layer")
async def history(tracker_id: UUID, body: HistoryIn, user=Depends(EDIT), conn=Depends(get_conn)):
    t = await tracker_of(conn, tracker_id, user)
    until = body.until or datetime.now(timezone.utc)
    since = body.since or until - timedelta(days=1)
    if since >= until:
        raise Problem(422, "The start must be before the end.", code="validation")
    pos = await _next_position(conn, t["project_id"])
    fields = [{"name": n, "type": ty} for n, ty in (("device", "string"), ("name", "string"), ("start", "string"), ("end", "string"),
                                                    ("points", "integer"), ("length_km", "number"), ("avg_speed_kmh", "number"))]
    async with conn.transaction():
        cur = await conn.execute(
            "INSERT INTO layers (project_id, org_id, name, geometry_type, fields, style, position, source, created_by) "
            "VALUES (%s, %s, %s, 'LineString', %s, %s, %s, %s, %s) RETURNING id",
            (t["project_id"], user["org_id"], (body.name or f"{t['name']} tracks").strip()[:200], Jsonb(fields),
             Jsonb({"type": "single", "color": "#6b3fa0", "stroke": "#6b3fa0", "width": 2.2, "size": 6, "opacity": 0.9}), pos,
             Jsonb({"tracking_history": str(tracker_id), "since": since.isoformat(), "until": until.isoformat()}), user["id"]))
        lid = (await cur.fetchone())["id"]
        cur = await conn.execute(
            "INSERT INTO features (layer_id, geom, props, updated_by) "
            "SELECT %s, ST_MakeLine(p.geom ORDER BY p.ts), jsonb_build_object('device', p.device, 'name', max(d.name), 'start', min(p.ts)::text, "
            "'end', max(p.ts)::text, 'points', count(*), 'length_km', round((ST_Length(ST_MakeLine(p.geom ORDER BY p.ts)::geography) / 1000)::numeric, 3), "
            "'avg_speed_kmh', round((ST_Length(ST_MakeLine(p.geom ORDER BY p.ts)::geography) / 1000 / nullif(extract(epoch FROM max(p.ts) - min(p.ts)) / 3600, 0))::numeric, 1)), %s "
            "FROM track_points p LEFT JOIN track_devices d ON d.tracker_id = p.tracker_id AND d.device = p.device "
            "WHERE p.tracker_id = %s AND p.ts BETWEEN %s AND %s GROUP BY p.device HAVING count(*) > 1",
            (lid, user["id"], tracker_id, since, until))
        n = cur.rowcount
    await _touch(conn, lid, t["project_id"])
    return {"layer": await layer_of(conn, lid, user), "tracks": n}
