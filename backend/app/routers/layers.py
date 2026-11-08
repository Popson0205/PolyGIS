"""Layers: importing files, empty layers to draw in, styling and labels, fields, the attribute table, selection,
vector tiles for the map, and export."""
import re
from typing import Literal
from uuid import UUID

import anyio
import shapely
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import gisdata
from ..audit import audit
from ..config import settings
from ..db import get_conn
from ..errors import Problem
from ..identity import need
from .projects import project_of

router = APIRouter(tags=["layers"])

PALETTE = ["#2f6db5", "#e07b1f", "#2f8a3b", "#c0392b", "#7d4fb0", "#1f9aa0", "#b58a1b", "#d14f8f", "#55606e", "#6b8e23"]
FIELD_NAME = re.compile(r"^[^\x00-\x1f\"\\]{1,64}$")

LAYER_SQL = ("SELECT l.*, s.feature_count, CASE WHEN s.e IS NOT NULL THEN "
             "  ARRAY[ST_XMin(s.e), ST_YMin(s.e), ST_XMax(s.e), ST_YMax(s.e)] END AS bbox FROM layers l "
             "LEFT JOIN LATERAL (SELECT count(*) AS feature_count, ST_Extent(f.geom) AS e FROM features f "
             "WHERE f.layer_id = l.id) s ON true ")


def default_style(kind: str, n: int) -> dict:
    color = PALETTE[n % len(PALETTE)]
    return {"type": "single", "color": color, "stroke": "#ffffff" if kind == "Point" else color, "width": 2 if kind == "LineString" else 1,
            "size": 6, "opacity": 0.35 if kind == "Polygon" else 0.9}


async def layers_of(conn, project_id) -> list[dict]:
    cur = await conn.execute(LAYER_SQL + "WHERE l.project_id = %s ORDER BY l.position, l.created_at", (project_id,))
    return [_shape(r) for r in await cur.fetchall()]


def _shape(r: dict) -> dict:
    r["feature_count"] = r.get("feature_count") or 0
    r["version"] = int(r["updated_at"].timestamp() * 1000)
    return r


async def layer_of(conn, layer_id, user, lock: bool = False) -> dict:
    cur = await conn.execute(LAYER_SQL + "WHERE l.id = %s AND l.org_id = %s", (layer_id, user["org_id"]))
    r = await cur.fetchone()
    if not r:
        raise Problem(404, "Layer not found.", code="not_found")
    if lock:
        await conn.execute("SELECT 1 FROM layers WHERE id = %s FOR UPDATE", (layer_id,))
    return _shape(r)


async def _next_position(conn, project_id) -> int:
    cur = await conn.execute("SELECT COALESCE(max(position), -1) + 1 AS n FROM layers WHERE project_id = %s", (project_id,))
    return (await cur.fetchone())["n"]


async def _touch(conn, layer_id, project_id=None) -> None:
    await conn.execute("UPDATE layers SET updated_at = clock_timestamp() WHERE id = %s", (layer_id,))
    if project_id:
        await conn.execute("UPDATE projects SET updated_at = now() WHERE id = %s", (project_id,))


# ---------- adding layers ----------
@router.post("/projects/{project_id}/layers/import", status_code=201,
             summary="Add the layers in a file: GeoJSON, zipped Shapefile, GeoPackage, KML/KMZ, CSV, DXF, GPX")
async def import_layers(project_id: UUID, file: UploadFile = File(...), epsg: int | None = Form(None),
                        user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise Problem(413, f"The file is larger than {settings.max_upload_mb} MB.", code="too_large")
    if not data:
        raise Problem(422, "The file is empty.", code="validation")
    if epsg is not None:
        from .projects import _epsg_ok
        _epsg_ok(epsg)
    name = file.filename or "upload"
    path = gisdata.save_upload(data, name)
    try:
        read = await anyio.to_thread.run_sync(lambda: gisdata.read_file(path, name, epsg, settings.max_features))
    except gisdata.ReadError as e:
        raise Problem(422, str(e), code="unreadable") from None
    finally:
        gisdata.remove_upload(path)
    pos = await _next_position(conn, project_id)
    made = []
    for i, lay in enumerate(read):
        cur = await conn.execute(
            "INSERT INTO layers (project_id, org_id, name, geometry_type, fields, style, position, source, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (project_id, user["org_id"], lay.name[:200], lay.geometry_type, Jsonb(lay.fields),
             Jsonb(default_style(lay.geometry_type, pos + i)), pos + i,
             Jsonb({"filename": name, "crs": lay.crs, "dropped": lay.dropped}), user["id"]))
        lid = (await cur.fetchone())["id"]
        await _copy_features(conn, lid, lay.geoms, lay.props, user["id"])
        made.append(lid)
        await audit(conn, user, "layer.import", "layer", lid, name=lay.name, file=name, features=len(lay.geoms))
    await _touch(conn, made[0], project_id)
    return [await layer_of(conn, x, user) for x in made]


async def _copy_features(conn, layer_id, geoms, props, actor) -> None:
    import json
    async with conn.cursor().copy("COPY features (layer_id, geom, props, updated_by) FROM STDIN") as cp:
        for g, p in zip(geoms, props):
            g = shapely.set_srid(g, 4326)
            await cp.write_row((str(layer_id), shapely.to_wkb(g, hex=True, include_srid=True), json.dumps(p), str(actor)))


class FieldIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: Literal["string", "integer", "number", "boolean", "date"] = "string"


class NewLayerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    geometry_type: Literal["Point", "LineString", "Polygon"]
    fields: list[FieldIn] = Field(default_factory=list, max_length=200)


def _fields_ok(fields: list[dict]) -> list[dict]:
    seen = set()
    for f in fields:
        if not FIELD_NAME.match(f["name"]):
            raise Problem(422, f"“{f['name']}” cannot be a field name.", code="validation", fields={"fields": ["name"]})
        if f["name"].lower() in seen:
            raise Problem(422, f"There are two fields called {f['name']}.", code="validation", fields={"fields": ["twice"]})
        seen.add(f["name"].lower())
    return fields


@router.post("/projects/{project_id}/layers", status_code=201, summary="Add an empty layer to draw in")
async def new_layer(project_id: UUID, body: NewLayerIn, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    fields = _fields_ok([f.model_dump() for f in body.fields])
    pos = await _next_position(conn, project_id)
    cur = await conn.execute(
        "INSERT INTO layers (project_id, org_id, name, geometry_type, fields, style, position, source, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (project_id, user["org_id"], body.name.strip(), body.geometry_type, Jsonb(fields),
         Jsonb(default_style(body.geometry_type, pos)), pos, Jsonb({"drawn": True}), user["id"]))
    lid = (await cur.fetchone())["id"]
    await audit(conn, user, "layer.create", "layer", lid, name=body.name.strip())
    await _touch(conn, lid, project_id)
    return await layer_of(conn, lid, user)


# ---------- changing a layer ----------
class StyleIn(BaseModel):
    type: Literal["single", "categorized", "graduated"] = "single"
    color: str = Field("#2f6db5", pattern=r"^#[0-9a-fA-F]{6}$")
    stroke: str = Field("#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    width: float = Field(1, ge=0, le=20)
    size: float = Field(6, ge=1, le=40)
    opacity: float = Field(0.8, ge=0, le=1)
    field: str | None = None
    categories: list[dict] = Field(default_factory=list, max_length=200, description="[{value, color, label?}]")
    classes: list[dict] = Field(default_factory=list, max_length=20, description="[{min, max, color}] for graduated")
    method: Literal["quantile", "equal", "manual"] = "quantile"
    ramp: str = Field("", max_length=40)


class LabelsIn(BaseModel):
    field: str
    size: float = Field(12, ge=6, le=40)
    color: str = Field("#1a2233", pattern=r"^#[0-9a-fA-F]{6}$")
    halo: str = Field("#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")


class LayerPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    visible: bool | None = None
    style: StyleIn | None = None
    labels: LabelsIn | None = None
    clear_labels: bool = False


def _has_field(layer: dict, name: str | None) -> None:
    if name and name not in {f["name"] for f in layer["fields"]}:
        raise Problem(422, f"{layer['name']} has no field called {name}.", code="validation", fields={"field": ["unknown"]})


@router.patch("/layers/{layer_id}", summary="Rename, show or hide, style and label a layer")
async def update_layer(layer_id: UUID, body: LayerPatch, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    layer = await layer_of(conn, layer_id, user, lock=True)
    sets, vals = [], []
    if body.name is not None:
        sets.append("name = %s"); vals.append(body.name.strip())
    if body.visible is not None:
        sets.append("visible = %s"); vals.append(body.visible)
    if body.style is not None:
        st = body.style.model_dump()
        if st["type"] != "single":
            if not st["field"]:
                raise Problem(422, "Choose the field to style by.", code="validation", fields={"style.field": ["missing"]})
            _has_field(layer, st["field"])
        for c in st["categories"] + st["classes"]:
            if not re.match(r"^#[0-9a-fA-F]{6}$", str(c.get("color", ""))):
                raise Problem(422, "Every class needs a colour like #2f6db5.", code="validation", fields={"style": ["colour"]})
        sets.append("style = %s"); vals.append(Jsonb(st))
    if body.clear_labels:
        sets.append("labels = NULL")
    elif body.labels is not None:
        _has_field(layer, body.labels.field)
        sets.append("labels = %s"); vals.append(Jsonb(body.labels.model_dump()))
    if sets:
        await conn.execute(f"UPDATE layers SET {', '.join(sets)}, updated_at = clock_timestamp() WHERE id = %s", (*vals, layer_id))
    return await layer_of(conn, layer_id, user)


class OrderIn(BaseModel):
    layer_ids: list[UUID] = Field(min_length=1, description="Bottom to top")


@router.put("/projects/{project_id}/layer-order", summary="Set the drawing order, bottom to top")
async def set_order(project_id: UUID, body: OrderIn, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    have = {str(r["id"]) for r in await (await conn.execute("SELECT id FROM layers WHERE project_id = %s", (project_id,))).fetchall()}
    if {str(x) for x in body.layer_ids} != have or len(body.layer_ids) != len(have):
        raise Problem(422, "Give every layer of the project once.", code="validation", fields={"layer_ids": ["mismatch"]})
    for i, lid in enumerate(body.layer_ids):
        await conn.execute("UPDATE layers SET position = %s WHERE id = %s", (i, lid))
    return await layers_of(conn, project_id)


class FieldOp(BaseModel):
    op: Literal["add", "rename", "delete"]
    name: str = Field(min_length=1, max_length=64)
    to: str | None = Field(None, min_length=1, max_length=64)
    type: Literal["string", "integer", "number", "boolean", "date"] = "string"


@router.post("/layers/{layer_id}/fields", summary="Add, rename or delete a field")
async def change_fields(layer_id: UUID, body: FieldOp, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    layer = await layer_of(conn, layer_id, user, lock=True)
    fields = list(layer["fields"])
    names = [f["name"] for f in fields]
    if body.op == "add":
        fields = _fields_ok(fields + [{"name": body.name.strip(), "type": body.type}])
    else:
        if body.name not in names:
            raise Problem(404, f"{layer['name']} has no field called {body.name}.", code="not_found")
        if body.op == "rename":
            if not body.to:
                raise Problem(422, "Give the new name.", code="validation", fields={"to": ["missing"]})
            fields = _fields_ok([{**f, "name": body.to.strip()} if f["name"] == body.name else f for f in fields])
            await conn.execute("UPDATE features SET props = (props - %s) || jsonb_build_object(%s::text, props->%s) "
                               "WHERE layer_id = %s AND props ? %s", (body.name, body.to.strip(), body.name, layer_id, body.name))
        else:
            fields = [f for f in fields if f["name"] != body.name]
            await conn.execute("UPDATE features SET props = props - %s WHERE layer_id = %s", (body.name, layer_id))
        # styles and labels on a field that changed follow it, or fall back
        st, lb = dict(layer["style"]), layer["labels"]
        if st.get("field") == body.name:
            st = {**st, "field": body.to.strip()} if body.op == "rename" else {**st, "type": "single", "field": None}
        if lb and lb.get("field") == body.name:
            lb = {**lb, "field": body.to.strip()} if body.op == "rename" else None
        await conn.execute("UPDATE layers SET style = %s, labels = %s WHERE id = %s", (Jsonb(st), Jsonb(lb) if lb else None, layer_id))
    await conn.execute("UPDATE layers SET fields = %s, updated_at = clock_timestamp() WHERE id = %s", (Jsonb(fields), layer_id))
    await audit(conn, user, f"field.{body.op}", "layer", layer_id, field=body.name, to=body.to)
    return await layer_of(conn, layer_id, user)


@router.delete("/layers/{layer_id}", status_code=204, summary="Remove a layer and its features")
async def delete_layer(layer_id: UUID, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    layer = await layer_of(conn, layer_id, user)
    await conn.execute("DELETE FROM layers WHERE id = %s", (layer_id,))
    await audit(conn, user, "layer.delete", "layer", layer_id, name=layer["name"], features=layer["feature_count"])
    return Response(status_code=204)


# ---------- reading features: tiles, the table, statistics ----------
@router.get("/layers/{layer_id}/tiles/{z}/{x}/{y}.pbf", summary="The layer as a Mapbox vector tile",
            response_class=Response, responses={200: {"content": {"application/vnd.mapbox-vector-tile": {}}}})
async def tile(layer_id: UUID, z: int, x: int, y: int, v: str | None = None, user=Depends(need()), conn=Depends(get_conn)):
    if not (0 <= z <= 24 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise Problem(404, "No such tile.", code="not_found")
    await layer_of(conn, layer_id, user)
    cur = await conn.execute(
        "WITH b AS (SELECT ST_TileEnvelope(%s, %s, %s) AS env) "
        "SELECT ST_AsMVT(t, 'features', 4096, 'geom', 'id') AS tile FROM ("
        "  SELECT f.id, ST_AsMVTGeom(ST_Transform(f.geom, 3857), b.env, 4096, 64, true) AS geom, f.props "
        "  FROM features f, b WHERE f.layer_id = %s AND f.geom && ST_Transform(b.env, 4326)) t WHERE t.geom IS NOT NULL",
        (z, x, y, layer_id))
    data = (await cur.fetchone())["tile"] or b""
    return Response(bytes(data), media_type="application/vnd.mapbox-vector-tile",
                    headers={"Cache-Control": "private, max-age=86400" if v else "no-cache"})


class Filter(BaseModel):
    field: str
    op: Literal["=", "!=", "<", "<=", ">", ">=", "contains", "starts", "empty", "not_empty"]
    value: str | float | int | bool | None = None


NUMERIC = "CASE WHEN jsonb_typeof(props->%s) = 'number' THEN (props->>%s)::numeric " \
          "WHEN props->>%s ~ '^\\s*-?[0-9]+(\\.[0-9]+)?([eE][-+]?[0-9]+)?\\s*$' THEN (props->>%s)::numeric END"


def where_of(layer: dict, filters: list[Filter]) -> tuple[str, list]:
    """SQL for the filters, all of which must hold; field names and values are parameters, never text in the SQL."""
    sql, vals = [], []
    types = {f["name"]: f["type"] for f in layer["fields"]}
    for f in filters:
        if f.field not in types:
            raise Problem(422, f"{layer['name']} has no field called {f.field}.", code="validation", fields={"filters": ["field"]})
        if f.op == "empty":
            sql.append("(props->>%s IS NULL OR props->>%s = '')"); vals += [f.field, f.field]
        elif f.op == "not_empty":
            sql.append("(props->>%s IS NOT NULL AND props->>%s <> '')"); vals += [f.field, f.field]
        elif f.op == "contains":
            sql.append("props->>%s ILIKE %s"); vals += [f.field, f"%{_like(str(f.value))}%"]
        elif f.op == "starts":
            sql.append("props->>%s ILIKE %s"); vals += [f.field, f"{_like(str(f.value))}%"]
        elif types[f.field] in ("integer", "number") and f.op in ("<", "<=", ">", ">=", "=", "!="):
            try:
                num = float(f.value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise Problem(422, f"{f.field} is a number: compare it with a number.", code="validation") from None
            sql.append(f"({NUMERIC}) {f.op} %s"); vals += [f.field] * 4 + [num]
        elif f.op in ("=", "!="):
            sql.append(f"COALESCE(props->>%s, '') {'=' if f.op == '=' else '<>'} %s"); vals += [f.field, "" if f.value is None else str(f.value).lower() if isinstance(f.value, bool) else str(f.value)]
        else:
            sql.append(f"props->>%s {f.op} %s"); vals += [f.field, str(f.value)]
    return (" AND ".join(sql) or "true"), vals


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class RowsIn(BaseModel):
    filters: list[Filter] = Field(default_factory=list, max_length=20)
    sort: str | None = None
    desc: bool = False
    offset: int = Field(0, ge=0)
    limit: int = Field(100, ge=1, le=1000)
    ids: list[int] | None = Field(None, max_length=10000, description="Only these features (the selection)")


@router.post("/layers/{layer_id}/rows", summary="The attribute table: a page of features, filtered and sorted")
async def rows(layer_id: UUID, body: RowsIn, user=Depends(need()), conn=Depends(get_conn)):
    layer = await layer_of(conn, layer_id, user)
    where, vals = where_of(layer, body.filters)
    if body.ids is not None:
        where += " AND id = ANY(%s)"; vals.append(body.ids)
    order = "id"
    if body.sort:
        _has_field(layer, body.sort)
        order = "props->%s " + ("DESC NULLS LAST" if body.desc else "ASC NULLS FIRST") + ", id"
        sort_vals = [body.sort]
    else:
        sort_vals = []
        order += " DESC" if body.desc else ""
    cur = await conn.execute(f"SELECT count(*) AS n FROM features WHERE layer_id = %s AND {where}", (layer_id, *vals))
    total = (await cur.fetchone())["n"]
    cur = await conn.execute(f"SELECT id, props FROM features WHERE layer_id = %s AND {where} ORDER BY {order} OFFSET %s LIMIT %s",
                             (layer_id, *vals, *sort_vals, body.offset, body.limit))
    return {"total": total, "rows": await cur.fetchall()}


@router.get("/layers/{layer_id}/stats", summary="A field's numbers (for graduated styles) or its values (for categories)")
async def stats(layer_id: UUID, field: str, classes: int = Query(5, ge=2, le=12), user=Depends(need()), conn=Depends(get_conn)):
    layer = await layer_of(conn, layer_id, user)
    _has_field(layer, field)
    kind = next(f["type"] for f in layer["fields"] if f["name"] == field)
    out = {"field": field, "type": kind}
    if kind in ("integer", "number"):
        n = f"({NUMERIC})"
        cur = await conn.execute(
            f"SELECT min({n}) AS min, max({n}) AS max, count({n}) AS count, avg({n}) AS mean, "
            f"percentile_cont(%s::float8[]) WITHIN GROUP (ORDER BY {n}) AS quantiles FROM features WHERE layer_id = %s",
            (*[field] * 16, [i / classes for i in range(classes + 1)], *[field] * 4, layer_id))
        r = await cur.fetchone()
        out |= {k: (float(r[k]) if r[k] is not None else None) for k in ("min", "max", "mean")} | {"count": r["count"],
                "quantiles": [float(x) for x in r["quantiles"]] if r["quantiles"] and r["quantiles"][0] is not None else []}
    cur = await conn.execute("SELECT props->>%s AS value, count(*) AS count FROM features WHERE layer_id = %s "
                             "GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 100", (field, layer_id))
    out["values"] = await cur.fetchall()
    cur = await conn.execute("SELECT count(DISTINCT props->>%s) AS n FROM features WHERE layer_id = %s", (field, layer_id))
    out["distinct"] = (await cur.fetchone())["n"]
    return out


# ---------- selecting ----------
class SelectIn(BaseModel):
    filters: list[Filter] = Field(default_factory=list, max_length=20)
    geometry: dict | None = Field(None, description="GeoJSON geometry in WGS 84 to select by location")
    relation: Literal["intersects", "within", "contains", "distance"] = "intersects"
    distance_m: float = Field(0, ge=0, le=1_000_000)
    other_layer_id: UUID | None = Field(None, description="Select by location against this layer's features")
    other_ids: list[int] | None = Field(None, max_length=10000, description="…only these of them (its selection)")


@router.post("/layers/{layer_id}/select", summary="Select by attributes, by location, or both")
async def select(layer_id: UUID, body: SelectIn, user=Depends(need()), conn=Depends(get_conn)):
    layer = await layer_of(conn, layer_id, user)
    where, vals = where_of(layer, body.filters)
    spatial, svals = "", []
    rel = {"intersects": "ST_Intersects(f.geom, g.g)", "within": "ST_Within(f.geom, g.g)", "contains": "ST_Contains(f.geom, g.g)",
           "distance": "ST_DWithin(f.geom::geography, g.g::geography, %s)"}[body.relation]
    if body.geometry is not None:
        g = _geom_in(body.geometry)
        spatial = f" AND EXISTS (SELECT 1 FROM (SELECT ST_GeomFromEWKB(%s) AS g) g WHERE {rel})"
        svals = [shapely.to_wkb(shapely.set_srid(g, 4326), include_srid=True)] + ([body.distance_m] if body.relation == "distance" else [])
    elif body.other_layer_id is not None:
        other = await layer_of(conn, body.other_layer_id, user)
        spatial = (f" AND EXISTS (SELECT 1 FROM (SELECT geom AS g FROM features o WHERE o.layer_id = %s"
                   f"{' AND o.id = ANY(%s)' if body.other_ids is not None else ''}) g WHERE {rel})")
        svals = [other["id"]] + ([body.other_ids] if body.other_ids is not None else []) + ([body.distance_m] if body.relation == "distance" else [])
    cur = await conn.execute(
        f"SELECT array_agg(f.id ORDER BY f.id) AS ids, ST_Extent(f.geom) AS ext FROM features f "
        f"WHERE f.layer_id = %s AND {where.replace('props', 'f.props')}{spatial}", (layer_id, *vals, *svals))
    r = await cur.fetchone()
    ids = r["ids"] or []
    bbox = None
    if r["ext"]:
        cur = await conn.execute("SELECT ST_XMin(%s::box2d) AS a, ST_YMin(%s::box2d) AS b, ST_XMax(%s::box2d) AS c, ST_YMax(%s::box2d) AS d",
                                 (r["ext"],) * 4)
        e = await cur.fetchone()
        bbox = [e["a"], e["b"], e["c"], e["d"]]
    return {"ids": ids, "count": len(ids), "bbox": bbox}


def _geom_in(g: dict):
    try:
        geom = shapely.geometry.shape(g)
    except Exception:  # noqa: BLE001
        raise Problem(422, "That is not a GeoJSON geometry.", code="validation", fields={"geometry": ["invalid"]}) from None
    geom = gisdata.clean(shapely.force_2d(geom))
    if geom is None:
        raise Problem(422, "The geometry is empty.", code="validation", fields={"geometry": ["empty"]})
    return geom


# ---------- export ----------
@router.get("/layers/{layer_id}/export", summary="Download the layer (or its selection)", response_class=Response)
async def export(layer_id: UUID, format: Literal["geojson", "gpkg", "shp", "csv", "kml"] = "geojson", epsg: int = 4326,
                 ids: str | None = Query(None, description="Comma-separated feature ids: only these"),
                 user=Depends(need()), conn=Depends(get_conn)):
    layer = await layer_of(conn, layer_id, user)
    if epsg != 4326:
        from .projects import _epsg_ok
        _epsg_ok(epsg)
    q, vals = "SELECT id, ST_AsBinary(geom) AS g, props FROM features WHERE layer_id = %s", [layer_id]
    if ids:
        try:
            want = [int(x) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise Problem(422, "ids must be numbers.", code="validation") from None
        q += " AND id = ANY(%s)"; vals.append(want)
    cur = await conn.execute(q + " ORDER BY id", vals)
    found = [(r["id"], shapely.from_wkb(bytes(r["g"])), r["props"]) for r in await cur.fetchall()]
    if not found:
        raise Problem(422, "There is nothing to export: the layer (or the selection) has no features.", code="empty")
    data, filename, media = await anyio.to_thread.run_sync(
        lambda: gisdata.write_file(format, layer["name"], layer["fields"], found, epsg))
    await audit(conn, user, "layer.export", "layer", layer_id, format=format, features=len(found))
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
