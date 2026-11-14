"""Georeferencing, checked on a synthetic "scanned map": an image whose true placement is known, so the fitted
transformation, the residuals and the warped result can be compared with the truth."""
import io
import math

import numpy as np
import pytest
import rasterio
from PIL import Image
from pyproj import Transformer

from test_rasters import pro  # noqa: F401  (fixture)

W, H = 400, 300
UTM = Transformer.from_crs(4326, 32631, always_xy=True)
LL = Transformer.from_crs(32631, 4326, always_xy=True)
X0, Y0, PX = 660_000.0, 830_000.0, 2.5           # near Ile-Ife: the scan's top-left corner, 2.5 m per pixel
ROT = math.radians(12)                           # the scan was laid crooked on the scanner


def true_xy(col, row):
    """Where an image pixel really is (UTM 31N): scaled, rotated 12°, shifted."""
    u, v = col * PX, row * PX
    return X0 + u * math.cos(ROT) + v * math.sin(ROT), Y0 + u * math.sin(ROT) - v * math.cos(ROT)


def scan_png() -> bytes:
    """A picture with a grid of dark lines every 50 pixels, a red square at pixel (100–150, 100–150)."""
    a = np.full((H, W, 3), 230, "uint8")
    a[::50, :] = 30
    a[:, ::50] = 30
    a[100:150, 100:150] = [200, 20, 20]
    b = io.BytesIO()
    Image.fromarray(a).save(b, "PNG")
    return b.getvalue()


def gcp(col, row, i, err_m=0.0):
    x, y = true_xy(col, row)
    lng, lat = LL.transform(x + err_m, y)
    return {"id": f"p{i}", "col": col, "row": row, "lng": lng, "lat": lat}


CORNERS = [(0, 0), (W, 0), (W, H), (0, H), (200, 150), (50, 250), (350, 60)]


@pytest.fixture
def G(pro):
    p = pro.post("/projects", json={"name": "Scans"}).json()
    r = pro.post(f"/projects/{p['id']}/georeference", files={"file": ("old map.png", scan_png())})
    assert r.status_code == 201, r.text
    return p, r.json()


def test_start_shows_the_image_and_keeps_the_work(pro, G):
    p, g = G
    assert (g["width"], g["height"], g["band_count"], g["name"]) == (W, H, 3, "old map")
    assert {m["id"] for m in g["methods"]} == {"similarity", "affine", "poly2", "poly3", "tps"}
    img = pro.get(f"/georeference/{g['id']}/image.png")
    assert img.status_code == 200 and Image.open(io.BytesIO(img.content)).size == (W, H)
    small = pro.get(f"/georeference/{g['id']}/image.png", params={"size": 256})
    assert Image.open(io.BytesIO(small.content)).size == (256, 192) and float(small.headers["x-scale"]) == pytest.approx(W / 256)
    pts = [gcp(c, r, i) for i, (c, r) in enumerate(CORNERS[:3])]
    saved = pro.put(f"/georeference/{g['id']}", json={"points": pts, "method": "affine"}).json()
    assert len(saved["points"]) == 3 and pro.get(f"/projects/{p['id']}/georeference").json()[0]["id"] == g["id"]
    assert pro.post(f"/projects/{p['id']}/georeference", files={"file": ("notes.txt", b"hello")}).status_code == 422


def test_residuals_point_out_the_wrong_point(pro, G):
    _, g = G
    good = [gcp(c, r, i) for i, (c, r) in enumerate(CORNERS)]
    f = pro.post(f"/georeference/{g['id']}/fit", json={"points": good, "method": "affine"}).json()
    assert f["rmse_m"] < 0.05 and f["crs"] == "EPSG:32631" and f["cell_m"] == pytest.approx(PX, rel=0.01)
    assert all(p["error_m"] < 0.05 for p in f["points"])
    bad = good[:-1] + [gcp(*CORNERS[-1], 6, err_m=40)]                              # one point placed 40 m off
    f = pro.post(f"/georeference/{g['id']}/fit", json={"points": bad, "method": "affine"}).json()
    worst = max(f["points"], key=lambda p: p["error_m"])
    assert worst["id"] == "p6" and worst["error_m"] > 20 and f["rmse_m"] > 5
    # switching the bad point off gives a clean fit again
    bad[-1]["on"] = False
    f = pro.post(f"/georeference/{g['id']}/fit", json={"points": bad, "method": "affine"}).json()
    assert len(f["points"]) == 6 and f["rmse_m"] < 0.05
    # a thin plate spline goes through every point
    f = pro.post(f"/georeference/{g['id']}/fit", json={"points": good, "method": "tps"}).json()
    assert f["exact"] and f["rmse_m"] < 1e-3
    # too few points, or all on a line, are refused plainly
    r = pro.post(f"/georeference/{g['id']}/fit", json={"points": good[:4], "method": "poly2"})
    assert r.status_code == 422 and "at least 6" in r.json()["detail"]
    line = [gcp(10 * i, 10 * i, i) for i in range(4)]
    r = pro.post(f"/georeference/{g['id']}/fit", json={"points": line, "method": "affine"})
    assert r.status_code == 422 and "line" in r.json()["detail"]


def test_preview_and_save_place_the_image_where_it_belongs(pro, G):
    p, g = G
    pts = [gcp(c, r, i) for i, (c, r) in enumerate(CORNERS)]
    f = pro.post(f"/georeference/{g['id']}/fit", json={"points": pts, "method": "affine", "preview": True}).json()
    pv = f["preview"]
    assert pv["url"].startswith("data:image/png;base64,") and len(pv["coordinates"]) == 4
    lngs = [c[0] for c in pv["coordinates"]]
    corners = [LL.transform(*true_xy(c, r)) for c, r in ((0, 0), (W, 0), (W, H), (0, H))]
    assert min(lngs) == pytest.approx(min(c[0] for c in corners), abs=2e-4)
    r = pro.post(f"/georeference/{g['id']}/save", json={"points": pts, "method": "affine", "name": "Ife 1962 sheet"})
    assert r.status_code == 201, r.text
    lay = r.json()["layer"]
    m = lay["raster"]
    assert lay["name"] == "Ife 1962 sheet" and m["epsg"] == 32631 and m["count"] == 3 and m["dtype"] == "uint8"
    assert m["res_m"][0] == pytest.approx(PX, rel=0.01)
    assert lay["style"]["mode"] == "rgb" and lay["style"]["mins"] == [0, 0, 0] and lay["style"]["maxs"] == [255, 255, 255]   # its own colours
    assert lay["source"]["summary"]["points"] == 7 and lay["source"]["summary"]["rmse_m"] < 0.05
    # the red square's centre, pixel (125, 125), is red on the map where it truly is
    lng, lat = LL.transform(*true_xy(125, 125))
    v = pro.get(f"/layers/{lay['id']}/raster/value", params={"lng": lng, "lat": lat}).json()["values"]
    assert v[0] > 180 and v[1] < 60 and v[2] < 60
    lng, lat = LL.transform(*true_xy(310, 225))                                     # elsewhere: the pale paper
    assert pro.get(f"/layers/{lay['id']}/raster/value", params={"lng": lng, "lat": lat}).json()["values"][0] > 180
    lng, lat = LL.transform(*true_xy(-40, -40))                                     # outside the scan: no data
    assert pro.get(f"/layers/{lay['id']}/raster/value", params={"lng": lng, "lat": lat}).json()["values"] in (None, [None, None, None])
    # in another coordinate system on request
    r = pro.post(f"/georeference/{g['id']}/save", json={"points": pts, "method": "similarity", "epsg": 4326})
    assert r.status_code == 201 and r.json()["layer"]["raster"]["epsg"] == 4326


def test_replacing_a_misplaced_raster(pro):
    p = pro.post("/projects", json={"name": "Fix"}).json()
    a = np.zeros((1, 50, 80), "float32")
    a[0, :, 40:] = 7
    b = io.BytesIO()
    with rasterio.MemoryFile() as mf:
        with mf.open(driver="GTiff", width=80, height=50, count=1, dtype="float32", crs="EPSG:32631",
                     transform=rasterio.transform.from_origin(100_000, 500_000, 10, 10)) as d:   # put down in the wrong place
            d.write(a)
        b.write(mf.read())
    lay = pro.post(f"/projects/{p['id']}/layers/import", files={"file": ("wrong.tif", b.getvalue())}).json()[0]
    g = pro.post(f"/projects/{p['id']}/georeference/from-layer", json={"layer_id": lay["id"]})
    assert g.status_code == 201, g.text
    g = g.json()
    assert g["layer_id"] == lay["id"] and g["had_crs"] == "EPSG:32631" and (g["width"], g["height"]) == (80, 50)
    pts = []
    for i, (c, r) in enumerate([(0, 0), (80, 0), (80, 50), (0, 50)]):
        lng, lat = LL.transform(X0 + c * 10, Y0 - r * 10)                              # where it belongs: 10 m cells at X0, Y0
        pts.append({"id": str(i), "col": c, "row": r, "lng": lng, "lat": lat})
    out = pro.post(f"/georeference/{g['id']}/save", json={"points": pts, "method": "affine", "resampling": "nearest"}).json()["layer"]
    lng, lat = LL.transform(X0 + 600, Y0 - 250)                                        # column 60: the 7s
    assert pro.get(f"/layers/{out['id']}/raster/value", params={"lng": lng, "lat": lat}).json()["values"] == [pytest.approx(7)]
    assert pro.delete(f"/georeference/{g['id']}").status_code == 204
    assert pro.get(f"/layers/{lay['id']}/raster/value", params={"lng": lng, "lat": lat}).status_code == 200   # the original's file is kept
    assert pro.get(f"/georeference/{g['id']}").status_code == 404
