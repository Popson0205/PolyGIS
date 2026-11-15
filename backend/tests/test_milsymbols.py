"""Military symbols: a point layer styled with MIL-STD-2525 codes from a field, its text fields, and what is refused."""
from test_rasters import pro  # noqa: F401  (fixture)
from test_scenarios import P, vector  # noqa: F401


def test_symbol_layer(pro, P):
    lay = pro.post(f"/projects/{P['id']}/layers", json={"name": "Own forces", "geometry_type": "Point", "fields": [
        {"name": "sidc", "type": "string"}, {"name": "designation", "type": "string"}, {"name": "higher_formation", "type": "string"}]}).json()
    style = {"type": "milsymbol", "mil": {"sidc": "SFGPUCI----D---", "sidc_field": "sidc", "designation_field": "designation",
                                          "higher_field": "higher_formation", "size": 40}}
    r = pro.patch(f"/layers/{lay['id']}", json={"style": style})
    assert r.status_code == 200, r.text
    assert r.json()["style"]["type"] == "milsymbol" and r.json()["style"]["mil"]["sidc_field"] == "sidc"
    f = pro.post(f"/layers/{lay['id']}/features", json={"geometry": {"type": "Point", "coordinates": [7.4, 9.0]},
                                                         "properties": {"sidc": "SHGPUCA----E---", "designation": "2", "higher_formation": "3 BDE"}})
    assert f.status_code == 201, f.text
    # with no mil settings, the default symbol
    r = pro.patch(f"/layers/{lay['id']}", json={"style": {"type": "milsymbol"}})
    assert r.json()["style"]["mil"]["sidc"] == "SFGPU----------"


def test_symbols_refused_where_they_do_not_fit(pro, P):
    poly = vector(pro, P, "area", [{"type": "Polygon", "coordinates": [[[7, 9], [7.1, 9], [7.1, 9.1], [7, 9]]]}])
    r = pro.patch(f"/layers/{poly['id']}", json={"style": {"type": "milsymbol"}})
    assert r.status_code == 422 and "point layers" in r.json()["detail"]
    pts = vector(pro, P, "pts", [{"type": "Point", "coordinates": [7, 9]}])
    r = pro.patch(f"/layers/{pts['id']}", json={"style": {"type": "milsymbol", "mil": {"sidc_field": "nope"}}})
    assert r.status_code == 422 and "nope" in r.json()["detail"]
    r = pro.patch(f"/layers/{pts['id']}", json={"style": {"type": "milsymbol", "mil": {"sidc": "bad code!"}}})
    assert r.status_code == 422
