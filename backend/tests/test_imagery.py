"""Open remote-sensing data (imagery, radar, elevation, land cover, rainfall). The providers themselves are stood in for (no network
in tests): STAC answers are canned and its scenes are small GeoTIFFs on disk, Earth Engine is a stand-in object; what is checked is
PolyGIS's own part: the request's checks, the bands and the grid, joining scenes and tiles, the saved raster and its style, and the API."""
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
from app.imagery import DATASETS, ImageryError

D1, D2 = dt.date(2026, 1, 1), dt.date(2026, 3, 1)
MINNA = [6.54, 9.61, 6.55, 9.62]               # about a kilometre square, UTM 32N
LAGOS = [3.38, 6.50, 3.40, 6.52]               # UTM 31N
RGBN = {"Red": 1100, "Green": 1200, "Blue": 1300, "NIR": 3400}
ES, PC = "earth-search", "planetary-computer"


def grid(w=20, h=20, cell=10.0):
    return {"epsg": 32632, "crs": CRS.from_epsg(32632), "cell": cell, "coarsened": False, "width": w, "height": h,
            "transform": from_origin(230000.0, 1010000.0, cell, cell)}


def band_file(path, g, value, cols=None, dtype="uint16", nodata=0):
    """One band of a scene on the grid, with `value` in the given columns and `nodata` elsewhere."""
    a = np.full((g["height"], g["width"]), nodata, dtype)
    a[:, cols or slice(None)] = value
    with rasterio.open(path, "w", driver="GTiff", width=g["width"], height=g["height"], count=1, dtype=dtype, crs=g["crs"],
                       transform=g["transform"], nodata=nodata) as d:
        d.write(a, 1)
    return str(path)


def item(tmp_path, g, name, dataset, provider, values: dict, cloud=None, cols=None, drop=()):
    """A STAC item whose assets are local files: one per band in `values` (band name -> value), none for those in `drop`."""
    ds = DATASETS[dataset]
    assets = {}
    for b in ds["bands"]:
        if b["id"] in values and b["id"] not in drop:
            assets[b[provider]] = {"href": band_file(tmp_path / f"{name}-{b['id']}.tif", g, values[b["id"]], cols, ds["dtype"], ds["nodata"])}
    p = {"datetime": "2026-02-10T10:00:00Z", "platform": "sentinel-2a"}
    if cloud is not None:
        p["eo:cloud_cover"] = cloud
    return {"id": name, "assets": assets, "properties": p}


def s2(tmp_path, g, name, cloud, shift=0, cols=None, **kw):
    return item(tmp_path, g, name, "sentinel2", ES, {k: v + shift for k, v in RGBN.items()}, cloud, cols, **kw)


def pick(dataset, provider, *names):
    return imagery.check(provider, dataset, MINNA, D1, D2, list(names))[1]


# ---------- the request ----------
@pytest.mark.parametrize("args,why", [
    ((ES, "landsat", MINNA, D1, D2, None), "does not have Landsat"),
    ((ES, "chirps", MINNA, D1, D2, None), "does not have CHIRPS"),
    ((PC, "chirps", MINNA, D1, D2, None), "does not have CHIRPS"),
    (("nowhere", "sentinel2", MINNA, D1, D2, None), "not known"),
    ((ES, "radar", MINNA, D1, D2, None), "dataset is not known"),
    ((ES, "sentinel2", MINNA, D1, D2, ["Elevation"]), "no band Elevation"),
    ((ES, "sentinel2", MINNA, D1, D2, ["RedEdge9"]), "no band RedEdge9"),
    ((ES, "sentinel2", [6.55, 9.61, 6.54, 9.62], D1, D2, None), "area is not valid"),
    ((ES, "sentinel2", [6.5, 9.6, 6.6, 95], D1, D2, None), "area is not valid"),
    ((ES, "sentinel2", ["a", 1, 2, 3], D1, D2, None), "area is not valid"),
    ((ES, "sentinel2", MINNA, D2, D1, None), "before the start"),
    ((ES, "sentinel2", MINNA, dt.date(2020, 1, 1), dt.date(2026, 1, 1), None), "three years"),
    ((ES, "sentinel2", MINNA, None, None, None), "Choose the dates"),
    (("gee", "sentinel2", MINNA, D1, D2, None), "not set up"),
])
def test_requests_are_checked(args, why):
    with pytest.raises(ImageryError, match=why):
        imagery.check(*args)


def test_bands_default_and_come_in_the_datasets_own_order():
    area, bands = imagery.check(PC, "sentinel2", MINNA, D1, D2)
    assert area == tuple(MINNA) and [b["id"] for b in bands] == ["Red", "Green", "Blue", "NIR"]
    assert [b["id"] for b in pick("sentinel2", PC, "SWIR1", "NIR", "Red", "NIR")] == ["Red", "NIR", "SWIR1"]           # their order, no repeats
    assert [b["id"] for b in pick("landsat", PC)] == ["Red", "Green", "Blue", "NIR"]
    assert imagery.check(ES, "copdem", MINNA, None, None)[1][0]["id"] == "Elevation"                                   # no dates for a static dataset


def test_a_provider_offers_only_the_bands_it_has(monkeypatch):
    got = {p["id"]: {d["id"]: d for d in p["datasets"]} for p in imagery.providers()}
    assert set(got[ES]) == {"sentinel2", "copdem"}
    assert set(got[PC]) == {"sentinel2", "landsat", "sentinel1", "copdem", "nasadem", "worldcover"}
    assert set(got["gee"]) == set(DATASETS)
    d = got[PC]["sentinel2"]
    assert [b["id"] for b in d["bands"]][:4] == ["Red", "Green", "Blue", "NIR"] and [b["id"] for b in d["bands"] if b["default"]] == ["Red", "Green", "Blue", "NIR"]
    assert {b["id"]: b["res"] for b in d["bands"]}["SWIR1"] == 20 and (d["dated"], d["cloud"], d["scenes"]) == (True, True, True)
    assert (got[PC]["copdem"]["dated"], got[PC]["copdem"]["scenes"]) == (False, False) and got["gee"]["sentinel1"]["scenes"] is False and got[PC]["sentinel1"]["scenes"] is True
    assert [p["ready"] for p in imagery.providers()] == [True, True, False]
    monkeypatch.setattr(settings, "gee_project", "my-project")
    assert imagery.providers()[2]["ready"] is True


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
        imagery.plan([6.0, 8.0, 10.0, 12.0], 10, 16_000_000)    # four degrees square at 10 m needs cells over 8 times larger


def test_fewer_bands_mean_finer_cells():
    s = DATASETS["sentinel2"]
    one, four = imagery.cell_budget(ES, s, 1), imagery.cell_budget(ES, s, 4)
    assert one == 4 * four and four == 128 * 1024 * 1024 // 8                                  # four uint16 bands in 128 MB
    assert imagery.cell_budget(ES, DATASETS["copdem"], 1) == one // 2                          # a float32 band takes twice the memory
    assert imagery.cell_budget("gee", s, 4) == 40 * 1024 * 1024 // 8


# ---------- STAC ----------
def sent(monkeypatch, found=()):
    seen = {}
    monkeypatch.setattr(imagery, "_stac_post", lambda provider, body: seen.update(provider=provider, body=body) or list(found))
    return seen


def test_stac_search_asks_for_the_scenes_and_reads_them(monkeypatch):
    seen = sent(monkeypatch, [{"id": "S2A_1", "properties": {"datetime": "2026-02-01T10:00:00Z", "eo:cloud_cover": 3.5, "platform": "sentinel-2a"}}])
    out = imagery.search(ES, "sentinel2", MINNA, D1, D2, 20)
    assert out == {"count": 1, "scenes": [{"id": "S2A_1", "datetime": "2026-02-01T10:00:00Z", "cloud": 3.5, "platform": "sentinel-2a"}]}
    b = seen["body"]
    assert seen["provider"] == ES and b["collections"] == ["sentinel-2-l2a"] and b["bbox"] == MINNA and b["sortby"][0]["direction"] == "asc"
    assert b["query"] == {"eo:cloud_cover": {"lte": 20}} and b["datetime"] == "2026-01-01T00:00:00Z/2026-03-01T23:59:59Z"
    imagery.search(PC, "landsat", MINNA, D1, D2, 40)
    assert seen["body"]["collections"] == ["landsat-c2-l2"] and seen["body"]["query"]["platform"] == {"in": ["landsat-8", "landsat-9"]}


def test_each_kind_of_data_is_asked_for_in_its_own_way(monkeypatch):
    seen = sent(monkeypatch)
    imagery.search(PC, "sentinel1", MINNA, D1, D2, 30)                                          # radar: dates, newest first, no cloud
    b = seen["body"]
    assert b["collections"] == ["sentinel-1-rtc"] and b["query"] == {"sar:instrument_mode": {"eq": "IW"}} and b["sortby"][0] == {"field": "properties.datetime", "direction": "desc"}
    assert "datetime" in b
    q = imagery._query(PC, DATASETS["copdem"], MINNA, None, None, 30, 40)                       # elevation: tiles by area alone
    assert q == {"collections": ["cop-dem-glo-30"], "bbox": MINNA, "limit": 40}
    wc = imagery._query(PC, DATASETS["worldcover"], MINNA, None, None, 30, 40)
    assert wc["datetime"] == "2021-01-01T00:00:00Z/2021-12-31T23:59:59Z" and "query" not in wc


def test_scenes_of_a_tiled_dataset_are_not_listed(monkeypatch):
    with pytest.raises(ImageryError, match="cannot be listed"):
        imagery.search(PC, "copdem", MINNA, None, None, 30)
    monkeypatch.setattr(settings, "gee_project", "p")
    with pytest.raises(ImageryError, match="cannot be listed"):
        imagery.search("gee", "sentinel1", MINNA, D1, D2, 30)


def test_a_provider_that_is_down_is_reported_as_the_providers_fault(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(ImageryError, match="Could not reach") as e:
        imagery.search(ES, "sentinel2", MINNA, D1, D2, 20)
    assert e.value.upstream


def test_scenes_join_least_cloudy_first(tmp_path):
    g = grid()
    clear = s2(tmp_path, g, "clear", 5, cols=slice(0, 10))                                     # covers the west half only
    hazy = s2(tmp_path, g, "hazy", 25, shift=7)                                                  # covers everything, but cloudier
    bands = pick("sentinel2", ES)
    data, used = imagery.stac_mosaic(ES, "sentinel2", [hazy, clear], g, bands)                  # given in the wrong order on purpose
    assert data.shape == (4, 20, 20) and data.dtype == np.uint16 and [s["id"] for s in used] == ["clear", "hazy"]
    assert (data[:, :, :10] == np.array(list(RGBN.values()))[:, None, None]).all()              # the clearer scene where it has data
    assert (data[:, :, 10:] == (np.array(list(RGBN.values())) + 7)[:, None, None]).all()        # the other fills the rest


def test_only_the_bands_asked_for_are_read(tmp_path):
    g = grid()
    sc = s2(tmp_path, g, "one", 3, drop=("Red", "Green", "Blue"))                               # a scene whose other bands are not even there
    data, used = imagery.stac_mosaic(ES, "sentinel2", [sc], g, pick("sentinel2", ES, "NIR"))
    assert data.shape == (1, 20, 20) and (data == RGBN["NIR"]).all() and len(used) == 1
    with pytest.raises(ImageryError, match="nothing usable"):                                    # asking for a band it lacks leaves it out
        imagery.stac_mosaic(ES, "sentinel2", [sc], g, pick("sentinel2", ES, "Red", "NIR"))


def test_the_scenes_after_a_full_cover_are_not_read(tmp_path):
    g = grid()
    best, other = s2(tmp_path, g, "best", 1), s2(tmp_path, g, "other", 9)
    other["assets"]["red"]["href"] = str(tmp_path / "missing.tif")                               # would fail if it were opened
    _, used = imagery.stac_mosaic(ES, "sentinel2", [other, best], g, pick("sentinel2", ES))
    assert [s["id"] for s in used] == ["best"]


def test_a_scene_that_cannot_be_read_is_skipped(tmp_path):
    g = grid()
    broken = s2(tmp_path, g, "broken", 1)
    broken["assets"]["red"]["href"] = str(tmp_path / "missing.tif")
    short = s2(tmp_path, g, "short", 2, drop=("NIR",))
    ok = s2(tmp_path, g, "ok", 30)
    _, used = imagery.stac_mosaic(ES, "sentinel2", [broken, short, ok], g, pick("sentinel2", ES))
    assert [s["id"] for s in used] == ["ok"]


def test_empty_scenes_are_an_error(tmp_path):
    g = grid()
    empty = item(tmp_path, g, "empty", "sentinel2", ES, {k: 0 for k in RGBN}, 1)
    with pytest.raises(ImageryError, match="nothing usable"):
        imagery.stac_mosaic(ES, "sentinel2", [empty], g, pick("sentinel2", ES))


def test_elevation_tiles_are_joined_without_filling_gaps_with_zero(tmp_path):
    g = grid()
    west = item(tmp_path, g, "N09E006", "copdem", ES, {"Elevation": 310.5}, cols=slice(0, 12))
    east = item(tmp_path, g, "N09E007", "copdem", ES, {"Elevation": 420.25}, cols=slice(8, 20))
    data, used = imagery.stac_mosaic(ES, "copdem", [west, east], g, pick("copdem", ES))
    assert data.dtype == np.float32 and data.shape == (1, 20, 20) and [s["id"] for s in used] == ["N09E006", "N09E007"]
    assert (data[0, :, :12] == np.float32(310.5)).all() and (data[0, :, 12:] == np.float32(420.25)).all()      # the first tile wins where they overlap


def test_land_cover_classes_stay_whole_numbers(tmp_path):
    g = grid()
    sc = item(tmp_path, g, "ESA_WorldCover_2021", "worldcover", PC, {"Class": 40})
    bands = imagery.check(PC, "worldcover", MINNA, None, None)[1]
    from unittest.mock import patch
    with patch.object(imagery, "_signed", lambda p, c, t, h: h):
        data, used = imagery.stac_mosaic(PC, "worldcover", [sc], g, bands)
    assert data.dtype == np.uint8 and (data == 40).all() and used[0]["id"] == "ESA_WorldCover_2021"


def test_radar_keeps_both_polarisations(tmp_path):
    g = grid()
    sc = item(tmp_path, g, "S1A_1", "sentinel1", PC, {"VV": 0.125, "VH": 0.03125})
    from unittest.mock import patch
    with patch.object(imagery, "_signed", lambda p, c, t, h: h):
        data, _ = imagery.stac_mosaic(PC, "sentinel1", [sc], g, imagery.check(PC, "sentinel1", MINNA, D1, D2)[1])
    assert data.dtype == np.float32 and data.shape == (2, 20, 20) and (data[0] == 0.125).all() and (data[1] == 0.03125).all()


def test_planetary_computer_files_are_signed_once_per_collection(monkeypatch):
    calls = []

    class R:
        def raise_for_status(self): pass
        def json(self): return {"token": "sv=1&sig=abc"}
    monkeypatch.setattr(imagery.httpx, "get", lambda url, **k: calls.append(url) or R())
    tokens: dict = {}
    a = imagery._signed(PC, "sentinel-2-l2a", tokens, "https://x.blob.core.windows.net/a.tif")
    b = imagery._signed(PC, "sentinel-2-l2a", tokens, "https://x.blob.core.windows.net/b.tif?x=1")
    assert a == "https://x.blob.core.windows.net/a.tif?sv=1&sig=abc" and b.endswith("b.tif?x=1&sv=1&sig=abc")
    assert len(calls) == 1 and calls[0].endswith("/token/sentinel-2-l2a")
    assert imagery._signed(ES, "x", {}, "https://s3/a.tif") == "https://s3/a.tif"                # public: left as it is


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "raster_dir", str(tmp_path / "store"))
    return tmp_path


def test_fetch_saves_a_four_band_raster(store, monkeypatch):
    g = imagery.plan(MINNA, 10, imagery.cell_budget(ES, DATASETS["sentinel2"], 4))
    monkeypatch.setattr(imagery, "_stac_items", lambda *a: [s2(store, g, "S2B_1", 4)])
    meta, info, style = imagery.fetch(ES, "sentinel2", MINNA, D1, D2, 20, [])
    assert (meta["count"], meta["dtype"], meta["epsg"], meta["nodata"]) == (4, "uint16", 32632, 0)
    assert [b["name"] for b in meta["bands"]] == ["Red", "Green", "Blue", "NIR"] and meta["res_m"] == [10.0, 10.0]
    assert [b["min"] for b in meta["bands"]] == list(RGBN.values())
    assert info["imagery"]["scenes"] == ["S2B_1"] and info["imagery"]["provider"] == ES and info["imagery"]["bands"] == ["Red", "Green", "Blue", "NIR"]
    assert info["summary"] == {"source": "Earth Search", "dataset": "Sentinel-2 imagery (10 m)", "bands": "Red, Green, Blue, NIR", "dates": "2026-02-10",
                               "scenes": 1, "cloud limit %": 20, "cell size m": 10.0}
    assert style["mode"] == "rgb" and style["bands"] == [1, 2, 3]                                # drawn as true colour
    n = 2 ** 14
    x, y = int((6.545 + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(9.615))) / math.pi) / 2 * n)
    png = rasters.render(meta, style, 14, x, y)
    assert png and png[:4] == b"\x89PNG"                                                        # the file reads back as map tiles


def test_only_the_chosen_bands_are_saved_and_coarser_bands_set_the_cells(store, monkeypatch):
    seen = {}

    def items(provider, dataset, area, start, end, cloud, ids):
        g = imagery.plan(area, 20, imagery.cell_budget(ES, DATASETS["sentinel2"], 2))
        seen["g"] = g
        return [item(store, g, "S2B_2", "sentinel2", ES, {"SWIR1": 2200, "SWIR2": 1700}, 4)]
    monkeypatch.setattr(imagery, "_stac_items", items)
    meta, info, style = imagery.fetch(ES, "sentinel2", MINNA, D1, D2, 20, [], ["SWIR1", "SWIR2"])
    assert meta["count"] == 2 and [b["name"] for b in meta["bands"]] == ["SWIR1", "SWIR2"] and meta["res_m"] == [20.0, 20.0]
    assert style["mode"] == "ramp" and style["band"] == 1                                         # no red/green/blue: one band as a ramp
    assert info["summary"]["bands"] == "SWIR1, SWIR2"


def test_style_picks_true_or_false_colour_when_it_can():
    meta = {"bands": [{"p2": 1, "p98": 9}, {"p2": 2, "p98": 8}, {"p2": 3, "p98": 7}, {"p2": 4, "p98": 6}]}
    assert imagery.style_for("sentinel2", meta, ["Red", "Green", "Blue", "NIR"])["bands"] == [1, 2, 3]
    assert imagery.style_for("sentinel2", meta, ["Blue", "Green", "Red", "NIR"])["bands"] == [3, 2, 1]
    fc = imagery.style_for("sentinel2", {"bands": meta["bands"][:3]}, ["Red", "Green", "NIR"])         # near-infrared, red, green
    assert fc["mode"] == "rgb" and fc["bands"] == [3, 1, 2] and fc["mins"] == [3, 1, 2]
    assert imagery.style_for("sentinel2", {"bands": meta["bands"][:2]}, ["Red", "SWIR1"])["mode"] == "ramp"
    assert imagery.style_for("copdem", {"bands": [{"p2": 100, "p98": 900}]}, ["Elevation"])["ramp"] == "Terrain"
    assert imagery.style_for("chirps", {"bands": [{"p2": 0, "p98": 90}]}, ["Rainfall"])["ramp"] == "Blues"
    wc = imagery.style_for("worldcover", {"bands": [{"values": [10, 40, 80]}]}, ["Class"])
    assert wc["mode"] == "classes" and [(c["value"], c["label"], c["color"]) for c in wc["categories"]] == [
        (10, "Tree cover", "#006400"), (40, "Cropland", "#f096ff"), (80, "Permanent water", "#0064c8")]


def test_fetch_an_elevation_model(store, monkeypatch):
    g = imagery.plan(MINNA, 30, imagery.cell_budget(ES, DATASETS["copdem"], 1))
    monkeypatch.setattr(imagery, "_stac_items", lambda *a: [item(store, g, "N09E006", "copdem", ES, {"Elevation": 312.5})])
    meta, info, style = imagery.fetch(ES, "copdem", MINNA, None, None, 30, [])                    # no dates, no cloud
    assert (meta["count"], meta["dtype"], meta["res_m"]) == (1, "float32", [30.0, 30.0]) and meta["bands"][0]["name"] == "Elevation"
    assert meta["bands"][0]["min"] == 312.5 and style["ramp"] == "Terrain"
    assert info["imagery"]["start"] is None and info["imagery"]["max_cloud"] is None
    assert info["summary"] == {"source": "Earth Search", "dataset": "Copernicus elevation model (30 m)", "bands": "Elevation", "scenes": 1, "cell size m": 30.0}


def test_fetch_land_cover_in_its_own_colours(store, monkeypatch):
    g = imagery.plan(MINNA, 10, imagery.cell_budget(PC, DATASETS["worldcover"], 1))
    sc = item(store, g, "ESA_WorldCover_2021", "worldcover", PC, {"Class": 40})
    sc["assets"]["map"]["href"] = band_file(store / "wc2.tif", g, 40, slice(0, 8), "uint8", 0)
    monkeypatch.setattr(imagery, "_stac_items", lambda *a: [sc])
    monkeypatch.setattr(imagery, "_signed", lambda p, c, t, h: h)
    meta, info, style = imagery.fetch(PC, "worldcover", MINNA, None, None, 30, [])
    assert meta["dtype"] == "uint8" and style["mode"] == "classes" and style["categories"][0]["label"] in ("Cropland", "Tree cover")
    assert meta["bands"][0]["values"] == [40]


def test_chosen_scenes_are_looked_up_by_id(monkeypatch):
    seen = sent(monkeypatch, [{"id": "a"}])
    with pytest.raises(ImageryError, match="no scene b"):
        imagery._stac_items(ES, "sentinel2", MINNA, D1, D2, 20, ["a", "b"])
    assert seen["body"]["ids"] == ["a", "b"]
    sent(monkeypatch, [])
    with pytest.raises(ImageryError, match="No data found"):
        imagery._stac_items(ES, "copdem", MINNA, None, None, 20, [])


# ---------- Earth Engine ----------
@pytest.fixture
def gee(monkeypatch, store):
    monkeypatch.setattr(settings, "gee_project", "p")
    monkeypatch.setattr(imagery, "_earth_engine", lambda: MagicMock())


def test_earth_engine_search(gee, monkeypatch):
    col = MagicMock()
    col.sort.return_value.limit.return_value.getInfo.return_value = {"features": [
        {"id": "COPERNICUS/S2_SR_HARMONIZED/20260210T100051_X", "properties": {"system:time_start": 1770717651000, "cloud": 2.5}}]}
    monkeypatch.setattr(imagery, "_gee_collection", lambda *a: (col, MagicMock()))
    out = imagery.search("gee", "sentinel2", MINNA, D1, D2, 30)
    assert out["count"] == 1 and out["scenes"][0]["id"].endswith("_X") and out["scenes"][0]["cloud"] == 2.5 and out["scenes"][0]["datetime"].startswith("2026-02-1")


def write_gee_file(store, g, names, dtype="uint16", value=1000):
    src = store / "sent.tif"
    with rasterio.open(src, "w", driver="GTiff", width=g["width"], height=g["height"], count=len(names), dtype=dtype, crs=g["crs"], transform=g["transform"]) as d:
        d.write(np.stack([np.full((g["height"], g["width"]), value + 100 * i, dtype) for i in range(len(names))]))
    return src.read_bytes()


def serve(monkeypatch, body):
    class Resp:
        def raise_for_status(self): pass
        def iter_bytes(self, n): yield body

    @contextmanager
    def stream(method, url, **k):
        assert url == "https://earthengine.example/dl"
        yield Resp()
    monkeypatch.setattr(imagery.httpx, "stream", stream)


def test_earth_engine_download_becomes_a_named_raster(gee, store, monkeypatch):
    col, image = MagicMock(), MagicMock()
    col.median.return_value = image
    col.size.return_value.getInfo.return_value = 12
    col.sort.return_value.limit.return_value.getInfo.return_value = {"features": [{"id": "C/1", "properties": {"cloud": 3}}]}
    image.select.return_value.round.return_value.toUint16.return_value.getDownloadURL.return_value = "https://earthengine.example/dl"
    monkeypatch.setattr(imagery, "_gee_collection", lambda *a: (col, "region"))
    g = imagery.plan(MINNA, 10, imagery.cell_budget("gee", DATASETS["sentinel2"], 2))
    serve(monkeypatch, write_gee_file(store, g, ["Red", "NIR"]))
    meta, info, style = imagery.fetch("gee", "sentinel2", MINNA, D1, D2, 30, [], ["Red", "NIR"])
    assert image.select.call_args[0][0] == ["Red", "NIR"]                                         # only the bands asked for are requested
    ask = image.select.return_value.round.return_value.toUint16.return_value.getDownloadURL.call_args[0][0]
    assert ask["crs"] == "EPSG:32632" and ask["scale"] == 10 and ask["format"] == "GEO_TIFF"
    assert [b["name"] for b in meta["bands"]] == ["Red", "NIR"] and meta["nodata"] == 0
    assert info["summary"]["source"] == "Earth Engine" and info["imagery"]["scenes"] == ["C/1"] and info["summary"]["dates"] == "2026-01-01 to 2026-03-01"


def test_earth_engine_elevation(gee, store, monkeypatch):
    image = MagicMock()
    image.getDownloadURL.return_value = "https://earthengine.example/dl"
    monkeypatch.setattr(imagery, "_gee_image", lambda *a: (image, [], "region"))
    g = imagery.plan(MINNA, 30, imagery.cell_budget("gee", DATASETS["copdem"], 1))
    serve(monkeypatch, write_gee_file(store, g, ["Elevation"], "float32", 350))
    meta, info, style = imagery.fetch("gee", "copdem", MINNA, None, None, 30, [])
    ask = image.getDownloadURL.call_args[0][0]
    assert ask["scale"] == 30 and meta["dtype"] == "float32" and meta["bands"][0]["name"] == "Elevation" and style["ramp"] == "Terrain"
    assert "scenes" not in info["summary"] and "dates" not in info["summary"]


@pytest.mark.parametrize("dataset,collection,cast,uses_image", [
    ("sentinel1", "COPERNICUS/S1_GRD", "toFloat", False), ("copdem", "COPERNICUS/DEM/GLO30", "toFloat", False),
    ("nasadem", "NASA/NASADEM_HGT/001", "toFloat", True), ("worldcover", "ESA/WorldCover/v200", "toUint8", False),
    ("chirps", "UCSB-CHG/CHIRPS/DAILY", "toFloat", False)])
def test_each_earth_engine_dataset_comes_from_its_own_collection(monkeypatch, dataset, collection, cast, uses_image):
    monkeypatch.setattr(settings, "gee_project", "p")
    ee = MagicMock()
    bands = imagery.check("gee", dataset, MINNA, D1, D2)[1]
    image, used, region = imagery._gee_image(ee, dataset, bands, MINNA, D1, D2, 30, [])
    called = ee.Image if uses_image else ee.ImageCollection
    assert called.call_args_list[0][0][0] == collection
    assert used == [] and region is not None
    assert any(c[0].endswith(cast) for c in ee.mock_calls), cast


def test_earth_engine_without_scenes_says_so(gee, monkeypatch):
    col = MagicMock()
    col.size.return_value.getInfo.return_value = 0
    monkeypatch.setattr(imagery, "_gee_collection", lambda *a: (col, "region"))
    with pytest.raises(ImageryError, match="No scenes found"):
        imagery.fetch("gee", "landsat", MINNA, D1, D2, 30, [])


# ---------- the API ----------
@pytest.fixture
def mapper(person):
    return person(role="member", name="Imo Mapper", features=["maps", "editing", "export"])


def sample_raster(tmp_path, monkeypatch, names=("Red", "Green", "Blue", "NIR")):
    monkeypatch.setattr(settings, "raster_dir", str(tmp_path / "store"))
    a = np.stack([np.full((30, 30), 1000 + 100 * i, "uint16") for i in range(len(names))])
    return rasters.save(a, CRS.from_epsg(32632), from_origin(230000.0, 1010000.0, 10, 10), nodata=0, dtype="uint16", names=list(names))


def test_api_lists_sources_datasets_and_bands(mapper):
    r = mapper.get("/imagery/providers")
    assert r.status_code == 200
    got = {p["id"]: p for p in r.json()}
    assert set(got) == {PC, ES, "gee"} and got["gee"]["ready"] is False and got[ES]["ready"]
    assert "copdem" in {d["id"] for d in got[ES]["datasets"]}


def test_api_search(mapper, monkeypatch):
    seen = {}
    monkeypatch.setattr(imagery, "search", lambda *a: seen.update(args=a) or {"count": 0, "scenes": []})
    r = mapper.post("/imagery/search", json={"provider": ES, "bbox": MINNA, "start": "2026-01-01", "end": "2026-03-01", "bands": ["NIR"]})
    assert r.status_code == 200 and r.json() == {"count": 0, "scenes": []}
    assert seen["args"][0:2] == (ES, "sentinel2") and seen["args"][5] == 30 and seen["args"][6] == ["NIR"]      # Sentinel-2 and 30 % by default


def test_api_checks_the_request(mapper):
    p = mapper.post("/projects", json={"name": "Imagery"}).json()
    base = {"provider": ES, "bbox": MINNA, "start": "2026-01-01", "end": "2026-03-01"}
    r = mapper.post(f"/projects/{p['id']}/imagery", json={**base, "bbox": [6.6, 9.6, 6.5, 9.7]})
    assert r.status_code == 422 and "area is not valid" in r.json()["detail"]
    assert mapper.post(f"/projects/{p['id']}/imagery", json={**base, "bbox": [1, 2, 3]}).status_code == 422          # not four numbers
    assert mapper.post("/imagery/search", json={**base, "max_cloud": 101}).status_code == 422
    assert mapper.post("/imagery/search", json={**base, "dataset": "weather"}).status_code == 422
    r = mapper.post("/imagery/search", json={**base, "provider": "gee"})
    assert r.status_code == 422 and "not set up" in r.json()["detail"]
    r = mapper.post(f"/projects/{p['id']}/imagery", json={**base, "bands": ["Elevation"]})
    assert r.status_code == 422 and "no band Elevation" in r.json()["detail"]
    r = mapper.post(f"/projects/{p['id']}/imagery", json={"provider": ES, "bbox": MINNA})
    assert r.status_code == 422 and "Choose the dates" in r.json()["detail"]


def test_api_provider_trouble_is_a_bad_gateway(mapper, monkeypatch):
    def down(*a):
        raise ImageryError("Could not reach Earth Search.", upstream=True)
    monkeypatch.setattr(imagery, "search", down)
    r = mapper.post("/imagery/search", json={"provider": ES, "bbox": MINNA, "start": "2026-01-01", "end": "2026-03-01"})
    assert r.status_code == 502 and r.json()["code"] == "imagery_unavailable"


def test_api_fetch_makes_a_raster_layer(mapper, tmp_path, monkeypatch):
    meta = sample_raster(tmp_path, monkeypatch, ("Red", "NIR"))
    info = {"imagery": {"provider": ES, "dataset": "sentinel2", "bands": ["Red", "NIR"], "scenes": ["S2B_1"]}, "summary": {"source": "Earth Search"}}
    style = {"type": "raster", "mode": "ramp", "band": 1, "min": 1000, "max": 1100, "ramp": "Viridis", "opacity": 1}
    asked = {}
    monkeypatch.setattr(imagery, "fetch", lambda *a: asked.update(args=a) or (meta, info, style))
    p = mapper.post("/projects", json={"name": "Imagery"}).json()
    r = mapper.post(f"/projects/{p['id']}/imagery", json={"provider": ES, "bbox": MINNA, "start": "2026-01-01", "end": "2026-03-01",
                                                          "max_cloud": 15, "scene_ids": ["S2B_1"], "bands": ["Red", "NIR"]})
    assert r.status_code == 201, r.text
    lay = r.json()
    assert lay["geometry_type"] == "Raster" and lay["name"] == "Sentinel-2 2026-01-01 to 2026-03-01"
    assert lay["raster"]["count"] == 2 and lay["style"]["ramp"] == "Viridis" and lay["source"]["imagery"]["bands"] == ["Red", "NIR"]
    assert asked["args"][-3:] == (15, ["S2B_1"], ["Red", "NIR"])
    assert mapper.get(f"/layers/{lay['id']}/raster/value?lng=6.55&lat=9.61").status_code == 200
    named = mapper.post(f"/projects/{p['id']}/imagery", json={"provider": ES, "bbox": MINNA, "start": "2026-01-01", "end": "2026-03-01", "name": "Minna, dry season"})
    assert named.json()["name"] == "Minna, dry season"


def test_api_an_elevation_layer_needs_no_dates(mapper, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "raster_dir", str(tmp_path / "store"))
    meta = rasters.save(np.full((30, 30), 312.5, "float32"), CRS.from_epsg(32632), from_origin(230000.0, 1010000.0, 30, 30), names=["Elevation"])
    monkeypatch.setattr(imagery, "fetch", lambda *a: (meta, {"imagery": {"provider": ES, "dataset": "copdem", "bands": ["Elevation"], "scenes": ["t"]}, "summary": {}},
                                                       {"type": "raster", "mode": "ramp", "band": 1, "min": 300, "max": 320, "ramp": "Terrain", "opacity": 1}))
    p = mapper.post("/projects", json={"name": "DEM"}).json()
    r = mapper.post(f"/projects/{p['id']}/imagery", json={"provider": ES, "dataset": "copdem", "bbox": MINNA})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "Copernicus DEM" and r.json()["raster"]["dtype"] == "float32"


def test_api_viewers_cannot_fetch(person, mapper):
    p = mapper.post("/projects", json={"name": "Imagery"}).json()
    viewer = person(role="viewer", org_id=mapper.claims["org"]["id"])
    r = viewer.post(f"/projects/{p['id']}/imagery", json={"provider": ES, "bbox": MINNA, "start": "2026-01-01", "end": "2026-03-01"})
    assert r.status_code == 403
