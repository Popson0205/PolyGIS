"""Scenarios: named what-if settings for a simulation, run into a raster layer (one band per time step or level) with a
summary of figures; duplicated to make variants and compared side by side. Running again updates the same layer."""
import time
from typing import Literal
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import raster_tools, rasters, simulations
from ..audit import audit
from ..db import get_conn
from ..errors import Problem
from ..identity import need
from .layers import _touch, layer_of
from .projects import project_of
from .rasters import _features, _insert

router = APIRouter(tags=["scenarios"])
EDIT = need("editor", write=True, feature="raster")
VIEW = need(feature="raster")
Kind = Literal["suitability", "flood", "land_change", "spread"]


class ScenarioIn(BaseModel):
    kind: Kind
    name: str = Field(min_length=1, max_length=200)
    notes: str = Field("", max_length=4000)
    params: dict = Field(default_factory=dict)


class ScenarioPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    notes: str | None = Field(None, max_length=4000)
    params: dict | None = None


def _out(r: dict) -> dict:
    return {k: r[k] for k in ("id", "project_id", "kind", "name", "notes", "params", "status", "summary", "error", "layer_id", "ran_at", "run_ms",
                              "created_at", "updated_at")}


async def _one(conn, sid: UUID, user) -> dict:
    cur = await conn.execute("SELECT * FROM scenarios WHERE id = %s AND org_id = %s", (sid, user["org_id"]))
    r = await cur.fetchone()
    if not r:
        raise Problem(404, "That scenario was not found.", code="not_found")
    await project_of(conn, r["project_id"], user)
    return r


@router.get("/scenario-kinds", summary="The kinds of scenario that can be made")
async def kinds(user=Depends(VIEW)):
    return [{"id": k, "label": v} for k, v in simulations.KINDS.items()]


@router.get("/projects/{project_id}/scenarios", summary="This project's scenarios")
async def listing(project_id: UUID, user=Depends(VIEW), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    cur = await conn.execute("SELECT * FROM scenarios WHERE project_id = %s ORDER BY kind, created_at", (project_id,))
    return [_out(r) for r in await cur.fetchall()]


@router.post("/projects/{project_id}/scenarios", status_code=201, summary="Make a scenario")
async def create(project_id: UUID, body: ScenarioIn, user=Depends(EDIT), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    cur = await conn.execute("INSERT INTO scenarios (project_id, org_id, kind, name, notes, params, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                             "RETURNING *", (project_id, user["org_id"], body.kind, body.name.strip(), body.notes, Jsonb(body.params), user["id"]))
    r = await cur.fetchone()
    await audit(conn, user, "scenario.create", "scenario", r["id"], name=r["name"], kind=r["kind"])
    return _out(r)


@router.patch("/scenarios/{sid}", summary="Rename a scenario or change its settings")
async def update(sid: UUID, body: ScenarioPatch, user=Depends(EDIT), conn=Depends(get_conn)):
    r = await _one(conn, sid, user)
    changed = body.params is not None and body.params != r["params"]
    cur = await conn.execute(
        "UPDATE scenarios SET name = %s, notes = %s, params = %s, status = CASE WHEN %s THEN 'draft' ELSE status END, updated_at = now() "
        "WHERE id = %s RETURNING *",
        ((body.name or r["name"]).strip(), r["notes"] if body.notes is None else body.notes, Jsonb(r["params"] if body.params is None else body.params),
         changed, sid))
    return _out(await cur.fetchone())


@router.post("/scenarios/{sid}/duplicate", status_code=201, summary="A copy of a scenario to vary")
async def duplicate(sid: UUID, user=Depends(EDIT), conn=Depends(get_conn)):
    r = await _one(conn, sid, user)
    cur = await conn.execute("SELECT name FROM scenarios WHERE project_id = %s", (r["project_id"],))
    taken = {x["name"] for x in await cur.fetchall()}
    n, name = 2, f"{r['name']} (variant)"
    while name in taken:
        name, n = f"{r['name']} (variant {n})", n + 1
    cur = await conn.execute("INSERT INTO scenarios (project_id, org_id, kind, name, notes, params, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                             "RETURNING *", (r["project_id"], r["org_id"], r["kind"], name[:200], r["notes"], Jsonb(r["params"]), user["id"]))
    return _out(await cur.fetchone())


@router.post("/scenarios/{sid}/run", summary="Run a scenario: its layer is made (or updated) and its figures kept")
async def run(sid: UUID, user=Depends(EDIT), conn=Depends(get_conn)):
    r = await _one(conn, sid, user)
    p = r["params"] or {}
    layers, feats = {}, {}
    for lid in simulations.layer_refs(r["kind"], p):
        try:
            lay = await layer_of(conn, UUID(lid), user)
        except (ValueError, Problem):
            raise Problem(422, "A layer this scenario uses has been removed: choose another.", code="validation") from None
        if str(lay["project_id"]) != str(r["project_id"]):
            raise Problem(422, f"{lay['name']} is in another project.", code="validation")
        layers[lid] = lay
        if lay["geometry_type"] != "Raster":
            feats[lid] = await _features(conn, lay, None)
    inp = raster_tools.Inputs(layers, feats)
    started = time.monotonic()

    def work():
        out = simulations.RUN[r["kind"]](p, inp)
        meta = rasters.save(out.data, out.crs, out.transform, nodata=out.nodata, dtype=out.dtype, names=out.names, categorical=out.categorical)
        return out, meta
    try:
        out, meta = await anyio.to_thread.run_sync(work)
    except (Problem, rasters.RasterError, MemoryError, ValueError) as e:
        msg = e.detail if isinstance(e, Problem) else ("The scenario needs more memory than the server has: use larger cells or a smaller area."
                                                       if isinstance(e, MemoryError) else str(e))
        await conn.execute("UPDATE scenarios SET status = 'failed', error = %s, updated_at = now() WHERE id = %s", (msg, sid))
        await conn.commit()
        raise Problem(422, msg, code="scenario_failed") from None
    ms = int((time.monotonic() - started) * 1000)
    style = out.style or rasters.default_style(meta)
    source = {"tool": f"scenario:{r['kind']}", "tool_name": simulations.KINDS[r["kind"]], "scenario_id": str(sid), "crs": meta["crs_name"],
              "summary": out.summary}
    name = r["name"][:200]
    old = None
    if r["layer_id"]:
        cur = await conn.execute("SELECT id, raster FROM layers WHERE id = %s", (r["layer_id"],))
        old = await cur.fetchone()
    async with conn.transaction():
        if old:                                         # the same layer, redrawn: anything pointing at it keeps working
            await conn.execute("UPDATE layers SET name = %s, raster = %s, style = %s, source = %s, updated_at = clock_timestamp() WHERE id = %s",
                               (name, Jsonb(meta), Jsonb(style), Jsonb(source), old["id"]))
            lid = old["id"]
        else:
            lid = await _insert(conn, r["project_id"], user, name, meta, style, source)
        cur = await conn.execute("UPDATE scenarios SET status = 'ready', error = NULL, summary = %s, layer_id = %s, ran_at = now(), run_ms = %s, "
                                 "updated_at = now() WHERE id = %s RETURNING *", (Jsonb(out.summary), lid, ms, sid))
        row = await cur.fetchone()
    if old and old["raster"] and old["raster"].get("key") != meta["key"]:
        rasters.remove(old["raster"].get("key"))
    await audit(conn, user, "scenario.run", "scenario", sid, name=name, kind=r["kind"], ms=ms)
    await _touch(conn, lid, r["project_id"])
    return {"scenario": _out(row), "layer": await layer_of(conn, lid, user)}


@router.delete("/scenarios/{sid}", status_code=204, summary="Delete a scenario (its layer stays on the map)")
async def delete(sid: UUID, user=Depends(EDIT), conn=Depends(get_conn)):
    r = await _one(conn, sid, user)
    await conn.execute("DELETE FROM scenarios WHERE id = %s", (sid,))
    await audit(conn, user, "scenario.delete", "scenario", sid, name=r["name"])
    return Response(status_code=204)
