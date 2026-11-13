"""Print layouts: a project's saved map sheets. The page itself is drawn and exported (PDF or PNG) in the browser, so
only its settings are kept: page size and orientation, the map's centre and scale, title, legend, scale bar, north
arrow, grid and notes. Everyone who can open the project can see them; editors on the Advanced plan save them."""
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from ..audit import audit
from ..db import get_conn
from ..errors import Problem
from ..identity import need
from .projects import project_of

router = APIRouter(tags=["layouts"])
MAX_SETTINGS = 20_000      # bytes of JSON: a layout is a few hundred; notes make it longer


class LayoutIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    settings: dict = Field(default_factory=dict)


class LayoutPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    settings: dict | None = None


def _settings_ok(s: dict) -> dict:
    if len(json.dumps(s)) > MAX_SETTINGS:
        raise Problem(422, "That layout is too large to keep: shorten the notes.", code="validation", fields={"settings": ["too_large"]})
    return s


async def _layout(conn, layout_id, user) -> dict:
    cur = await conn.execute("SELECT * FROM layouts WHERE id = %s AND org_id = %s", (layout_id, user["org_id"]))
    row = await cur.fetchone()
    if not row:
        raise Problem(404, "Layout not found.", code="not_found")
    return row


@router.get("/projects/{project_id}/layouts", summary="The project's saved print layouts, latest first")
async def list_layouts(project_id: UUID, user=Depends(need()), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    cur = await conn.execute("SELECT * FROM layouts WHERE project_id = %s ORDER BY updated_at DESC", (project_id,))
    return await cur.fetchall()


@router.post("/projects/{project_id}/layouts", status_code=201, summary="Save a print layout")
async def create_layout(project_id: UUID, body: LayoutIn, user=Depends(need("editor", write=True, feature="layouts")), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    cur = await conn.execute(
        "INSERT INTO layouts (project_id, org_id, name, settings, created_by, created_by_name, updated_by_name) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (project_id, user["org_id"], body.name.strip(), Jsonb(_settings_ok(body.settings)), user["id"], user["name"], user["name"]))
    row = await cur.fetchone()
    await audit(conn, user, "layout.create", "layout", row["id"], name=row["name"], project_id=str(project_id))
    return row


@router.patch("/layouts/{layout_id}", summary="Rename a layout or keep its new settings")
async def update_layout(layout_id: UUID, body: LayoutPatch, user=Depends(need("editor", write=True, feature="layouts")), conn=Depends(get_conn)):
    await _layout(conn, layout_id, user)
    sets, vals = ["updated_at = now()", "updated_by_name = %s"], [user["name"]]
    if body.name is not None:
        sets.append("name = %s"); vals.append(body.name.strip())
    if body.settings is not None:
        sets.append("settings = %s"); vals.append(Jsonb(_settings_ok(body.settings)))
    cur = await conn.execute(f"UPDATE layouts SET {', '.join(sets)} WHERE id = %s RETURNING *", (*vals, layout_id))
    return await cur.fetchone()


@router.delete("/layouts/{layout_id}", status_code=204, summary="Delete a layout")
async def delete_layout(layout_id: UUID, user=Depends(need("editor", write=True, feature="layouts")), conn=Depends(get_conn)):
    row = await _layout(conn, layout_id, user)
    await conn.execute("DELETE FROM layouts WHERE id = %s", (layout_id,))
    await audit(conn, user, "layout.delete", "layout", layout_id, name=row["name"])
    return Response(status_code=204)
