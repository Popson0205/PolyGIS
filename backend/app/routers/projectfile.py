"""Project files: save a whole project to the person's computer as one ``.polygis`` file, and open it again later —
the way a desktop GIS keeps a project on disk. The file is a zip holding project.json (the project, its maps,
geodatabases and layer definitions) and one GeoPackage per geodatabase with the features. Vector layers travel in
full; raster layers stay in server storage and are listed but not bundled (noted on export)."""
import io
import json
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import shapely
from fastapi import APIRouter, Depends, Response, UploadFile
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ..audit import audit
from ..db import get_conn
from ..errors import Problem
from ..identity import need

router = APIRouter(tags=["project files"])

EXT = ".polygis"
MAX_FILE_MB = 200


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip() or "project"


@router.get("/projects/{project_id}/file", summary="Download the project as a .polygis file", response_class=Response)
async def export_project(project_id: UUID, user=Depends(need()), conn=Depends(get_conn)):
    from .. import gisdata
    pr = await (await conn.execute("SELECT * FROM projects WHERE id = %s AND org_id = %s", (project_id, user["org_id"]))).fetchone()
    if not pr:
        raise Problem(404, "Project not found.", code="not_found")
    maps = await (await conn.execute("SELECT id, name, position, view FROM maps WHERE project_id = %s ORDER BY position, created_at", (project_id,))).fetchall()
    gdbs = await (await conn.execute("SELECT id, name, is_default FROM geodatabases WHERE project_id = %s AND storage = 'postgis' ORDER BY is_default DESC, created_at", (project_id,))).fetchall()
    mkey = {m["id"]: f"m{i}" for i, m in enumerate(maps)}
    gkey = {g["id"]: f"g{i}" for i, g in enumerate(gdbs)}

    manifest = {
        "polygis": 1, "exported_at": datetime.now(timezone.utc).isoformat(),
        "project": {"name": pr["name"], "description": pr["description"], "display_epsg": pr["display_epsg"],
                    "view": pr["view"], "active_map": mkey.get(pr["active_map_id"])},
        "maps": [{"key": mkey[m["id"]], "name": m["name"], "position": m["position"], "view": m["view"]} for m in maps],
        "geodatabases": [{"key": gkey[g["id"]], "name": g["name"], "is_default": g["is_default"]} for g in gdbs],
        "layers": [],
    }
    buf = io.BytesIO()
    rasters_skipped = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for g in gdbs:
            lyrs = await (await conn.execute(
                "SELECT id, name, geometry_type, fields, style, labels, visible, position, source, raster, map_id "
                "FROM layers WHERE geodatabase_id = %s ORDER BY position, created_at", (g["id"],))).fetchall()
            items: list[tuple] = []
            for j, l in enumerate(lyrs):
                entry = {"name": l["name"], "geometry_type": l["geometry_type"], "fields": l["fields"],
                         "style": l["style"], "labels": l["labels"], "visible": l["visible"], "position": l["position"],
                         "source": l["source"], "gdb": gkey[g["id"]], "map": mkey.get(l["map_id"])}
                if l["geometry_type"] == "Raster":
                    rasters_skipped += 1
                    continue   # raster pixels stay in server storage; not bundled in v1
                key = f"l{j}"
                fc = await (await conn.execute(
                    "SELECT ST_AsBinary(geom) AS wkb, props FROM features WHERE layer_id = coalesce((SELECT data_id FROM layers WHERE id = %s), %s) ORDER BY id",
                    (l["id"], l["id"]))).fetchall()
                rows = [(0, shapely.from_wkb(bytes(r["wkb"])), r["props"]) for r in fc]
                if rows:
                    items.append((key, l["fields"] or [], rows))
                    entry["data"] = f"data/{gkey[g['id']]}.gpkg"
                    entry["data_layer"] = key
                manifest["layers"].append(entry)
            if items:
                z.writestr(f"data/{gkey[g['id']]}.gpkg", gisdata.pack_geopackage(items))
        manifest["rasters_skipped"] = rasters_skipped
        z.writestr("project.json", json.dumps(manifest, default=str, indent=1))

    await audit(conn, user, "project.export_file", "project", project_id, layers=len(manifest["layers"]), rasters_skipped=rasters_skipped)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{_safe(pr["name"])}{EXT}"'})


class ImportedProject(BaseModel):
    id: str
    name: str
    layers: int
    rasters_skipped: int


@router.post("/projects/import-file", status_code=201, summary="Open a .polygis file as a new project")
async def import_project(file: UploadFile, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    from .. import gisdata
    raw = await file.read()
    if len(raw) > MAX_FILE_MB * 1024 * 1024:
        raise Problem(413, f"That project file is larger than {MAX_FILE_MB} MB.", code="too_large")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        man = json.loads(zf.read("project.json"))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError):
        raise Problem(422, f"That doesn't look like a {EXT} project file.", code="bad_file") from None
    if man.get("polygis") != 1:
        raise Problem(422, "This project file was made by a newer version of PolyGIS.", code="bad_version")

    p = man["project"]
    org = user["org_id"]
    pid = (await (await conn.execute(
        "INSERT INTO projects (org_id, name, description, display_epsg, view, created_by, created_by_name) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (org, p["name"][:200], p.get("description", "")[:4000], p.get("display_epsg", 4326),
         Jsonb(p.get("view") or {}), user["id"], user["name"]))).fetchone())["id"]
    # the project came with a "Default" geodatabase and a "Map" (triggers); reuse them for the file's first/default.
    auto_gdb = (await (await conn.execute("SELECT id FROM geodatabases WHERE project_id = %s AND is_default", (pid,))).fetchone())["id"]
    auto_map = (await (await conn.execute("SELECT id FROM maps WHERE project_id = %s ORDER BY position, created_at LIMIT 1", (pid,))).fetchone())["id"]

    gdbmap: dict[str, object] = {}
    for g in man.get("geodatabases", []):
        if g.get("is_default"):
            await conn.execute("UPDATE geodatabases SET name = %s WHERE id = %s", (g["name"][:200], auto_gdb))
            gdbmap[g["key"]] = auto_gdb
        else:
            gid = (await (await conn.execute(
                "INSERT INTO geodatabases (project_id, org_id, name, is_default, created_by) VALUES (%s, %s, %s, false, %s) RETURNING id",
                (pid, org, g["name"][:200], user["id"]))).fetchone())["id"]
            gdbmap[g["key"]] = gid
    default_gdb = next((gdbmap[g["key"]] for g in man.get("geodatabases", []) if g.get("is_default")), auto_gdb)

    mapmap: dict[str, object] = {}
    for i, m in enumerate(sorted(man.get("maps", []), key=lambda x: x.get("position", 0))):
        if i == 0:
            await conn.execute("UPDATE maps SET name = %s, position = %s, view = %s WHERE id = %s",
                               (m["name"][:200], m.get("position", 0), Jsonb(m.get("view") or {}), auto_map))
            mapmap[m["key"]] = auto_map
        else:
            mid = (await (await conn.execute(
                "INSERT INTO maps (project_id, org_id, name, position, view, created_by) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (pid, org, m["name"][:200], m.get("position", i), Jsonb(m.get("view") or {}), user["id"]))).fetchone())["id"]
            mapmap[m["key"]] = mid
    active = mapmap.get(p.get("active_map")) or auto_map
    await conn.execute("UPDATE projects SET active_map_id = %s WHERE id = %s", (active, pid))

    gpkgs: dict[str, Path] = {}
    tmp = tempfile.mkdtemp(prefix="polygis-import-")
    for entry in man.get("layers", []):
        gdbid = gdbmap.get(entry.get("gdb"), default_gdb)
        mapid = mapmap.get(entry.get("map"), active)
        lid = (await (await conn.execute(
            "INSERT INTO layers (project_id, org_id, geodatabase_id, map_id, name, geometry_type, fields, style, labels, visible, position, source, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (pid, org, gdbid, mapid, entry["name"][:200], entry["geometry_type"], Jsonb(entry.get("fields") or []),
             Jsonb(entry.get("style") or {}), Jsonb(entry["labels"]) if entry.get("labels") is not None else None,
             entry.get("visible", True), entry.get("position", 0), Jsonb(entry.get("source") or {}), user["id"]))).fetchone())["id"]
        if entry.get("data") and entry.get("data_layer"):
            path = gpkgs.get(entry["data"])
            if path is None:
                path = Path(tmp) / entry["data"].replace("/", "_")
                path.write_bytes(zf.read(entry["data"]))
                gpkgs[entry["data"]] = path
            for geom, props in gisdata.read_gpkg_layer(path, entry["data_layer"], entry.get("fields") or []):
                await conn.execute(
                    "INSERT INTO features (layer_id, geom, props, updated_by) VALUES (%s, ST_GeomFromWKB(%s, 4326), %s, %s)",
                    (lid, shapely.to_wkb(geom), Jsonb(props), user["id"]))

    await audit(conn, user, "project.import_file", "project", pid, name=p["name"], layers=len(man.get("layers", [])))
    return ImportedProject(id=str(pid), name=p["name"], layers=len(man.get("layers", [])),
                           rasters_skipped=int(man.get("rasters_skipped", 0)))


__all__ = ["router"]
