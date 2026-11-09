"""PolyGIS end to end against PostgreSQL with PostGIS: projects, importing files in several formats and coordinate
systems, the attribute table, styles, fields, editing with undo, selection, tiles and export."""
import io
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pyogrio
import pyogrio.raw
import pytest
import shapely
from pyproj import Transformer

from conftest import ORG

GEOJSON = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"name": "Wuse market", "kind": "market", "stalls": 120, "rating": 4.5},
     "geometry": {"type": "Point", "coordinates": [7.4664, 9.0765]}},
    {"type": "Feature", "properties": {"name": "Garki clinic", "kind": "health", "stalls": 0, "rating": 3.9},
     "geometry": {"type": "Point", "coordinates": [7.4890, 9.0350]}},
    {"type": "Feature", "properties": {"name": "Maitama school", "kind": "school", "stalls": 4, "rating": 4.1},
     "geometry": {"type": "Point", "coordinates": [7.4950, 9.0880]}},
    {"type": "Feature", "properties": {"name": "Ring road", "kind": "road", "stalls": None, "rating": None},
     "geometry": {"type": "LineString", "coordinates": [[7.45, 9.05], [7.50, 9.06], [7.52, 9.09]]}},
]}
SQUARE = {"type": "Polygon", "coordinates": [[[7.46, 9.07], [7.47, 9.07], [7.47, 9.08], [7.46, 9.08], [7.46, 9.07]]]}


def upload(p, project_id, name, data, epsg=None):
    files = {"file": (name, data)}
    return p.post(f"/projects/{project_id}/layers/import", files=files, data={"epsg": str(epsg)} if epsg else {})


@pytest.fixture
def editor(person):
    return person(role="member", name="Bisi Editor")


@pytest.fixture
def project(editor):
    r = editor.post("/projects", json={"name": "Abuja facilities", "display_epsg": 26332})
    assert r.status_code == 201, r.text
    return r.json()


def test_who_and_projects(person, editor, project):
    me = editor.get("/me").json()
    assert me["role"] == "editor" and me["product"]["id"] == "gis"
    viewer = person(role="viewer")
    assert viewer.get(f"/projects/{project['id']}").status_code == 200                 # the whole organization sees it
    assert viewer.post("/projects", json={"name": "x"}).status_code == 403
    assert person(org_id="00000000-0000-0000-0000-000000000001").get(f"/projects/{project['id']}").status_code == 404
    ended = person(role="admin", status="expired")
    assert ended.post("/projects", json={"name": "x"}).status_code == 403 and "read-only" in ended.post("/projects", json={"name": "x"}).json()["detail"]
    assert editor.post("/projects", json={"name": "x", "display_epsg": 1}).status_code == 422
    r = editor.patch(f"/projects/{project['id']}", json={"view": {"center": [7.49, 9.06], "zoom": 12, "basemap": "imagery", "evil": 1}})
    assert r.json()["view"] == {"center": [7.49, 9.06], "zoom": 12.0, "basemap": "imagery"}
    # only whoever made it, or an admin, deletes it
    other = person(role="member", name="Other Editor")
    assert other.delete(f"/projects/{project['id']}").status_code == 403
    assert any(p["id"] == project["id"] for p in editor.get("/projects").json())


def test_import_geojson_splits_kinds_and_infers_fields(editor, project):
    r = upload(editor, project["id"], "facilities.geojson", json.dumps(GEOJSON).encode())
    assert r.status_code == 201, r.text
    layers = {l["geometry_type"]: l for l in r.json()}
    assert set(layers) == {"Point", "LineString"}
    pts = layers["Point"]
    assert pts["name"] == "facilities (points)" and pts["feature_count"] == 3
    assert {f["name"]: f["type"] for f in pts["fields"]} == {"name": "string", "kind": "string", "stalls": "integer", "rating": "number"}
    assert pts["bbox"] == pytest.approx([7.4664, 9.035, 7.495, 9.088])
    assert pts["style"]["type"] == "single" and pts["source"]["crs"] == "EPSG:4326"
    p = editor.get(f"/projects/{project['id']}").json()
    assert [l["name"] for l in p["layers"]] == ["facilities (points)", "facilities (lines)"]


def test_import_shapefile_on_a_nigerian_grid_and_csv(editor, project):
    # a zipped Shapefile in Minna / UTM zone 32N (EPSG:26332), with its .prj
    to_utm = Transformer.from_crs(4326, 26332, always_xy=True)
    e, n = to_utm.transform(7.4664, 9.0765)
    with tempfile.TemporaryDirectory() as d:
        pyogrio.raw.write(f"{d}/plots.shp", np.array([shapely.to_wkb(shapely.box(e, n, e + 100, n + 50))], dtype=object),
                          [np.array(["A1"], dtype=object), np.array([5000.0])], ["plot", "area"],
                          driver="ESRI Shapefile", geometry_type="Polygon", crs="EPSG:26332")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for f in Path(d).iterdir():
                z.write(f, f.name)
    r = upload(editor, project["id"], "plots.zip", buf.getvalue())
    assert r.status_code == 201, r.text
    lay = r.json()[0]
    assert lay["geometry_type"] == "Polygon" and lay["source"]["crs"] == "EPSG:26332"
    # the plot's south-west beacon lands where it was surveyed, to well under a millimetre
    fid = editor.post(f"/layers/{lay['id']}/rows", json={}).json()["rows"][0]["id"]
    ring = editor.get(f"/layers/{lay['id']}/features/{fid}").json()["geometry"]["coordinates"][0]
    assert min(abs(x - 7.4664) + abs(y - 9.0765) for x, y in ring) < 1e-8
    # a CSV of longitude/latitude points
    csv = b"name,longitude,latitude,count\nA,7.48,9.05,3\nB,7.49,9.06,5\n"
    r = upload(editor, project["id"], "wells.csv", csv)
    assert r.status_code == 201 and r.json()[0]["feature_count"] == 2
    # eastings and northings with no system: refused with what to do; with the system: read
    csv = f"name,x,y\nC,{e:.2f},{n:.2f}\n".encode()
    r = upload(editor, project["id"], "beacons.csv", csv)
    assert r.status_code == 422 and "EPSG:26332" in r.json()["detail"]
    r = upload(editor, project["id"], "beacons.csv", csv, epsg=26332)
    assert r.status_code == 201 and r.json()[0]["bbox"][0] == pytest.approx(7.4664, abs=1e-6)
    assert upload(editor, project["id"], "junk.geojson", b"not a file").status_code == 422


def test_table_stats_styles_and_fields(editor, project):
    pts = next(l for l in upload(editor, project["id"], "f.geojson", json.dumps(GEOJSON).encode()).json() if l["geometry_type"] == "Point")
    lid = pts["id"]
    t = editor.post(f"/layers/{lid}/rows", json={"filters": [{"field": "stalls", "op": ">", "value": 3}], "sort": "stalls", "desc": True}).json()
    assert t["total"] == 2 and [r["props"]["name"] for r in t["rows"]] == ["Wuse market", "Maitama school"]
    t = editor.post(f"/layers/{lid}/rows", json={"filters": [{"field": "name", "op": "contains", "value": "clin"}]}).json()
    assert t["total"] == 1
    assert editor.post(f"/layers/{lid}/rows", json={"filters": [{"field": "nope", "op": "=", "value": 1}]}).status_code == 422
    # sneaking SQL in through a value or field is just a value
    t = editor.post(f"/layers/{lid}/rows", json={"filters": [{"field": "name", "op": "=", "value": "x' OR '1'='1"}]}).json()
    assert t["total"] == 0
    s = editor.get(f"/layers/{lid}/stats", params={"field": "rating", "classes": 2}).json()
    assert s["min"] == 3.9 and s["max"] == 4.5 and len(s["quantiles"]) == 3 and s["distinct"] == 3
    # a graduated style needs a field the layer has
    bad = editor.patch(f"/layers/{lid}", json={"style": {"type": "graduated"}})
    assert bad.status_code == 422
    ok = editor.patch(f"/layers/{lid}", json={"style": {"type": "categorized", "field": "kind", "categories": [{"value": "market", "color": "#ff0000"}]},
                                              "labels": {"field": "name"}}).json()
    assert ok["style"]["field"] == "kind" and ok["labels"]["field"] == "name"
    # renaming a field carries its values, style and labels along; deleting it drops them
    r = editor.post(f"/layers/{lid}/fields", json={"op": "rename", "name": "kind", "to": "category"}).json()
    assert r["style"]["field"] == "category" and "category" in [f["name"] for f in r["fields"]]
    assert editor.post(f"/layers/{lid}/rows", json={"filters": [{"field": "category", "op": "=", "value": "health"}]}).json()["total"] == 1
    r = editor.post(f"/layers/{lid}/fields", json={"op": "delete", "name": "category"}).json()
    assert r["style"]["type"] == "single"
    r = editor.post(f"/layers/{lid}/fields", json={"op": "add", "name": "checked", "type": "boolean"}).json()
    assert {"name": "checked", "type": "boolean"} in r["fields"]
    assert editor.post(f"/layers/{lid}/fields", json={"op": "add", "name": "Checked"}).status_code == 422
    # field calculator on the selection
    assert editor.post(f"/layers/{lid}/calculate", json={"field": "checked", "value": "yes", "ids": [t["rows"][0]["id"]] if t["rows"] else None}).status_code == 200


def test_editing_with_undo(person, editor, project):
    lay = editor.post(f"/projects/{project['id']}/layers", json={"name": "Plots", "geometry_type": "Polygon",
                                                                  "fields": [{"name": "plot", "type": "string"}, {"name": "area", "type": "number"}]}).json()
    lid = lay["id"]
    assert editor.post(f"/projects/{project['id']}/layers", json={"name": "x", "geometry_type": "Polygon",
                                                                  "fields": [{"name": "a"}, {"name": "A"}]}).status_code == 422
    f = editor.post(f"/layers/{lid}/features", json={"geometry": SQUARE, "properties": {"plot": "P1", "area": "1200.5"}})
    assert f.status_code == 201, f.text
    f = f.json()
    assert f["properties"] == {"plot": "P1", "area": 1200.5}
    # the wrong kind, an unknown field, a bad number: refused
    assert editor.post(f"/layers/{lid}/features", json={"geometry": {"type": "Point", "coordinates": [7.4, 9.0]}}).status_code == 422
    assert editor.post(f"/layers/{lid}/features", json={"geometry": SQUARE, "properties": {"owner": "x"}}).status_code == 422
    assert editor.post(f"/layers/{lid}/features", json={"geometry": SQUARE, "properties": {"area": "big"}}).status_code == 422
    # the features in an area, at full precision, for snapping
    fc = editor.get(f"/layers/{lid}/features.geojson", params={"bbox": "7.4,9.0,7.5,9.1"}).json()
    assert len(fc["features"]) == 1 and fc["features"][0]["geometry"]["coordinates"][0][0] == [7.46, 9.07] and not fc["truncated"]
    assert editor.get(f"/layers/{lid}/features.geojson", params={"bbox": "1,1,2,2"}).json()["features"] == []
    # a viewer cannot edit
    assert person(role="viewer").post(f"/layers/{lid}/features", json={"geometry": SQUARE}).status_code == 403
    # move a corner, then undo it
    moved = json.loads(json.dumps(SQUARE))
    moved["coordinates"][0][2] = [7.475, 9.085]
    ch = editor.patch(f"/layers/{lid}/features/{f['id']}", json={"geometry": moved, "properties": {"plot": "P1a"}}).json()
    assert ch["properties"]["plot"] == "P1a" and ch["geometry"]["coordinates"][0][2] == [7.475, 9.085]
    u = editor.post(f"/layers/{lid}/undo").json()
    assert u["undone"] == "change" and u["feature"]["properties"]["plot"] == "P1"
    # delete, then undo brings it back with the same id
    assert editor.post(f"/layers/{lid}/features/delete", json={"ids": [f["id"]]}).json() == {"deleted": 1}
    assert editor.get(f"/layers/{lid}/features/{f['id']}").status_code == 404
    assert editor.post(f"/layers/{lid}/undo").json()["undone"] == "delete"
    assert editor.get(f"/layers/{lid}/features/{f['id']}").json()["properties"]["plot"] == "P1"
    # someone else's edits are not yours to undo
    assert person(role="member", name="Other").post(f"/layers/{lid}/undo").status_code == 409
    assert [e["action"] for e in editor.get(f"/layers/{lid}/edits").json()] == ["delete", "change", "add"]
    # undoing the undo-able rest (the change and the add) leaves the layer empty
    assert editor.post(f"/layers/{lid}/undo").json()["undone"] == "add"
    assert editor.post(f"/layers/{lid}/rows", json={}).json()["total"] == 0
    assert editor.post(f"/layers/{lid}/undo").status_code == 409


def test_selection_tiles_and_export(editor, project):
    made = upload(editor, project["id"], "f.geojson", json.dumps(GEOJSON).encode()).json()
    pts = next(l for l in made if l["geometry_type"] == "Point")
    road = next(l for l in made if l["geometry_type"] == "LineString")
    r = editor.post(f"/layers/{pts['id']}/select", json={"geometry": SQUARE}).json()
    assert r["count"] == 1 and r["bbox"][0] == pytest.approx(7.4664)
    r = editor.post(f"/layers/{pts['id']}/select", json={"filters": [{"field": "rating", "op": ">=", "value": 4}]}).json()
    assert r["count"] == 2
    # within 2.3 km of the road: the school (about 2.2 km away), not the market or the clinic (about 2.5 km)
    r = editor.post(f"/layers/{pts['id']}/select", json={"other_layer_id": road["id"], "relation": "distance", "distance_m": 2300}).json()
    names = {x["props"]["name"] for x in editor.post(f"/layers/{pts['id']}/rows", json={"ids": r["ids"]}).json()["rows"]}
    assert names == {"Maitama school"}
    r = editor.post(f"/layers/{pts['id']}/select", json={"other_layer_id": road["id"], "relation": "distance", "distance_m": 2600}).json()
    assert r["count"] == 3
    r = editor.post(f"/layers/{pts['id']}/select", json={"other_layer_id": road["id"], "relation": "distance", "distance_m": 1}).json()
    assert r["count"] == 0


def test_tiles_and_export(editor, project):
    made = upload(editor, project["id"], "f.geojson", json.dumps(GEOJSON).encode()).json()
    pts = next(l for l in made if l["geometry_type"] == "Point")
    import math
    z = 10                                                         # the tile over Wuse market at zoom 10
    x = int((7.4664 + 180) / 360 * 2 ** z)
    y = int((1 - math.asinh(math.tan(math.radians(9.0765))) / math.pi) / 2 * 2 ** z)
    t = editor.get(f"/layers/{pts['id']}/tiles/{z}/{x}/{y}.pbf")
    assert t.status_code == 200 and len(t.content) > 20 and b"Wuse market" in t.content
    assert editor.get(f"/layers/{pts['id']}/tiles/10/0/0.pbf").content == b""
    assert editor.get(f"/layers/{pts['id']}/tiles/3/99/0.pbf").status_code == 404
    for fmt, ext in (("geojson", ".geojson"), ("gpkg", ".gpkg"), ("shp", ".zip"), ("csv", ".csv"), ("kml", ".kml")):
        r = editor.get(f"/layers/{pts['id']}/export", params={"format": fmt})
        assert r.status_code == 200, (fmt, r.text)
        assert r.headers["content-disposition"].endswith(f'{ext}"')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / f"out{ext}"
            p.write_bytes(r.content)
            src = f"/vsizip/{p}" if ext == ".zip" else str(p)
            if fmt == "csv":
                assert r.content.count(b"\n") >= 4 and b"POINT" in r.content
                continue
            df = pyogrio.raw.read(src)
            assert len(df[2]) == 3, fmt
    # on a Nigerian grid, and only the selection
    ids = [r["id"] for r in editor.post(f"/layers/{pts['id']}/rows", json={}).json()["rows"]][:2]
    r = editor.get(f"/layers/{pts['id']}/export", params={"format": "gpkg", "epsg": 26332, "ids": ",".join(map(str, ids))})
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "out.gpkg"
        p.write_bytes(r.content)
        meta, _, geoms, _ = pyogrio.raw.read(str(p))
        assert len(geoms) == 2 and "26332" in meta["crs"] and shapely.from_wkb(geoms[0]).x > 100000


def test_order_delete_crs_activity(editor, project):
    made = upload(editor, project["id"], "f.geojson", json.dumps(GEOJSON).encode()).json()
    ids = [l["id"] for l in made]
    r = editor.put(f"/projects/{project['id']}/layer-order", json={"layer_ids": ids[::-1]}).json()
    assert [l["id"] for l in r] == ids[::-1]
    assert editor.put(f"/projects/{project['id']}/layer-order", json={"layer_ids": ids[:1]}).status_code == 422
    assert editor.delete(f"/layers/{ids[0]}").status_code == 204
    c = editor.get("/crs").json()
    assert c[0]["epsg"] == 4326 and any(x["epsg"] == 26332 for x in c)
    assert any("Minna" in x["name"] for x in editor.get("/crs", params={"q": "minna"}).json())
    d = editor.get("/crs/26332").json()
    assert "+proj=utm" in d["proj4"] and "+zone=32" in d["proj4"] and d["unit"] == "metre"
    # the browser's definition carries the datum shift, so it agrees with the server to the millimetre
    from pyproj import CRS, Transformer
    server = Transformer.from_crs(4326, 26332, always_xy=True).transform(7.4664, 9.0765)
    browser = Transformer.from_crs(4326, CRS.from_proj4(d["proj4"]), always_xy=True).transform(7.4664, 9.0765)
    assert abs(server[0] - browser[0]) < 0.001 and abs(server[1] - browser[1]) < 0.001
    assert editor.get("/crs/1").status_code == 404
    acts = [a["action"] for a in editor.get("/activity").json()]
    assert "layer.import" in acts and "layer.delete" in acts
    assert ORG


def test_a_pasted_secret_is_trimmed(monkeypatch):
    from app.config import Settings
    s = Settings(database_url="postgresql://x/y", id_secret='  "xYMOeTlczXWK3DaZ0mWuKN3z8bz_AQdWU1H0aLwfTX4U7Gl0"\n', id_client=" gis ")
    assert s.id_secret == "xYMOeTlczXWK3DaZ0mWuKN3z8bz_AQdWU1H0aLwfTX4U7Gl0" and s.id_client == "gis"
