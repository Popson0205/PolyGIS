"""The projects page's map pictures: each layer drawn in its own colour over the project's extent."""
import json

from tests.test_rasters import PLANE, tif


def test_thumbnail_draws_every_kind_of_layer(person):
    c = person(role="member", features=["maps", "editing", "export"])
    p = c.post("/projects", json={"name": "Thumbs"}).json()
    empty = c.get(f"/projects/{p['id']}/thumbnail.svg")
    assert empty.status_code == 200 and empty.headers["content-type"].startswith("image/svg+xml") and "<path" not in empty.text
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[[6.6, 9.0], [6.62, 9.0], [6.62, 9.02], [6.6, 9.02], [6.6, 9.0]]]}},
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [6.61, 9.01]}}]}
    for name, data in (("a.geojson", json.dumps(fc).encode()), ("dem.tif", tif(PLANE))):
        assert c.post(f"/projects/{p['id']}/layers/import", files={"file": (name, data)}).status_code == 201
    lays = c.get(f"/projects/{p['id']}").json()["layers"]
    poly = next(x for x in lays if x["geometry_type"] == "Polygon")
    c.patch(f"/layers/{poly['id']}", json={"style": {**poly["style"], "color": "#123456"}})
    svg = c.get(f"/projects/{p['id']}/thumbnail.svg?v=1")
    assert svg.status_code == 200 and "private" in svg.headers["cache-control"]
    t = svg.text
    assert "<path" in t and 'fill="#123456"' in t and "<circle" in t and "data:image/png;base64," in t
    other = person(role="member", features=["maps"], org="Kano Mappers", org_id="00000000-0000-0000-0000-0000000000aa")
    assert other.get(f"/projects/{p['id']}/thumbnail.svg").status_code == 404
