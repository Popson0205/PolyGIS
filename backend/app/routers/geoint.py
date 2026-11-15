"""Area of interest brief: what lies inside an area, for an intelligence summary. Its size and position (in MGRS and
degrees), the ground (heights, slopes and the highest point, from an elevation model), what each layer of the open map
has inside it, and the military symbols there by affiliation."""
from uuid import UUID

import anyio
import numpy as np
import shapely
import shapely.geometry
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from rasterio import features as rfeatures

from .. import milcoords as mc
from .. import raster_tools
from ..db import get_conn
from ..errors import Problem
from ..identity import need
from .layers import layer_of
from .projects import project_of

router = APIRouter(tags=["military"])
AFFILIATION = {"F": "Friend", "A": "Assumed friend", "H": "Hostile", "S": "Suspect", "J": "Joker", "K": "Faker", "N": "Neutral",
               "U": "Unknown", "P": "Pending", "G": "Exercise pending", "W": "Exercise unknown", "D": "Exercise friend", "L": "Exercise neutral",
               "M": "Exercise assumed friend"}


class BriefIn(BaseModel):
    geometry: dict | None = Field(None, description="The area, a GeoJSON polygon in longitude/latitude")
    layer_id: UUID | None = Field(None, description="Or a polygon feature of a layer")
    feature_id: int | None = None
    dem_id: UUID | None = Field(None, description="An elevation model for the ground figures")
    name: str = Field("Area of interest", max_length=200)


def _affiliation(sidc: str) -> str:
    s = (sidc or "").strip().upper()
    if len(s) >= 20 and s.isdigit():          # 2525D: the standard identity is the 4th digit
        return {"0": "Pending", "1": "Unknown", "2": "Assumed friend", "3": "Friend", "4": "Neutral", "5": "Suspect", "6": "Hostile"}.get(s[3], "Unknown")
    return AFFILIATION.get(s[1:2], "Unknown")


@router.post("/projects/{project_id}/area-brief", summary="What lies inside an area: size, position, ground, layers and military units")
async def area_brief(project_id: UUID, body: BriefIn, user=Depends(need()), conn=Depends(get_conn)):
    p = await project_of(conn, project_id, user)
    if body.layer_id and body.feature_id is not None:
        src = await layer_of(conn, body.layer_id, user)
        if str(src["project_id"]) != str(project_id):
            raise Problem(422, "That layer is in another project.", code="validation")
        cur = await conn.execute("SELECT ST_AsGeoJSON(geom)::jsonb AS g FROM features WHERE layer_id = layer_data(%s) AND id = %s", (src["id"], body.feature_id))
        row = await cur.fetchone()
        if not row:
            raise Problem(404, "That feature was not found.", code="not_found")
        geom = row["g"]
    elif body.geometry:
        geom = body.geometry
    else:
        raise Problem(422, "Give the area: draw it, or choose a polygon.", code="validation")
    try:
        aoi = shapely.geometry.shape(geom)
    except Exception:
        raise Problem(422, "The area is not a valid GeoJSON shape.", code="validation") from None
    if aoi.geom_type not in ("Polygon", "MultiPolygon") or aoi.is_empty:
        raise Problem(422, "The area must be a polygon.", code="validation")
    aoi = shapely.make_valid(aoi)
    wkt = aoi.wkt

    cur = await conn.execute("SELECT ST_Area(g::geography) AS area, ST_Perimeter(g::geography) AS perim, ST_X(ST_PointOnSurface(g)) AS cx, "
                             "ST_Y(ST_PointOnSurface(g)) AS cy, ST_XMin(g) AS w, ST_YMin(g) AS s, ST_XMax(g) AS e, ST_YMax(g) AS n "
                             "FROM (SELECT ST_SetSRID(ST_GeomFromText(%s), 4326) AS g) q", (wkt,))
    a = await cur.fetchone()
    out = {"name": body.name, "area_km2": round(a["area"] / 1e6, 4), "perimeter_km": round(a["perim"] / 1000, 3),
           "centre": {"lng": a["cx"], "lat": a["cy"], **mc.all_notations(a["cx"], a["cy"])},
           "extent": {"south_west": mc.write("usng", a["w"], a["s"]) or mc.write("dd", a["w"], a["s"]),
                      "north_east": mc.write("usng", a["e"], a["n"]) or mc.write("dd", a["e"], a["n"])},
           "layers": [], "units": [], "ground": None, "geometry": shapely.geometry.mapping(aoi)}

    # every vector layer of the open map: how much of it is inside
    cur = await conn.execute("SELECT id, name, geometry_type, style, fields FROM layers WHERE project_id = %s AND geometry_type <> 'Raster' "
                             "AND map_id = coalesce((SELECT active_map_id FROM projects WHERE id = %s), map_id) ORDER BY position DESC", (project_id, project_id))
    for lay in await cur.fetchall():
        if body.layer_id and str(lay["id"]) == str(body.layer_id):
            continue
        kind = lay["geometry_type"]
        measure = {"Point": "count(*)", "LineString": "sum(ST_Length(ST_Intersection(geom, a.g)::geography)) / 1000",
                   "Polygon": "sum(ST_Area(ST_Intersection(geom, a.g)::geography)) / 1e6"}[kind]
        c2 = await conn.execute(f"WITH a AS (SELECT ST_SetSRID(ST_GeomFromText(%s), 4326) AS g) SELECT count(*) AS n, {measure} AS m "
                                f"FROM features, a WHERE layer_id = layer_data(%s) AND ST_Intersects(geom, a.g)", (wkt, lay["id"]))
        r = await c2.fetchone()
        if not r["n"]:
            continue
        out["layers"].append({"id": str(lay["id"]), "name": lay["name"], "kind": kind, "features": r["n"],
                              "measure": None if kind == "Point" else round(float(r["m"] or 0), 3),
                              "unit": {"Point": None, "LineString": "km", "Polygon": "km²"}[kind]})
        st = lay["style"] or {}
        if kind == "Point" and st.get("type") == "milsymbol":
            mil = st.get("mil") or {}
            sf, df, hf = mil.get("sidc_field"), mil.get("designation_field"), mil.get("higher_field")
            c3 = await conn.execute("WITH a AS (SELECT ST_SetSRID(ST_GeomFromText(%s), 4326) AS g) SELECT ST_X(geom) AS x, ST_Y(geom) AS y, props "
                                    "FROM features, a WHERE layer_id = layer_data(%s) AND ST_Intersects(geom, a.g) ORDER BY id LIMIT 500", (wkt, lay["id"]))
            for u in await c3.fetchall():
                pr = u["props"] or {}
                code = str(pr.get(sf) or mil.get("sidc") or "") if sf else str(mil.get("sidc") or "")
                out["units"].append({"layer": lay["name"], "sidc": code, "affiliation": _affiliation(code),
                                     "designation": pr.get(df) if df else None, "higher": pr.get(hf) if hf else None,
                                     "position": mc.write("usng", u["x"], u["y"]) or mc.write("dd", u["x"], u["y"])})

    # the ground, from an elevation model
    if body.dem_id:
        dem = await layer_of(conn, body.dem_id, user)
        if dem["geometry_type"] != "Raster" or not dem.get("raster"):
            raise Problem(422, f"{dem['name']} is not an elevation model.", code="validation")

        def ground():
            g = raster_tools.Inputs({"input": dem}, {}).grid("input")
            poly = raster_tools.to_crs([aoi], g.crs)[0]
            inside = rfeatures.geometry_mask([poly], out_shape=g.shape, transform=g.transform, invert=True, all_touched=True)
            z = np.ma.filled(g.data, np.nan)
            ok = inside & np.isfinite(z)
            if not ok.any():
                return {"dem": dem["name"], "covered": False}
            gx, gy = raster_tools.horn(g)
            slope = np.degrees(np.arctan(np.hypot(gx, gy)))
            zs, ss = z[ok], slope[ok]
            r, c = np.unravel_index(np.nanargmax(np.where(ok, z, -np.inf)), z.shape)
            hx, hy = g.transform * (c + 0.5, r + 0.5)
            hp = raster_tools.to_wgs84([shapely.Point(hx, hy)], g.crs)[0]
            return {"dem": dem["name"], "covered": True, "share_covered": round(float(ok.sum() / max(1, inside.sum())), 3),
                    "min_m": round(float(zs.min()), 1), "max_m": round(float(zs.max()), 1), "mean_m": round(float(zs.mean()), 1),
                    "relief_m": round(float(zs.max() - zs.min()), 1), "mean_slope_deg": round(float(np.nanmean(ss)), 1),
                    "steep_share": round(float(np.nanmean(ss > 15)), 3), "very_steep_share": round(float(np.nanmean(ss > 30)), 3),
                    "highest": {"elevation_m": round(float(z[r, c]), 1), "position": mc.write("usng", hp.x, hp.y) or mc.write("dd", hp.x, hp.y),
                                "lng": hp.x, "lat": hp.y}}
        out["ground"] = await anyio.to_thread.run_sync(ground)

    # units by affiliation
    counts: dict[str, int] = {}
    for u in out["units"]:
        counts[u["affiliation"]] = counts.get(u["affiliation"], 0) + 1
    out["unit_counts"] = counts
    out["project"] = p["name"]
    return out
