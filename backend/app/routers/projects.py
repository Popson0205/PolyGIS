"""Projects: an organization's maps. Everyone in the organization can open them; editors and admins make and change
them; a project is deleted by whoever made it or an admin."""
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from ..audit import audit
from ..db import get_conn
from ..errors import Problem
from ..identity import current_user, need

router = APIRouter(tags=["projects"])

PROJECT_SQL = ("SELECT p.*, (SELECT count(*) FROM layers l WHERE l.project_id = p.id) AS layer_count FROM projects p ")


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=4000)
    display_epsg: int = Field(4326, ge=1024, le=999999)


class ProjectPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    display_epsg: int | None = Field(None, ge=1024, le=999999)
    view: dict | None = Field(None, description="{center:[lng,lat], zoom, bearing, pitch, basemap}")


async def project_of(conn, project_id, user) -> dict:
    """The project, if it belongs to the person's organization."""
    cur = await conn.execute(PROJECT_SQL + "WHERE p.id = %s AND p.org_id = %s", (project_id, user["org_id"]))
    p = await cur.fetchone()
    if not p:
        raise Problem(404, "Project not found.", code="not_found")
    return p


def _epsg_ok(epsg: int) -> None:
    from pyproj import CRS
    try:
        CRS.from_epsg(epsg)
    except Exception:  # noqa: BLE001
        raise Problem(422, f"EPSG:{epsg} is not a coordinate system PolyGIS knows.", code="validation",
                      fields={"display_epsg": ["unknown"]}) from None


def _view_ok(view: dict) -> dict:
    out = {}
    c = view.get("center")
    if isinstance(c, list) and len(c) == 2 and all(isinstance(x, (int, float)) for x in c) and -180 <= c[0] <= 180 and -90 <= c[1] <= 90:
        out["center"] = [float(c[0]), float(c[1])]
    for k, lo, hi in (("zoom", 0, 24), ("bearing", -360, 360), ("pitch", 0, 85)):
        if isinstance(view.get(k), (int, float)) and lo <= view[k] <= hi:
            out[k] = float(view[k])
    if isinstance(view.get("basemap"), str) and len(view["basemap"]) <= 40:
        out["basemap"] = view["basemap"]
    return out


@router.get("/projects", summary="The organization's projects, latest first")
async def list_projects(user=Depends(need()), conn=Depends(get_conn)):
    cur = await conn.execute(PROJECT_SQL + "WHERE p.org_id = %s ORDER BY p.updated_at DESC", (user["org_id"],))
    return await cur.fetchall()


@router.post("/projects", status_code=201, summary="Start a project")
async def create_project(body: ProjectIn, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    _epsg_ok(body.display_epsg)
    cur = await conn.execute(
        "INSERT INTO projects (org_id, name, description, display_epsg, view, created_by, created_by_name) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (user["org_id"], body.name.strip(), body.description.strip(), body.display_epsg,
         Jsonb({"center": [8.68, 9.08], "zoom": 5.5, "basemap": "streets"}), user["id"], user["name"]))
    pid = (await cur.fetchone())["id"]
    await audit(conn, user, "project.create", "project", pid, name=body.name.strip())
    return await project_of(conn, pid, user)


@router.get("/projects/{project_id}", summary="A project and its layers, bottom to top")
async def get_project(project_id: UUID, user=Depends(need()), conn=Depends(get_conn)):
    from .layers import layers_of
    p = await project_of(conn, project_id, user)
    p["layers"] = await layers_of(conn, project_id)
    return p


@router.patch("/projects/{project_id}", summary="Rename, describe, or keep the map's view")
async def update_project(project_id: UUID, body: ProjectPatch, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    sets, vals = [], []
    if body.name is not None:
        sets.append("name = %s"); vals.append(body.name.strip())
    if body.description is not None:
        sets.append("description = %s"); vals.append(body.description.strip())
    if body.display_epsg is not None:
        _epsg_ok(body.display_epsg)
        sets.append("display_epsg = %s"); vals.append(body.display_epsg)
    if body.view is not None:
        sets.append("view = %s"); vals.append(Jsonb(_view_ok(body.view)))
    if sets:
        await conn.execute(f"UPDATE projects SET {', '.join(sets)}, updated_at = now() WHERE id = %s", (*vals, project_id))
    return await get_project(project_id, user, conn)


@router.delete("/projects/{project_id}", status_code=204, summary="Delete a project and its layers")
async def delete_project(project_id: UUID, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    p = await project_of(conn, project_id, user)
    if user["role"] != "admin" and str(p["created_by"]) != str(user["id"]):
        raise Problem(403, f"Only {p['created_by_name'] or 'whoever made it'} or an admin can delete this project.", code="forbidden")
    await conn.execute("DELETE FROM projects WHERE id = %s", (project_id,))
    await audit(conn, user, "project.delete", "project", project_id, name=p["name"])
    return Response(status_code=204)


@router.get("/activity", summary="What happened in the organization's projects, latest first")
async def activity(user=Depends(need()), conn=Depends(get_conn)):
    cur = await conn.execute("SELECT actor_name, action, target_type, target_id, details, at FROM audit_log "
                             "WHERE org_id = %s ORDER BY at DESC LIMIT 200", (user["org_id"],))
    return await cur.fetchall()


__all__ = ["router", "project_of", "current_user"]
