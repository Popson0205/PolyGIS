"""Raster layers: adding them, their map tiles, values at a point, their style, download, and the raster tools."""
import math
import shutil
import tempfile
import time
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Literal
from uuid import UUID

import anyio
import psycopg
import shapely
from fastapi import APIRouter, Depends, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import raster_tools, rasters
from ..audit import audit
from ..config import settings
from ..db import get_conn, pool
from ..errors import Problem
from ..identity import need
from .layers import _copy_features, _next_position, _touch, default_style, layer_of
from .projects import project_of

router = APIRouter(tags=["rasters"])
SIDECARS = (".tfw", ".tifw", ".aux.xml", ".prj", ".hdr", ".ovr", ".rrd", ".j2w", ".wld", ".xml", ".ige", ".rde")


def is_raster(name: str) -> bool:
    n = name.lower()
    return n.endswith(rasters.EXTENSIONS) or n.endswith((".hgt.zip", ".tif.zip", ".tiff.zip"))


def _unzip_raster(path: Path) -> Path:
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if not n.endswith("/") and "__MACOSX" not in n]
        main = [n for n in names if n.lower().endswith(rasters.EXTENSIONS)]
        if len(main) != 1:
            raise rasters.RasterError("The zip must hold one raster (with its .tfw, .aux.xml or .prj if it has them).")
        for n in names:
            if n == main[0] or n.lower().endswith(SIDECARS):
                target = path.parent / Path(n).name
                with z.open(n) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        return path.parent / Path(main[0]).name


async def import_raster(conn, project_id, file, epsg, user) -> dict:
    d = Path(tempfile.mkdtemp(prefix="polygis-r-"))
    try:
        path = d / (Path(file.filename or "raster.tif").name)
        size, most = 0, settings.max_raster_mb * 1024 * 1024
        with open(path, "wb") as f:
            while chunk := await file.read(4 * 1024 * 1024):
                size += len(chunk)
                if size > most:
                    raise Problem(413, f"The raster is larger than {settings.max_raster_mb} MB.", code="too_large")
                f.write(chunk)
        if not size:
            raise Problem(422, "The file is empty.", code="validation")
        try:
            if path.suffix.lower() == ".zip":
                path = _unzip_raster(path)
            meta = await anyio.to_thread.run_sync(lambda: rasters.ingest(path, epsg))
        except (rasters.RasterError, zipfile.BadZipFile) as e:
            raise Problem(422, str(e) if isinstance(e, rasters.RasterError) else "The zip file is damaged.", code="unreadable") from None
    finally:
        shutil.rmtree(d, ignore_errors=True)
    name = Path(file.filename or "raster").name
    for ext in (".zip", *rasters.EXTENSIONS):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
    lid = await _insert(conn, project_id, user, name or "Raster", meta, rasters.default_style(meta), {"filename": file.filename, "crs": meta["crs_name"]})
    await audit(conn, user, "layer.import", "layer", lid, name=name, file=file.filename, raster=f"{meta['width']}x{meta['height']}x{meta['count']}")
    await _touch(conn, lid, project_id)
    return await layer_of(conn, lid, user)


async def _insert(conn, project_id, user, name, meta, style, source) -> UUID:
    pos = await _next_position(conn, project_id)
    cur = await conn.execute(
        "INSERT INTO layers (project_id, org_id, name, geometry_type, fields, style, position, source, raster, created_by) "
        "VALUES (%s, %s, %s, 'Raster', '[]', %s, %s, %s, %s, %s) RETURNING id",
        (project_id, user["org_id"], name[:200], Jsonb(style), pos, Jsonb(source), Jsonb(meta), user["id"]))
    return (await cur.fetchone())["id"]


async def raster_of(conn, layer_id, user) -> dict:
    lay = await layer_of(conn, layer_id, user)
    if lay["geometry_type"] != "Raster" or not lay.get("raster"):
        raise Problem(422, f"{lay['name']} is not a raster.", code="validation")
    return lay


# ---------- map tiles: the layer is looked up once per version, and no database connection is held while drawing ----------
_cache: OrderedDict = OrderedDict()
EMPTY = None


def _empty_png() -> bytes:
    global EMPTY
    if EMPTY is None:
        import io

        from PIL import Image
        b = io.BytesIO()
        Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(b, "PNG")
        EMPTY = b.getvalue()
    return EMPTY


async def _cached(layer_id: UUID, v: str | None, user) -> dict:
    key = (str(layer_id), v)
    hit = _cache.get(key)
    if hit and v and hit["org_id"] == user["org_id"]:
        _cache.move_to_end(key)
        return hit
    async with pool.connection() as conn:
        lay = await raster_of(conn, layer_id, user)
    _cache[key] = {"org_id": lay["org_id"], "raster": lay["raster"], "style": lay["style"]}
    while len(_cache) > 256:
        _cache.popitem(last=False)
    return _cache[key]


@router.get("/layers/{layer_id}/raster/{z}/{x}/{y}.png", summary="The raster drawn as a map tile", response_class=Response,
            responses={200: {"content": {"image/png": {}}}})
async def raster_tile(layer_id: UUID, z: int, x: int, y: int, v: str | None = None, user=Depends(need())):
    if not (0 <= z <= 24 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise Problem(404, "No such tile.", code="not_found")
    lay = await _cached(layer_id, v, user)
    try:
        png = await anyio.to_thread.run_sync(lambda: rasters.render(lay["raster"], lay["style"], z, x, y))
    except rasters.RasterError as e:
        raise Problem(404, str(e), code="not_found") from None
    return Response(png or _empty_png(), media_type="image/png", headers={"Cache-Control": "private, max-age=86400" if v else "no-cache"})


@router.get("/layers/{layer_id}/raster/terrain/{z}/{x}/{y}.png", summary="The raster's heights as a 3D terrain tile (Terrarium)",
            response_class=Response, responses={200: {"content": {"image/png": {}}}})
async def terrain_tile(layer_id: UUID, z: int, x: int, y: int, band: int = Query(1, ge=1, le=64), v: str | None = None, user=Depends(need())):
    if not (0 <= z <= 24 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise Problem(404, "No such tile.", code="not_found")
    lay = await _cached(layer_id, v, user)
    try:
        png = await anyio.to_thread.run_sync(lambda: rasters.terrain(lay["raster"], z, x, y, band))
    except rasters.RasterError as e:
        raise Problem(404, str(e), code="not_found") from None
    if png is None:
        raise Problem(404, "The elevation model does not reach this tile.", code="not_found")
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=86400" if v else "no-cache"})


@router.get("/layers/{layer_id}/raster/value", summary="The raster's values (every band) at a point")
async def raster_value(layer_id: UUID, lng: float = Query(ge=-180, le=180), lat: float = Query(ge=-90, le=90), user=Depends(need())):
    lay = await _cached(layer_id, None, user)
    vals = await anyio.to_thread.run_sync(lambda: rasters.values_at(lay["raster"], lng, lat))
    return {"values": vals, "bands": [b.get("name") for b in lay["raster"]["bands"]]}


@router.get("/layers/{layer_id}/raster/download", summary="Download the raster (a Cloud-Optimized GeoTIFF)", response_class=FileResponse)
async def raster_download(layer_id: UUID, user=Depends(need(feature="export")), conn=Depends(get_conn)):
    lay = await raster_of(conn, layer_id, user)
    path = await anyio.to_thread.run_sync(lambda: rasters.local(lay["raster"]["key"]))
    safe = "".join(c if c.isalnum() or c in " -_." else "_" for c in lay["name"]).strip() or "raster"
    await audit(conn, user, "layer.export", "layer", layer_id, name=lay["name"], format="tif")
    return FileResponse(path, media_type="image/tiff", filename=f"{safe}.tif")


class Category(BaseModel):
    value: float
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    label: str | None = Field(None, max_length=200)
    hidden: bool = False


class RasterStyleIn(BaseModel):
    type: Literal["raster"] = "raster"
    mode: Literal["ramp", "rgb", "classes"] = "ramp"
    band: int = Field(1, ge=1, le=64)
    bands: list[int] = Field(default_factory=lambda: [1, 2, 3], min_length=3, max_length=3)  # red, green, blue in "rgb"
    min: float | None = None
    max: float | None = None
    mins: list[float] = Field(default_factory=lambda: [0, 0, 0], min_length=3, max_length=3)
    maxs: list[float] = Field(default_factory=lambda: [255, 255, 255], min_length=3, max_length=3)
    ramp: str = Field("Terrain", max_length=40)
    invert: bool = False
    categories: list[Category] = Field(default_factory=list, max_length=256)
    opacity: float = Field(1, ge=0, le=1)


@router.put("/layers/{layer_id}/raster-style", summary="How the raster is coloured")
async def raster_style(layer_id: UUID, body: RasterStyleIn, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    lay = await raster_of(conn, layer_id, user)
    n = lay["raster"]["count"]
    if (body.mode == "rgb" and any(not 1 <= b <= n for b in body.bands)) or (body.mode != "rgb" and body.band > n):
        raise Problem(422, f"{lay['name']} has {n} band{'s' if n > 1 else ''}.", code="validation", fields={"band": ["range"]})
    await conn.execute("UPDATE layers SET style = %s WHERE id = %s", (Jsonb(body.model_dump()), layer_id))
    await _touch(conn, layer_id, lay["project_id"])
    return await layer_of(conn, layer_id, user)


@router.get("/layers/{layer_id}/raster/histogram", summary="A band's histogram (for stretching)")
async def histogram(layer_id: UUID, band: int = Query(1, ge=1, le=64), bins: int = Query(64, ge=4, le=256), user=Depends(need())):
    lay = await _cached(layer_id, None, user)
    meta = lay["raster"]

    def work():
        import numpy as np
        import rasterio
        with rasterio.open(rasters.local(meta["key"])) as src:
            a = rasters._sample(src, min(band, src.count)).compressed().astype(float)
        a = a[np.isfinite(a)]
        if not a.size:
            return {"counts": [], "edges": []}
        c, e = np.histogram(a, bins=bins)
        return {"counts": c.tolist(), "edges": e.tolist()}
    return await anyio.to_thread.run_sync(work)


# ---------- raster tools ----------
class RunIn(BaseModel):
    params: dict = Field(default_factory=dict)
    output_name: str | None = Field(None, max_length=200)


@router.get("/raster-tools", summary="The raster tools and their parameters")
async def list_tools(user=Depends(need())):
    return raster_tools.catalog()


async def _features(conn, lay: dict, ids) -> list[dict]:
    cur = await conn.execute("SELECT id, ST_AsBinary(geom) AS wkb, props FROM features WHERE layer_id = %s "
                             "AND (%s::bigint[] IS NULL OR id = ANY(%s::bigint[])) ORDER BY id", (lay["id"], ids, ids))
    return [{"id": r["id"], "geom": shapely.from_wkb(bytes(r["wkb"])), "props": r["props"]} for r in await cur.fetchall()]


@router.post("/projects/{project_id}/raster-tools/{tool}", status_code=201, summary="Run a raster tool; its result is a new layer")
async def run(project_id: UUID, tool: str, body: RunIn, user=Depends(need("editor", write=True, feature="raster")), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    spec = raster_tools.RTOOLS.get(tool)
    if not spec:
        raise Problem(404, f"No tool called {tool}.", code="not_found")
    params = dict(body.params)
    for p in spec["params"]:
        if "default" in p and params.get(p["name"]) in (None, ""):
            params[p["name"]] = p["default"]
    layers: dict = {}
    feats: dict = {}
    for p in spec["params"]:
        v = params.get(p["name"])
        if p["type"] == "layer":
            if not v:
                if p.get("optional"):
                    continue
                raise Problem(422, f"Choose the {p['label'].lower()}.", code="validation", fields={"params": [p["name"]]})
            lay = await layer_of(conn, v, user)
            if str(lay["project_id"]) != str(project_id):
                raise Problem(422, f"{lay['name']} is in another project.", code="validation", fields={"params": [p["name"]]})
            want = p.get("geometry")
            if want == ["Raster"] and lay["geometry_type"] != "Raster":
                raise Problem(422, f"{p['label']} must be a raster; {lay['name']} is a vector layer.", code="validation", fields={"params": [p["name"]]})
            if want != ["Raster"] and lay["geometry_type"] == "Raster":
                raise Problem(422, f"{p['label']} must be a vector layer; {lay['name']} is a raster.", code="validation", fields={"params": [p["name"]]})
            if want and want != ["Raster"] and lay["geometry_type"] not in want:
                words = {"Point": "points", "LineString": "lines", "Polygon": "polygons"}
                raise Problem(422, f"{p['label']} must be {' or '.join(words[k] for k in want)}; {lay['name']} holds {words[lay['geometry_type']]}.",
                              code="validation", fields={"params": [p["name"]]})
            layers[p["name"]] = lay
            if lay["geometry_type"] != "Raster":
                ids = params.get(f"{p['name']}_ids")
                feats[p["name"]] = await _features(conn, lay, [int(x) for x in ids] if ids else None)
        elif p["type"] == "layers":
            got = [await layer_of(conn, x, user) for x in (v or [])]
            if len(got) < p.get("min", 1):
                raise Problem(422, f"Choose at least {p.get('min', 1)} rasters.", code="validation", fields={"params": [p["name"]]})
            if any(x["geometry_type"] != "Raster" for x in got):
                raise Problem(422, "Choose rasters only.", code="validation", fields={"params": [p["name"]]})
            layers[p["name"]] = got
        elif p["type"] == "field" and v:
            of = layers.get(p.get("of", "input"))
            if of and v not in {f["name"] for f in of["fields"]}:
                raise Problem(422, f"{of['name']} has no field called {v}.", code="validation", fields={"params": [p["name"]]})
    started = time.monotonic()
    inp = raster_tools.Inputs(layers, feats)

    def work():
        out = raster_tools.RBUILD[tool](params, inp)
        meta = None
        if out.kind == "raster":
            meta = rasters.save(out.data, out.crs, out.transform, dtype=out.dtype, names=out.names, categorical=out.categorical)
        return out, meta
    try:
        out, meta = await anyio.to_thread.run_sync(work)
    except (Problem, rasters.RasterError, MemoryError, ValueError) as e:
        ms = int((time.monotonic() - started) * 1000)
        why = e.detail if isinstance(e, Problem) else "it needs more memory than the server has: use a smaller area or larger cells." if isinstance(e, MemoryError) \
            else str(e)[:300]
        await conn.execute("INSERT INTO runs (project_id, org_id, tool, params, output_name, status, message, ms, actor_id, actor_name) "
                           "VALUES (%s, %s, %s, %s, %s, 'failed', %s, %s, %s, %s)",
                           (project_id, user["org_id"], tool, Jsonb(params), body.output_name or spec["name"], why, ms, user["id"], user["name"]))
        p = e if isinstance(e, Problem) else Problem(422, f"{spec['name']} did not finish: {why}", code="tool_failed")
        return JSONResponse(jsonable_encoder(p.body()), status_code=p.status, media_type="application/problem+json")
    name = ((body.output_name or "").strip() or out.name)[:200]
    source = {"tool": tool, "tool_name": spec["name"], **({"summary": _plain(out.summary)} if out.summary else {})}
    n = None
    try:
        async with conn.transaction():
            if meta:
                style = out.style or rasters.default_style(meta)
                lid = await _insert(conn, project_id, user, name, meta, style, {**source, "crs": meta["crs_name"]})
            else:
                if len(out.rows) > settings.max_features:
                    raise Problem(422, f"The result has {len(out.rows):,} features, more than the {settings.max_features:,} a layer may hold.", code="too_many")
                pos = await _next_position(conn, project_id)
                cur = await conn.execute(
                    "INSERT INTO layers (project_id, org_id, name, geometry_type, fields, style, position, source, created_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (project_id, user["org_id"], name, out.geometry_type, Jsonb(out.fields), Jsonb(default_style(out.geometry_type, pos)), pos,
                     Jsonb(source), user["id"]))
                lid = (await cur.fetchone())["id"]
                good = [(g, _plain(pp)) for g, pp in out.rows if g is not None and not g.is_empty]
                await _copy_features(conn, lid, [g for g, _ in good], [pp for _, pp in good], user["id"])
                n = len(good)
    except (Problem, psycopg.Error):
        if meta:
            rasters.remove(meta["key"])
        raise
    ms = int((time.monotonic() - started) * 1000)
    cur = await conn.execute(
        "INSERT INTO runs (project_id, org_id, tool, params, output_layer_id, output_name, status, feature_count, ms, actor_id, actor_name) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'done', %s, %s, %s, %s) RETURNING *",
        (project_id, user["org_id"], tool, Jsonb(params), lid, name, n, ms, user["id"], user["name"]))
    r = await cur.fetchone()
    await audit(conn, user, "tool.run", "layer", lid, name=name, tool=spec["name"], features=n)
    await _touch(conn, lid, project_id)
    return {"run": r, "layer": await layer_of(conn, lid, user)}


def _plain(p: dict) -> dict:
    out = {}
    for k, v in p.items():
        if hasattr(v, "item"):
            v = v.item()
        if isinstance(v, float) and not math.isfinite(v):
            v = None
        if isinstance(v, list):
            v = [x.item() if hasattr(x, "item") else x for x in v]
        out[k] = v
    return out
