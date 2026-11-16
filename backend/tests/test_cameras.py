"""Live cameras: a phone sends pictures to its secret address; people of the organization watch them live."""
import io

from PIL import Image

from app import cameras as hub
from app.routers import cameras as cam_router
from test_rasters import pro  # noqa: F401  (fixture)
from test_scenarios import P  # noqa: F401


def jpeg(color=(200, 30, 30)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(b, "JPEG")
    return b.getvalue()


def test_a_phone_sends_and_the_organization_watches(pro, P, client, person, monkeypatch):
    c = pro.post(f"/projects/{P['id']}/cameras", json={"name": "Gate camera"})
    assert c.status_code == 201, c.text
    c = c.json()
    token = c["send_url"].rsplit("/", 1)[1]
    assert c["live"] is False and "token" not in c and c["send_url"].endswith(f"/cam/{token}")
    client.cookies.clear()
    assert client.get(f"/cam/{token}").json()["name"] == "Gate camera"
    # the phone needs no sign-in and no page header: its address is the key
    r = client.post(f"/cam/{token}/frame?lat=6.45&lng=3.39&accuracy=12&heading=90", content=jpeg(), headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 200, r.text
    assert client.post(f"/cam/{token}/frame", content=b"not a picture").status_code == 422
    assert client.post("/cam/nope/frame", content=jpeg()).status_code == 404
    got = next(x for x in pro.get(f"/projects/{P['id']}/cameras").json() if x["id"] == c["id"])
    assert got["live"] is True and (got["lat"], got["lng"], got["heading"]) == (6.45, 3.39, 90)
    snap = pro.get(f"/cameras/{c['id']}/snapshot.jpg")
    assert snap.status_code == 200 and snap.headers["content-type"] == "image/jpeg" and snap.content[:2] == b"\xff\xd8"
    # watching: MJPEG, the last picture at once, then the next ones
    monkeypatch.setattr(cam_router, "WATCH_MAX_S", 0.5)
    pro._go("GET", "/projects")                       # (the cookies of pro, for the stream below)
    with client.stream("GET", f"/cameras/{c['id']}/live.mjpg") as s:
        assert s.headers["content-type"].startswith("multipart/x-mixed-replace")
        first = b"".join(s.iter_bytes())
    assert first.startswith(b"--polygisframe\r\nContent-Type: image/jpeg") and b"\xff\xd8" in first
    # another organization sees nothing of it
    stranger = person(role="admin", name="Other Org", org_id="99999999-9999-9999-9999-999999999999")
    assert stranger.get(f"/cameras/{c['id']}/snapshot.jpg").status_code == 404
    # a new address: the old one stops working
    newc = pro.patch(f"/cameras/{c['id']}", json={"new_address": True}).json()
    assert newc["send_url"] != c["send_url"] and client.post(f"/cam/{token}/frame", content=jpeg()).status_code == 404
    assert pro.delete(f"/cameras/{c['id']}").status_code == 204
    assert hub.latest(c["id"]) is None


def test_the_store_waits_for_the_next_picture():
    import asyncio

    async def go():
        await hub.put("t1", b"\xff\xd8a")
        assert (await hub.next_frame("t1", 0, timeout=0.1))[1] == 1
        assert await hub.next_frame("t1", 1, timeout=0.05) is None
        waiter = asyncio.create_task(hub.next_frame("t1", 1, timeout=2))
        await asyncio.sleep(0.01)
        await hub.put("t1", b"\xff\xd8b")
        assert (await waiter) == (b"\xff\xd8b", 2)
        hub.drop("t1")
    asyncio.run(go())
