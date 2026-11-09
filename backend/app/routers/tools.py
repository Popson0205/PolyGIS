"""Running geoprocessing tools. A run makes a new layer in the project and is kept in the project's history, failed
runs included (with what went wrong), so a result can always be traced to the tool and settings that made it."""
import time
from uuid import UUID

import math

import psycopg
import shapely
from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import tools
from ..audit import audit
from ..config import settings
from ..db import get_conn
from ..errors import Problem
from ..identity import need
from .layers import default_style, layer_of, _next_position, _touch
from .projects import project_of

router = APIRouter(tags=["geoprocessing"])


class RunIn(BaseModel):
    params: dict = Field(default_factory=dict, description="The tool's parameters; a layer parameter X may come with X_ids, "
                                                          "the selected features to use")
    output_name: str | None = Field(None, max_length=200)


@router.get("/tools", summary="The geoprocessing tools and their parameters")
async def list_tools(user=Depends(need())):
    return tools.catalog()


@router.get("/projects/{project_id}/runs", summary="The project's geoprocessing history, latest first")
async def runs(project_id: UUID, user=Depends(need()), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    cur = await conn.execute("SELECT * FROM runs WHERE project_id = %s ORDER BY id DESC LIMIT 200", (project_id,))
    return await cur.fetchall()


@router.post("/projects/{project_id}/tools/{tool}", status_code=201, summary="Run a tool; its result is a new layer")
async def run(project_id: UUID, tool: str, body: RunIn,
              user=Depends(need("editor", write=True, feature="geoprocessing")), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    spec = tools.TOOLS.get(tool)
    if not spec:
        raise Problem(404, f"No tool called {tool}.", code="not_found")
    params = dict(body.params)
    layers: dict = {}
    for p in spec["params"]:
        v = params.get(p["name"])
        if p["type"] == "layer":
            if not v:
                raise Problem(422, f"Choose the {p['label'].lower()}.", code="validation", fields={"params": [p["name"]]})
            lay = await layer_of(conn, v, user)
            if str(lay["project_id"]) != str(project_id):
                raise Problem(422, f"{lay['name']} is in another project.", code="validation", fields={"params": [p["name"]]})
            if lay["geometry_type"] == "Raster":
                raise Problem(422, f"{lay['name']} is a raster: use the raster tools for it.", code="validation", fields={"params": [p["name"]]})
            if p.get("geometry"):
                tools.need_kind(lay, p["geometry"], p["label"])
            layers[p["name"]] = lay
        elif p["type"] == "layers":
            ids = v or []
            if len(ids) < p.get("min", 1):
                raise Problem(422, f"Choose at least {p.get('min', 1)} layers.", code="validation", fields={"params": [p["name"]]})
            layers[p["name"]] = [await layer_of(conn, x, user) for x in ids]
        elif p["type"] == "field" and v:
            of = layers.get(p.get("of", "input"))
            if of and v not in {f["name"] for f in of["fields"]}:
                raise Problem(422, f"{of['name']} has no field called {v}.", code="validation", fields={"params": [p["name"]]})
    built = tools.build(tool, params, layers)
    out_name = (body.output_name or built.name).strip()[:200] or built.name
    started = time.monotonic()
    pos = await _next_position(conn, project_id)
    try:
        async with conn.transaction():                       # a savepoint: a failed tool leaves no half-made layer
            await conn.execute(f"SET LOCAL statement_timeout = '{int(settings.tool_timeout_s * 1000)}'")
            cur = await conn.execute(
                "INSERT INTO layers (project_id, org_id, name, geometry_type, fields, style, position, source, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (project_id, user["org_id"], out_name, built.kind, Jsonb(built.fields), Jsonb(default_style(built.kind, pos)), pos,
                 Jsonb({"tool": tool, "tool_name": spec["name"]}), user["id"]))
            lid = (await cur.fetchone())["id"]
            if built.compute:                                  # worked out in Python (statistics, networks)
                made = await built.compute(conn)
                if len(made) > settings.max_features:
                    raise Problem(422, f"The result has {len(made):,} features, more than the {settings.max_features:,} a layer may hold.",
                                  code="too_many")
                n = 0
                if made:
                    async with conn.cursor() as c:
                        await c.executemany(
                            f"INSERT INTO features (layer_id, geom, props, updated_by) SELECT %s, ST_Multi(q.g), %s, %s FROM "
                            f"(SELECT ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromWKB(%s), 4326)), {tools.TYPE_NO[built.kind]}) AS g) q "
                            f"WHERE NOT ST_IsEmpty(q.g)",
                            [(lid, Jsonb(_plain(p)), user["id"], shapely.to_wkb(g)) for g, p in made if g is not None and not g.is_empty])
                    cur = await conn.execute("SELECT count(*) AS n FROM features WHERE layer_id = %s", (lid,))
                    n = (await cur.fetchone())["n"]
            else:
                cur = await conn.execute(
                    f"INSERT INTO features (layer_id, geom, props, updated_by) "
                    f"SELECT %(out)s, ST_Multi(q.g), COALESCE(q.p, '{{}}'::jsonb), %(actor)s FROM ({built.sql}) q "
                    f"WHERE q.g IS NOT NULL AND NOT ST_IsEmpty(q.g)",
                    {**built.args, "out": lid, "actor": user["id"]})
                n = cur.rowcount
            if n > settings.max_features:
                raise Problem(422, f"The result has {n:,} features, more than the {settings.max_features:,} a layer may hold.",
                              code="too_many")
            # single parts stay single: ST_Multi above only keeps mixed parts storable; undo it where one part remains
            await conn.execute("UPDATE features SET geom = ST_GeometryN(geom, 1) WHERE layer_id = %s AND ST_NumGeometries(geom) = 1", (lid,))
            await conn.execute("SET LOCAL statement_timeout = 0")
    except (psycopg.Error, Problem) as e:
        ms = int((time.monotonic() - started) * 1000)
        why = e.detail if isinstance(e, Problem) else _db_words(e)
        await conn.execute("INSERT INTO runs (project_id, org_id, tool, params, output_name, status, message, ms, actor_id, actor_name) "
                           "VALUES (%s, %s, %s, %s, %s, 'failed', %s, %s, %s, %s)",
                           (project_id, user["org_id"], tool, Jsonb(params), out_name, why, ms, user["id"], user["name"]))
        p = e if isinstance(e, Problem) else Problem(422, f"{spec['name']} did not finish: {why}", code="tool_failed")
        return JSONResponse(jsonable_encoder(p.body()), status_code=p.status, media_type="application/problem+json")
    ms = int((time.monotonic() - started) * 1000)
    cur = await conn.execute(
        "INSERT INTO runs (project_id, org_id, tool, params, output_layer_id, output_name, status, feature_count, ms, actor_id, actor_name) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'done', %s, %s, %s, %s) RETURNING *",
        (project_id, user["org_id"], tool, Jsonb(params), lid, out_name, n, ms, user["id"], user["name"]))
    r = await cur.fetchone()
    await audit(conn, user, "tool.run", "layer", lid, name=out_name, tool=spec["name"], features=n)
    await _touch(conn, lid, project_id)
    return {"run": r, "layer": await layer_of(conn, lid, user)}


def _plain(p: dict) -> dict:
    """Props as plain JSON: numpy numbers to Python ones, NaN and infinity to null."""
    out = {}
    for k, v in p.items():
        if hasattr(v, "item"):
            v = v.item()
        if isinstance(v, float) and not math.isfinite(v):
            v = None
        out[k] = v
    return out


def _db_words(e: psycopg.Error) -> str:
    if isinstance(e, psycopg.errors.QueryCanceled):
        return f"it ran longer than {settings.tool_timeout_s} seconds. Try it on fewer features (a selection) or a smaller area."
    msg = (getattr(e.diag, "message_primary", None) or str(e)).strip()
    return msg[:300]
