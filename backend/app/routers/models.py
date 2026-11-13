"""Model builder: chains of geoprocessing and raster tools, saved for the organization and run again on other layers,
as ArcGIS's ModelBuilder does without the scripting.

A model names its inputs (e.g. "parcels", a polygon layer). Each step is one tool; its layer parameters take
"input:<name>" or "step:<id>" (the result of an earlier step), and its other parameters are fixed values. Running it
on a project's layers runs the steps in order, each exactly as the tool would run by itself (so each is in the
project's history), and keeps the final result, or every step's result."""
import json
import re
import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import raster_tools, tools
from ..audit import audit
from ..db import get_conn
from ..errors import Problem
from ..identity import need
from . import rasters as rasters_router
from . import tools as tools_router
from .layers import layer_of
from .projects import project_of

router = APIRouter(tags=["model builder"])
NAME = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
KINDS = {"Point", "LineString", "Polygon", "Raster"}


class ModelInput(BaseModel):
    name: str = Field(min_length=1, max_length=31)
    label: str = Field("", max_length=100)
    kinds: list[Literal["Point", "LineString", "Polygon", "Raster"]] = Field(default_factory=lambda: ["Point", "LineString", "Polygon"], min_length=1)


class ModelStep(BaseModel):
    id: str = Field(min_length=1, max_length=20, pattern=r"^[a-z0-9_]+$")
    tool: str = Field(min_length=1, max_length=80)
    kind: Literal["vector", "raster"] = "vector"
    params: dict = Field(default_factory=dict)
    output_name: str = Field("", max_length=200)
    keep: bool = Field(False, description="Keep this step's result even when only the final result is kept")


class ModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=4000)
    inputs: list[ModelInput] = Field(default_factory=list, max_length=20)
    steps: list[ModelStep] = Field(default_factory=list, max_length=50)


class ModelPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    inputs: list[ModelInput] | None = Field(None, max_length=20)
    steps: list[ModelStep] | None = Field(None, max_length=50)


class RunModelIn(BaseModel):
    inputs: dict[str, UUID] = Field(default_factory=dict, description="Each input's layer in the project")
    keep: Literal["final", "all"] = "final"


def _spec(step: dict) -> dict:
    spec = (raster_tools.RTOOLS if step["kind"] == "raster" else tools.TOOLS).get(step["tool"])
    if not spec:
        raise Problem(422, f"Step {step['id']}: there is no {'raster ' if step['kind'] == 'raster' else ''}tool called {step['tool']}.",
                      code="validation", fields={"steps": [step["id"]]})
    return spec


def check(inputs: list[dict], steps: list[dict]) -> None:
    """A model that can run: names are unique, every layer parameter is an input or an earlier step, nothing loops."""
    names = [i["name"] for i in inputs]
    for n in names:
        if not NAME.match(n):
            raise Problem(422, f"“{n}” cannot name an input: lower-case letters, digits and _ (e.g. parcels).", code="validation", fields={"inputs": [n]})
    if len(set(names)) != len(names):
        raise Problem(422, "Two inputs have the same name.", code="validation", fields={"inputs": ["twice"]})
    ids = [s["id"] for s in steps]
    if len(set(ids)) != len(ids):
        raise Problem(422, "Two steps have the same id.", code="validation", fields={"steps": ["twice"]})
    for k, s in enumerate(steps):
        spec = _spec(s)
        earlier = set(ids[:k])
        for p in spec["params"]:
            if p["type"] not in ("layer", "layers"):
                continue
            v = s["params"].get(p["name"])
            refs = v if isinstance(v, list) else ([v] if v else [])
            if not refs and not p.get("optional") and p["type"] == "layer":
                raise Problem(422, f"Step {s['id']} ({spec['name']}): choose its {p['label'].lower()}.", code="validation", fields={"steps": [s["id"]]})
            for r in refs:
                kind, _, name = str(r).partition(":")
                if not ((kind == "input" and name in names) or (kind == "step" and name in earlier)):
                    why = "a later step" if kind == "step" and name in ids else "nothing in the model"
                    raise Problem(422, f"Step {s['id']} ({spec['name']}): its {p['label'].lower()} refers to {why} ({r}).", code="validation",
                                  fields={"steps": [s["id"]]})


async def _model(conn, model_id, user) -> dict:
    cur = await conn.execute("SELECT * FROM models WHERE id = %s AND org_id = %s", (model_id, user["org_id"]))
    m = await cur.fetchone()
    if not m:
        raise Problem(404, "Model not found.", code="not_found")
    return m


async def _drop(conn, lid: str) -> None:
    """An intermediate result not kept: the layer and, for a raster, its file."""
    cur = await conn.execute("DELETE FROM layers WHERE id = %s RETURNING raster", (lid,))
    r = await cur.fetchone()
    if r and r.get("raster"):
        import anyio

        from .. import rasters
        await anyio.to_thread.run_sync(rasters.remove, r["raster"].get("key"))


@router.get("/models", summary="The organization's models, latest first")
async def list_models(user=Depends(need()), conn=Depends(get_conn)):
    cur = await conn.execute("SELECT * FROM models WHERE org_id = %s ORDER BY updated_at DESC", (user["org_id"],))
    return await cur.fetchall()


@router.post("/models", status_code=201, summary="Save a model")
async def create_model(body: ModelIn, user=Depends(need("editor", write=True, feature="geoprocessing")), conn=Depends(get_conn)):
    d = body.model_dump()
    check(d["inputs"], d["steps"])
    cur = await conn.execute(
        "INSERT INTO models (org_id, name, description, inputs, steps, created_by, created_by_name, updated_by_name) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (user["org_id"], body.name.strip(), body.description.strip(), Jsonb(d["inputs"]), Jsonb(d["steps"]), user["id"], user["name"], user["name"]))
    m = await cur.fetchone()
    await audit(conn, user, "model.create", "model", m["id"], name=m["name"], steps=len(d["steps"]))
    return m


@router.patch("/models/{model_id}", summary="Change a model")
async def update_model(model_id: UUID, body: ModelPatch, user=Depends(need("editor", write=True, feature="geoprocessing")), conn=Depends(get_conn)):
    m = await _model(conn, model_id, user)
    d = body.model_dump(exclude_none=True)
    inputs, steps = d.get("inputs", m["inputs"]), d.get("steps", m["steps"])
    check(inputs, steps)
    cur = await conn.execute(
        "UPDATE models SET name = %s, description = %s, inputs = %s, steps = %s, updated_by_name = %s, updated_at = now() WHERE id = %s RETURNING *",
        ((body.name or m["name"]).strip(), (body.description if body.description is not None else m["description"]).strip(),
         Jsonb(inputs), Jsonb(steps), user["name"], model_id))
    return await cur.fetchone()


@router.delete("/models/{model_id}", status_code=204, summary="Delete a model (the layers it made stay)")
async def delete_model(model_id: UUID, user=Depends(need("editor", write=True, feature="geoprocessing")), conn=Depends(get_conn)):
    m = await _model(conn, model_id, user)
    await conn.execute("DELETE FROM models WHERE id = %s", (model_id,))
    await audit(conn, user, "model.delete", "model", model_id, name=m["name"])
    return Response(status_code=204)


@router.post("/projects/{project_id}/models/{model_id}/run", summary="Run a model on the project's layers")
async def run_model(project_id: UUID, model_id: UUID, body: RunModelIn,
                    user=Depends(need("editor", write=True, feature="geoprocessing")), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    m = await _model(conn, model_id, user)
    check(m["inputs"], m["steps"])
    if not m["steps"]:
        raise Problem(422, f"{m['name']} has no steps yet.", code="validation")
    if any(s["kind"] == "raster" for s in m["steps"]) and "raster" not in (user.get("features") or []):
        raise Problem(403, "This model uses raster tools, which are in PolyGIS Advanced.", code="not_in_plan")
    bound: dict[str, str] = {}
    for i in m["inputs"]:
        lid = body.inputs.get(i["name"])
        if not lid:
            raise Problem(422, f"Choose the layer for {i['label'] or i['name']}.", code="validation", fields={"inputs": [i["name"]]})
        lay = await layer_of(conn, lid, user)
        if str(lay["project_id"]) != str(project_id):
            raise Problem(422, f"{lay['name']} is in another project.", code="validation", fields={"inputs": [i["name"]]})
        if lay["geometry_type"] not in i["kinds"]:
            raise Problem(422, f"{i['label'] or i['name']} takes {', '.join(i['kinds'])}; {lay['name']} is {lay['geometry_type']}.", code="validation",
                          fields={"inputs": [i["name"]]})
        bound[f"input:{i['name']}"] = str(lid)
    made, done = [], []
    started = time.monotonic()
    for k, s in enumerate(m["steps"]):
        spec = _spec(s)
        params = dict(s["params"])
        for p in spec["params"]:
            if p["type"] == "layer" and params.get(p["name"]):
                params[p["name"]] = bound[params[p["name"]]]
            elif p["type"] == "layers":
                params[p["name"]] = [bound[r] for r in params.get(p["name"]) or []]
        name = (s.get("output_name") or "").strip() or f"{m['name']}: {spec['name']}"
        t0 = time.monotonic()
        try:
            if s["kind"] == "raster":
                out = await rasters_router.run(project_id, s["tool"], rasters_router.RunIn(params=params, output_name=name[:200]), user, conn)
            else:
                out = await tools_router.run(project_id, s["tool"], tools_router.RunIn(params=params, output_name=name[:200]), user, conn)
        except Problem as e:                     # the tool refused its settings (a field the layer does not have, say)
            out = e
        step = {"id": s["id"], "tool": s["tool"], "name": spec["name"], "ms": int((time.monotonic() - t0) * 1000)}
        if isinstance(out, (JSONResponse, Problem)):
            step["error"] = out.detail if isinstance(out, Problem) else json.loads(out.body).get("detail", "It did not finish.")
            done.append(step)
            if body.keep == "final":
                for lid in made:
                    await _drop(conn, lid)
            await audit(conn, user, "model.run", "model", model_id, name=m["name"], status="failed", step=s["id"])
            return {"status": "failed", "failed_step": s["id"], "message": f"Step {k + 1} ({spec['name']}) did not finish: {step['error']}",
                    "steps": done, "layers": [] if body.keep == "final" else [await layer_of(conn, x, user) for x in made]}
        lid = str(out["layer"]["id"])
        bound[f"step:{s['id']}"] = lid
        made.append(lid)
        done.append({**step, "layer_id": lid, "features": out["layer"].get("feature_count")})
    keep = made if body.keep == "all" else [made[-1], *[bound[f"step:{s['id']}"] for s in m["steps"][:-1] if s.get("keep")]]
    for lid in made:
        if lid not in keep:
            await _drop(conn, lid)
    await conn.execute("UPDATE models SET runs = runs + 1 WHERE id = %s", (model_id,))
    await audit(conn, user, "model.run", "model", model_id, name=m["name"], status="done", steps=len(made), ms=int((time.monotonic() - started) * 1000))
    return {"status": "done", "steps": done, "layers": [await layer_of(conn, x, user) for x in keep], "ms": int((time.monotonic() - started) * 1000)}
