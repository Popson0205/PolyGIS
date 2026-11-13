"""Print layouts: kept per project, saved only by editors on a plan with layouts, seen by everyone in the organization."""
import pytest


@pytest.fixture
def adv(person):
    return person(role="member", name="Lola Layout", features=["maps", "editing", "export", "layouts"])


SETTINGS = {"page": "A3", "orientation": "landscape", "scale": 2500, "center": [7.466, 9.076], "title": "Bosso parcels",
            "legend": "side", "grid": {"on": True, "interval": 200}, "north": True, "scalebar": True, "dpi": 300}


def test_layouts_saved_listed_changed_and_deleted(person, adv):
    p = adv.post("/projects", json={"name": "Layouts", "display_epsg": 26332}).json()
    r = adv.post(f"/projects/{p['id']}/layouts", json={"name": "A3 sheet", "settings": SETTINGS})
    assert r.status_code == 201, r.text
    lay = r.json()
    assert lay["settings"]["scale"] == 2500 and lay["created_by_name"] == "Lola Layout"

    viewer = person(role="viewer", name="Vic Viewer", features=["maps"])
    seen = viewer.get(f"/projects/{p['id']}/layouts").json()
    assert [x["name"] for x in seen] == ["A3 sheet"]
    assert viewer.patch(f"/layouts/{lay['id']}", json={"name": "Mine"}).status_code == 403

    r = adv.patch(f"/layouts/{lay['id']}", json={"name": "A3 sheet, final", "settings": {**SETTINGS, "scale": 5000}})
    assert r.status_code == 200 and r.json()["settings"]["scale"] == 5000 and r.json()["name"] == "A3 sheet, final"

    assert adv.post(f"/projects/{p['id']}/layouts", json={"name": "Huge", "settings": {"notes": "x" * 30000}}).status_code == 422
    assert adv.delete(f"/layouts/{lay['id']}").status_code == 204
    assert adv.get(f"/projects/{p['id']}/layouts").json() == []
    assert adv.delete(f"/layouts/{lay['id']}").status_code == 404


def test_layouts_need_the_plan_and_the_organization(person, adv):
    p = adv.post("/projects", json={"name": "Plan check"}).json()
    standard = person(role="member", name="Sam Standard")            # maps and editing only
    r = standard.post(f"/projects/{p['id']}/layouts", json={"name": "No", "settings": {}})
    assert r.status_code == 403 and "Print layouts" in r.json()["detail"]
    outsider = person(role="owner", name="Other Org", org_id="00000000-0000-4000-8000-000000000009", features=["maps", "editing", "layouts"])
    assert outsider.get(f"/projects/{p['id']}/layouts").status_code == 404
    assert outsider.post(f"/projects/{p['id']}/layouts", json={"name": "No", "settings": {}}).status_code == 404
