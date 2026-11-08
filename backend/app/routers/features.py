"""Editing features: add, change and delete, each recorded so it can be undone; one feature in full."""
import json
from datetime import date
from uuid import UUID

import shapely
from fastapi import APIRouter, Depends
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import gisdata
from ..db import get_conn
from ..errors import Problem
from ..identity import need
from .layers import _geom_in, _touch, layer_of

router = APIRouter(tags=["editing"])

KIND = {"Point": ("Point", "MultiPoint"), "LineString": ("LineString", "MultiLineString"),
        "Polygon": ("Polygon", "MultiPolygon")}
WORD = {"Point": "points", "LineString": "lines", "Polygon": "polygons"}


def _props_ok(layer: dict, props: dict) -> dict:
    """Properties checked against the layer's fields, each turned into its field's type."""
    types = {f["name"]: f["type"] for f in layer["fields"]}
    out = {}
    for k, v in props.items():
        if k not in types:
            raise Problem(422, f"{layer['name']} has no field called {k}. Add the field first.", code="validation",
                          fields={"properties": [k]})
        if v is None or v == "":
            out[k] = None
            continue
        t = types[k]
        try:
            if t == "integer":
                f = float(v)
                if not f.is_integer():
                    raise ValueError
                out[k] = int(f)
            elif t == "number":
                out[k] = float(v)
            elif t == "boolean":
                if isinstance(v, bool):
                    out[k] = v
                elif str(v).strip().lower() in ("true", "yes", "1", "y"):
                    out[k] = True
                elif str(v).strip().lower() in ("false", "no", "0", "n"):
                    out[k] = False
                else:
                    raise ValueError
            elif t == "date":
                out[k] = date.fromisoformat(str(v)[:10]).isoformat()
            else:
                out[k] = str(v)[:10000]
        except (TypeError, ValueError):
            words = {"integer": "a whole number", "number": "a number", "boolean": "yes or no", "date": "a date (YYYY-MM-DD)"}[t]
            raise Problem(422, f"{k} must be {words}.", code="validation", fields={"properties": [k]}) from None
    return out


def _geom_ok(layer: dict, g: dict):
    geom = _geom_in(g)
    if geom.geom_type not in KIND[layer["geometry_type"]]:
        raise Problem(422, f"{layer['name']} holds {WORD[layer['geometry_type']]}: a {geom.geom_type} cannot go in it.",
                      code="validation", fields={"geometry": ["kind"]})
    if layer["geometry_type"] == "Polygon" and geom.area == 0:
        raise Problem(422, "A polygon needs at least three corners.", code="validation", fields={"geometry": ["empty"]})
    return geom


def _wkb(g) -> bytes:
    return shapely.to_wkb(shapely.set_srid(g, 4326), include_srid=True)


async def _snapshot(conn, layer_id, fid) -> dict | None:
    cur = await conn.execute("SELECT id, ST_AsGeoJSON(geom, 9)::jsonb AS geometry, props FROM features WHERE id = %s AND layer_id = %s",
                             (fid, layer_id))
    r = await cur.fetchone()
    return {"type": "Feature", "id": r["id"], "geometry": r["geometry"], "properties": r["props"]} if r else None


async def _log(conn, layer_id, fid, action, before, after, user) -> None:
    await conn.execute("INSERT INTO edits (layer_id, feature_id, action, before, after, actor_id, actor_name) "
                       "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                       (layer_id, fid, action, Jsonb(before) if before else None, Jsonb(after) if after else None, user["id"], user["name"]))


class FeatureIn(BaseModel):
    geometry: dict
    properties: dict = Field(default_factory=dict)


class FeaturePatch(BaseModel):
    geometry: dict | None = None
    properties: dict | None = Field(None, description="Only the fields given change")


@router.get("/layers/{layer_id}/features/{fid}", summary="One feature as GeoJSON")
async def get_feature(layer_id: UUID, fid: int, user=Depends(need()), conn=Depends(get_conn)):
    await layer_of(conn, layer_id, user)
    f = await _snapshot(conn, layer_id, fid)
    if not f:
        raise Problem(404, "Feature not found.", code="not_found")
    return f


@router.post("/layers/{layer_id}/features/geojson", summary="Several features in full, for editing (at most 2,000)")
async def get_features(layer_id: UUID, ids: list[int], user=Depends(need()), conn=Depends(get_conn)):
    await layer_of(conn, layer_id, user)
    if len(ids) > 2000:
        raise Problem(422, "Ask for at most 2,000 features at a time.", code="validation")
    cur = await conn.execute("SELECT id, ST_AsGeoJSON(geom, 9)::jsonb AS geometry, props FROM features "
                             "WHERE layer_id = %s AND id = ANY(%s) ORDER BY id", (layer_id, ids))
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "id": r["id"], "geometry": r["geometry"], "properties": r["props"]}
                                                      for r in await cur.fetchall()]}


@router.get("/layers/{layer_id}/features.geojson", summary="Features in an area, at full precision (for snapping and editing)")
async def features_in(layer_id: UUID, bbox: str, limit: int = 5000, user=Depends(need()), conn=Depends(get_conn)):
    await layer_of(conn, layer_id, user)
    try:
        w, s_, e, n = (float(x) for x in bbox.split(","))
    except ValueError:
        raise Problem(422, "bbox is west,south,east,north.", code="validation") from None
    limit = max(1, min(limit, 20000))
    cur = await conn.execute("SELECT id, ST_AsGeoJSON(geom, 9)::jsonb AS geometry, props FROM features WHERE layer_id = %s "
                             "AND geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326) ORDER BY id LIMIT %s", (layer_id, w, s_, e, n, limit + 1))
    rows = await cur.fetchall()
    return {"type": "FeatureCollection", "truncated": len(rows) > limit,
            "features": [{"type": "Feature", "id": r["id"], "geometry": r["geometry"], "properties": r["props"]} for r in rows[:limit]]}


@router.post("/layers/{layer_id}/features", status_code=201, summary="Add a feature")
async def add_feature(layer_id: UUID, body: FeatureIn, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    layer = await layer_of(conn, layer_id, user, lock=True)
    geom = _geom_ok(layer, body.geometry)
    props = _props_ok(layer, body.properties)
    cur = await conn.execute("INSERT INTO features (layer_id, geom, props, updated_by) VALUES (%s, ST_GeomFromEWKB(%s), %s, %s) RETURNING id",
                             (layer_id, _wkb(geom), Jsonb(props), user["id"]))
    fid = (await cur.fetchone())["id"]
    after = await _snapshot(conn, layer_id, fid)
    await _log(conn, layer_id, fid, "add", None, after, user)
    await _touch(conn, layer_id, layer["project_id"])
    return after


@router.patch("/layers/{layer_id}/features/{fid}", summary="Change a feature's shape or attributes")
async def change_feature(layer_id: UUID, fid: int, body: FeaturePatch, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    layer = await layer_of(conn, layer_id, user, lock=True)
    before = await _snapshot(conn, layer_id, fid)
    if not before:
        raise Problem(404, "Feature not found.", code="not_found")
    if body.geometry is not None:
        await conn.execute("UPDATE features SET geom = ST_GeomFromEWKB(%s) WHERE id = %s", (_wkb(_geom_ok(layer, body.geometry)), fid))
    if body.properties is not None:
        await conn.execute("UPDATE features SET props = props || %s WHERE id = %s", (Jsonb(_props_ok(layer, body.properties)), fid))
    await conn.execute("UPDATE features SET updated_by = %s, updated_at = now() WHERE id = %s", (user["id"], fid))
    after = await _snapshot(conn, layer_id, fid)
    if after != before:
        await _log(conn, layer_id, fid, "change", before, after, user)
        await _touch(conn, layer_id, layer["project_id"])
    return after


class ManyIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=10000)


@router.post("/layers/{layer_id}/features/delete", summary="Delete features (each can be undone)")
async def delete_features(layer_id: UUID, body: ManyIn, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    layer = await layer_of(conn, layer_id, user, lock=True)
    n = 0
    for fid in body.ids:
        before = await _snapshot(conn, layer_id, fid)
        if before:
            await conn.execute("DELETE FROM features WHERE id = %s", (fid,))
            await _log(conn, layer_id, fid, "delete", before, None, user)
            n += 1
    if n:
        await _touch(conn, layer_id, layer["project_id"])
    return {"deleted": n}


class CalcIn(BaseModel):
    field: str
    value: str | float | int | bool | None = Field(None, description="Set every chosen feature's field to this")
    from_field: str | None = Field(None, description="…or copy another field")
    ids: list[int] | None = Field(None, max_length=100000, description="Only these features (else all)")


@router.post("/layers/{layer_id}/calculate", summary="Field calculator: set a field for many features at once")
async def calculate(layer_id: UUID, body: CalcIn, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    layer = await layer_of(conn, layer_id, user, lock=True)
    names = {f["name"] for f in layer["fields"]}
    if body.field not in names or (body.from_field and body.from_field not in names):
        raise Problem(422, f"{layer['name']} has no such field.", code="validation", fields={"field": ["unknown"]})
    where, vals = "layer_id = %s", [layer_id]
    if body.ids is not None:
        where += " AND id = ANY(%s)"; vals.append(body.ids)
    if body.from_field:
        cur = await conn.execute(f"SELECT id, props->%s AS v FROM features WHERE {where}", (body.from_field, *vals))
        rows = await cur.fetchall()
        for r in rows:
            p = _props_ok(layer, {body.field: r["v"]})
            await conn.execute("UPDATE features SET props = props || %s WHERE id = %s", (Jsonb(p), r["id"]))
        n = len(rows)
    else:
        p = _props_ok(layer, {body.field: body.value})
        cur = await conn.execute(f"UPDATE features SET props = props || %s, updated_by = %s, updated_at = now() WHERE {where}",
                                 (Jsonb(p), user["id"], *vals))
        n = cur.rowcount
    await _touch(conn, layer_id, layer["project_id"])
    from ..audit import audit
    await audit(conn, user, "field.calculate", "layer", layer_id, field=body.field, features=n)
    return {"changed": n}


@router.post("/layers/{layer_id}/undo", summary="Undo your latest edit on this layer")
async def undo(layer_id: UUID, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    layer = await layer_of(conn, layer_id, user, lock=True)
    cur = await conn.execute("SELECT * FROM edits WHERE layer_id = %s AND actor_id = %s AND undone_at IS NULL ORDER BY id DESC LIMIT 1",
                             (layer_id, user["id"]))
    e = await cur.fetchone()
    if not e:
        raise Problem(409, "There is nothing of yours to undo on this layer.", code="nothing_to_undo")
    fid, before = e["feature_id"], e["before"]
    if e["action"] == "add":
        await conn.execute("DELETE FROM features WHERE id = %s AND layer_id = %s", (fid, layer_id))
    else:
        geom = _geom_in(before["geometry"])
        if e["action"] == "delete":
            await conn.execute("INSERT INTO features (id, layer_id, geom, props, updated_by) VALUES (%s, %s, ST_GeomFromEWKB(%s), %s, %s) "
                               "ON CONFLICT (id) DO NOTHING", (fid, layer_id, _wkb(geom), Jsonb(before["properties"]), user["id"]))
        else:
            await conn.execute("UPDATE features SET geom = ST_GeomFromEWKB(%s), props = %s, updated_by = %s, updated_at = now() "
                               "WHERE id = %s AND layer_id = %s", (_wkb(geom), Jsonb(before["properties"]), user["id"], fid, layer_id))
    await conn.execute("UPDATE edits SET undone_at = now() WHERE id = %s", (e["id"],))
    await _touch(conn, layer_id, layer["project_id"])
    return {"undone": e["action"], "feature_id": fid, "feature": await _snapshot(conn, layer_id, fid)}


@router.get("/layers/{layer_id}/edits", summary="Who changed what on this layer, latest first")
async def edits(layer_id: UUID, user=Depends(need()), conn=Depends(get_conn)):
    await layer_of(conn, layer_id, user)
    cur = await conn.execute("SELECT id, feature_id, action, actor_name, at, undone_at FROM edits WHERE layer_id = %s ORDER BY id DESC LIMIT 200",
                             (layer_id,))
    return await cur.fetchall()


__all__ = ["router", "gisdata", "json"]
