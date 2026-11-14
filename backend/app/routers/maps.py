"""Maps: a project holds several maps, as in ArcGIS Pro, each with its own layers, view and basemap. The same data
can be in several maps (a layer sharing another's features), each styled and labelled its own way."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from ..audit import audit
from ..db import get_conn
from ..errors import Problem
from ..identity import RANK, need
from .layers import _next_position, _touch, drop_file, hand_on, layer_of
from .projects import _view_ok, project_of

router = APIRouter(tags=["maps"])
EDIT = need("editor", write=True)


async def maps_of(conn, project_id) -> list[dict]:
    cur = await conn.execute("SELECT m.id, m.project_id, m.name, m.position, m.view, m.created_at, m.updated_at, "
                             "(SELECT count(*) FROM layers l WHERE l.map_id = m.id) AS layer_count FROM maps m "
                             "WHERE m.project_id = %s ORDER BY m.position, m.created_at", (project_id,))
    return await cur.fetchall()


async def map_of(conn, map_id, user) -> dict:
    cur = await conn.execute("SELECT * FROM maps WHERE id = %s AND org_id = %s", (map_id, user["org_id"]))
    m = await cur.fetchone()
    if not m:
        raise Problem(404, "That map was not found.", code="not_found")
    return m


class MapIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    copy_of: UUID | None = Field(None, description="Start with this map's layers (sharing their data) and its view")


class MapPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    view: dict | None = Field(None, description="{center:[lng,lat], zoom, bearing, pitch, basemap}")
    position: int | None = Field(None, ge=0, le=10_000)


@router.get("/projects/{project_id}/maps", summary="The project's maps")
async def listing(project_id: UUID, user=Depends(need()), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    return await maps_of(conn, project_id)


@router.post("/projects/{project_id}/maps", status_code=201, summary="Insert a new map (empty, or a copy of another)")
async def create(project_id: UUID, body: MapIn, user=Depends(EDIT), conn=Depends(get_conn)):
    p = await project_of(conn, project_id, user)
    src = await map_of(conn, body.copy_of, user) if body.copy_of else None
    if src and str(src["project_id"]) != str(project_id):
        raise Problem(422, "That map is in another project.", code="validation")
    cur = await conn.execute("SELECT coalesce(max(position) + 1, 0) AS n FROM maps WHERE project_id = %s", (project_id,))
    pos = (await cur.fetchone())["n"]
    view = (src or {}).get("view") or p["view"] or {}
    async with conn.transaction():
        cur = await conn.execute("INSERT INTO maps (project_id, org_id, name, position, view, created_by) VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
                                 (project_id, user["org_id"], body.name.strip(), pos, Jsonb(view), user["id"]))
        m = await cur.fetchone()
        if src:                                   # its layers, sharing their data, styled the same to start with
            await conn.execute(
                "INSERT INTO layers (project_id, org_id, map_id, data_id, name, geometry_type, fields, style, labels, visible, position, source, raster, created_by) "
                "SELECT project_id, org_id, %s, CASE WHEN geometry_type = 'Raster' THEN NULL ELSE coalesce(data_id, id) END, name, geometry_type, fields, "
                "style, labels, visible, position, source, raster, %s FROM layers WHERE map_id = %s ORDER BY position", (m["id"], user["id"], src["id"]))
    await audit(conn, user, "map.create", "map", m["id"], name=m["name"], copy_of=str(body.copy_of) if body.copy_of else None)
    return {**m, "layer_count": 0}


@router.patch("/maps/{map_id}", summary="Rename a map, keep its view or move it in the list")
async def update(map_id: UUID, body: MapPatch, user=Depends(EDIT), conn=Depends(get_conn)):
    m = await map_of(conn, map_id, user)
    sets, vals = [], []
    if body.name is not None:
        sets.append("name = %s"); vals.append(body.name.strip())
    if body.view is not None:
        sets.append("view = %s"); vals.append(Jsonb(_view_ok(body.view)))
    if body.position is not None:
        sets.append("position = %s"); vals.append(body.position)
    if sets:
        cur = await conn.execute(f"UPDATE maps SET {', '.join(sets)}, updated_at = now() WHERE id = %s RETURNING *", (*vals, map_id))
        m = await cur.fetchone()
    return m


@router.post("/maps/{map_id}/open", summary="Make this the map the project has open (new layers go into it)")
async def open_map(map_id: UUID, user=Depends(need()), conn=Depends(get_conn)):
    m = await map_of(conn, map_id, user)
    await project_of(conn, m["project_id"], user)
    if RANK[user["role"]] >= RANK["editor"] and user["can_write"]:
        await conn.execute("UPDATE projects SET active_map_id = %s WHERE id = %s", (map_id, m["project_id"]))
    return m


@router.delete("/maps/{map_id}", status_code=204, summary="Delete a map and its layers (data shown in other maps stays there)")
async def delete(map_id: UUID, user=Depends(EDIT), conn=Depends(get_conn)):
    m = await map_of(conn, map_id, user)
    cur = await conn.execute("SELECT count(*) AS n FROM maps WHERE project_id = %s", (m["project_id"],))
    if (await cur.fetchone())["n"] <= 1:
        raise Problem(422, "A project needs at least one map.", code="last_map")
    cur = await conn.execute("SELECT id, raster FROM layers WHERE map_id = %s", (map_id,))
    gone = await cur.fetchall()
    async with conn.transaction():
        for lay in gone:
            await hand_on(conn, lay["id"])
            await conn.execute("DELETE FROM layers WHERE id = %s", (lay["id"],))
        await conn.execute("UPDATE projects SET active_map_id = (SELECT id FROM maps WHERE project_id = %s AND id <> %s ORDER BY position, created_at LIMIT 1) "
                           "WHERE id = %s AND (active_map_id = %s OR active_map_id IS NULL)", (m["project_id"], map_id, m["project_id"], map_id))
        await conn.execute("DELETE FROM maps WHERE id = %s", (map_id,))
    for lay in gone:
        await drop_file(conn, lay)
    await audit(conn, user, "map.delete", "map", map_id, name=m["name"], layers=len(gone))
    return Response(status_code=204)


class AddToMapIn(BaseModel):
    map_id: UUID
    how: Literal["share", "copy"] = Field("share", description="share: the same data, styled on its own; copy: an independent copy of the data")


@router.post("/layers/{layer_id}/add-to-map", status_code=201, summary="Put a layer into another map (sharing its data, or as a copy)")
async def add_to_map(layer_id: UUID, body: AddToMapIn, user=Depends(EDIT), conn=Depends(get_conn)):
    lay = await layer_of(conn, layer_id, user)
    m = await map_of(conn, body.map_id, user)
    if str(m["project_id"]) != str(lay["project_id"]):
        raise Problem(422, "That map is in another project.", code="validation")
    pos = await _next_position(conn, lay["project_id"])
    raster = lay["geometry_type"] == "Raster"
    share = body.how == "share" and not raster                 # a raster's file is shared either way (it never changes)
    async with conn.transaction():
        cur = await conn.execute(
            "INSERT INTO layers (project_id, org_id, map_id, data_id, name, geometry_type, fields, style, labels, visible, position, source, raster, created_by) "
            "SELECT project_id, org_id, %s, %s, name, geometry_type, fields, style, labels, true, %s, source, raster, %s FROM layers WHERE id = %s RETURNING id",
            (m["id"], lay["data_id"] or lay["id"] if share else None, pos, user["id"], layer_id))
        lid = (await cur.fetchone())["id"]
        if body.how == "copy" and not raster:
            await conn.execute("INSERT INTO features (layer_id, geom, props, updated_by) SELECT %s, geom, props, %s FROM features WHERE layer_id = layer_data(%s)",
                               (lid, user["id"], layer_id))
    await audit(conn, user, "layer.add_to_map", "layer", lid, name=lay["name"], map=m["name"], how=body.how)
    await _touch(conn, lid, lay["project_id"])
    return await layer_of(conn, lid, user)
