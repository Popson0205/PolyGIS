"""Geodatabases: like ArcGIS, every project comes with a default geodatabase, and new layers and analysis results
are stored in it. A project can hold several; the default one is where fresh work lands. A geodatabase downloads as a
GeoPackage, so the whole working set travels as one file (QGIS, ArcGIS or back into PolyGIS)."""
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from ..audit import audit
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


async def _project_ok(conn, project_id, user) -> None:
    cur = await conn.execute("SELECT 1 FROM projects WHERE id = %s AND org_id = %s", (project_id, user["org_id"]))
    if not await cur.fetchone():
        raise Problem(404, "Project not found.", code="not_found")


async def gdb_of(conn, gdb_id, user) -> dict:
    """The geodatabase, if it is in the person's organization."""
    cur = await conn.execute(GDB_SQL + "WHERE g.id = %s AND g.org_id = %s", (gdb_id, user["org_id"]))
    g = await cur.fetchone()
    if not g:
        raise Problem(404, "Geodatabase not found.", code="not_found")
    return g


@router.get("/projects/{project_id}/geodatabases", summary="A project's geodatabases, default first")
async def list_geodatabases(project_id: UUID, user=Depends(need()), conn=Depends(get_conn)):
    await _project_ok(conn, project_id, user)
    cur = await conn.execute(GDB_SQL + "WHERE g.project_id = %s ORDER BY g.is_default DESC, g.created_at", (project_id,))
    return await cur.fetchall()


@router.post("/projects/{project_id}/geodatabases", status_code=201, summary="Add a geodatabase to the project")
async def create_geodatabase(project_id: UUID, body: GdbIn, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    await _project_ok(conn, project_id, user)
    cur = await conn.execute(
        "INSERT INTO geodatabases (project_id, org_id, name, is_default, created_by) VALUES (%s, %s, %s, false, %s) RETURNING id",
        (project_id, user["org_id"], body.name.strip(), user["id"]))
    gid = (await cur.fetchone())["id"]
    await audit(conn, user, "geodatabase.create", "geodatabase", gid, name=body.name.strip())
    return await gdb_of(conn, gid, user)


@router.patch("/geodatabases/{gdb_id}", summary="Rename a geodatabase or make it the default")
async def update_geodatabase(gdb_id: UUID, body: GdbPatch, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    g = await gdb_of(conn, gdb_id, user)
    if body.name is not None:
        await conn.execute("UPDATE geodatabases SET name = %s, updated_at = now() WHERE id = %s", (body.name.strip(), gdb_id))
    if body.is_default:
        # one default per project: clear the old one first (the partial unique index forbids two)
        await conn.execute("UPDATE geodatabases SET is_default = false, updated_at = now() WHERE project_id = %s AND is_default", (g["project_id"],))
        await conn.execute("UPDATE geodatabases SET is_default = true, updated_at = now() WHERE id = %s", (gdb_id,))
    return await gdb_of(conn, gdb_id, user)


@router.delete("/geodatabases/{gdb_id}", status_code=204, summary="Delete a geodatabase and the layers in it")
async def delete_geodatabase(gdb_id: UUID, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    g = await gdb_of(conn, gdb_id, user)
    cur = await conn.execute("SELECT count(*) AS n FROM geodatabases WHERE project_id = %s", (g["project_id"],))
    if (await cur.fetchone())["n"] <= 1:
        raise Problem(409, "A project keeps at least one geodatabase.", code="last_geodatabase")
    if g["is_default"]:
        raise Problem(409, "This is the project's default geodatabase. Make another one the default first.", code="default_geodatabase")
    await conn.execute("DELETE FROM geodatabases WHERE id = %s", (gdb_id,))
    await audit(conn, user, "geodatabase.delete", "geodatabase", gdb_id, name=g["name"], layers=g["layer_count"])
    return Response(status_code=204)


@router.get("/geodatabases/{gdb_id}/download", summary="Download the geodatabase as a GeoPackage", response_class=Response)
async def download_geodatabase(gdb_id: UUID, epsg: int = 4326, user=Depends(need()), conn=Depends(get_conn)):
    from .. import gisdata
    g = await gdb_of(conn, gdb_id, user)
    # every vector layer in the geodatabase, with its features (follow data_id for linked/derived layers)
    cur = await conn.execute(
        "SELECT id, name, fields FROM layers WHERE geodatabase_id = %s AND geometry_type <> 'Raster' ORDER BY position, created_at", (gdb_id,))
    layers = await cur.fetchall()
    out: list[tuple] = []
    for lyr in layers:
        fc = await conn.execute(
            "SELECT id, ST_AsBinary(geom) AS wkb, props FROM features WHERE layer_id = coalesce((SELECT data_id FROM layers WHERE id = %s), %s) ORDER BY id",
            (lyr["id"], lyr["id"]))
        import shapely
        rows = [(r["id"], shapely.from_wkb(bytes(r["wkb"])), r["props"]) for r in await fc.fetchall()]
        out.append((lyr["name"], lyr["fields"] or [], rows))
    data, filename, media = gisdata.write_geopackage(g["name"], out, epsg)
    await audit(conn, user, "geodatabase.download", "geodatabase", gdb_id, layers=len(out))
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


__all__ = ["router", "gdb_of"]
