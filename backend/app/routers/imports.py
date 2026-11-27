"""Server-side ingest of large (gigabyte / national) vector datasets.

An admin places a file in the server's ingest directory (POLYGIS_INGEST_DIR); PolyGIS streams it
straight into PostGIS in a background job, far past the ordinary upload/feature caps, and marks the
layer `big` so the map draws it from vector tiles while bulk table/export stay guarded.

The file never travels through the browser, so size is bounded only by the server's disk and the
database — not by an HTTP upload.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from uuid import UUID

import anyio
import psycopg
from fastapi import APIRouter, Depends
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import gisdata
from ..audit import audit
from ..config import settings
from ..db import OPTIONS, get_conn
from ..errors import Problem
from ..identity import need
from . import layers as L

router = APIRouter(tags=["imports"])
log = logging.getLogger("imports")


def _root() -> Path:
    if not settings.ingest_dir:
        raise Problem(409, "Server-side import is not set up. Set POLYGIS_INGEST_DIR to a folder on the server.",
                      code="ingest_disabled")
    return Path(settings.ingest_dir)


def _source_path(name: str) -> Path:
    """Resolve a source file inside the ingest directory, refusing anything outside it."""
    root = _root().resolve()
    p = (root / name).resolve()
    if root != p.parent or not p.is_file():
        raise Problem(404, "That file is not in the server ingest folder.", code="not_found")
    return p


class StartIn(BaseModel):
    source: str = Field(min_length=1, max_length=400, description="File name inside the server ingest folder")
    name: str = Field(min_length=1, max_length=200, description="Name for the new layer")
    epsg: int | None = Field(None, description="Source EPSG, when the file has no usable .prj / CRS")


@router.get("/imports/sources", summary="Files waiting in the server ingest folder (admin)")
async def sources(user=Depends(need("admin"))):
    root = _root()
    out = []
    if root.is_dir():
        for p in sorted(root.iterdir()):
            if p.is_file() and not p.name.startswith("."):
                out.append({"name": p.name, "size": p.stat().st_size})
    return {"dir": str(root), "files": out}


@router.get("/projects/{project_id}/imports", summary="Large-dataset imports for a project")
async def list_imports(project_id: UUID, user=Depends(need()), conn=Depends(get_conn)):
    await _project_org(conn, project_id, user)
    cur = await conn.execute(
        "SELECT id, layer_id, source, name, status, features, total, message, created_by_name, created_at, updated_at "
        "FROM imports WHERE project_id = %s AND org_id = %s ORDER BY created_at DESC LIMIT 50",
        (project_id, user["org_id"]))
    return {"imports": await cur.fetchall()}


@router.get("/imports/{import_id}", summary="How a large-dataset import is going")
async def get_import(import_id: UUID, user=Depends(need()), conn=Depends(get_conn)):
    cur = await conn.execute(
        "SELECT id, project_id, layer_id, source, name, status, features, total, message, created_at, updated_at "
        "FROM imports WHERE id = %s AND org_id = %s", (import_id, user["org_id"]))
    r = await cur.fetchone()
    if not r:
        raise Problem(404, "Import not found.", code="not_found")
    return r


@router.post("/projects/{project_id}/imports", summary="Start a large-dataset import from the server (admin)")
async def start(project_id: UUID, body: StartIn, user=Depends(need("admin", write=True)), conn=Depends(get_conn)):
    await _project_org(conn, project_id, user)
    path = _source_path(body.source)
    # Read just the header (fast) to shape the layer and catch CRS/geometry problems before we start.
    try:
        meta = await anyio.to_thread.run_sync(lambda: gisdata.large_meta(path, None, body.epsg))
    except gisdata.ReadError as e:
        raise Problem(422, str(e), code="unreadable") from None

    pos = await L._next_position(conn, project_id)
    cur = await conn.execute(
        "INSERT INTO layers (project_id, org_id, name, geometry_type, fields, style, position, source, created_by, big) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true) RETURNING id",
        (project_id, user["org_id"], body.name[:200], meta["geometry_type"], Jsonb(meta["fields"]),
         Jsonb(L.default_style(meta["geometry_type"], pos)), pos,
         Jsonb({"filename": body.source, "crs": meta["crs"], "big": True}), user["id"]))
    layer_id = (await cur.fetchone())["id"]
    cur = await conn.execute(
        "INSERT INTO imports (org_id, project_id, layer_id, source, name, status, total, created_by, created_by_name) "
        "VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s, %s) RETURNING id, status, features, total, name, layer_id",
        (user["org_id"], project_id, layer_id, body.source, body.name[:200], meta["total"] or None, user["id"], user["name"]))
    imp = await cur.fetchone()
    await audit(conn, user, "import.start", "layer", layer_id, source=body.source, features=meta["total"])

    asyncio.create_task(anyio.to_thread.run_sync(
        _ingest_sync, imp["id"], layer_id, str(path), int(meta["srid"]), int(settings.ingest_batch)))
    return imp


async def _project_org(conn, project_id, user) -> None:
    cur = await conn.execute("SELECT 1 FROM projects WHERE id = %s AND org_id = %s", (project_id, user["org_id"]))
    if not await cur.fetchone():
        raise Problem(404, "Project not found.", code="not_found")


# ---- the background worker (its own synchronous connection, run in a thread) -------------------
def _ingest_sync(import_id, layer_id, path: str, srid: int, chunk: int) -> None:
    tmp = f"ing_{import_id.hex}"
    conn = psycopg.connect(settings.database_url, autocommit=True, options=OPTIONS)
    count = 0
    try:
        conn.execute("UPDATE imports SET status = 'running', updated_at = now() WHERE id = %s", (import_id,))
        conn.execute(f'CREATE UNLOGGED TABLE "{tmp}" (wkb text, props text)')
        buf: list[tuple[bytes, dict]] = []

        def flush():
            nonlocal count
            if not buf:
                return
            with conn.cursor() as cur, cur.copy(f'COPY "{tmp}" (wkb, props) FROM STDIN') as cp:
                for g, p in buf:
                    cp.write_row((g.hex(), json.dumps(p, default=str, ensure_ascii=False)))
            count += len(buf)
            buf.clear()
            conn.execute("UPDATE imports SET features = %s, updated_at = now() WHERE id = %s", (count, import_id))

        for g, p in gisdata.stream_batches(Path(path), batch=chunk):
            buf.append((g, p))
            if len(buf) >= chunk:
                flush()
        flush()

        # One bulk reprojection into the real features table (PostGIS does it in C).
        conn.execute(
            'INSERT INTO features (layer_id, geom, props) '
            'SELECT %s, ST_Transform(ST_SetSRID(ST_Force2D(ST_GeomFromWKB(decode(wkb, \'hex\'))), %s), 4326), props::jsonb '
            f'FROM "{tmp}"',
            (layer_id, srid))
        conn.execute(f'DROP TABLE IF EXISTS "{tmp}"')
        conn.execute("UPDATE imports SET status = 'done', features = %s, updated_at = now() WHERE id = %s", (count, import_id))
        log.info("large import %s done: %s features", import_id, count)
    except Exception as e:  # noqa: BLE001
        log.exception("large import %s failed", import_id)
        try:
            conn.execute(f'DROP TABLE IF EXISTS "{tmp}"')
            conn.execute("DELETE FROM layers WHERE id = %s", (layer_id,))   # remove the empty layer
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.execute("UPDATE imports SET status = 'failed', layer_id = NULL, message = %s, updated_at = now() WHERE id = %s",
                         (str(e)[:500], import_id))
        except Exception:  # noqa: BLE001
            pass
    finally:
        conn.close()
