"""Poly Earth: Earth Engine-style analyses on open data. The catalogues are stood in for (no network in tests): their scenes are
small GeoTIFFs on disk laid on the very grid PolyGIS asks for, so every expected value can be worked out by hand: clouds masked,
scenes combined, indices, time series, change, burn severity, flood mapping, classification and its accuracy, and the API's
background runs."""
import datetime as dt
import time

import numpy as np
import pytest
import rasterio
import shapely

from app import imagery, polyearth as pe, raster_tools as rt
from app.config import settings
from app.imagery import PC, ES

MINNA = [6.54, 9.61, 6.55, 9.62]                      # about 1.1 km square, UTM 32N
S2_KEYS = {"Red": "B04", "Green": "B03", "Blue": "B02", "NIR": "B08", "RedEdge1": "B05", "RedEdge2": "B06", "RedEdge3": "B07",
           "NIR2": "B8A", "SWIR1": "B11", "SWIR2": "B12"}
LS_KEYS = {"Red": "red", "Green": "green", "Blue": "blue", "NIR": "nir08", "SWIR1": "swir16", "SWIR2": "swir22"}


def G(res=10):
    return imagery.plan(MINNA, res, 10**9)


def tif(path, g, a, dtype, nodata=None):
    a = np.broadcast_to(np.asarray(a, dtype), (g["height"], g["width"]))
    with rasterio.open(path, "w", driver="GTiff", width=g["width"], height=g["height"], count=1, dtype=dtype, crs=g["crs"],
                       transform=g["transform"], nodata=nodata) as d:
        d.write(a, 1)
    return str(path)


class Catalogue:
    """Answers STAC searches from items held in memory, by collection, date range and ids."""
    def __init__(self):
        self.items: list[dict] = []
        self.asked: list[dict] = []

    def add(self, coll, it):
        it["_coll"] = coll
        self.items.append(it)
        return it

    def post(self, provider, body):
        self.asked.append(body)
        out = [i for i in self.items if i["_coll"] in body["collections"]]
        if "ids" in body:
            return [i for i in out if i["id"] in body["ids"]]
        if "datetime" in body:
            a, b = body["datetime"].split("/")
            out = [i for i in out if not i["properties"].get("datetime") or a[:10] <= i["properties"]["datetime"][:10] <= b[:10]]
        q = body.get("query", {})
        if "sat:orbit_state" in q:
            out = [i for i in out if i["properties"].get("sat:orbit_state") == q["sat:orbit_state"]["eq"]]
        if "eo:cloud_cover" in q:
            out = [i for i in out if i["properties"].get("eo:cloud_cover", 0) <= q["eo:cloud_cover"]["lte"]]
        return out[: body.get("limit", 100)]


@pytest.fixture
def cat(monkeypatch, tmp_path):
    c = Catalogue()
    monkeypatch.setattr(imagery, "_stac_post", c.post)
    monkeypatch.setattr(imagery, "_signed", lambda p, coll, t, h: h)
    monkeypatch.setattr(settings, "raster_dir", str(tmp_path / "store"))
    c.dir = tmp_path
    return c


def s2_scene(c, name, date, refl: dict, scl=4, g=None, tile="32PNR", cloud=5, provider=PC, baseline="05.11"):
    """A Sentinel-2 scene whose bands hold reflectance x 10000 (plus 1000 for Planetary Computer's newer baselines)."""
    g = g or G()
    off = 1000 if provider == PC and float(baseline) >= 4 else 0
    assets = {}
    for b, key in S2_KEYS.items():
        v = refl.get(b, 0.1)
        dn = np.rint(np.asarray(v, np.float64) * 10000 + off).astype(np.uint16)
        k = key if provider == PC else imagery.DATASETS["sentinel2"]["bands"][[x["id"] for x in imagery.DATASETS["sentinel2"]["bands"]].index(b)][ES]
        assets[k] = {"href": tif(c.dir / f"{name}-{b}.tif", g, dn, "uint16", 0)}
    assets["SCL" if provider == PC else "scl"] = {"href": tif(c.dir / f"{name}-scl.tif", g, scl, "uint8", 0)}
    props = {"datetime": f"{date}T10:00:00Z", "eo:cloud_cover": cloud, "s2:mgrs_tile": tile, "s2:processing_baseline": baseline, "platform": "sentinel-2a"}
    return c.add("sentinel-2-l2a", {"id": name, "assets": assets, "properties": props, "bbox": MINNA})


def ctx(params, layers=None, feats=None):
    return pe.Ctx(params, rt.Inputs(layers or {}, feats or {}))


BASE = {"bbox": MINNA, "sensor": "sentinel2", "source": PC, "max_cloud": 60, "scenes": 10}


# ---------- reading, masking, combining ----------
def test_clouds_are_masked_pixel_by_pixel_and_the_median_taken(cat):
    g = G()
    half = np.full((g["height"], g["width"]), 4, np.uint8)
    half[:, : g["width"] // 2] = 9                                                 # cloud (high probability) over the west half
    s2_scene(cat, "a", "2026-01-05", {"Red": 0.10, "NIR": 0.40})
    s2_scene(cat, "b", "2026-01-15", {"Red": 0.30, "NIR": 0.20}, scl=half)
    s2_scene(cat, "c", "2026-01-25", {"Red": 0.20, "NIR": 0.30})
    res = pe.composite_tool({**BASE, "start": "2026-01-01", "end": "2026-01-31", "bands": "rgbn"}, ctx({}))
    o = res.outs[0]
    red = o.data[0].astype(float) / 10000
    w = g["width"] // 2
    assert o.names == ["Red", "Green", "Blue", "NIR"] and o.dtype == "uint16"
    assert np.allclose(red[:, :w], 0.15) and np.allclose(red[:, w:], 0.20)             # median of a, c (cloud in b) and of a, b, c
    assert o.summary["scenes"] == 3 and o.summary["clear %"] == 100.0


def test_planetary_computer_offset_is_removed_and_earth_search_left_alone(cat):
    s2_scene(cat, "new", "2026-02-01", {"Red": 0.05, "NIR": 0.35}, baseline="05.11")
    s2_scene(cat, "old", "2021-02-01", {"Red": 0.05, "NIR": 0.35}, baseline="02.12")
    for start in ("2026-01-01", "2021-01-01"):
        r = pe.index_tool({**BASE, "index": "ndvi", "start": start, "end": start[:4] + "-03-01"}, ctx({}))
        assert np.allclose(r.outs[0].data, 0.75, atol=1e-4)                                # (0.35 - 0.05) / 0.40, offset or not


def test_greenest_and_latest_pixels(cat):
    s2_scene(cat, "dry", "2026-01-05", {"Red": 0.20, "NIR": 0.25})
    s2_scene(cat, "green", "2026-01-10", {"Red": 0.04, "NIR": 0.45})
    s2_scene(cat, "late", "2026-01-28", {"Red": 0.10, "NIR": 0.30})
    p = {**BASE, "start": "2026-01-01", "end": "2026-01-31", "bands": "rgbn"}
    green = pe.composite_tool({**p, "method": "greenest"}, ctx({})).outs[0].data
    late = pe.composite_tool({**p, "method": "latest"}, ctx({})).outs[0].data
    assert (green[0] == 400).all() and (green[3] == 4500).all() and (late[0] == 1000).all()


def test_scenes_are_picked_evenly_across_tiles():
    items = [{"id": f"{t}{k}", "properties": {"s2:mgrs_tile": t, "eo:cloud_cover": k}} for t in ("A", "B") for k in range(5)]
    items.append({"id": "B-clear", "properties": {"s2:mgrs_tile": "B", "eo:cloud_cover": 0.1}})
    got = [i["id"] for i in pe.pick(items, 2)]
    assert sorted(got) == ["A0", "A1", "B-clear", "B0"]


def test_landsat_quality_bits_and_surface_temperature(cat):
    g = G(30)
    qa = np.full((g["height"], g["width"]), 21824, np.uint16)                           # clear land
    qa[:5] = 22280                                                                       # cloud (bit 3)
    st_dn = np.rint((30.0 + 273.15 - 149.0) / 0.00341802)
    assets = {k: {"href": tif(cat.dir / f"ls-{b}.tif", g, np.rint((0.2 + 0.2) / 0.0000275), "uint16", 0)} for b, k in LS_KEYS.items()}
    assets["qa_pixel"] = {"href": tif(cat.dir / "ls-qa.tif", g, qa, "uint16", 1)}
    assets["lwir11"] = {"href": tif(cat.dir / "ls-st.tif", g, st_dn, "uint16", 0)}
    cat.add("landsat-c2-l2", {"id": "LC09_1", "assets": assets, "properties": {"datetime": "2026-01-10T10:00:00Z", "eo:cloud_cover": 3, "platform": "landsat-9",
                                                                           "landsat:wrs_path": "189", "landsat:wrs_row": "053"}, "bbox": MINNA})
    r = pe.lst_tool({"bbox": MINNA, "start": "2026-01-01", "end": "2026-01-31", "max_cloud": 50, "scenes": 5}, ctx({}))
    t = r.outs[0].data
    assert t.mask[:5].all() and np.allclose(t[5:], 30.0, atol=0.01) and r.outs[0].summary["unit"] == "deg C"
    comp = pe.composite_tool({"bbox": MINNA, "sensor": "landsat", "start": "2026-01-01", "end": "2026-01-31", "max_cloud": 50, "scenes": 5, "bands": "rgbn"}, ctx({}))
    assert np.allclose(comp.outs[0].data[0][5:], 2000, atol=1)                          # DN x 0.0000275 - 0.2 = 0.2


def test_index_formulas():
    b = {"NIR": np.array([0.4]), "Red": np.array([0.1]), "Blue": np.array([0.05]), "Green": np.array([0.08]), "SWIR1": np.array([0.25]),
         "SWIR2": np.array([0.15]), "RedEdge1": np.array([0.2])}
    assert pe.index_of("ndvi", b)[0] == pytest.approx(0.6)
    assert pe.index_of("evi", b)[0] == pytest.approx(2.5 * 0.3 / (0.4 + 0.6 - 0.375 + 1))
    assert pe.index_of("savi", b)[0] == pytest.approx(1.5 * 0.3 / 1.0)
    assert pe.index_of("msavi", b)[0] == pytest.approx((1.8 - np.sqrt(1.8 ** 2 - 8 * 0.3)) / 2)
    assert pe.index_of("mndwi", b)[0] == pytest.approx((0.08 - 0.25) / 0.33)
    assert pe.index_of("nbr", b)[0] == pytest.approx(0.25 / 0.55)
    assert pe.index_of("bsi", b)[0] == pytest.approx((0.35 - 0.45) / 0.8)
    with pytest.raises(Exception, match="red edge|RedEdge1"):
        pe.check_index("landsat", "ndre")


def test_band_formula_names_the_bands_it_knows(cat):
    s2_scene(cat, "a", "2026-01-05", {"Red": 0.1, "NIR": 0.3, "SWIR1": 0.2})
    r = pe.band_formula_tool({**BASE, "expression": "NIR / Red + con(SWIR1 > NIR, 1, 0)", "start": "2026-01-01", "end": "2026-01-31"}, ctx({}))
    assert np.allclose(r.outs[0].data, 3.0)
    with pytest.raises(Exception, match="Thermal is not a band"):
        pe.band_formula_tool({**BASE, "expression": "Thermal * 2", "start": "2026-01-01", "end": "2026-01-31"}, ctx({}))


def test_no_scenes_and_bad_settings_are_explained(cat):
    with pytest.raises(Exception, match="No Sentinel-2 scenes"):
        pe.index_tool({**BASE, "index": "ndvi", "start": "2026-01-01", "end": "2026-01-31"}, ctx({}))
    with pytest.raises(Exception, match="a year or less"):
        pe.index_tool({**BASE, "start": "2024-01-01", "end": "2026-01-31"}, ctx({}))
    with pytest.raises(Exception, match="end is before"):
        pe.index_tool({**BASE, "start": "2026-02-01", "end": "2026-01-31"}, ctx({}))
    with pytest.raises(Exception, match="area"):
        pe.index_tool({**BASE, "bbox": [1, 2, 0, 3], "start": "2026-01-01", "end": "2026-01-31"}, ctx({}))


# ---------- time series, trend, anomaly, change ----------
def test_monthly_ndvi_series_for_points_and_polygons(cat):
    for m, ndvi_red in ((1, 0.2), (2, 0.1), (3, 0.05)):
        s2_scene(cat, f"m{m}", f"2026-0{m}-10", {"Red": ndvi_red, "NIR": 0.3})
    g = G()
    left, top = g["transform"].c, g["transform"].f
    pt = rt.to_wgs84([shapely.Point(left + 205, top - 205)], g["crs"])[0]
    poly = rt.to_wgs84([shapely.box(left + 300, top - 600, left + 600, top - 300)], g["crs"])[0]
    feats = [{"id": 1, "geom": pt, "props": {"name": "Well"}}]
    layers = {"zones": {"name": "Places", "geometry_type": "Point", "fields": [{"name": "name", "type": "string"}]}}
    r = pe.time_series_tool({"bbox": MINNA, "measure": "index", "sensor": "sentinel2", "source": PC, "index": "ndvi", "start": "2026-01-01",
                             "end": "2026-03-31", "step": "month", "max_cloud": 60, "scenes": 3, "label": "name"}, ctx({}, layers, {"zones": feats}))
    assert r.chart["x"] == ["2026-01", "2026-02", "2026-03"] and r.chart["series"][0]["name"] == "Well"
    assert r.chart["series"][0]["values"] == pytest.approx([0.2, 0.5, 0.7142857], abs=1e-4)
    layer = r.outs[0]
    assert layer.kind == "vector" and layer.rows[0][1]["2026-02"] == pytest.approx(0.5, abs=1e-4)
    whole = pe.time_series_tool({"bbox": MINNA, "measure": "index", "index": "ndvi", "start": "2026-01-01", "end": "2026-03-31", "step": "scene"}, ctx({}))
    assert whole.chart["x"] == ["2026-01-10", "2026-02-10", "2026-03-10"]
    assert [o.name for o in whole.outs] == ["NDVI (Sentinel-2) 2026-01-10", "NDVI (Sentinel-2) 2026-02-10", "NDVI (Sentinel-2) 2026-03-10"]
    assert [o.summary["mean"] for o in whole.outs] == pytest.approx([0.2, 0.5, 0.7142857], abs=1e-4)          # a raster for each interval
    feats = [{"id": 2, "geom": poly, "props": {}}]
    layers = {"zones": {"name": "Farms", "geometry_type": "Polygon", "fields": []}}
    yr = pe.time_series_tool({"bbox": MINNA, "measure": "index", "index": "ndvi", "start": "2026-01-01", "end": "2026-03-31", "step": "year"}, ctx({}, layers, {"zones": feats}))
    assert yr.chart["x"] == ["2026"] and yr.chart["series"][0]["values"][0] == pytest.approx(0.5, abs=1e-4)      # median of the three scenes


def test_change_classes_and_their_areas(cat):
    g = G()
    nir = np.full((g["height"], g["width"]), 0.4)
    nir[:, :20] = 0.12                                                                    # cleared: NDVI 0.6 -> 0.09
    s2_scene(cat, "before", "2025-01-10", {"Red": 0.1, "NIR": 0.4})
    s2_scene(cat, "after", "2026-01-10", {"Red": 0.1, "NIR": nir})
    r = pe.change_tool({**BASE, "index": "ndvi", "before_start": "2025-01-01", "before_end": "2025-01-31", "after_start": "2026-01-01",
                        "after_end": "2026-01-31", "threshold": 0.15}, ctx({}))
    diff, cls = r.outs
    assert np.allclose(diff.data[:, :20], 0.02 / 0.22 - 0.6, atol=1e-4) and (cls.data[:, :20] == 1).all() and (cls.data[:, 20:] == 2).all()
    assert cls.summary["Vegetation loss (ha)"] == pytest.approx(g["height"] * 20 * 0.01, rel=1e-6)
    assert [c["label"] for c in cls.style["categories"]] == ["Vegetation loss", "No change", "Vegetation gain"]


def test_burn_severity_follows_the_usgs_classes(cat):
    g = G()
    swir2 = np.full((g["height"], g["width"]), 0.1)
    swir2[:, :30] = 0.30                                                                 # NBR after: (0.3-0.3)/0.6 = 0 there
    s2_scene(cat, "pre", "2026-01-05", {"NIR": 0.3, "SWIR2": 0.1})                      # NBR before 0.5
    s2_scene(cat, "post", "2026-02-20", {"NIR": 0.3, "SWIR2": swir2})
    r = pe.burn_tool({**BASE, "before_start": "2026-01-01", "before_end": "2026-01-31", "after_start": "2026-02-15", "after_end": "2026-02-28"}, ctx({}))
    cls, dnbr = r.outs
    assert np.allclose(dnbr.data[:, :30], 0.5, atol=1e-5) and (cls.data[:, :30] == 6).all() and (cls.data[:, 30:] == 3).all()   # moderate-high; unburned
    assert cls.summary["burnt area (ha)"] == pytest.approx(g["height"] * 30 * 0.01)
    edges = [(-0.3, 1), (-0.2, 2), (0.0, 3), (0.2, 4), (0.3, 5), (0.5, 6), (0.7, 7)]
    assert [int(np.digitize(v, pe.BURN_EDGES) + 1) for v, _ in edges] == [c for _, c in edges]


def test_trend_is_the_slope_per_year(cat):
    for k, y in enumerate((2022, 2023, 2024, 2025)):
        ndvi = 0.3 + 0.05 * k
        s2_scene(cat, f"y{y}", f"{y}-06-10", {"Red": (1 - ndvi) * 0.2, "NIR": (1 + ndvi) * 0.2})
    r = pe.trend_tool({**BASE, "index": "ndvi", "first_year": 2022, "last_year": 2025, "from_month": 5, "to_month": 7, "scenes": 3}, ctx({}))
    assert np.allclose(r.outs[0].data, 0.05, atol=1e-4) and r.outs[0].summary["rising %"] == 100.0


def test_index_anomaly_against_earlier_years(cat):
    for y, ndvi in ((2023, 0.5), (2024, 0.7), (2025, 0.6)):
        s2_scene(cat, f"a{y}", f"{y}-08-15", {"Red": (1 - ndvi) * 0.2, "NIR": (1 + ndvi) * 0.2})
    r = pe.anomaly_tool({**BASE, "measure": "index", "index": "ndvi", "start": "2025-08-01", "end": "2025-08-31", "years": 2}, ctx({}))
    assert np.allclose(r.outs[0].data, 0.0, atol=1e-4) and r.outs[0].summary["compared with"] == "2 earlier years"


def test_water_change(cat):
    g = G()
    green = np.full((g["height"], g["width"]), 0.05)
    green[:10] = 0.3                                                                     # rows 0-9 become water (MNDWI > 0)
    s2_scene(cat, "b", "2025-07-01", {"Green": 0.05, "SWIR1": 0.2})
    s2_scene(cat, "a", "2026-07-01", {"Green": green, "SWIR1": 0.2})
    r = pe.water_change_tool({**BASE, "before_start": "2025-06-01", "before_end": "2025-07-31", "after_start": "2026-06-01", "after_end": "2026-07-31"}, ctx({}))
    c = r.outs[0].data
    assert (c[:10] == 2).all() and c[10:].mask.all()


# ---------- rainfall ----------
def test_rainfall_totals_and_months_not_yet_published(cat, monkeypatch):
    world = {"crs": rasterio.crs.CRS.from_epsg(4326), "transform": rasterio.transform.from_origin(6.0, 10.0, 0.05, 0.05), "width": 40, "height": 40}
    for m, mm in ((6, 120.0), (7, 200.0)):
        tif(cat.dir / f"chirps-2026-{m:02d}.tif", world, mm, "float32", -9999)
    monkeypatch.setattr(pe, "CHIRPS", str(cat.dir / "chirps-{y}-{m:02d}.tif"))
    r = pe.rainfall_tool({"bbox": MINNA, "start": "2026-06-01", "end": "2026-08-31"}, ctx({}))
    assert np.allclose(r.outs[0].data, 320.0) and r.outs[0].summary["months"] == 2 and "Aug 2026" in r.notes[0]
    s = pe.time_series_tool({"bbox": MINNA, "measure": "rainfall", "start": "2026-06-01", "end": "2026-07-31", "step": "month"}, ctx({}))
    assert s.chart["x"] == ["2026-06", "2026-07"] and s.chart["series"][0]["values"] == [120.0, 200.0] and s.chart["unit"] == "mm"
    with pytest.raises(Exception, match="50 degrees"):
        pe.rainfall_tool({"bbox": [10, 55, 11, 56], "start": "2026-06-01", "end": "2026-07-31"}, ctx({}))


# ---------- flood mapping (UN-SPIDER) ----------
def test_flood_map_follows_un_spider(cat):
    g = G()
    H, W = g["height"], g["width"]
    before_lin = np.full((H, W), 10 ** (-1.0), np.float32)                              # -10 dB everywhere
    after_db = np.full((H, W), -10.0)
    after_db[30:70, 30:70] = -22.0                                                       # a flooded field (ratio 2.2)
    after_db[5, 100] = -22.0                                                             # one speck (smoothed away / too small)
    def radar(name, date, lin, orbit="descending"):
        return cat.add("sentinel-1-rtc", {"id": name, "properties": {"datetime": f"{date}T05:00:00Z", "sat:orbit_state": orbit},
                                           "assets": {"vh": {"href": tif(cat.dir / f"{name}.tif", g, lin, "float32", -32768)}}})
    radar("pre", "2026-08-01", before_lin)
    radar("post", "2026-09-10", (10 ** (after_db / 10)).astype(np.float32))
    cat.add("cop-dem-glo-30", {"id": "dem", "properties": {}, "assets": {"data": {"href": tif(cat.dir / "dem.tif", g, 150.0, "float32", -32768)}}})
    season = np.zeros((H, W), np.uint8)
    season[30:40, 30:70] = 12                                                            # a permanent river through the field
    cat.add("jrc-gsw", {"id": "gsw", "properties": {}, "assets": {"seasonality": {"href": tif(cat.dir / "season.tif", g, season, "uint8", 255)}}})
    r = pe.flood_tool({"bbox": MINNA, "before_start": "2026-07-20", "before_end": "2026-08-10", "after_start": "2026-09-05", "after_end": "2026-09-15",
                       "polarisation": "vh", "orbit": "auto", "threshold": 1.25}, ctx({}))
    fl = r.outs[0].data.filled(0)
    assert fl[45:65, 35:65].all()                                                        # the field's inside is flooded
    assert not fl[30:40, 30:70].any()                                                    # the river is permanent water, not flood
    assert not fl[:20, 90:].any()                                                        # no speck
    assert r.outs[0].summary["pass"] == "descending" and r.outs[0].summary["flooded area (ha)"] > 9


# ---------- classification ----------
def two_class_scene(cat):
    g = G()
    nir = np.full((g["height"], g["width"]), 0.05)
    green = np.full((g["height"], g["width"]), 0.08)
    nir[:, 55:] = 0.45                                                                   # east: vegetation; west: water
    green[:, 55:] = 0.06
    s2_scene(cat, "img", "2026-01-10", {"Red": 0.05, "NIR": nir, "Green": green, "SWIR1": np.where(nir > 0.1, 0.2, 0.02)})
    left, top = g["transform"].c, g["transform"].f
    boxes = [(5, 5, "water"), (5, 60, "water"), (5, 90, "water"), (65, 10, "trees"), (65, 60, "trees"), (65, 90, "trees")]
    geoms = rt.to_wgs84([shapely.box(left + c * 10, top - (r + 15) * 10, left + (c + 15) * 10, top - r * 10) for r, c, _ in
                         [(row, col, k) for col, row, k in boxes]], g["crs"])
    feats = [{"id": i + 1, "geom": gg, "props": {"cover": k}} for i, (gg, (_, _, k)) in enumerate(zip(geoms, boxes))]
    return g, feats, {"training": {"name": "Training", "geometry_type": "Polygon", "fields": [{"name": "cover", "type": "string"}]}}


@pytest.mark.parametrize("method", ["rf", "cart", "svm", "nb", "mindist"])
def test_classification_and_its_accuracy(cat, method):
    g, feats, layers = two_class_scene(cat)
    r = pe.classify_tool({**BASE, "start": "2026-01-01", "end": "2026-01-31", "field": "cover", "method": method, "test": 34, "trees": 20},
                         ctx({}, layers, {"training": feats}))
    o = r.outs[0]
    assert [c["label"] for c in o.style["categories"]] == ["trees", "water"]
    assert (o.data[:, :50] == 2).all() and (o.data[:, 60:] == 1).all()
    rep = r.report
    assert rep["classes"] == ["trees", "water"] and rep["overall"] == 100.0 and rep["kappa"] == 1.0 and sum(map(sum, rep["matrix"])) > 0
    assert o.summary["water (ha)"] + o.summary["trees (ha)"] == pytest.approx(g["width"] * g["height"] * 0.01)


def test_classification_needs_two_classes_and_says_when_a_class_has_one_place(cat):
    g, feats, layers = two_class_scene(cat)
    with pytest.raises(Exception, match="at least two classes"):
        pe.classify_tool({**BASE, "start": "2026-01-01", "end": "2026-01-31", "field": "cover"}, ctx({}, layers, {"training": feats[:3]}))
    r = pe.classify_tool({**BASE, "start": "2026-01-01", "end": "2026-01-31", "field": "cover", "method": "cart"}, ctx({}, layers, {"training": [feats[0], *feats[3:]]}))
    assert "water had a single training place" in r.report["note"]
    far = {"id": 99, "geom": shapely.box(7.5, 9.0, 7.51, 9.01), "props": {"cover": "trees"}}                  # outside the area
    r = pe.classify_tool({**BASE, "start": "2026-01-01", "end": "2026-01-31", "field": "cover", "method": "cart"}, ctx({}, layers, {"training": feats + [far]}))
    assert r.notes and r.notes[0].startswith("1 of the 7 training places were outside the area")


def test_unsupervised_classes_run_from_least_to_most_green(cat):
    two_class_scene(cat)
    r = pe.cluster_tool({**BASE, "start": "2026-01-01", "end": "2026-01-31", "classes": 2}, ctx({}))
    o = r.outs[0].data
    assert (o[:, :50] == 1).all() and (o[:, 60:] == 2).all()


# ---------- statistics ----------
def test_index_statistics_by_polygon(cat):
    g = G()
    nir = np.full((g["height"], g["width"]), 0.3)
    nir[:, 50:] = 0.5
    s2_scene(cat, "img", "2026-01-10", {"Red": 0.1, "NIR": nir})
    left, top = g["transform"].c, g["transform"].f
    polys = rt.to_wgs84([shapely.box(left + 100, top - 400, left + 400, top - 100), shapely.box(left + 600, top - 400, left + 900, top - 100)], g["crs"])
    feats = [{"id": i, "geom": p, "props": {"farm": f"F{i}"}} for i, p in enumerate(polys)]
    layers = {"zones": {"name": "Farms", "geometry_type": "Polygon", "fields": [{"name": "farm", "type": "string"}]}}
    r = pe.zonal_index_tool({**BASE, "index": "ndvi", "start": "2026-01-01", "end": "2026-01-31"}, ctx({}, layers, {"zones": feats}))
    rows = r.outs[0].rows
    assert rows[0][1]["farm"] == "F0" and rows[0][1]["ndvi_mean"] == pytest.approx(0.5) and rows[1][1]["ndvi_mean"] == pytest.approx(0.6667, abs=1e-4)
    assert rows[0][1]["clear_pct"] == 100.0 and {"name": "ndvi_median", "type": "number"} in r.outs[0].fields


def test_land_cover_breakdown(cat):
    g = imagery.plan(MINNA, 10, 10**9)
    lc = np.full((g["height"], g["width"]), 10, np.uint8)
    lc[:, :55] = 40
    cat.add("esa-worldcover", {"id": "wc", "properties": {"datetime": "2021-01-01T00:00:00Z"}, "assets": {"map": {"href": tif(cat.dir / "wc.tif", g, lc, "uint8", 0)}}})
    r = pe.land_cover_tool({"bbox": MINNA}, ctx({}))
    o = r.outs[0]
    assert [c["label"] for c in o.style["categories"]] == ["Tree cover", "Cropland"]
    assert o.summary["Cropland (ha)"] == pytest.approx(g["height"] * 55 * 0.01)


# ---------- the API: background runs ----------
@pytest.fixture
def analyst(person):
    return person(role="member", name="Ngozi Analyst", features=["maps", "editing", "export", "raster", "geoprocessing"])


def wait(who, job, most=60):
    t = time.time()
    while time.time() - t < most:
        j = who.get(f"/poly-earth/jobs/{job['id']}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.1)
    raise AssertionError("the run did not finish")


def test_api_lists_the_tools(analyst):
    r = analyst.get("/poly-earth/tools")
    assert r.status_code == 200
    ids = {t["id"] for t in r.json()}
    assert {"composite", "index", "time_series", "change", "flood", "classify", "trend", "rainfall"} <= ids and len(ids) >= 18
    comp = next(t for t in r.json() if t["id"] == "composite")
    assert comp["params"][0]["type"] == "area" and any(p["type"] == "date" for p in comp["params"])


def test_api_runs_in_the_background_and_adds_the_layers(analyst, cat):
    s2_scene(cat, "a", "2026-01-05", {"Red": 0.1, "NIR": 0.4})
    pid = analyst.post("/projects", json={"name": "Poly Earth"}).json()["id"]
    r = analyst.post(f"/projects/{pid}/poly-earth/index", json={"params": {"bbox": MINNA, "index": "ndvi", "start": "2026-01-01", "end": "2026-01-31"}})
    assert r.status_code == 202 and r.json()["status"] in ("queued", "running", "done")
    j = wait(analyst, r.json())
    assert j["status"] == "done", j
    lay = j["layers"][0]
    assert lay["geometry_type"] == "Raster" and lay["name"].startswith("NDVI") and lay["source"]["summary"]["mean"] == pytest.approx(0.6, abs=1e-4)
    runs = analyst.get(f"/projects/{pid}/runs").json()
    assert runs[0]["tool"] == "earth:index" and runs[0]["status"] == "done" and runs[0]["output_layer_id"] == lay["id"]


def test_api_a_failed_run_says_why_and_is_kept_in_the_history(analyst, cat):
    pid = analyst.post("/projects", json={"name": "Empty"}).json()["id"]
    r = analyst.post(f"/projects/{pid}/poly-earth/index", json={"params": {"bbox": MINNA, "start": "2026-01-01", "end": "2026-01-31"}})
    j = wait(analyst, r.json())
    assert j["status"] == "failed" and "No Sentinel-2 scenes" in j["error"]
    assert analyst.get(f"/projects/{pid}/runs").json()[0]["status"] == "failed"


def test_api_defaults_dates_and_checks_layers(analyst, cat):
    pid = analyst.post("/projects", json={"name": "Checks"}).json()["id"]
    lines = analyst.post(f"/projects/{pid}/layers", json={"name": "Roads", "geometry_type": "LineString", "fields": []}).json()
    r = analyst.post(f"/projects/{pid}/poly-earth/zonal_index", json={"params": {"zones": lines["id"]}})
    assert r.status_code == 422 and "must be polygons" in r.json()["detail"]
    r = analyst.post(f"/projects/{pid}/poly-earth/nothing", json={"params": {}})
    assert r.status_code == 404


def test_api_others_cannot_see_a_run_and_viewers_cannot_start_one(analyst, person, cat):
    s2_scene(cat, "a", "2026-01-05", {"Red": 0.1, "NIR": 0.4})
    pid = analyst.post("/projects", json={"name": "Mine"}).json()["id"]
    j = analyst.post(f"/projects/{pid}/poly-earth/index", json={"params": {"bbox": MINNA, "start": "2026-01-01", "end": "2026-01-31"}}).json()
    other = person(role="member", name="Other", features=["maps", "raster"])
    assert other.get(f"/poly-earth/jobs/{j['id']}").status_code == 404
    wait(analyst, j)
    viewer = person(role="viewer", name="Vee", features=["maps", "raster"])
    assert viewer.post(f"/projects/{pid}/poly-earth/index", json={"params": {}}).status_code == 403
    basic = person(role="member", name="Basic", features=["maps", "editing"])
    assert basic.post(f"/projects/{pid}/poly-earth/index", json={"params": {}}).status_code == 403


# ---------- reading only what is needed ----------
def test_a_coarse_grid_reads_the_files_overviews_and_masks_their_full_detail(cat, monkeypatch):
    fine = imagery.plan(MINNA, 10, 10**9)
    path = cat.dir / "big.tif"
    a = np.full((fine["height"], fine["width"]), 2500, np.uint16)
    with rasterio.open(path, "w", driver="GTiff", width=fine["width"], height=fine["height"], count=1, dtype="uint16", crs=fine["crs"],
                       transform=fine["transform"], nodata=0, tiled=True, blockxsize=32, blockysize=32) as d:
        d.write(a, 1)
        d.build_overviews([2, 4, 8], rasterio.enums.Resampling.average)
    coarse = imagery.plan(MINNA, 40, 10**9)
    opened = []
    real = rasterio.open
    monkeypatch.setattr(pe.rasterio, "open", lambda p, *k, **kw: (opened.append(kw.get("overview_level")), real(p, *k, **kw))[1])
    v, ok = pe.read_onto(str(path), coarse)
    assert ok.all() and np.allclose(v, 2500) and opened[-1] == 1               # overview x4: 40 m cells, the grid's own
    opened.clear()
    pe.read_onto(str(path), coarse, nearest=True)
    assert opened == [None]                                                      # a mask: full detail
    opened.clear()
    pe.read_onto(str(path), fine)
    assert opened[-1] is None                                                    # a 10 m grid: no overview is fine enough


def test_a_period_takes_scenes_from_every_tile_in_turn_up_to_a_limit():
    items = [{"id": f"{t}{k}", "properties": {"s2:mgrs_tile": t, "eo:cloud_cover": k}} for t in "ABCDEFGHIJ" for k in range(10)]
    got = pe.pick(items, 6)
    assert len(got) == pe.MAX_PERIOD_SCENES and {i["id"][0] for i in got} == set("ABCDEFGHIJ")
    assert [i["id"] for i in got[:10]] == [f"{t}0" for t in "ABCDEFGHIJ"]          # each tile's clearest first



# ---------- a raster for each interval, and clipping to polygons ----------
def test_time_series_keeps_a_raster_for_each_interval_or_one_band_each(cat):
    for m, red in ((1, 0.2), (2, 0.1), (3, 0.05)):
        s2_scene(cat, f"m{m}", f"2026-0{m}-10", {"Red": red, "NIR": 0.3})
    p = {"bbox": MINNA, "measure": "index", "index": "ndvi", "start": "2026-01-01", "end": "2026-03-31", "step": "month"}
    each = pe.time_series_tool({**p, "rasters": "each"}, ctx({}))
    assert [o.name for o in each.outs] == [f"NDVI (Sentinel-2) 2026-0{m}" for m in (1, 2, 3)]
    assert all(o.style["min"] == -0.2 and o.style["max"] == 0.9 for o in each.outs)                         # one scale to compare by eye
    stack = pe.time_series_tool({**p, "rasters": "stack"}, ctx({})).outs
    assert len(stack) == 1 and stack[0].names == ["2026-01", "2026-02", "2026-03"] and stack[0].data.shape[0] == 3
    assert np.nanmean(stack[0].data[1].filled(np.nan)) == pytest.approx(0.5, abs=1e-4)
    assert pe.time_series_tool({**p, "rasters": "none"}, ctx({})).outs == []
    yearly = pe.time_series_tool({**p, "step": "year", "start": "2025-06-01"}, ctx({}))
    assert [o.name for o in yearly.outs] == ["NDVI (Sentinel-2) 2026"] and "No raster for 2025" in " ".join(yearly.notes)
    assert yearly.chart["x"] == ["2025", "2026"] and yearly.chart["series"][0]["values"][0] is None


def test_a_run_clipped_to_a_polygon_has_nothing_outside_it(cat):
    g = G()
    s2_scene(cat, "a", "2026-01-05", {"Red": 0.1, "NIR": 0.4})
    left, top = g["transform"].c, g["transform"].f
    poly = rt.to_wgs84([shapely.box(left + 200, top - 600, left + 600, top - 200)], g["crs"])[0]
    pe.set_clip(poly)
    try:
        r = pe.index_tool({**BASE, "index": "ndvi", "start": "2026-01-01", "end": "2026-01-31"}, ctx({}))
        comp = pe.composite_tool({**BASE, "start": "2026-01-01", "end": "2026-01-31", "bands": "rgbn"}, ctx({}))
        ts = pe.time_series_tool({"bbox": MINNA, "measure": "index", "index": "ndvi", "start": "2026-01-01", "end": "2026-01-31", "step": "month",
                                  "rasters": "none"}, ctx({}))
    finally:
        pe.set_clip(None)
    d = r.outs[0].data
    kept = (~d.mask).sum()
    assert 1500 <= kept <= 1800 and d.mask[0, 0] and np.allclose(d.compressed(), 0.6)                     # 40 x 40 cells (+ edges touched)
    assert r.outs[0].summary["mean"] == pytest.approx(0.6)
    assert comp.outs[0].data.mask[:, 0, 0].all() and comp.outs[0].summary["clear %"] == 100.0
    assert ts.chart["series"][0]["name"] == "The polygons"


def test_scenes_that_miss_the_polygons_are_not_read(cat):
    s2_scene(cat, "near", "2026-01-05", {"Red": 0.1, "NIR": 0.4})
    far = s2_scene(cat, "far", "2026-01-06", {"Red": 0.1, "NIR": 0.4})
    far["geometry"] = shapely.geometry.mapping(shapely.box(10, 10, 11, 11))
    got = pe.find_scenes("sentinel2", PC, MINNA, dt.date(2026, 1, 1), dt.date(2026, 1, 31), 60, 5, shapely.box(*MINNA))
    assert [i["id"] for i in got] == ["near"]


def test_api_clips_to_a_polygon_layer(analyst, cat):
    s2_scene(cat, "a", "2026-01-05", {"Red": 0.1, "NIR": 0.4})
    pid = analyst.post("/projects", json={"name": "Clip"}).json()["id"]
    farms = analyst.post(f"/projects/{pid}/layers", json={"name": "Farms", "geometry_type": "Polygon", "fields": []}).json()
    g = G()
    left, top = g["transform"].c, g["transform"].f
    poly = rt.to_wgs84([shapely.box(left + 200, top - 600, left + 600, top - 200)], g["crs"])[0]
    analyst.post(f"/layers/{farms['id']}/features", json={"geometry": shapely.geometry.mapping(poly), "properties": {}})
    r = analyst.post(f"/projects/{pid}/poly-earth/index", json={"params": {"bbox": [0, 0, 1, 1], "clip": farms["id"], "start": "2026-01-01", "end": "2026-01-31"}})
    j = wait(analyst, r.json())
    assert j["status"] == "done", j
    lay = j["layers"][0]
    w, s, e, n = lay["bbox"]
    assert w > poly.bounds[0] - 0.001 and e < poly.bounds[2] + 0.001                   # the grid is the polygon's, not the box sent
    lines = analyst.post(f"/projects/{pid}/layers", json={"name": "Roads", "geometry_type": "LineString", "fields": []}).json()
    bad = analyst.post(f"/projects/{pid}/poly-earth/index", json={"params": {"clip": lines["id"]}})
    assert bad.status_code == 422 and "must be polygons" in bad.json()["detail"]
