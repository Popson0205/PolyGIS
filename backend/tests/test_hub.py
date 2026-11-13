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
