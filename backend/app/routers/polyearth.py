"""Poly Earth: Earth Engine-style analyses run on open data. A run can take minutes (it reads many scenes), so it runs in the
background: starting one returns a job, and the job tells how far along it is until its layers (and chart, or accuracy
report) are ready. Runs are kept in the project's geoprocessing history, failed ones too."""
import asyncio
import datetime as dt
import logging
import time
import uuid
from uuid import UUID

import anyio
import psycopg
import shapely
from fastapi import APIRouter, Depends
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import polyearth, raster_tools, rasters
from ..audit import audit
from ..config import settings
from ..db import get_conn, pool
from ..errors import Problem
from ..imagery import ImageryError
from ..identity import need
from .layers import _copy_features, _next_position, _touch, default_style, layer_of
from .projects import project_of
from .rasters import _features, _insert, _plain

log = logging.getLogger("polyearth")
router = APIRouter(tags=["poly earth"])
JOBS: dict[str, dict] = {}
KEEP_S = 3 * 3600
_slots: asyncio.Semaphore | None = None
_tasks: set = set()


class RunIn(BaseModel):
    params: dict = Field(default_factory=dict, description="The tool's settings; a layer setting X may come with X_ids, the selected features to use")
    output_name: str | None = Field(None, max_length=200)


@router.get("/poly-earth/tools", summary="The Poly Earth tools and their settings")
async def tools(user=Depends(need())):
    return polyearth.catalog()


def _public(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}


@router.get("/poly-earth/jobs/{job_id}", summary="How a Poly Earth run is going, and its results when done")
async def job(job_id: str, user=Depends(need())):
    j = JOBS.get(job_id)
    if not j or j["_user"] != user["id"]:
        raise Problem(404, "That run is not known (runs are kept for three hours).", code="not_found")
    return _public(j)


@router.post("/projects/{project_id}/poly-earth/{tool}", status_code=202, summary="Start a Poly Earth run; poll its job for the result")
async def start(project_id: UUID, tool: str, body: RunIn, user=Depends(need("editor", write=True, feature="raster")), conn=Depends(get_conn)):
    global _slots
    await project_of(conn, project_id, user)
    spec = polyearth.ETOOLS.get(tool)
    if not spec:
        raise Problem(404, f"No tool called {tool}.", code="not_found")
    params = dict(body.params)
    today = dt.date.today()
    for p in spec["params"]:
        if params.get(p["name"]) in (None, "") and "default" in p:
            d = p["default"]
            params[p["name"]] = (today - dt.timedelta(days=d["ago"])).isoformat() if isinstance(d, dict) and "ago" in d else d
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
            want = p.get("geometry") or []
            if lay["geometry_type"] not in want:
                words = {"Point": "points", "LineString": "lines", "Polygon": "polygons", "Raster": "a raster"}
                raise Problem(422, f"{p['label'].split(' (')[0]} must be {' or '.join(words[k] for k in want)}; {lay['name']} is {words[lay['geometry_type']]}.",
                              code="validation", fields={"params": [p["name"]]})
            layers[p["name"]] = lay
            ids = params.get(f"{p['name']}_ids")
            feats[p["name"]] = await _features(conn, lay, [int(x) for x in ids] if ids else None)
        elif p["type"] == "field" and v:
            of = layers.get(p.get("of", "input"))
            if of and v not in {f["name"] for f in of["fields"]}:
                raise Problem(422, f"{of['name']} has no field called {v}.", code="validation", fields={"params": [p["name"]]})
    clip = None
    if params.get("clip") and any(p["type"] == "area" for p in spec["params"]):
        lay = await layer_of(conn, params["clip"], user)
        if str(lay["project_id"]) != str(project_id):
            raise Problem(422, f"{lay['name']} is in another project.", code="validation", fields={"params": ["bbox"]})
        if lay["geometry_type"] != "Polygon":
            raise Problem(422, f"The area to clip to must be polygons; {lay['name']} is not.", code="validation", fields={"params": ["bbox"]})
        ids = params.get("clip_ids")
        fs = await _features(conn, lay, [int(x) for x in ids] if ids else None)
        if not fs:
            raise Problem(422, f"{lay['name']} has no polygons.", code="validation", fields={"params": ["bbox"]})
        if len(fs) > 5000:
            raise Problem(422, "Clip to 5,000 polygons or fewer: select some of them first.", code="validation", fields={"params": ["bbox"]})
        clip = shapely.make_valid(shapely.union_all([f["geom"] for f in fs]))
        w, s_, e, n = clip.bounds
        pad = 0.0003                                       # a few cells round the polygons, so their edges are whole
        params["bbox"] = [max(-180, w - pad), max(-85, s_ - pad), min(180, e + pad), min(85, n + pad)]
        params["clip_name"] = lay["name"] + (f" ({len(fs)} selected)" if ids else "")
    now = time.time()
    for k in [k for k, j in JOBS.items() if now - j["_at"] > KEEP_S]:
        JOBS.pop(k, None)
    if sum(1 for j in JOBS.values() if j["_user"] == user["id"] and j["status"] in ("queued", "running", "stopping")) >= 3:
        raise Problem(429, "You have three Poly Earth runs going already: wait for one to finish.", code="too_many")
    jid = uuid.uuid4().hex
    JOBS[jid] = {"id": jid, "tool": tool, "tool_name": spec["name"], "status": "queued", "message": "Waiting for a free slot", "progress": 0.0,
                 "layers": [], "chart": None, "report": None, "notes": [], "error": None, "ms": None,
                 "_user": user["id"], "_at": now}
    _slots = _slots or asyncio.Semaphore(max(1, settings.earth_jobs))
    ctx = polyearth.Ctx(params, raster_tools.Inputs(layers, feats), clip=clip)
    JOBS[jid]["_ctx"] = ctx
    task = asyncio.create_task(_run(jid, project_id, tool, spec, params, body.output_name, ctx, user))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return _public(JOBS[jid])


async def _run(jid: str, project_id: UUID, tool: str, spec: dict, params: dict, output_name: str | None, ctx: polyearth.Ctx, user: dict):
    j = JOBS[jid]

    def say(message: str, done: float | None = None):
        ctx.check()
        j["_last"] = time.monotonic()
        j["message"] = message
        if done is not None:
            j["progress"] = round(max(j["progress"], min(0.99, float(done))), 3)
    ctx.say = say

    def work():
        polyearth.set_clip(ctx.clip)
        try:
            res = polyearth.EBUILD[tool](params, ctx)
        finally:
            polyearth.set_clip(None)
        say("Saving the results", 0.97)
        metas = [rasters.save(o.data, o.crs, o.transform, nodata=o.nodata, dtype=o.dtype, names=o.names, categorical=o.categorical) if o.kind == "raster" else None
                 for o in res.outs]
        return res, metas

    assert _slots is not None
    async with _slots:
        started = time.monotonic()
        res = metas = None
        if ctx.stop.is_set():                                       # stopped while waiting for a slot
            await _stopped(j, project_id, tool, spec, params, output_name, user, 0)
            return
        j["status"], j["message"], j["started"] = "running", "Starting", time.time()
        j["_last"] = time.monotonic()
        why_stopped: list[str] = []

        async def watch():
            """No fixed time limit while the run keeps making progress: it is stopped only when nothing has moved for
            EARTH_STALL_S (a stuck file or step), or after EARTH_TIMEOUT_S in all."""
            while True:
                await anyio.sleep(2)
                now = time.monotonic()
                if now - j["_last"] > settings.earth_stall_s:
                    why_stopped.append(f"it made no progress for {_words(settings.earth_stall_s)} (a source may not be answering). "
                                       "Try again, or choose a smaller area or a shorter period.")
                elif settings.earth_timeout_s and now - started > settings.earth_timeout_s:
                    why_stopped.append(f"it was still running after {_words(settings.earth_timeout_s)}: choose a smaller area, "
                                       "a shorter period or fewer scenes.")
                if why_stopped:
                    ctx.stop.set()
                    return
        try:
            watcher = asyncio.create_task(watch())
            try:
                res, metas = await anyio.to_thread.run_sync(work)
            finally:
                watcher.cancel()
            ctx.check()                                              # stopped while saving the files: keep nothing
            made = await _save(project_id, tool, spec, params, output_name, res, metas, user, int((time.monotonic() - started) * 1000))
            j.update(status="done", progress=1.0, message="Done", layers=made, chart=res.chart, report=res.report, notes=res.notes,
                     ms=int((time.monotonic() - started) * 1000))
            return
        except polyearth.Stopped:
            if why_stopped:                                          # stopped by the watch, not the person: a failure
                why = why_stopped[0]
            else:
                for m in metas or []:
                    if m:
                        rasters.remove(m["key"])
                await _stopped(j, project_id, tool, spec, params, output_name, user, int((time.monotonic() - started) * 1000))
                return
        except Problem as e:
            why = e.detail or "it did not finish."
        except ImageryError as e:
            why = str(e)
        except MemoryError:
            why = "it needs more memory than the server has: choose a smaller area, a shorter period or fewer scenes."
        except Exception as e:  # noqa: BLE001
            log.exception("Poly Earth %s failed", tool)
            why = f"something went wrong ({type(e).__name__}). Try again; if it keeps happening, choose a smaller area or other dates."
        for m in metas or []:
            if m:
                rasters.remove(m["key"])
        ms = int((time.monotonic() - started) * 1000)
        j.update(status="failed", message="Did not finish", error=why, ms=ms)
        try:
            async with pool.connection() as conn:
                await conn.execute("INSERT INTO runs (project_id, org_id, tool, params, output_name, status, message, ms, actor_id, actor_name) "
                                   "VALUES (%s, %s, %s, %s, %s, 'failed', %s, %s, %s, %s)",
                                   (project_id, user["org_id"], f"earth:{tool}", Jsonb(_plain(params)), output_name or spec["name"], why, ms, user["id"], user["name"]))
        except psycopg.Error:
            log.exception("could not record the failed run")


def _words(s: int) -> str:
    return f"{s // 3600} hours" if s >= 7200 else f"{s // 60} minutes" if s >= 120 else f"{s} seconds"


async def _stopped(j: dict, project_id, tool, spec, params, output_name, user, ms: int) -> None:
    j.update(status="stopped", message="Stopped", error=None, ms=ms)
    try:
        async with pool.connection() as conn:
            await conn.execute("INSERT INTO runs (project_id, org_id, tool, params, output_name, status, message, ms, actor_id, actor_name) "
                               "VALUES (%s, %s, %s, %s, %s, 'failed', %s, %s, %s, %s)",
                               (project_id, user["org_id"], f"earth:{tool}", Jsonb(_plain(params)), output_name or spec["name"],
                                f"Stopped by {user['name']}.", ms, user["id"], user["name"]))
    except psycopg.Error:
        log.exception("could not record the stopped run")


@router.post("/poly-earth/jobs/{job_id}/stop", summary="Stop a Poly Earth run; nothing it made is kept")
async def stop(job_id: str, user=Depends(need())):
    j = JOBS.get(job_id)
    if not j or j["_user"] != user["id"]:
        raise Problem(404, "That run is not known (runs are kept for three hours).", code="not_found")
    if j["status"] in ("queued", "running"):
        j["_ctx"].stop.set()
        j.update(status="stopping", message="Stopping: finishing the files being read")
    return _public(j)


async def _save(project_id, tool, spec, params, output_name, res: polyearth.Result, metas, user, ms) -> list[dict]:
    """The results as layers of the project (all or none), and one run in the history."""
    made: list = []
    try:
        async with pool.connection() as conn, conn.transaction():
            for k, (o, meta) in enumerate(zip(res.outs, metas)):
                name = (((output_name or "").strip() + (f" ({o.name})" if len(res.outs) > 1 else "")) if (output_name or "").strip() else o.name)[:200]
                source = {"tool": f"earth:{tool}", "tool_name": f"Poly Earth: {spec['name']}", **({"summary": _plain(o.summary)} if o.summary else {})}
                if meta:
                    style = o.style or rasters.default_style(meta)
                    lid = await _insert(conn, project_id, user, name, meta, style, {**source, "crs": meta["crs_name"]})
                    n = None
                else:
                    if len(o.rows) > settings.max_features:
                        raise Problem(422, f"The result has {len(o.rows):,} features, more than the {settings.max_features:,} a layer may hold.")
                    pos = await _next_position(conn, project_id)
                    cur = await conn.execute(
                        "INSERT INTO layers (project_id, org_id, name, geometry_type, fields, style, position, source, created_by) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                        (project_id, user["org_id"], name, o.geometry_type, Jsonb(o.fields), Jsonb(default_style(o.geometry_type, pos)), pos,
                         Jsonb(source), user["id"]))
                    lid = (await cur.fetchone())["id"]
                    good = [(g, _plain(pp)) for g, pp in o.rows if g is not None and not g.is_empty]
                    await _copy_features(conn, lid, [g for g, _ in good], [pp for _, pp in good], user["id"])
                    n = len(good)
                made.append((lid, name, n))
            first = made[0] if made else (None, spec["name"], None)
            await conn.execute(
                "INSERT INTO runs (project_id, org_id, tool, params, output_layer_id, output_name, status, feature_count, ms, actor_id, actor_name) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'done', %s, %s, %s, %s)",
                (project_id, user["org_id"], f"earth:{tool}", Jsonb(_plain(params)), first[0], ", ".join(x[1] for x in made) or spec["name"], first[2], ms,
                 user["id"], user["name"]))
            for lid, name, n in made:
                await audit(conn, user, "tool.run", "layer", lid, name=name, tool=f"Poly Earth: {spec['name']}", features=n)
                await _touch(conn, lid, project_id)
            return [await layer_of(conn, lid, user) for lid, _, _ in made]
    except BaseException:
        for m in metas:
            if m:
                rasters.remove(m["key"])
        raise
