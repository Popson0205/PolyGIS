"""Line of sight between two points picked on the map: the terrain profile between them and what can be seen."""
from uuid import UUID

import anyio
import numpy as np
import shapely
import shapely.geometry
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import raster_tools, visibility
from ..db import get_conn
from ..errors import Problem
from ..identity import need
from .layers import layer_of

router = APIRouter(tags=["military"])


class SightIn(BaseModel):
    observer: tuple[float, float] = Field(description="[longitude, latitude]")
    target: tuple[float, float]
    height: float = Field(2, ge=0, le=10_000, description="Observer's eye above the ground, metres")
    target_height: float = Field(0, ge=0, le=10_000)
    curvature: bool = True


@router.post("/layers/{layer_id}/line-of-sight", summary="Whether a target can be seen from an observer over an elevation model, with the profile")
async def line_of_sight(layer_id: UUID, body: SightIn, user=Depends(need(feature="raster")), conn=Depends(get_conn)):
    lay = await layer_of(conn, layer_id, user)
    if lay["geometry_type"] != "Raster" or not lay.get("raster"):
        raise Problem(422, f"{lay['name']} is not an elevation model.", code="validation")
    for lng, lat in (body.observer, body.target):
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            raise Problem(422, "Positions are [longitude, latitude].", code="validation")

    def work():
        g = raster_tools.Inputs({"input": lay}, {}).grid("input")
        z = np.ma.filled(g.data, np.nan)
        pr = visibility.profile(g, z, body.observer, body.target, body.height, body.target_height, body.curvature)
        segs = visibility.segments(g, pr)
        n = len(pr["d"])
        pick = np.unique(np.linspace(0, n - 1, min(n, 600)).round().astype(int))     # enough for a chart
        f = lambda v: None if not np.isfinite(v) else round(float(v), 2)  # noqa: E731
        return {
            "distance_m": round(pr["total"], 2), "target_visible": pr["target_seen"], "target_on_dem": pr["target_ok"],
            "observer_ground": f(pr["z"][0]), "observer_eye": f(pr["z0"]), "target_ground": f(pr["z"][-1]),
            "blocked_at_m": None if pr["blocked"] is None else round(float(pr["d"][pr["blocked"]]), 1),
            "visible_share": round(float(sum(d1 - d0 for _, v, d0, d1 in segs if v) / pr["total"]), 4),
            "profile": [{"d": round(float(pr["d"][i]), 1), "z": f(pr["z"][i]), "seen": bool(pr["seen"][i]), "sight": f(pr["sight"][i] + (pr["z"][i] - pr["eff"][i]))}
                        for i in pick],
            "segments": {"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": shapely.geometry.mapping(line), "properties": {"visible": "Visible" if v else "Hidden", "from_m": round(d0, 1), "to_m": round(d1, 1)}}
                for line, v, d0, d1 in segs]},
        }
    return await anyio.to_thread.run_sync(work)
