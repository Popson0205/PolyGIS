"""Share links: a read-only map from a link, without signing in; the layers it names only; downloads only when
allowed; organization-only links; ending and revoking."""
import json

import pytest

SQUARE = {"type": "Polygon", "coordinates": [[[7.46, 9.07], [7.47, 9.07], [7.47, 9.08], [7.46, 9.08], [7.46, 9.07]]]}


@pytest.fixture
def editor(person):
    return person(role="member", name="Shola Sharer")


@pytest.fixture
def anon(client):
    """Someone with no sign-in at all."""
    client.cookies.clear()
    return client


def _setup(editor):
    p = editor.post("/projects", json={"name": "Shared estate", "display_epsg": 26332}).json()
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "Plot 1"}, "geometry": SQUARE}]}
    a = editor.post(f"/projects/{p['id']}/layers/import", files={"file": ("plots.geojson", json.dumps(fc))}).json()[0]
    b = editor.post(f"/projects/{p['id']}/layers/import", files={"file": ("private.geojson", json.dumps(fc))}).json()[0]
    return p, a, b


def test_a_link_shows_its_layers_to_anyone_and_nothing_else(editor, anon):
    p, plots, private = _setup(editor)
    r = editor.post(f"/projects/{p['id']}/shares", json={"title": "Plots for the buyers", "layer_ids": [plots["id"]],
                                                            "view": {"center": [7.465, 9.075], "zoom": 15}})
    assert r.status_code == 201, r.text
    share = r.json()
    assert len(share["token"]) >= 30 and share["audience"] == "anyone" and not share["downloads"]

    anon.cookies.clear()
    pub = anon.get(f"/public/shares/{share['token']}")
    assert pub.status_code == 200, pub.text
    body = pub.json()
    assert body["title"] == "Plots for the buyers" and [l["name"] for l in body["layers"]] == ["plots"]
    assert body["view"]["zoom"] == 15 and "org_id" not in body and "token" not in body
    assert anon.get(f"/public/shares/{share['token']}/layers/{plots['id']}/tiles/15/17063/15462.pbf").status_code == 200
    fid = editor.post(f"/layers/{plots['id']}/select", json={"filters": []}).json()["ids"][0]
    assert anon.get(f"/public/shares/{share['token']}/layers/{plots['id']}/features/{fid}").json()["properties"]["name"] == "Plot 1"
    # a layer the link does not name, even of the same project, is not reachable
    assert anon.get(f"/public/shares/{share['token']}/layers/{private['id']}/tiles/15/17063/15462.pbf").status_code == 404
    pfid = editor.post(f"/layers/{private['id']}/select", json={"filters": []}).json()["ids"][0]
    assert anon.get(f"/public/shares/{share['token']}/layers/{private['id']}/features/{pfid}").status_code == 404
    assert anon.get(f"/public/shares/{share['token']}/layers/{plots['id']}/features/{pfid}").status_code == 404   # not of that layer
    # downloads are off unless the link allows them
    assert anon.get(f"/public/shares/{share['token']}/layers/{plots['id']}/export").status_code == 403
    r = editor.patch(f"/shares/{share['id']}", json={"downloads": True})
    assert r.status_code == 200 and r.json()["downloads"]
    d = anon.get(f"/public/shares/{share['token']}/layers/{plots['id']}/export?format=geojson")
    assert d.status_code == 200 and json.loads(d.content)["features"][0]["properties"]["name"] == "Plot 1"
    # views are counted, the shared page may be embedded
    assert editor.get(f"/projects/{p['id']}/shares").json()[0]["views"] >= 1
    assert "X-Frame-Options" not in anon.get(f"/share/{share['token']}").headers
    assert anon.get("/public/shares/not-a-token").status_code == 404


def test_revoked_ended_and_organization_only_links(person, editor, anon):
    p, plots, _ = _setup(editor)
    s = editor.post(f"/projects/{p['id']}/shares", json={"title": "Team only", "layer_ids": [plots["id"]], "audience": "organization"}).json()
    anon.cookies.clear()
    assert anon.get(f"/public/shares/{s['token']}").status_code == 401
    colleague = person(role="viewer", name="Col League")
    assert colleague.get(f"/public/shares/{s['token']}").status_code == 200
    outsider = person(role="owner", name="Out Sider", org_id="00000000-0000-4000-8000-000000000077")
    assert outsider.get(f"/public/shares/{s['token']}").status_code == 403

    # a viewer can't make or revoke links; an editor revokes, and the link stops at once
    assert colleague.post(f"/projects/{p['id']}/shares", json={"title": "x", "layer_ids": [plots["id"]]}).status_code == 403
    assert editor.delete(f"/shares/{s['id']}").json()["revoked_at"]
    assert colleague.get(f"/public/shares/{s['token']}").status_code == 404
    assert editor.patch(f"/shares/{s['id']}", json={"title": "again"}).status_code == 409

    e = editor.post(f"/projects/{p['id']}/shares", json={"title": "A week", "layer_ids": [plots["id"]], "expires_in_days": 7}).json()
    assert e["expires_at"]
    import psycopg
    from app.config import settings
    with psycopg.connect(settings.database_url, autocommit=True) as c:
        c.execute(f'UPDATE "{settings.db_schema}".shares SET expires_at = now() - interval \'1 day\' WHERE id = %s', (e["id"],))
    r = anon.get(f"/public/shares/{e['token']}")
    assert r.status_code == 410 and "ended" in r.json()["detail"]

    # layers must be the project's own
    other = editor.post("/projects", json={"name": "Elsewhere"}).json()
    r = editor.post(f"/projects/{other['id']}/shares", json={"title": "Sneaky", "layer_ids": [plots["id"]]})
    assert r.status_code == 422
