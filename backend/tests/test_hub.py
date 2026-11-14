"""Publishing a layer to Geohub's Data hub: the features and fields go to Geohub as the person, as a new dataset or its
next version; only editors; only vector layers."""
import json

from conftest import HUB

SQUARE = {"type": "Polygon", "coordinates": [[[7.46, 9.07], [7.47, 9.07], [7.47, 9.08], [7.46, 9.08], [7.46, 9.07]]]}


def test_a_layer_goes_to_the_data_hub_and_again_as_a_new_version(person):
    ed = person(role="member", name="Hub Publisher")
    p = ed.post("/projects", json={"name": "Bosso Estate"}).json()
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"Plot no": f"B{i}", "Area (ha)": 0.1 * i},
                                                     "geometry": {**SQUARE, "coordinates": [[[x + i * 0.02, y] for x, y in SQUARE["coordinates"][0]]]}} for i in range(3)]}
    lay = ed.post(f"/projects/{p['id']}/layers/import", files={"file": ("parcels.geojson", json.dumps(fc))}).json()[0]
    r = ed.post(f"/layers/{lay['id']}/hub", json={"title": "Bosso parcels", "visibility": "organization", "tags": ["minna"]})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["version"] == 1 and d["feature_count"] == 3 and d["url"].endswith(f"/hub/{d['slug']}")
    sent = HUB.datasets[d["slug"]]["body"]
    assert sent["geometry_type"] == "area" and sent["ref"] == lay["id"] and sent["label"] == "Bosso Estate › parcels"
    assert {f["name"] for f in sent["fields"]} == {"Plot no", "Area (ha)"} and sent["features"][0]["geometry"]["type"] == "Polygon"
    assert sent["features"][1]["properties"]["Plot no"] == "B1"

    listed = ed.get(f"/layers/{lay['id']}/hub").json()
    assert [x["slug"] for x in listed] == [d["slug"]]
    ids = ed.post(f"/layers/{lay['id']}/select", json={"filters": []}).json()["ids"][:2]
    r = ed.post(f"/layers/{lay['id']}/hub/{d['slug']}/versions", json={"ids": ids})
    assert r.status_code == 201 and r.json()["version"] == 2 and r.json()["feature_count"] == 2

    # Geohub's refusals come back in its own words
    r = ed.post(f"/layers/{lay['id']}/hub", json={"title": "Bad", "license": "Private", "visibility": "public"})
    assert r.status_code == 422 and "Geohub" in r.json()["detail"]
    other = person(role="member", name="Another Editor")
    assert other.post(f"/layers/{lay['id']}/hub/{d['slug']}/versions", json={}).status_code == 404
    viewer = person(role="viewer", name="Just Looking")
    assert viewer.post(f"/layers/{lay['id']}/hub", json={"title": "No"}).status_code == 403


def test_a_raster_layer_goes_to_the_data_hub_as_its_geotiff(person):
    """A raster goes as its GeoTIFF, in parts straight to Geohub's storage, drawn there with the layer's colour ramp;
    Geohub prepares it, and PolyGIS asks how it is going."""
    import numpy as np
    from test_rasters import tif
    ed = person(role="member", name="Raster Publisher", features=["maps", "editing", "raster"])
    p = ed.post("/projects", json={"name": "Bosso Estate"}).json()
    dem = (np.random.default_rng(7).random((200, 200)) * 950 + 250).astype("float32")   # noisy, so it does not compress into one part
    lay = ed.post(f"/projects/{p['id']}/layers/import", files={"file": ("minna_dem.tif", tif(dem))}).json()[0]
    assert lay["geometry_type"] == "Raster"
    st = ed.put(f"/layers/{lay['id']}/raster-style", json={**lay["style"], "ramp": "Spectral", "min": 260, "max": 1200, "invert": True})
    assert st.status_code == 200, st.text

    r = ed.post(f"/layers/{lay['id']}/hub", json={"title": "Minna elevation", "visibility": "organization", "tags": ["dem"]})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["status"] == "processing" and out["url"].endswith(f"/hub/{out['slug']}") and out["upload"]
    u = HUB.uploads[out["upload"]]
    sent = u["body"]
    assert sent["filename"] == "minna_dem.tif" and sent["ref"] == lay["id"] and sent["label"] == "Bosso Estate › minna_dem"
    from app.raster_style import RAMPS
    assert sent["render"] == {"colormap": "spectral", "colors": RAMPS["Spectral"][::-1], "rescale": [260, 1200]}   # Spectral, inverted
    assert u["parts"] > 1 and len(u["file"]) == sent["size"] and u["file"][:4] in (b"II*\x00", b"II+\x00")   # the whole file, in parts

    # PolyGIS asks how it is going; then the next version goes the same way
    s = ed.get(f"/layers/{lay['id']}/hub/uploads/{out['upload']}").json()
    assert s["status"] == "ready" and s["version"] == 1 and s["slug"] == out["slug"]
    listed = ed.get(f"/layers/{lay['id']}/hub").json()
    assert [x["slug"] for x in listed] == [out["slug"]] and listed[0]["latest_version"] == 1
    r = ed.post(f"/layers/{lay['id']}/hub/{out['slug']}/versions", json={})
    assert r.status_code == 201 and r.json()["status"] == "processing"
    assert ed.get(f"/layers/{lay['id']}/hub/uploads/{r.json()['upload']}").json()["version"] == 2
    other = person(role="member", name="Not The Publisher", features=["maps", "editing", "raster"])
    assert other.get(f"/layers/{lay['id']}/hub/uploads/{out['upload']}").status_code == 404
    assert other.post(f"/layers/{lay['id']}/hub/{out['slug']}/versions", json={}).status_code == 404


def test_a_geohub_not_yet_updated_is_named_plainly(person):
    """A Geohub without the raster update answers 405 or 404 with no code: PolyGIS says it needs updating, not 'Method Not Allowed'."""
    import numpy as np
    from test_rasters import tif
    ed = person(role="member", name="Early Publisher", features=["maps", "editing", "raster"])
    p = ed.post("/projects", json={"name": "Early"}).json()
    lay = ed.post(f"/projects/{p['id']}/layers/import", files={"file": ("dem.tif", tif(np.ones((20, 20), dtype="float32")))}).json()[0]
    real = HUB.rasters
    for status, detail in ((405, "Method Not Allowed"), (404, "Not Found")):
        HUB.rasters = lambda req, path, s=status, d=detail: __import__("httpx").Response(s, json={"detail": d})
        try:
            r = ed.post(f"/layers/{lay['id']}/hub", json={"title": "DEM"})
        finally:
            HUB.rasters = real
        assert r.status_code == 502 and r.json()["code"] == "geohub_outdated" and "has not been updated to take rasters" in r.json()["detail"], r.text


def test_a_land_cover_raster_goes_with_its_classes(person):
    """A raster shown by class goes with each shown class's value, colour and name; hidden classes are left out."""
    import numpy as np
    from test_rasters import tif
    ed = person(role="member", name="Land Cover Publisher", features=["maps", "editing", "raster"])
    p = ed.post("/projects", json={"name": "Land Check Mapping"}).json()
    lc = np.where(np.arange(400).reshape(20, 20) % 20 < 10, 10, 20).astype("uint8")
    lay = ed.post(f"/projects/{p['id']}/layers/import", files={"file": ("worldcover.tif", tif(lc))}).json()[0]
    style = {**lay["style"], "mode": "classes", "categories": [{"value": 10, "color": "#006400", "label": "Tree cover"},
                                                               {"value": 20, "color": "#FFBB22", "label": "Shrubland", "hidden": True}]}
    assert ed.put(f"/layers/{lay['id']}/raster-style", json=style).status_code == 200
    out = ed.post(f"/layers/{lay['id']}/hub", json={"title": "Land cover"}).json()
    assert HUB.uploads[out["upload"]]["body"]["render"] == {"classes": [{"value": 10, "color": "#006400", "label": "Tree cover"}]}
