"""Open remote-sensing data (imagery, radar, elevation, land cover, rainfall): what can be offered, a search of the scenes, and
fetching the area, with only the bands asked for, as a raster layer."""
import datetime as dt
from typing import Literal
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import imagery, rasters
from ..audit import audit
from ..config import settings
from ..db import get_conn
from ..errors import Problem
from ..identity import need
from .layers import _touch, layer_of
from .projects import project_of
from .rasters import _insert

router = APIRouter(tags=["imagery"])


class SearchIn(BaseModel):
    provider: Literal["planetary-computer", "earth-search", "gee"] = "planetary-computer"
    dataset: Literal["sentinel2", "landsat", "sentinel1", "copdem", "nasadem", "worldcover", "chirps"] = "sentinel2"
    bands: list[str] = Field(default_factory=list, max_length=12, description="the bands wanted (by name); empty means the dataset's usual ones")
    bbox: list[float] = Field(min_length=4, max_length=4, description="west, south, east, north in degrees")
    start: dt.date | None = Field(None, description="for datasets with a date range")
    end: dt.date | None = None
    max_cloud: int = Field(30, ge=0, le=100, description="the most cloud cover a scene may have, in percent (optical imagery)")


class FetchIn(SearchIn):
    scene_ids: list[str] = Field(default_factory=list, max_length=12, description="scenes to use; empty lets PolyGIS pick")
    name: str | None = Field(None, max_length=200)


async def _run(fn, *args):
    """The (slow, blocking) lookup run off the event loop, within the time allowed; the provider's faults reach the person in words."""
    try:
        with anyio.fail_after(settings.imagery_timeout_s):
            return await anyio.to_thread.run_sync(lambda: fn(*args), abandon_on_cancel=True)
    except imagery.ImageryError as e:
        raise Problem(502 if e.upstream else 422, str(e), code="imagery_unavailable" if e.upstream else "validation") from None
    except TimeoutError:
        raise Problem(504, "The imagery source took too long. Choose a smaller area or a shorter period, or try again.", code="imagery_timeout") from None


@router.get("/imagery/providers", summary="The imagery sources the server can use")
async def providers(user=Depends(need())):
    return imagery.providers()


@router.post("/imagery/search", summary="Scenes available for an area and period (least cloudy first)")
async def search(body: SearchIn, user=Depends(need())):
    return await _run(imagery.search, body.provider, body.dataset, body.bbox, body.start, body.end, body.max_cloud, body.bands)


@router.post("/projects/{project_id}/imagery", status_code=201, summary="Fetch imagery for an area as a new raster layer")
async def fetch(project_id: UUID, body: FetchIn, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    meta, info, style = await _run(imagery.fetch, body.provider, body.dataset, body.bbox, body.start, body.end, body.max_cloud, body.scene_ids, body.bands)
    ds = imagery.DATASETS[body.dataset]
    named = ds["short"] + (f" {body.start.isoformat()} to {body.end.isoformat()}" if ds["dated"] and body.start and body.end else "")
    name = ((body.name or "").strip() or named)[:200]
    try:
        lid = await _insert(conn, project_id, user, name, meta, style, {**info, "crs": meta["crs_name"]})
    except Exception:
        rasters.remove(meta["key"])
        raise
    await audit(conn, user, "layer.import", "layer", lid, name=name, imagery=body.provider, dataset=body.dataset,
                raster=f"{meta['width']}x{meta['height']}x{meta['count']}")
    await _touch(conn, lid, project_id)
    return await layer_of(conn, lid, user)
