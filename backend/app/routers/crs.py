"""Coordinate systems: find one by name or code, and its definition for the map's coordinate read-out."""
import re

from fastapi import APIRouter, Depends, Query
from pyproj import CRS
from pyproj.database import query_crs_info

from ..errors import Problem
from ..identity import need

router = APIRouter(tags=["coordinate systems"])

# The systems surveyors in Nigeria use most, offered first.
COMMON = [4326, 3857, 26331, 26332, 32631, 32632, 32633, 26391, 26392, 26393, 4263]
_INFO = None


def _all():
    global _INFO
    if _INFO is None:
        _INFO = [i for i in query_crs_info(auth_name="EPSG") if i.type.name in ("PROJECTED_CRS", "GEOGRAPHIC_2D_CRS")]
    return _INFO


@router.get("/crs", summary="Coordinate systems matching a name or EPSG code (the common Nigerian ones when empty)")
async def search(q: str = Query("", max_length=80), user=Depends(need())):
    q = q.strip().lower().removeprefix("epsg:")
    if not q:
        pick = {int(i.code): i for i in _all() if int(i.code) in COMMON}
        rows = [pick[c] for c in COMMON if c in pick]
    else:
        rows = [i for i in _all() if q == i.code or q in i.name.lower()][:50]
    return [{"epsg": int(i.code), "name": i.name, "kind": "projected" if i.type.name == "PROJECTED_CRS" else "geographic"} for i in rows]


@router.get("/crs/{epsg}", summary="A coordinate system's PROJ definition and units")
async def definition(epsg: int, user=Depends(need())):
    try:
        c = CRS.from_epsg(epsg)
    except Exception:  # noqa: BLE001
        raise Problem(404, f"EPSG:{epsg} is not a coordinate system PolyGIS knows.", code="not_found") from None
    unit = c.axis_info[0].unit_name if c.axis_info else ""
    return {"epsg": epsg, "name": c.name, "proj4": proj4_of(c), "geographic": c.is_geographic, "unit": unit}


def proj4_of(c: CRS) -> str:
    """A PROJ string the browser (proj4js) can use, carrying the same datum shift to WGS 84 that the server uses.
    A plain PROJ string drops it: Minna / UTM 32N would then read about 85 m off."""
    import warnings
    from pyproj.transformer import TransformerGroup
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        s = c.to_proj4()
    if "+towgs84" in s or "+datum=WGS84" in s or c.geodetic_crs is None:
        return s
    try:
        best = TransformerGroup(c.geodetic_crs, CRS.from_epsg(4326), always_xy=True).transformers[0].definition
    except Exception:  # noqa: BLE001
        return s
    m = re.search(r"proj=helmert((?: \w+=[-\d.e]+)+)(?: convention=(\w+))?", best)
    if not m:
        return s
    p = dict(kv.split("=") for kv in m.group(1).split())
    rot = [float(p.get(k, 0)) for k in ("rx", "ry", "rz")]
    if m.group(2) == "coordinate_frame":                  # proj4's +towgs84 takes rotations the other way round
        rot = [-r for r in rot]
    vals = [float(p.get(k, 0)) for k in ("x", "y", "z")] + rot + [float(p.get("s", 0))]
    shift = ",".join(f"{v:g}" for v in (vals if any(vals[3:]) else vals[:3]))
    return s.replace(" +no_defs", f" +towgs84={shift} +no_defs")
