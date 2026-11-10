"""Satellite imagery from open sources. The providers themselves are stood in for (no network in tests): STAC answers are canned
and its scenes are small GeoTIFFs on disk, Earth Engine is a stand-in object; what is checked is PolyGIS's own part: the
request's checks, the grid, joining scenes least cloudy first, the saved raster, and the API around it."""
import datetime as dt
import math
from contextlib import contextmanager
from unittest.mock import MagicMock

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from app import imagery, rasters
from app.config import settings
from app.imagery import ImageryError

D1, D2 = dt.date(2026, 1, 1), dt.date(2026, 3, 1)
MINNA = [6.54, 9.61, 6.55, 9.62]               # about a kilometre square, UTM 32N
LAGOS = [3.38, 6.50, 3.40, 6.52]               # UTM 31N
VALUES = [1100, 1200, 1300, 3400]               # red, green, blue, NIR


def grid(w=20, h=20, cell=10.0):
    return {"epsg": 32632, "crs": CRS.from_epsg(32632), "cell": cell, "coarsened": False, "width": w, "height": h,
            "transform": from_origin(230000.0, 1010000.0, cell, cell)}


def band_file(path, g, value, cols=None, rows=None):
    """One band of a scene on the grid, with `value` in the given columns and rows and 0 (no data) elsewhere."""
    a = np.zeros((g["height"], g["width"]), "uint16")
    a[rows or slice(None), cols or slice(None)] = value
    with rasterio.open(path, "w", driver="GTiff", width=g["width"], height=g["height"], count=1, dtype="uint16", crs=g["crs"],
                       transform=g["transform"], nodata=0) as d:
        d.write(a, 1)
    return str(path)


def item(tmp_path, g, name, cloud, values, cols=None, assets=("red", "green", "blue", "nir")):
    hrefs = {a: {"href": band_file(tmp_path / f"{name}-{a}.tif", g, v, cols)} for a, v in zip(assets, values)}
    return {"id": name, "assets": hrefs, "properties": {"eo:cloud_cover": cloud, "datetime": "2026-02-10T10:00:00Z", "platform": "sentinel-2a"}}


# ---------- the request ----------
@pytest.mark.parametrize("args,why", [
    (("earth-search", "landsat", MINNA, D1, D2), "does not have"),
    (("nowhere", "sentinel2", MINNA, D1, D2), "not known"),
    (("earth-search", "radar", MINNA, D1, D2), "Sentinel-2 or Landsat"),
    (("earth-search", "sentinel2", [6.55, 9.61, 6.54, 9.62], D1, D2), "area is not valid"),
    (("earth-search", "sentinel2", [6.5, 9.6, 6.6, 95], D1, D2), "area is not valid"),
    (("earth-search", "sentinel2", ["a", 1, 2, 3], D1, D2), "area is not valid"),
    (("earth-search", "sentinel2", MINNA, D2, D1), "before the start"),
    (("earth-search", "sentinel2", MINNA, dt.date(2020, 1, 1), dt.date(2026, 1, 1)), "three years"),
    (("gee", "sentinel2", MINNA, D1, D2), "not set up"),
])
def test_requests_are_checked(args, why):
    with pytest.raises(ImageryError, match=why):
        imagery.check(*args)


def test_check_returns_the_area():
    assert imagery.check("planetary-computer", "landsat", MINNA, D1, D2) == tuple(MINNA)


def test_grid_is_the_utm_zone_of_the_area():
    assert imagery.plan(MINNA, 10, 16_000_000)["epsg"] == 32632
    assert imagery.plan(LAGOS, 10, 16_000_000)["epsg"] == 32631
    assert imagery.plan([-58.5, -34.7, -58.4, -34.6], 10, 16_000_000)["epsg"] == 32721        # southern hemisphere: 327xx


def test_grid_cells_grow_to_fit_the_cap():
    g = imagery.plan(MINNA, 10, 16_000_000)
    assert g["cell"] == 10 and not g["coarsened"] and 90 < g["width"] < 130
    small = imagery.plan(MINNA, 10, 2_000)                      # ~11 000 cells wanted, 2 000 allowed: 3 times larger
    assert small["coarsened"] and small["cell"] == 30 and small["width"] * small["height"] <= 2_000
    with pytest.raises(ImageryError, match="too large"):
        imagery.plan([6.0, 8.0, 10.0, 12.0], 10, 16_000_000)     # four degrees square at 10 m needs cells over 8 times larger


# ---------- STAC ----------
def test_stac_search_asks_for_the_scenes_and_reads_them(monkeypatch):
    sent = {}

    def post(provider, body):
        sent.update(provider=provider, body=body)
        return [{"id": "S2A_1", "properties": {"datetime": "2026-02-01T10:00:00Z", "eo:cloud_cover": 3.5, "platform": "sentinel-2a"}}]
    monkeypatch.setattr(imagery, "_stac_post", post)
    out = imagery.search("earth-search", "sentinel2", MINNA, D1, D2, 20)
    assert out == {"count": 1, "scenes": [{"id": "S2A_1", "datetime": "2026-02-01T10:00:00Z", "cloud": 3.5, "platform": "sentinel-2a"}]}
    b = sent["body"]
    assert sent["provider"] == "earth-search" and b["collections"] == ["sentinel-2-l2a"] and b["bbox"] == MINNA
    assert b["query"] == {"eo:cloud_cover": {"lte": 20}} and b["datetime"] == "2026-01-01T00:00:00Z/2026-03-01T23:59:59Z"
    imagery.search("planetary-computer", "landsat", MINNA, D1, D2, 40)
    assert sent["body"]["collections"] == ["landsat-c2-l2"] and sent["body"]["query"]["platform"] == {"in": ["landsat-8", "landsat-9"]}


def test_a_provider_that_is_down_is_reported_as_the_providers_fault(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(ImageryError, match="Could not reach") as e:
        imagery.search("earth-search", "sentinel2", MINNA, D1, D2, 20)
    assert e.value.upstream


def test_scenes_join_least_cloudy_first(tmp_path):
    g = grid()
    clear = item(tmp_path, g, "clear", 5, VALUES, cols=slice(0, 10))                        # covers the west half only
    hazy = item(tmp_path, g, "hazy", 25, [v + 7 for v in VALUES])                            # covers everything, but cloudier
    data, used = imagery.stac_mosaic("earth-search", "sentinel2", [hazy, clear], g)         # given in the wrong order on purpose
    assert data.shape == (4, 20, 20) and data.dtype == np.uint16
    assert [s["id"] for s in used] == ["clear", "hazy"]
    assert (data[:, :, :10] == np.array(VALUES)[:, None, None]).all()                       # the clearer scene where it has data
    assert (data[:, :, 10:] == (np.array(VALUES) + 7)[:, None, None]).all()                 # the other fills the rest


def test_the_scenes_after_a_full_cover_are_not_read(tmp_path):
    g = grid()
    best = item(tmp_path, g, "best", 1, VALUES)
    other = item(tmp_path, g, "other", 9, VALUES)
    other["assets"]["red"]["href"] = str(tmp_path / "missing.tif")                           # would fail if it were opened
    _, used = imagery.stac_mosaic("earth-search", "sentinel2", [other, best], g)
    assert [s["id"] for s in used] == ["best"]


def test_a_scene_that_cannot_be_read_is_skipped(tmp_path):
    g = grid()
    broken = item(tmp_path, g, "broken", 1, VALUES)
    broken["assets"]["red"]["href"] = str(tmp_path / "missing.tif")
    short = item(tmp_path, g, "short", 2, VALUES, assets=("red", "green", "blue", "swir"))   # lacks the NIR asset
    ok = item(tmp_path, g, "ok", 30, VALUES)
    _, used = imagery.stac_mosaic("earth-search", "sentinel2", [broken, short, ok], g)
    assert [s["id"] for s in used] == ["ok"]


def test_empty_scenes_are_an_error(tmp_path):
    g = grid()
    empty = item(tmp_path, g, "empty", 1, [0, 0, 0, 0])
    with pytest.raises(ImageryError, match="no usable pixels"):
        imagery.stac_mosaic("earth-search", "sentinel2", [empty], g)


def test_planetary_computer_files_are_signed_once_per_collection(monkeypatch):
    calls = []

    class R:
        def raise_for_status(self): pass
        def json(self): return {"token": "sv=1&sig=abc"}
    monkeypatch.setattr(imagery.httpx, "get", lambda url, **k: calls.append(url) or R())
    tokens: dict = {}
    a = imagery._signed("planetary-computer", "sentinel-2-l2a", tokens, "https://x.blob.core.windows.net/a.tif")
    b = imagery._signed("planetary-computer", "sentinel-2-l2a", tokens, "https://x.blob.core.windows.net/b.tif?x=1")
    assert a == "https://x.blob.core.windows.net/a.tif?sv=1&sig=abc" and b.endswith("b.tif?x=1&sv=1&sig=abc")
    assert len(calls) == 1 and calls[0].endswith("/token/sentinel-2-l2a")
    assert imagery._signed("earth-search", "x", {}, "https://s3/a.tif") == "https://s3/a.tif"      # public: left as it is


def test_fetch_saves_a_four_band_raster(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "raster_dir", str(tmp_path / "store"))
    g = imagery.plan(MINNA, 10, settings.imagery_max_cells)
    scene = item(tmp_path, g, "S2B_1", 4, VALUES)
    monkeypatch.setattr(imagery, "_stac_items", lambda *a: [scene])
    meta, info = imagery.fetch("earth-search", "sentinel2", MINNA, D1, D2, 20, [])
    assert (meta["count"], meta["dtype"], meta["epsg"], meta["nodata"]) == (4, "uint16", 32632, 0)
    assert [b["name"] for b in meta["bands"]] == ["Red", "Green", "Blue", "NIR"] and meta["res_m"] == [10.0, 10.0]
    assert [b["min"] for b in meta["bands"]] == VALUES
    assert info["imagery"]["scenes"] == ["S2B_1"] and info["imagery"]["provider"] == "earth-search"
    assert info["summary"] == {"source": "Earth Search", "satellite": "Sentinel-2 (10 m)", "dates": "2026-02-10", "scenes": 1,
                               "cloud limit %": 20, "cell size m": 10.0}
    st = rasters.default_style(meta)
    assert st["mode"] == "rgb" and st["bands"] == [1, 2, 3]                                   # drawn as true colour
    n = 2 ** 14
    x, y = int((6.545 + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(9.615))) / math.pi) / 2 * n)
    png = rasters.render(meta, st, 14, x, y)
    assert png and png[:4] == b"\x89PNG"                                                        # the file reads back as map tiles


def test_chosen_scenes_are_looked_up_by_id(monkeypatch):
    sent = []
    monkeypatch.setattr(imagery, "_stac_post", lambda p, b: sent.append(b) or [{"id": "a"}])
    with pytest.raises(ImageryError, match="no scene b"):
        imagery._stac_items("earth-search", "sentinel2", MINNA, D1, D2, 20, ["a", "b"])
    assert sent[0]["ids"] == ["a", "b"]


# ---------- Earth Engine ----------
def test_earth_engine_is_only_offered_when_the_server_has_a_project(monkeypatch):
    by_id = lambda: {p["id"]: p for p in imagery.providers()}  # noqa: E731
    assert by_id()["gee"]["ready"] is False and by_id()["earth-search"]["ready"] and by_id()["planetary-computer"]["ready"]
    monkeypatch.setattr(settings, "gee_project", "my-project")
    assert by_id()["gee"]["ready"] is True
    assert [s["id"] for s in by_id()["earth-search"]["satellites"]] == ["sentinel2"]           # Landsat is not on that catalogue
    assert [s["id"] for s in by_id()["gee"]["satellites"]] == ["sentinel2", "landsat"]


def test_earth_engine_search(monkeypatch):
    monkeypatch.setattr(settings, "gee_project", "p")
    monkeypatch.setattr(imagery, "_earth_engine", lambda: MagicMock())
    col = MagicMock()
    col.sort.return_value.limit.return_value.getInfo.return_value = {"features": [
        {"id": "COPERNICUS/S2_SR_HARMONIZED/20260210T100051_X", "properties": {"system:time_start": 1770717651000, "cloud": 2.5}}]}
    monkeypatch.setattr(imagery, "_gee_collection", lambda *a: (col, MagicMock()))
    out = imagery.search("gee", "sentinel2", MINNA, D1, D2, 30)
    assert out["count"] == 1 and out["scenes"][0]["id"].endswith("_X") and out["scenes"][0]["cloud"] == 2.5
    assert out["scenes"][0]["datetime"].startswith("2026-02-1")


def test_earth_engine_download_becomes_a_named_raster(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "raster_dir", str(tmp_path / "store"))
    monkeypatch.setattr(settings, "gee_project", "p")
    monkeypatch.setattr(imagery, "_earth_engine", lambda: MagicMock())
    col, image = MagicMock(), MagicMock()
    col.median.return_value = image
    col.size.return_value.getInfo.return_value = 12
    col.sort.return_value.limit.return_value.getInfo.return_value = {"features": [{"id": "C/1", "properties": {"cloud": 3}}]}
    image.select.return_value.round.return_value.toUint16.return_value.getDownloadURL.return_value = "https://earthengine.example/dl"
    monkeypatch.setattr(imagery, "_gee_collection", lambda *a: (col, "region"))

    g = imagery.plan(MINNA, 10, settings.imagery_gee_max_cells)
    src = tmp_path / "sent.tif"
    with rasterio.open(src, "w", driver="GTiff", width=g["width"], height=g["height"], count=4, dtype="uint16", crs=g["crs"], transform=g["transform"]) as d:
        d.write(np.stack([np.full((g["height"], g["width"]), v, "uint16") for v in VALUES]))
    body = src.read_bytes()

    class Resp:
        def raise_for_status(self): pass
        def iter_bytes(self, n): yield body

    @contextmanager
    def stream(method, url, **k):
        assert url == "https://earthengine.example/dl"
        yield Resp()
    monkeypatch.setattr(imagery.httpx, "stream", stream)
    meta, info = imagery.fetch("gee", "sentinel2", MINNA, D1, D2, 30, [])
    ask = image.select.return_value.round.return_value.toUint16.return_value.getDownloadURL.call_args[0][0]
    assert ask["crs"] == "EPSG:32632" and ask["scale"] == 10 and ask["format"] == "GEO_TIFF"
    assert [b["name"] for b in meta["bands"]] == ["Red", "Green", "Blue", "NIR"] and meta["count"] == 4
    assert info["summary"]["source"] == "Earth Engine" and info["imagery"]["scenes"] == ["C/1"] and info["summary"]["dates"] == "2026-01-01 to 2026-03-01"


def test_earth_engine_without_scenes_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "raster_dir", str(tmp_path / "store"))
    monkeypatch.setattr(settings, "gee_project", "p")
    monkeypatch.setattr(imagery, "_earth_engine", lambda: MagicMock())
    col = MagicMock()
    col.size.return_value.getInfo.return_value = 0
    monkeypatch.setattr(imagery, "_gee_collection", lambda *a: (col, "region"))
    with pytest.raises(ImageryError, match="No scenes found"):
        imagery.fetch("gee", "landsat", MINNA, D1, D2, 30, [])


# ---------- the API ----------
@pytest.fixture
def mapper(person):
    return person(role="member", name="Imo Mapper", features=["maps", "editing", "export"])


def sample_raster(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "raster_dir", str(tmp_path / "store"))
    a = np.stack([np.full((30, 30), v, "uint16") for v in VALUES])
    return rasters.save(a, CRS.from_epsg(32632), from_origin(230000.0, 1010000.0, 10, 10), nodata=0, dtype="uint16", names=imagery.BANDS)


def test_api_lists_sources(mapper):
    r = mapper.get("/imagery/providers")
    assert r.status_code == 200
    got = {p["id"]: p for p in r.json()}
    assert set(got) == {"planetary-computer", "earth-search", "gee"} and got["gee"]["ready"] is False and got["earth-search"]["ready"]


def test_api_search(mapper, monkeypatch):
    seen = {}
    monkeypatch.setattr(imagery, "search", lambda *a: seen.update(args=a) or {"count": 0, "scenes": []})
    r = mapper.post("/imagery/search", json={"provider": "earth-search", "bbox": MINNA, "start": "2026-01-01", "end": "2026-03-01"})
    assert r.status_code == 200 and r.json() == {"count": 0, "scenes": []}
    assert seen["args"][0:2] == ("earth-search", "sentinel2") and seen["args"][-1] == 30                      # Sentinel-2 and 30 % by default


def test_api_checks_the_request(mapper):
    p = mapper.post("/projects", json={"name": "Imagery"}).json()
    bad = {"provider": "earth-search", "bbox": [6.6, 9.6, 6.5, 9.7], "start": "2026-01-01", "end": "2026-03-01"}
    r = mapper.post(f"/projects/{p['id']}/imagery", json=bad)
    assert r.status_code == 422 and "area is not valid" in r.json()["detail"]
    assert mapper.post(f"/projects/{p['id']}/imagery", json={**bad, "bbox": [1, 2, 3]}).status_code == 422          # not four numbers
    assert mapper.post("/imagery/search", json={**bad, "bbox": MINNA, "max_cloud": 101}).status_code == 422
    r = mapper.post("/imagery/search", json={**bad, "bbox": MINNA, "provider": "gee"})
    assert r.status_code == 422 and "not set up" in r.json()["detail"]


def test_api_provider_trouble_is_a_bad_gateway(mapper, monkeypatch):
    def down(*a):
        raise ImageryError("Could not reach Earth Search.", upstream=True)
    monkeypatch.setattr(imagery, "search", down)
    r = mapper.post("/imagery/search", json={"provider": "earth-search", "bbox": MINNA, "start": "2026-01-01", "end": "2026-03-01"})
    assert r.status_code == 502 and r.json()["code"] == "imagery_unavailable"


def test_api_fetch_makes_a_raster_layer(mapper, tmp_path, monkeypatch):
    meta = sample_raster(tmp_path, monkeypatch)
    info = {"imagery": {"provider": "earth-search", "satellite": "sentinel2", "scenes": ["S2B_1"]}, "summary": {"source": "Earth Search"}}
    asked = {}
    monkeypatch.setattr(imagery, "fetch", lambda *a: asked.update(args=a) or (meta, info))
    p = mapper.post("/projects", json={"name": "Imagery"}).json()
    r = mapper.post(f"/projects/{p['id']}/imagery", json={"provider": "earth-search", "bbox": MINNA, "start": "2026-01-01", "end": "2026-03-01",
                                                          "max_cloud": 15, "scene_ids": ["S2B_1"]})
    assert r.status_code == 201, r.text
    lay = r.json()
    assert lay["geometry_type"] == "Raster" and lay["name"] == "Sentinel-2 2026-01-01 to 2026-03-01"
    assert lay["raster"]["count"] == 4 and lay["style"]["mode"] == "rgb" and lay["source"]["imagery"]["scenes"] == ["S2B_1"]
    assert asked["args"][-2:] == (15, ["S2B_1"])
    assert mapper.get(f"/layers/{lay['id']}/raster/value?lng=6.55&lat=9.61").status_code == 200
    named = mapper.post(f"/projects/{p['id']}/imagery", json={"provider": "earth-search", "bbox": MINNA, "start": "2026-01-01", "end": "2026-03-01", "name": "Minna, dry season"})
    assert named.json()["name"] == "Minna, dry season"


def test_api_viewers_cannot_fetch(person, mapper, monkeypatch):
    p = mapper.post("/projects", json={"name": "Imagery"}).json()
    viewer = person(role="viewer", org_id=mapper.claims["org"]["id"])
    r = viewer.post(f"/projects/{p['id']}/imagery", json={"provider": "earth-search", "bbox": MINNA, "start": "2026-01-01", "end": "2026-03-01"})
    assert r.status_code == 403
