"""Military tools: coordinate notations, converting them, and MGRS and GARS grids."""
import json

import pytest

from app import milcoords as mc
from test_rasters import pro  # noqa: F401  (fixture)
from test_scenarios import P, vector  # noqa: F401

ABUJA = (7.4664, 9.0765)


@pytest.mark.parametrize("lng,lat,ref", [
    (7.4664, 9.0765, "32PLR3145203665"),
    (-77.0352, 38.8895, "18SUJ2348606483"),        # Washington Monument
    (151.2153, -33.8568, "56HLH3490052288"),       # Sydney Opera House
    (15.6267, 78.2232, "33XWG1427883355"),         # Svalbard: zone 33X
    (5.32, 60.39, "32VKN9723000510"),              # Bergen: Norway's widened 32V
])
def test_mgrs_matches_reference_values(lng, lat, ref):
    assert mc.to_mgrs(lng, lat) == ref
    x, y = mc.from_mgrs(ref)
    assert abs(x - lng) < 2e-5 and abs(y - lat) < 2e-5


def test_every_notation_round_trips():
    lng, lat = ABUJA
    notes = mc.all_notations(lng, lat)
    assert notes == {"dd": "9.076500° N 7.466400° E", "ddm": "09° 04.590' N 007° 27.984' E", "dms": "09° 04' 35.4\" N 007° 27' 59.0\" E",
                     "utm": "32N 331452 1003666", "mgrs": "32PLR3145203665", "usng": "32P LR 31452 03665", "gars": "375JG49", "georef": "NGHK2704"}
    tol = {"gars": 5 / 60, "georef": 1 / 60}
    for n, text in notes.items():
        x, y, used = mc.parse(text)
        assert used == n or {used, n} <= {"dd", "ddm", "dms"}, (n, used)
        assert abs(x - lng) <= tol.get(n, 1e-4) and abs(y - lat) <= tol.get(n, 1e-4), (n, text, x, y)


@pytest.mark.parametrize("text", ["9.0765, 7.4664", "7.4664 E 9.0765 N", "N 9 4 35.4 E 7 27 59", "9°04'35.4\"N 7°27'59.0\"E",
                                  "32P LR 31452 03665", "32plr3145203665", "32N 331452 1003666", "32P 331452 1003666"])
def test_typed_coordinates_are_read(text):
    x, y, _ = mc.parse(text)
    assert abs(x - 7.4664) < 1e-3 and abs(y - 9.0765) < 1e-3


def test_gars_and_georef_cells():
    assert mc.to_gars(-77.0352, 38.8895) == "206LT26"
    assert mc.to_gars(-77.0352, 38.8895, 30) == "206LT" and mc.to_gars(-77.0352, 38.8895, 15) == "206LT2"
    assert mc.to_georef(-77.0352, 38.8895) == "GJNJ5753"
    assert mc.gars_cell("001AA") == (-180, -90, 0.5)


@pytest.mark.parametrize("bad", ["32P ZZ 1 2", "hello", "32P LR 3145 036", "95.1, 200", ""])
def test_nonsense_is_refused(bad):
    with pytest.raises(mc.CoordError):
        mc.parse(bad)


def test_polar_areas_have_no_mgrs():
    assert mc.write("mgrs", 0, 85) is None and mc.write("gars", 0, 85)


def test_convert_notation_from_locations_and_from_a_field(pro, P):
    lay = vector(pro, P, "posts", [{"type": "Point", "coordinates": list(ABUJA)}])
    r = pro.post(f"/projects/{P['id']}/tools/convert_notation", json={"params": {"input": lay["id"]}})
    assert r.status_code == 201, r.text
    out = r.json()["layer"]
    assert {"mgrs", "usng", "utm", "dms", "gars", "georef"} <= {f["name"] for f in out["fields"]}
    row = pro.post(f"/layers/{out['id']}/rows", json={}).json()["rows"][0]["props"]
    assert row["mgrs"] == "32PLR3145203665"
    # coordinates written in a field: the points go where the field says
    pro.post(f"/layers/{lay['id']}/fields", json={"op": "add", "name": "grid", "type": "string"})
    fid = pro.post(f"/layers/{lay['id']}/rows", json={}).json()["rows"][0]["id"]
    pro.patch(f"/layers/{lay['id']}/features/{fid}", json={"properties": {"grid": "18S UJ 23486 06483"}})
    r = pro.post(f"/projects/{P['id']}/tools/convert_notation", json={"params": {"input": lay["id"], "source": "field", "field": "grid"}})
    out = r.json()["layer"]
    assert abs(out["bbox"][0] - -77.0352) < 1e-3 and abs(out["bbox"][1] - 38.8895) < 1e-3


def test_points_from_typed_coordinates(pro, P):
    text = "OP North: 32P LR 31452 03665\n9.0765, 7.4664\n\n375JG49"
    r = pro.post(f"/projects/{P['id']}/tools/points_from_coordinates", json={"params": {"coordinates": text}})
    assert r.status_code == 201, r.text
    rows = [x["props"] for x in pro.post(f"/layers/{r.json()['layer']['id']}/rows", json={}).json()["rows"]]
    assert [x["name"] for x in rows] == ["OP North", None, None]
    assert [x["notation"] for x in rows] == ["USNG", "Decimal degrees", "GARS"]
    r = pro.post(f"/projects/{P['id']}/tools/points_from_coordinates", json={"params": {"coordinates": "32P LR 31452 03665\nnot a place"}})
    assert r.status_code == 422 and "line 2" in r.json()["detail"]


def test_mgrs_grid_over_the_view_and_a_layer(pro, P):
    r = pro.post(f"/projects/{P['id']}/tools/mgrs_grid", json={"params": {"area": [7.0, 8.8, 7.9, 9.4], "level": "100km"}})
    assert r.status_code == 201, r.text
    lay = r.json()["layer"]
    labels = sorted(x["props"]["label"] for x in pro.post(f"/layers/{lay['id']}/rows", json={"limit": 100}).json()["rows"])
    assert "32P LR" in labels and all(x.startswith("32P ") for x in labels)
    # across a zone edge (6° E): squares are cut to their zone
    r = pro.post(f"/projects/{P['id']}/tools/mgrs_grid", json={"params": {"area": [5.8, 9.0, 6.2, 9.2], "level": "10km"}})
    labels = {x["props"]["gzd"] for x in pro.post(f"/layers/{r.json()['layer']['id']}/rows", json={"limit": 500}).json()["rows"]}
    assert labels == {"31P", "32P"}
    pts = vector(pro, P, "posts", [{"type": "Point", "coordinates": [7.40, 9.00]}, {"type": "Point", "coordinates": [7.43, 9.03]}])
    r = pro.post(f"/projects/{P['id']}/tools/mgrs_grid", json={"params": {"area": {"layer": pts["id"]}, "level": "1km"}})
    assert r.status_code == 201 and r.json()["layer"]["feature_count"] >= 9
    r = pro.post(f"/projects/{P['id']}/tools/mgrs_grid", json={"params": {"area": [0, 0, 20, 20], "level": "100m"}})
    assert r.status_code == 422 and r.json()["code"] == "too_many"


def test_gars_grid(pro, P):
    r = pro.post(f"/projects/{P['id']}/tools/gars_grid", json={"params": {"area": [7.0, 9.0, 8.0, 9.5], "level": "30"}})
    assert r.status_code == 201, r.text
    assert r.json()["layer"]["feature_count"] == 2 * 1


# ---------- distance and direction ----------
from pyproj import Geod  # noqa: E402

G = Geod(ellps="WGS84")


def _rows(pro, lay, n=500):
    return pro.post(f"/layers/{lay['id']}/rows", json={"limit": n}).json()["rows"]


def _geo(pro, lay):
    return pro.get(f"/layers/{lay['id']}/features.geojson", params={"bbox": "-180,-85,180,85"}).json()["features"]


def test_range_rings_are_true_distances(pro, P):
    r = pro.post(f"/projects/{P['id']}/tools/range_rings", json={"params": {"origin_text": "Gun 1: 32P LR 31452 03665", "ranges": "2, 5", "unit": "km", "radials": 4}})
    assert r.status_code == 201, r.text
    lay = r.json()["layer"]
    feats = _geo(pro, lay)
    rings = [f for f in feats if f["properties"]["kind"] == "ring"]
    radials = [f for f in feats if f["properties"]["kind"] == "radial"]
    assert len(rings) == 2 and len(radials) == 4 and lay["labels"]["field"] == "label"
    lng, lat = mc.from_mgrs("32P LR 31452 03665")
    for f in rings:
        want = f["properties"]["range_m"]
        for x, y in f["geometry"]["coordinates"][::30]:
            assert abs(G.inv(lng, lat, x, y)[2] - want) < 0.5
    assert {f["properties"]["bearing"] for f in radials} == {0, 90, 180, 270}
    assert {f["properties"]["origin"] for f in feats} == {"Gun 1"}
    # an interval and a count, in nautical miles, from a point layer
    pts = vector(pro, P, "ships", [{"type": "Point", "coordinates": [3.4, 6.4]}, {"type": "Point", "coordinates": [3.5, 6.3]}])
    r = pro.post(f"/projects/{P['id']}/tools/range_rings", json={"params": {"origin": "layer", "input": pts["id"], "how": "interval", "interval": 1.5,
                                                                         "count": 3, "unit": "nmi", "radials": 0}})
    assert r.status_code == 201, r.text
    assert sorted({x["props"]["range"] for x in _rows(pro, r.json()["layer"])}) == [1.5, 3.0, 4.5]
    assert r.json()["layer"]["feature_count"] == 6


def test_bearing_lines_in_degrees_and_mils(pro, P):
    r = pro.post(f"/projects/{P['id']}/tools/bearing_lines", json={"params": {"origin_text": "9.0765, 7.4664", "bearings": "1600, 3200", "angle": "mil",
                                                                           "distance": 10, "unit": "km"}})
    assert r.status_code == 201, r.text
    feats = sorted(_geo(pro, r.json()["layer"]), key=lambda f: f["properties"]["bearing"])
    assert [f["properties"]["bearing"] for f in feats] == [90, 180]
    assert [f["properties"]["bearing_mils"] for f in feats] == [1600, 3200]
    for f in feats:
        (x1, y1), (x2, y2) = f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][-1]
        az, back, d = G.inv(x1, y1, x2, y2)
        assert abs(d - 10_000) < 0.5 and abs((az % 360) - f["properties"]["bearing"]) < 1e-3
        assert abs((back % 360) - f["properties"]["back_bearing"]) < 1e-3


def test_distance_and_bearing_between_points(pro, P):
    r = pro.post(f"/projects/{P['id']}/tools/distance_bearing", json={"params": {"origin_text": "Lagos: 6.4550, 3.3841", "dest_text": "Abuja: 9.0765, 7.4664\nKano: 12.0022, 8.5920",
                                                                              "unit": "km"}})
    assert r.status_code == 201, r.text
    rows = {x["props"]["destination"]: x["props"] for x in _rows(pro, r.json()["layer"])}
    az, back, d = G.inv(3.3841, 6.4550, 7.4664, 9.0765)
    assert abs(rows["Abuja"]["distance_m"] - d) < 0.01 and abs(rows["Abuja"]["bearing"] - az % 360) < 1e-3
    assert abs(rows["Abuja"]["back_bearing"] - back % 360) < 1e-3 and 520 < rows["Abuja"]["distance"] < 540
    assert set(rows) == {"Abuja", "Kano"}
    r = pro.post(f"/projects/{P['id']}/tools/distance_bearing", json={"params": {"origin_text": "Lagos: 6.4550, 3.3841", "dest_text": "here"}})
    assert r.status_code == 422 and "destinations" in r.json()["detail"]


def test_ellipse_axes_and_orientation(pro, P):
    r = pro.post(f"/projects/{P['id']}/tools/ellipse", json={"params": {"origin_text": "9.0765, 7.4664", "major": 10, "minor": 4, "axes": "full",
                                                                     "orientation": 45, "unit": "km"}})
    assert r.status_code == 201, r.text
    f = _geo(pro, r.json()["layer"])[0]
    ring_ = f["geometry"]["coordinates"][0]
    ds = [(G.inv(7.4664, 9.0765, x, y)[2], G.inv(7.4664, 9.0765, x, y)[0] % 360) for x, y in ring_]
    far = max(ds)
    assert abs(far[0] - 5000) < 1 and abs(far[1] - 45) < 1.5 or abs(far[1] - 225) < 1.5
    assert abs(min(d for d, _ in ds) - 2000) < 1
    assert f["properties"]["major"] == 10 and f["properties"]["minor"] == 4


def test_typed_origins_must_be_readable(pro, P):
    r = pro.post(f"/projects/{P['id']}/tools/range_rings", json={"params": {"origin_text": "32P LR 31452 03665\nsomewhere", "ranges": "1"}})
    assert r.status_code == 422 and "line 2" in r.json()["detail"]
    r = pro.post(f"/projects/{P['id']}/tools/range_rings", json={"params": {"origin": "layer", "ranges": "1"}})
    assert r.status_code == 422
