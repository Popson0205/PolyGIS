"""Raster layers and raster tools, checked against numbers worked out by hand: slopes of planes, curvature of a
paraboloid, flow on a valley built to drain to one cell, volumes, distances, densities, zonal statistics."""
import io
import json
import math

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

X0, Y0, CELL = 230000.0, 1010000.0, 30.0          # UTM 32N, near Minna
UTM = "EPSG:32632"
TO_LL = Transformer.from_crs(UTM, 4326, always_xy=True)


def tif(arr, crs=UTM, transform=None, nodata=None, dtype=None) -> bytes:
    arr = np.asarray(arr)
    a = arr if arr.ndim == 3 else arr[None]
    b = io.BytesIO()
    with rasterio.MemoryFile() as mf:
        with mf.open(driver="GTiff", width=a.shape[2], height=a.shape[1], count=a.shape[0], dtype=dtype or a.dtype, crs=crs,
                     transform=transform or from_origin(X0, Y0, CELL, CELL), nodata=nodata) as d:
            d.write(a.astype(dtype or a.dtype))
        b.write(mf.read())
    return b.getvalue()


def ll(x, y):
    return list(TO_LL.transform(x, y))


def cell_ll(r, c):
    return ll(X0 + (c + 0.5) * CELL, Y0 - (r + 0.5) * CELL)


N = 100
cols = np.arange(N)[None, :].repeat(N, 0)
rows = np.arange(N)[:, None].repeat(N, 1)
PLANE = (100 + 0.1 * (cols + 0.5) * CELL).astype("float32")                    # rises 0.1 m per metre to the east
VALLEY = (10 + 3.0 * np.abs(cols - 50) + 0.6 * (N - 1 - rows)).astype("float32")  # drains south, to the middle of the bottom edge
VALLEY[40, 50] -= 50                                                             # a pit
PARAB = ((((cols - 50) * CELL) ** 2 + ((rows - 50) * CELL) ** 2) / 1000).astype("float32")
CLASSES = np.ones((N, N), "uint8")
CLASSES[:, 50:] = 2
CLASSES[60:, :] = 3
CLASSES[10, 10] = 2                                                              # a speck
IMAGE = np.stack([np.full((N, N), 400), np.full((N, N), 600), np.full((N, N), 500), np.full((N, N), 3000)]).astype("uint16")
IMAGE[2:, :, :50] = np.array([2500, 2500])[:, None, None]                       # left half: red = NIR (bare soil, NDVI 0)
WALL = np.zeros((N, N), "float32")
WALL[:, 60] = 100                                                                # a wall 10 cells east of the observer at col 50


def fc(*feats):
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": p, "geometry": g} for g, p in feats]}


def square_ll(r1, c1, r2, c2):
    """A polygon on cell edges, rows r1..r2 and cols c1..c2 (exclusive)."""
    pts = [(X0 + c1 * CELL, Y0 - r1 * CELL), (X0 + c2 * CELL, Y0 - r1 * CELL), (X0 + c2 * CELL, Y0 - r2 * CELL), (X0 + c1 * CELL, Y0 - r2 * CELL)]
    ring = [ll(*q) for q in pts]
    return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}


@pytest.fixture
def pro(person):
    return person(role="member", name="Rasa Analyst", features=["maps", "editing", "export", "geoprocessing", "raster"])


@pytest.fixture
def R(pro):
    p = pro.post("/projects", json={"name": "Rasters"}).json()

    def up(name, data):
        r = pro.post(f"/projects/{p['id']}/layers/import", files={"file": (name, data)})
        assert r.status_code == 201, r.text
        return r.json()[0]
    rng = np.random.default_rng(1)
    samples = [(c, r, float(PLANE[r, c])) for r, c in rng.integers(5, 95, (30, 2))]
    return {
        "p": p, "plane": up("plane.tif", tif(PLANE)), "valley": up("valley.tif", tif(VALLEY)), "parab": up("parab.tif", tif(PARAB)),
        "classes": up("classes.tif", tif(CLASSES)), "image": up("image.tif", tif(IMAGE)), "wall": up("wall.tif", tif(WALL)),
        "obs": up("obs.geojson", json.dumps(fc(({"type": "Point", "coordinates": cell_ll(50, 50)}, {"name": "tower"}))).encode()),
        "outlet": up("outlet.geojson", json.dumps(fc(({"type": "Point", "coordinates": cell_ll(98, 50)}, {"name": "outlet"}))).encode()),
        "zones": up("zones.geojson", json.dumps(fc((square_ll(0, 50, 60, 100), {"zone": "NE"}), (square_ll(0, 0, 60, 50), {"zone": "NW"}),
                                                   (square_ll(60, 0, 100, 100), {"zone": "S"}))).encode()),
        "samples": up("samples.geojson", json.dumps(fc(*[({"type": "Point", "coordinates": cell_ll(r, c)}, {"z": z}) for c, r, z in samples])).encode()),
        "line": up("line.geojson", json.dumps(fc(({"type": "LineString", "coordinates": [cell_ll(50, 5), cell_ll(50, 95)]}, {"n": 1}))).encode()),
        "training": up("training.geojson", json.dumps(fc((square_ll(10, 5, 40, 40), {"cls": 1}), (square_ll(10, 60, 40, 95), {"cls": 2}))).encode()),
    }


def run(c, R, tool, **params):
    params = {k: (R[v]["id"] if isinstance(v, str) and v in R and k != "field" else [R[x]["id"] for x in v] if isinstance(v, list) and v and v[0] in R else v)
              for k, v in params.items()}
    r = c.post(f"/projects/{R['p']['id']}/raster-tools/{tool}", json={"params": params})
    assert r.status_code == 201, (tool, r.text)
    return r.json()["layer"]


def read(layer) -> tuple[np.ndarray, object]:
    from app import rasters
    with rasterio.open(rasters.local(layer["raster"]["key"])) as s:
        return s.read(1, masked=True).astype(float).filled(np.nan), s


def rows(c, layer):
    return [r["props"] for r in c.post(f"/layers/{layer['id']}/rows", json={"limit": 1000}).json()["rows"]]


EVERY = {
    "slope": dict(input="plane"), "aspect": dict(input="plane"), "hillshade": dict(input="valley"), "multi_hillshade": dict(input="valley"),
    "curvature": dict(input="parab", kind="profile"), "tpi": dict(input="valley"), "tri": dict(input="valley"), "roughness": dict(input="valley"),
    "vrm": dict(input="valley"), "contours": dict(input="plane", interval=50), "viewshed": dict(input="wall", observers="obs", radius=2000),
    "cut_fill": dict(input="plane", after="valley"), "profile": dict(lines="line", input="plane", interval=100),
    "fill": dict(input="valley"), "sinks": dict(input="valley"), "flow_direction": dict(input="valley"), "flow_accumulation": dict(input="valley", unit="km2"),
    "stream_order": dict(input="valley", threshold=0.5), "stream_network": dict(input="valley", threshold=0.5),
    "snap_pour_points": dict(points="outlet", input="valley"), "watershed": dict(points="outlet", input="valley"), "basins": dict(input="valley"),
    "twi": dict(input="valley"), "flow_length": dict(input="valley"),
    "calculator": dict(A="plane", B="valley", expression="con(A > B, A - B, 0)"), "normalized_difference": dict(input="image", band_1=4, band_2=3),
    "savi": dict(input="image", band_1=4, band_2=3), "evi": dict(input="image", scale=10000), "reclassify": dict(input="plane", table="100 - 500: 1; 500 - 1000: 2"),
    "set_null": dict(input="classes", min=3, max=3), "rescale": dict(input="plane"), "math": dict(input="plane", function="sqrt"),
    "cell_statistics": dict(rasters=["plane", "valley"], stat="max"), "slice": dict(input="valley", classes=4, method="quantile"),
    "focal_statistics": dict(input="classes", stat="majority", radius=1), "zonal_statistics": dict(zones="zones", input="plane"),
    "tabulate_area": dict(zones="zones", input="classes"), "extract_values": dict(points="samples", input="image"),
    "region_group": dict(input="classes"), "majority_filter": dict(input="classes"), "sieve": dict(input="classes", cells=4),
    "expand": dict(input="classes", values="3", cells=2), "shrink": dict(input="classes", values="3", cells=2),
    "euclidean_distance": dict(sources="obs", cell=30, max_distance=900), "euclidean_allocation": dict(sources="samples", cell=60, max_distance=500),
    "cost_distance": dict(sources="obs", input="classes"), "cost_path": dict(sources="obs", destinations="samples", input="classes"),
    "kernel_density": dict(points="samples", radius=600, cell=30), "point_density": dict(points="samples", radius=600, cell=60),
    "idw": dict(points="samples", field="z", cell=60), "nearest_interpolation": dict(points="samples", field="z", cell=60),
    "tin_interpolation": dict(points="samples", field="z", cell=60), "spline": dict(points="samples", field="z", cell=60),
    "raster_to_polygons": dict(input="classes"), "raster_to_points": dict(input="classes"), "features_to_raster": dict(features="zones", cell=60),
    "reproject_raster": dict(input="plane", epsg="4326"), "resample": dict(input="plane", cell=90), "clip_raster": dict(input="plane", clip="zones"),
    "mosaic": dict(rasters=["plane", "valley"], overlap="max"), "composite_bands": dict(rasters=["plane", "valley", "parab"]),
    "extract_bands": dict(input="image", bands="4, 3, 2"), "copy_raster": dict(input="plane", dtype="int16"),
    "unsupervised_classification": dict(input="image", classes=2), "supervised_classification": dict(input="image", training="training", field="cls"),
    "principal_components": dict(input="image", components=2), "filter": dict(input="valley", kind="sobel"), "stretch": dict(input="image", method="equalize"),
    "change_detection": dict(input="plane", after="valley", threshold=5),
    "to_reflectance": dict(input="image"), "dark_object_subtraction": dict(input="image"), "cloud_mask": dict(input="image"),
    "topographic_correction": dict(input="image", dem="valley"), "pan_sharpen": dict(input="image", pan="plane"), "fill_nodata": dict(input="classes"),
    "brightness_contrast": dict(input="image", gamma=1.5), "unsharp_mask": dict(input="image"), "clahe": dict(input="image"),
    "dehaze": dict(input="image"), "histogram_match": dict(input="image", reference="image"), "denoise": dict(input="valley", method="lee"),
}


@pytest.mark.parametrize("tool", sorted(EVERY))
def test_every_raster_tool_runs(pro, R, tool):
    lay = run(pro, R, tool, **EVERY[tool])
    assert lay["geometry_type"] == "Raster" and lay["raster"]["width"] > 0 or lay["feature_count"] > 0, (tool, lay)


def test_catalog_covers_every_tool(pro):
    ids = {t["id"] for t in pro.get("/raster-tools").json()}
    assert ids == set(EVERY) and len(ids) >= 60


def test_import_tiles_values_and_style(pro, R):
    lay = R["plane"]
    m = lay["raster"]
    assert (m["width"], m["height"], m["count"], m["epsg"], m["res_m"]) == (100, 100, 1, 32632, [30.0, 30.0])
    assert lay["bbox"] == m["bbox"] and lay["style"]["mode"] == "ramp"
    # the value at a cell's centre
    v = pro.get(f"/layers/{lay['id']}/raster/value", params=dict(zip(("lng", "lat"), cell_ll(20, 37)))).json()
    assert v["values"] == [pytest.approx(float(PLANE[20, 37]))]
    # a tile over the raster is a PNG with colour; one far away is empty
    lng, lat = cell_ll(50, 50)
    z = 12
    x, y = int((lng + 180) / 360 * 2 ** z), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2 ** z)
    t = pro.get(f"/layers/{lay['id']}/raster/{z}/{x}/{y}.png?v=1")
    assert t.status_code == 200 and t.headers["content-type"] == "image/png" and len(t.content) > 500
    assert len(pro.get(f"/layers/{lay['id']}/raster/{z}/0/0.png").content) < 200
    st = pro.put(f"/layers/{lay['id']}/raster-style", json={"mode": "ramp", "band": 1, "min": 0, "max": 500, "ramp": "Viridis"})
    assert st.status_code == 200 and st.json()["style"]["ramp"] == "Viridis", st.text
    assert pro.put(f"/layers/{lay['id']}/raster-style", json={"mode": "ramp", "band": 2}).status_code == 422
    d = pro.get(f"/layers/{lay['id']}/raster/download")
    assert d.status_code == 200 and d.content[:2] in (b"II", b"MM")
    # vector tools and drawing refuse rasters
    r = pro.post(f"/projects/{R['p']['id']}/tools/buffer", json={"params": {"input": lay["id"], "distance": 10}})
    assert r.status_code == 422 and "raster" in r.json()["detail"]
    assert pro.post(f"/layers/{lay['id']}/features", json={"geometry": {"type": "Point", "coordinates": [7, 9]}}).status_code == 422
    assert pro.delete(f"/layers/{lay['id']}").status_code == 204


def test_plan_gates_raster_tools(person, R):
    std = person(role="member", features=["maps", "editing", "export", "geoprocessing"])
    r = std.post(f"/projects/{R['p']['id']}/raster-tools/slope", json={"params": {"input": R["plane"]["id"]}})
    assert r.status_code == 403 and "Advanced" in r.json()["detail"]


def test_slope_aspect_hillshade_on_a_plane(pro, R):
    s, _ = read(run(pro, R, "slope", input="plane"))
    assert np.allclose(s[1:-1, 1:-1], math.degrees(math.atan(0.1)), atol=1e-4)
    pc, _ = read(run(pro, R, "slope", input="plane", unit="percent"))
    assert np.allclose(pc[1:-1, 1:-1], 10, atol=1e-3)
    a, _ = read(run(pro, R, "aspect", input="plane"))
    assert np.allclose(a[1:-1, 1:-1], 270, atol=1e-3)                  # rises to the east: faces west
    h, _ = read(run(pro, R, "hillshade", input="plane"))
    sl = math.atan(0.1)
    want = 255 * (math.cos(math.radians(45)) * math.cos(sl) + math.sin(math.radians(45)) * math.sin(sl) * math.cos(math.radians(135) - math.pi))
    assert np.all(np.abs(h[1:-1, 1:-1] - want) <= 0.5)
    t, _ = read(run(pro, R, "tpi", input="plane", radius=2))
    assert np.allclose(t[3:-3, 3:-3], 0, atol=1e-3)


def test_slope_of_a_geographic_raster_is_in_metres(pro, R):
    lat0, lng0, d = 9.0, 6.5, 0.0003                                     # about 33 m cells
    lng = lng0 + (np.arange(N) + 0.5) * d
    lat = lat0 - (np.arange(N) + 0.5) * d
    z = 0.1 * (lng[None, :] - lng0) * 111_320 * np.cos(np.radians(lat[:, None]))
    up = pro.post(f"/projects/{R['p']['id']}/layers/import", files={"file": ("geo.tif", tif(z.astype("float32"), crs="EPSG:4326",
                                                                                         transform=from_origin(lng0, lat0, d, d)))}).json()[0]
    s, _ = read(run(pro, {**R, "geo": up}, "slope", input="geo"))
    assert np.allclose(s[2:-2, 2:-2], math.degrees(math.atan(0.1)), atol=0.02)


def test_curvature_of_a_paraboloid(pro, R):
    c, _ = read(run(pro, R, "curvature", input="parab"))
    # z = (x² + y²) / 1000: D = E = 1/1000, total curvature = -2 (D + E) × 100 = -0.4 everywhere
    assert np.allclose(c[1:-1, 1:-1], -0.4, atol=1e-4)


def test_hydrology_on_a_valley(pro, R):
    s, _ = read(run(pro, R, "sinks", input="valley"))
    assert s[40, 50] == pytest.approx(10 + 0.6 * 58 - (10 + 0.6 * 59 - 50), abs=1e-3)     # filled to the spill height below it
    assert np.count_nonzero(s > 1e-6) == 1
    acc, _ = read(run(pro, R, "flow_accumulation", input="valley"))
    assert acc[99, 50] == N * N                                        # every cell drains to the outlet
    assert acc[0, 0] == 1 and acc[0, 50] == N        # the whole top row runs into the valley floor
    fd, _ = read(run(pro, R, "flow_direction", input="valley"))
    assert fd[20, 50] == 4 and fd[20, 10] == 1 and fd[20, 90] == 16  # down the middle south; the sides flow in
    ws = run(pro, R, "watershed", points="outlet", input="valley")
    assert rows(pro, ws)[0]["area_km2"] == pytest.approx(N * N * CELL * CELL / 1e6, rel=1e-9)
    b = run(pro, R, "basins", input="valley")
    assert b["source"]["summary"]["basins"] == 1
    fl, _ = read(run(pro, R, "flow_length", input="valley"))
    assert fl[0, 50] == pytest.approx(99 * CELL, abs=1e-6)            # straight down the middle to the outlet
    sn = rows(pro, run(pro, R, "stream_network", input="valley", threshold=0.5))
    assert max(r["order"] for r in sn) >= 1 and all(r["length_m"] > 0 for r in sn)


def test_map_algebra(pro, R):
    c, _ = read(run(pro, R, "calculator", A="plane", expression="A * 2 + 1"))
    assert np.allclose(c, PLANE * 2 + 1, atol=1e-3)
    nd, _ = read(run(pro, R, "normalized_difference", input="image", band_1=4, band_2=3))
    assert nd[50, 75] == pytest.approx((3000 - 500) / 3500, abs=1e-6) and nd[50, 10] == pytest.approx(0, abs=1e-6)
    rc, _ = read(run(pro, R, "reclassify", input="classes", table="1: 10; 2: 20; 3: 30"))
    assert set(np.unique(rc[np.isfinite(rc)])) == {10, 20, 30} and np.count_nonzero(rc == 30) == np.count_nonzero(CLASSES == 3)
    bad = pro.post(f"/projects/{R['p']['id']}/raster-tools/calculator", json={"params": {"A": R["plane"]["id"], "expression": "__import__('os')"}})
    assert bad.status_code == 422
    cf = run(pro, R, "cut_fill", input="plane", after="plane")
    assert cf["source"]["summary"]["cut_m3"] == 0 and cf["source"]["summary"]["fill_m3"] == 0


def test_cut_and_fill_volumes(pro, R):
    after = PLANE.copy()
    after[10:20, 10:20] += 2
    after[30:35, 30:40] -= 1
    up = pro.post(f"/projects/{R['p']['id']}/layers/import", files={"file": ("after.tif", tif(after))}).json()[0]
    s = run(pro, {**R, "after": up}, "cut_fill", input="plane", after="after")["source"]["summary"]
    assert s["fill_m3"] == pytest.approx(2 * 100 * CELL * CELL, rel=1e-5) and s["cut_m3"] == pytest.approx(1 * 50 * CELL * CELL, rel=1e-5)


def test_zonal_and_generalization(pro, R):
    z = {r["zone"]: r for r in rows(pro, run(pro, R, "zonal_statistics", zones="zones", input="plane"))}
    assert z["NE"]["count"] == 60 * 50 and z["NE"]["mean"] == pytest.approx(float(PLANE[:60, 50:].mean()), rel=1e-6)
    assert z["S"]["area_m2"] == pytest.approx(40 * 100 * CELL * CELL)
    t = {r["zone"]: r for r in rows(pro, run(pro, R, "tabulate_area", zones="zones", input="classes"))}
    assert t["S"]["class_3_m2"] == pytest.approx(40 * 100 * CELL * CELL) and t["NW"]["class_2_m2"] == pytest.approx(CELL * CELL)
    sv, _ = read(run(pro, R, "sieve", input="classes", cells=4))
    assert sv[10, 10] == 1                                             # the speck is gone
    mj, _ = read(run(pro, R, "majority_filter", input="classes"))
    assert mj[10, 10] == 1
    rg = run(pro, R, "region_group", input="classes")
    assert rg["source"]["summary"]["regions"] == 4
    ex, _ = read(run(pro, R, "expand", input="classes", values="3", cells=2))
    assert ex[58, 20] == 3 and ex[57, 20] == 1


def test_distance_density_interpolation(pro, R):
    d = run(pro, R, "euclidean_distance", sources="obs", cell=30, max_distance=900)
    a, s = read(d)
    # the observer sits at a cell centre of the new grid's UTM zone (also 32N): distances in whole cells
    r0, c0 = np.unravel_index(np.nanargmin(a), a.shape)
    assert a[r0, c0] == 0 and a[r0, c0 + 10] == pytest.approx(300, abs=1e-6) and a[r0 + 3, c0 + 4] == pytest.approx(150, abs=1e-6)
    kd = run(pro, R, "kernel_density", points="samples", radius=600, cell=30)
    assert kd["source"]["summary"]["total"] == pytest.approx(30, rel=0.02)   # a kernel spreads each point's weight of 1
    nn, _ = read(run(pro, R, "nearest_interpolation", points="samples", field="z", cell=30))
    idw, _ = read(run(pro, R, "idw", points="samples", field="z", cell=30))
    # on a plane, the interpolated surfaces stay within the samples' range
    lo, hi = PLANE.min(), PLANE.max()
    assert np.nanmin(idw) >= lo - 1e-3 and np.nanmax(idw) <= hi + 1e-3 and np.isfinite(nn).all()
    tin, _ = read(run(pro, R, "tin_interpolation", points="samples", field="z", cell=30))
    v = tin[np.isfinite(tin)]
    assert v.size > 100                                                 # linear interpolation of a plane is the plane
    ext = rows(pro, run(pro, R, "extract_values", points="samples", input="plane"))
    assert all(r["value"] == pytest.approx(r["z"], abs=1e-3) for r in ext)


def test_viewshed_behind_a_wall(pro, R):
    v, _ = read(run(pro, R, "viewshed", input="wall", observers="obs", radius=900, curvature=False))
    assert v[50, 55] == 1 and v[50, 59] == 1 and v[50, 60] == 1    # up to and including the wall
    assert v[50, 70] == 0 and v[50, 79] == 0                          # hidden behind it
    assert v[50, 40] == 1 and v[50, 21] == 1                          # open ground to the west, within 900 m
    assert np.isnan(v[50, 10]) or v[50, 10] == 0                      # beyond the radius


def test_imagery(pro, R):
    sc = run(pro, R, "supervised_classification", input="image", training="training", field="cls")
    assert sc["source"]["summary"]["training_accuracy"] == 1.0
    c, _ = read(sc)
    assert c[50, 10] == 1 and c[50, 80] == 2
    u, _ = read(run(pro, R, "unsupervised_classification", input="image", classes=2))
    assert len(np.unique(u[np.isfinite(u)])) == 2 and u[50, 10] != u[50, 80]


def test_contours_on_a_plane(pro, R):
    out = run(pro, R, "contours", input="plane", interval=50)
    rs = rows(pro, out)
    # z = 100 + 0.1 x: values 150 … 350 (the plane spans 101.5 to 398.5)
    assert sorted({r["value"] for r in rs}) == [150, 200, 250, 300, 350]
    ids = [r["id"] for r in pro.post(f"/layers/{out['id']}/rows", json={"limit": 50}).json()["rows"]]
    fcol = pro.post(f"/layers/{out['id']}/features/geojson", json=ids).json()
    to_utm = Transformer.from_crs(4326, UTM, always_xy=True)
    for f in fcol["features"]:
        xs = [to_utm.transform(*c)[0] for c in f["geometry"]["coordinates"]]
        assert np.allclose(np.array(xs) - X0, (f["properties"]["value"] - 100) / 0.1, atol=0.01)


def test_a_raster_whose_file_is_gone_is_marked_missing(person, tmp_path, monkeypatch):
    from app import rasters
    from app.config import settings
    monkeypatch.setattr(settings, "raster_dir", str(tmp_path / "kept"))
    monkeypatch.setattr(settings, "s3_endpoint", "")
    who = person(role="member", name="Gone Raster", features=["maps", "editing", "raster"])
    pid = who.post("/projects", json={"name": "Lost rasters"}).json()["id"]
    import numpy as np
    from rasterio.crs import CRS
    from rasterio.transform import from_origin
    meta = rasters.save(np.ones((10, 10), "float32"), CRS.from_epsg(32632), from_origin(230000, 1010000, 10, 10))
    from app.routers.rasters import _insert
    import asyncio, psycopg
    from psycopg.rows import dict_row

    async def add():
        async with await psycopg.AsyncConnection.connect(settings.database_url, row_factory=dict_row, options=f"-c search_path={settings.db_schema},public") as c:
            u = {"id": who.claims["sub"], "org_id": who.claims["org"]["id"]}
            lid = await _insert(c, pid, u, "DEM", meta, rasters.default_style(meta), {})
            await c.commit()
            return lid
    lid = str(asyncio.run(add()))
    lay = next(l for l in who.get(f"/projects/{pid}").json()["layers"] if l["id"] == lid)
    assert not lay["raster"].get("missing")
    (tmp_path / "kept" / meta["key"]).unlink()                                     # e.g. a redeploy replaced the disk
    lay = next(l for l in who.get(f"/projects/{pid}").json()["layers"] if l["id"] == lid)
    assert lay["raster"]["missing"] is True
    r = who.get(f"/layers/{lid}/raster/5/16/15.png")
    assert r.status_code == 404 and "redeploy" in r.json()["detail"]


def test_terrain_tiles_decode_to_the_heights_and_polygons_extrude(pro, R):
    """3D: an elevation model as Terrarium tiles (height = R*256 + G + B/256 - 32768), and polygons with a height."""
    import io

    from PIL import Image
    lay = R["plane"]
    lng, lat = cell_ll(50, 50)
    z = 12
    x, y = int((lng + 180) / 360 * 2 ** z), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2 ** z)
    t = pro.get(f"/layers/{lay['id']}/raster/terrain/{z}/{x}/{y}.png?v=1")
    assert t.status_code == 200 and t.headers["content-type"] == "image/png"
    px = np.asarray(Image.open(io.BytesIO(t.content)).convert("RGB")).astype(float)
    h = px[..., 0] * 256 + px[..., 1] + px[..., 2] / 256 - 32768
    assert float(PLANE.min()) - 1 <= h.min() and h.max() <= float(PLANE.max()) + 1     # heights of the plane, nothing invented
    assert pro.get(f"/layers/{lay['id']}/raster/terrain/{z}/0/0.png").status_code == 404

    p = R["p"]
    poly = pro.post(f"/projects/{p['id']}/layers/import", files={"file": ("blocks.geojson", json.dumps(fc((square_ll(10, 10, 20, 20), {"storeys": 3}))))}).json()[0]
    st = {**poly["style"], "extrude": {"field": "storeys", "scale": 3}}
    r = pro.patch(f"/layers/{poly['id']}", json={"style": st})
    assert r.status_code == 200 and r.json()["style"]["extrude"] == {"field": "storeys", "height": 10, "scale": 3, "base": 0}
    assert pro.patch(f"/layers/{poly['id']}", json={"style": {**st, "extrude": {"field": "floors"}}}).status_code == 422
