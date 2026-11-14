"""Several maps in one project, as in ArcGIS Pro: each map has its own layers and view; the same data can be shown in
several maps, each styled its own way, and edits to it show everywhere."""
import json

from test_scenarios import P, raster, vector  # noqa: F401  (fixture)
from test_rasters import pro  # noqa: F401
import numpy as np

def one(c, P, lid):
    return next(l for l in c.get(f"/projects/{P['id']}").json()["layers"] if l["id"] == lid)


PT = {"type": "Point", "coordinates": [7.45, 9.05]}


def test_a_project_starts_with_one_map(pro, P):
    p = pro.get(f"/projects/{P['id']}").json()
    assert [m["name"] for m in p["maps"]] == ["Map"] and p["active_map_id"] == p["maps"][0]["id"]
    lay = vector(pro, P, "wells", [PT])
    assert lay["map_id"] == p["maps"][0]["id"]


def test_new_layers_go_into_the_open_map(pro, P):
    first = pro.get(f"/projects/{P['id']}/maps").json()[0]
    m1 = pro.post(f"/projects/{P['id']}/maps", json={"name": "Map1"}).json()
    assert pro.post(f"/maps/{m1['id']}/open").status_code == 200
    lay = vector(pro, P, "roads", [PT])
    assert lay["map_id"] == m1["id"]
    pro.post(f"/maps/{first['id']}/open")
    assert vector(pro, P, "wells", [PT])["map_id"] == first["id"]
    maps = {m["name"]: m["layer_count"] for m in pro.get(f"/projects/{P['id']}/maps").json()}
    assert maps == {"Map": 1, "Map1": 1}


def test_shared_data_own_style_and_edits_show_in_both(pro, P):
    lay = vector(pro, P, "wells", [PT])
    m1 = pro.post(f"/projects/{P['id']}/maps", json={"name": "Map1"}).json()
    r = pro.post(f"/layers/{lay['id']}/add-to-map", json={"map_id": m1["id"]})
    assert r.status_code == 201, r.text
    twin = r.json()
    assert twin["map_id"] == m1["id"] and twin["data_id"] == lay["id"] and twin["feature_count"] == 1
    # each styled its own way
    pro.patch(f"/layers/{twin['id']}", json={"style": {**twin["style"], "color": "#ff0000"}})
    assert one(pro, P, lay['id'])["style"]["color"] != "#ff0000"
    # a feature drawn in one shows in the other
    assert pro.post(f"/layers/{twin['id']}/features", json={"geometry": {"type": "Point", "coordinates": [7.5, 9.1]}}).status_code == 201
    assert one(pro, P, lay['id'])["feature_count"] == 2
    assert len(pro.get(f"/layers/{lay['id']}/features.geojson", params={"bbox": "7,9,8,10"}).json()["features"]) == 2
    # fields are the data's: a new field shows in both
    assert pro.post(f"/layers/{lay['id']}/fields", json={"op": "add", "name": "depth", "type": "number"}).status_code == 200
    assert "depth" in [f["name"] for f in one(pro, P, twin['id'])["fields"]]
    # deleting the owner hands the data on
    assert pro.delete(f"/layers/{lay['id']}").status_code == 204
    t = one(pro, P, twin['id'])
    assert t["data_id"] is None and t["feature_count"] == 2


def test_copy_is_independent(pro, P):
    lay = vector(pro, P, "wells", [PT])
    m1 = pro.post(f"/projects/{P['id']}/maps", json={"name": "Map1"}).json()
    cp = pro.post(f"/layers/{lay['id']}/add-to-map", json={"map_id": m1["id"], "how": "copy"}).json()
    assert cp["data_id"] is None and cp["feature_count"] == 1
    pro.post(f"/layers/{cp['id']}/features", json={"geometry": PT})
    assert one(pro, P, lay['id'])["feature_count"] == 1


def test_copy_a_map_and_delete_maps(pro, P):
    first = pro.get(f"/projects/{P['id']}/maps").json()[0]
    lay = vector(pro, P, "wells", [PT])
    ras = raster(pro, P, "dem", np.arange(100, dtype="float32").reshape(10, 10))
    pro.patch(f"/maps/{first['id']}", json={"view": {"center": [3.4, 6.5], "zoom": 9}})
    cp = pro.post(f"/projects/{P['id']}/maps", json={"name": "Copy", "copy_of": first["id"]})
    assert cp.status_code == 201, cp.text
    cp = cp.json()
    assert cp["view"]["center"] == [3.4, 6.5]
    lays = [l for l in pro.get(f"/projects/{P['id']}").json()["layers"] if l["map_id"] == cp["id"]]
    assert sorted(l["name"] for l in lays) == ["dem", "wells"]
    assert next(l for l in lays if l["name"] == "wells")["data_id"] == lay["id"]
    # the original map goes; its data stays shown in the copy, the raster's file too
    assert pro.delete(f"/maps/{first['id']}").status_code == 204
    p = pro.get(f"/projects/{P['id']}").json()
    assert [m["name"] for m in p["maps"]] == ["Copy"] and p["active_map_id"] == cp["id"]
    w = next(l for l in p["layers"] if l["name"] == "wells")
    assert w["feature_count"] == 1 and w["data_id"] is None
    d = next(l for l in p["layers"] if l["name"] == "dem")
    from app import rasters
    import os
    assert os.path.exists(rasters.local(d["raster"]["key"]))
    # never the last map
    assert pro.delete(f"/maps/{cp['id']}").json()["code"] == "last_map"


def test_rename_and_views_are_per_map(pro, P):
    m0 = pro.get(f"/projects/{P['id']}/maps").json()[0]
    m1 = pro.post(f"/projects/{P['id']}/maps", json={"name": "Map1"}).json()
    assert pro.patch(f"/maps/{m1['id']}", json={"name": "Flood"}).json()["name"] == "Flood"
    pro.post(f"/maps/{m1['id']}/open")
    pro.patch(f"/projects/{P['id']}", json={"view": {"center": [5, 7], "zoom": 8}})
    maps = {m["name"]: m for m in pro.get(f"/projects/{P['id']}/maps").json()}
    assert maps["Flood"]["view"]["center"] == [5, 7] and maps["Map"]["view"].get("center") != [5, 7]


def test_order_within_a_map(pro, P):
    a = vector(pro, P, "a", [PT]); b = vector(pro, P, "b", [PT])
    m1 = pro.post(f"/projects/{P['id']}/maps", json={"name": "Map1"}).json()
    pro.post(f"/maps/{m1['id']}/open")
    c = vector(pro, P, "c", [PT])
    assert pro.put(f"/projects/{P['id']}/layer-order", json={"layer_ids": [b["id"], a["id"]]}).status_code == 200
    assert pro.put(f"/projects/{P['id']}/layer-order", json={"layer_ids": [c["id"], a["id"]]}).status_code == 422
    assert pro.put(f"/projects/{P['id']}/layer-order", json={"layer_ids": [c["id"]]}).status_code == 200
