"""Geodatabases: like ArcGIS, every project comes with a default geodatabase, and new layers and analysis results
are stored in it. A project can hold several; the default one is where fresh work lands. A geodatabase downloads as a
GeoPackage, and can be archived to object storage — its features are packed into a GeoPackage in the bucket and
removed from the database, so a cold project's data does not sit in the central DB — then restored on demand."""
import asyncio
import tempfile
from pathlib import Path
from uuid import UUID

import shapely
from fastapi import APIRouter, Depends, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from ..audit import audit
from ..config import settings
from ..db import get_conn
from ..errors import Problem
from ..identity import need

router = APIRouter(tags=["geodatabases"])

GDB_SQL = (
    "SELECT g.*, "
    "  (SELECT count(*) FROM layers l WHERE l.geodatabase_id = g.id) AS layer_count, "
    "  (SELECT count(*) FROM features f JOIN layers l ON l.id = f.layer_id "
    "     WHERE l.geodatabase_id = g.id) AS feature_count "
    "FROM geodatabases g ")


class GdbIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class GdbPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    is_default: bool | None = None


def _public(g: dict) -> dict:
    """The geodatabase as the client sees it: the internal storage key and manifest are dropped, and an archived
    geodatabase reports the layer and feature counts it had when it was archived."""
    out = {k: v for k, v in g.items() if k not in ("archive_key", "manifest")}
    if g.get("storage") == "archived" and g.get("manifest"):
        out["layer_count"] = g["manifest"].get("layer_count", 0)
        out["feature_count"] = g["manifest"].get("feature_count", 0)
    return out


def _bucket():
    """The object-storage client and bucket, or a clear error when none is configured."""
    if not settings.s3_endpoint:
        raise Problem(409, "No storage bucket is configured, so geodatabases can't be archived. "
                           "Set S3_* (a Supabase Storage bucket) first.", code="no_bucket")
    from .. import rasters
    return rasters._bucket(), settings.s3_bucket


def _archive_key(gdb_id) -> str:
    return f"polygis/geodatabases/{gdb_id}.gpkg"


async def _project_ok(conn, project_id, user) -> None:
    cur = await conn.execute("SELECT 1 FROM projects WHERE id = %s AND org_id = %s", (project_id, user["org_id"]))
    if not await cur.fetchone():
        raise Problem(404, "Project not found.", code="not_found")


async def gdb_of(conn, gdb_id, user) -> dict:
    """The geodatabase row (with counts), if it is in the person's organization."""
    cur = await conn.execute(GDB_SQL + "WHERE g.id = %s AND g.org_id = %s", (gdb_id, user["org_id"]))
    g = await cur.fetchone()
    if not g:
        raise Problem(404, "Geodatabase not found.", code="not_found")
    return g


@router.get("/projects/{project_id}/geodatabases", summary="A project's geodatabases, default first")
async def list_geodatabases(project_id: UUID, user=Depends(need()), conn=Depends(get_conn)):
    await _project_ok(conn, project_id, user)
    cur = await conn.execute(GDB_SQL + "WHERE g.project_id = %s ORDER BY g.is_default DESC, g.created_at", (project_id,))
    return [_public(r) for r in await cur.fetchall()]


@router.post("/projects/{project_id}/geodatabases", status_code=201, summary="Add a geodatabase to the project")
async def create_geodatabase(project_id: UUID, body: GdbIn, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    await _project_ok(conn, project_id, user)
    cur = await conn.execute(
        "INSERT INTO geodatabases (project_id, org_id, name, is_default, created_by) VALUES (%s, %s, %s, false, %s) RETURNING id",
        (project_id, user["org_id"], body.name.strip(), user["id"]))
    gid = (await cur.fetchone())["id"]
    await audit(conn, user, "geodatabase.create", "geodatabase", gid, name=body.name.strip())
    return _public(await gdb_of(conn, gid, user))


@router.patch("/geodatabases/{gdb_id}", summary="Rename a geodatabase or make it the default")
async def update_geodatabase(gdb_id: UUID, body: GdbPatch, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    g = await gdb_of(conn, gdb_id, user)
    if body.name is not None:
        await conn.execute("UPDATE geodatabases SET name = %s, updated_at = now() WHERE id = %s", (body.name.strip(), gdb_id))
    if body.is_default:
        if g["storage"] == "archived":
            raise Problem(409, "Restore this geodatabase before making it the default.", code="archived")
        await conn.execute("UPDATE geodatabases SET is_default = false, updated_at = now() WHERE project_id = %s AND is_default", (g["project_id"],))
        await conn.execute("UPDATE geodatabases SET is_default = true, updated_at = now() WHERE id = %s", (gdb_id,))
    return _public(await gdb_of(conn, gdb_id, user))


@router.delete("/geodatabases/{gdb_id}", status_code=204, summary="Delete a geodatabase and the layers in it")
async def delete_geodatabase(gdb_id: UUID, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    g = await gdb_of(conn, gdb_id, user)
    cur = await conn.execute("SELECT count(*) AS n FROM geodatabases WHERE project_id = %s", (g["project_id"],))
    if (await cur.fetchone())["n"] <= 1:
        raise Problem(409, "A project keeps at least one geodatabase.", code="last_geodatabase")
    if g["is_default"]:
        raise Problem(409, "This is the project's default geodatabase. Make another one the default first.", code="default_geodatabase")
    if g["storage"] == "archived" and g.get("archive_key"):
        try:
            client, bucket = _bucket()
            await asyncio.to_thread(client.delete_object, Bucket=bucket, Key=g["archive_key"])
        except Problem:
            pass   # bucket gone or unconfigured: still drop the catalog row
    await conn.execute("DELETE FROM geodatabases WHERE id = %s", (gdb_id,))
    await audit(conn, user, "geodatabase.delete", "geodatabase", gdb_id, name=g["name"], layers=g["layer_count"])
    return Response(status_code=204)


@router.get("/geodatabases/{gdb_id}/download", summary="Download the geodatabase as a GeoPackage", response_class=Response)
async def download_geodatabase(gdb_id: UUID, epsg: int = 4326, user=Depends(need()), conn=Depends(get_conn)):
    from .. import gisdata
    g = await gdb_of(conn, gdb_id, user)
    if g["storage"] == "archived":
        raise Problem(409, "This geodatabase is archived. Restore it first to download it.", code="archived")
    cur = await conn.execute(
        "SELECT id, name, fields FROM layers WHERE geodatabase_id = %s AND geometry_type <> 'Raster' ORDER BY position, created_at", (gdb_id,))
    layers = await cur.fetchall()
    out: list[tuple] = []
    for lyr in layers:
        fc = await conn.execute(
            "SELECT id, ST_AsBinary(geom) AS wkb, props FROM features WHERE layer_id = coalesce((SELECT data_id FROM layers WHERE id = %s), %s) ORDER BY id",
            (lyr["id"], lyr["id"]))
        rows = [(r["id"], shapely.from_wkb(bytes(r["wkb"])), r["props"]) for r in await fc.fetchall()]
        out.append((lyr["name"], lyr["fields"] or [], rows))
    data, filename, media = gisdata.write_geopackage(g["name"], out, epsg)
    await audit(conn, user, "geodatabase.download", "geodatabase", gdb_id, layers=len(out))
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/geodatabases/{gdb_id}/archive", summary="Archive the geodatabase to storage, freeing the database")
async def archive_geodatabase(gdb_id: UUID, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    from .. import gisdata
    g = await gdb_of(conn, gdb_id, user)
    if g["storage"] == "archived":
        raise Problem(409, "This geodatabase is already archived.", code="already_archived")
    if g["is_default"]:
        raise Problem(409, "This is the project's default geodatabase, where new work lands. "
                           "Make another one the default before archiving this one.", code="default_geodatabase")
    client, bucket = _bucket()
    cur = await conn.execute(
        "SELECT id, name, geometry_type, fields, style, labels, visible, position, source, raster, map_id "
        "FROM layers WHERE geodatabase_id = %s ORDER BY position, created_at", (gdb_id,))
    layers = await cur.fetchall()
    items: list[tuple] = []           # (key, fields, rows) for vector layers that have features
    manifest_layers: list[dict] = []
    total = 0
    for i, l in enumerate(layers):
        key = f"l{i}"
        entry = {"key": key, "name": l["name"], "geometry_type": l["geometry_type"], "fields": l["fields"],
                 "style": l["style"], "labels": l["labels"], "visible": l["visible"], "position": l["position"],
                 "source": l["source"], "raster": l["raster"], "map_id": str(l["map_id"]) if l["map_id"] else None,
                 "has_features": False}
        if l["geometry_type"] != "Raster":
            fc = await conn.execute(
                "SELECT ST_AsBinary(geom) AS wkb, props FROM features WHERE layer_id = coalesce((SELECT data_id FROM layers WHERE id = %s), %s) ORDER BY id",
                (l["id"], l["id"]))
            rows = [(0, shapely.from_wkb(bytes(r["wkb"])), r["props"]) for r in await fc.fetchall()]
            if rows:
                items.append((key, l["fields"] or [], rows))
                entry["has_features"] = True
                total += len(rows)
        manifest_layers.append(entry)
    data = gisdata.pack_geopackage(items)
    key = _archive_key(gdb_id)
    await asyncio.to_thread(client.put_object, Bucket=bucket, Key=key, Body=data,
                            ContentType="application/geopackage+sqlite3")
    manifest = {"version": 1, "layers": manifest_layers, "layer_count": len(layers), "feature_count": total}
    await conn.execute("DELETE FROM layers WHERE geodatabase_id = %s", (gdb_id,))
    await conn.execute("UPDATE geodatabases SET storage = 'archived', archive_key = %s, manifest = %s, "
                       "archived_at = now(), updated_at = now() WHERE id = %s", (key, Jsonb(manifest), gdb_id))
    await audit(conn, user, "geodatabase.archive", "geodatabase", gdb_id, name=g["name"], layers=len(layers), features=total)
    return _public(await gdb_of(conn, gdb_id, user))


@router.post("/geodatabases/{gdb_id}/restore", summary="Bring an archived geodatabase back into the database")
async def restore_geodatabase(gdb_id: UUID, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    from .. import gisdata
    g = await gdb_of(conn, gdb_id, user)
    if g["storage"] != "archived":
        raise Problem(409, "This geodatabase is not archived.", code="not_archived")
    client, bucket = _bucket()
    manifest = g.get("manifest") or {"layers": []}
    obj = await asyncio.to_thread(client.get_object, Bucket=bucket, Key=g["archive_key"])
    body = await asyncio.to_thread(obj["Body"].read)
    with tempfile.NamedTemporaryFile(suffix=".gpkg") as tf:
        tf.write(body); tf.flush()
        gpkg = Path(tf.name)
        for e in manifest["layers"]:
            mid = e.get("map_id")
            if mid and not await (await conn.execute("SELECT 1 FROM maps WHERE id = %s AND project_id = %s", (mid, g["project_id"]))).fetchone():
                mid = None
            cur = await conn.execute(
                "INSERT INTO layers (project_id, org_id, geodatabase_id, name, geometry_type, fields, style, labels, "
                "visible, position, source, raster, map_id, created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (g["project_id"], g["org_id"], gdb_id, e["name"], e["geometry_type"], Jsonb(e["fields"] or []),
                 Jsonb(e["style"] or {}), Jsonb(e["labels"]) if e.get("labels") is not None else None,
                 e.get("visible", True), e.get("position", 0), Jsonb(e["source"] or {}),
                 Jsonb(e["raster"]) if e.get("raster") is not None else None, mid, user["id"]))
            lid = (await cur.fetchone())["id"]
            if e.get("has_features"):
                for geom, props in gisdata.read_gpkg_layer(gpkg, e["key"], e["fields"] or []):
                    await conn.execute(
                        "INSERT INTO features (layer_id, geom, props, updated_by) VALUES (%s, ST_GeomFromWKB(%s, 4326), %s, %s)",
                        (lid, shapely.to_wkb(geom), Jsonb(props), user["id"]))
    await asyncio.to_thread(client.delete_object, Bucket=bucket, Key=g["archive_key"])
    await conn.execute("UPDATE geodatabases SET storage = 'postgis', archive_key = NULL, manifest = NULL, "
                       "archived_at = NULL, updated_at = now() WHERE id = %s", (gdb_id,))
    await audit(conn, user, "geodatabase.restore", "geodatabase", gdb_id, name=g["name"], layers=len(manifest["layers"]))
    return _public(await gdb_of(conn, gdb_id, user))


__all__ = ["router", "gdb_of"]
