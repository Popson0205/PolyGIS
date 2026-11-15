"""Live tracking: devices and apps sending in every supported format, the ADS-B, AIS and Geohub feeds (answered by
stand-ins), the layer of latest positions, trails, geofence alerts and tracks kept as a layer."""
import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import tracking
from app.routers import tracking as tr_router
from conftest import HUB
from test_rasters import pro  # noqa: F401  (fixture)
from test_scenarios import P, vector  # noqa: F401

AREA = [7.0, 8.8, 7.9, 9.4]


def new(pro, P, kind="device", **cfg):
    r = pro.post(f"/projects/{P['id']}/trackers", json={"name": f"{kind} feed", "kind": kind, "config": cfg.pop("config", {}), **cfg})
    assert r.status_code == 201, r.text
    return r.json()


def path(t):
    return "/track/" + t["ingest_url"].rsplit("/", 1)[1]


def live(pro, P):
    return pro.get(f"/projects/{P['id']}/tracking/live").json()


def test_every_device_format(pro, P, client):
    t = new(pro, P)
    assert t["ingest_url"].endswith(path(t)) and t["layer"]["style"]["type"] == "milsymbol"
    p = path(t)
    now = int(time.time())
    # Traccar Client / OsmAnd: speed in knots; no cookie and no X-GeoEstate header
    client.cookies.clear()
    r = client.post(p, params={"id": "truck-1", "lat": 9.05, "lon": 7.45, "timestamp": now - 60, "speed": 10, "bearing": 90, "altitude": 480, "batt": 77})
    assert r.status_code == 200 and r.json() == {"received": 1}, r.text
    r = client.get(p, params={"id": "truck-1", "lat": 9.06, "lon": 7.46, "timestamp": now - 30, "speed": 12})
    assert r.json() == {"received": 1}
    r = client.get(p, params={"id": "osmand", "lat": 9.07, "lon": 7.36, "timestamp": now - 25, "speed_ms": 10})      # the OsmAnd app's template
    assert r.json() == {"received": 1}
    # OwnTracks
    r = client.post(p, json={"_type": "location", "lat": 9.10, "lon": 7.40, "tst": now - 20, "vel": 40, "cog": 180, "tid": "AB"},
                    headers={"X-Limit-U": "ade", "X-Limit-D": "phone"})
    assert r.status_code == 200 and r.json() == []
    # a Traccar server forwarding
    r = client.post(p, json={"position": {"latitude": 9.2, "longitude": 7.6, "fixTime": datetime.now(timezone.utc).isoformat(), "speed": 5, "course": 45,
                                          "attributes": {"ignition": True}}, "device": {"uniqueId": "8661", "name": "Patrol 2"}})
    assert r.json() == {"received": 1}
    # plain JSON (a list) and GeoJSON
    r = client.post(p, json=[{"device_id": "drone-7", "lat": 9.0, "lon": 7.3, "speed": 60, "heading": 270, "time": now - 5, "mission": "recce"}])
    assert r.json() == {"received": 1}
    r = client.post(p, json={"type": "FeatureCollection", "features": [{"type": "Feature", "id": "boat-3", "geometry": {"type": "Point", "coordinates": [7.2, 8.9]},
                                                                         "properties": {"name": "Boat 3"}}]})
    assert r.json() == {"received": 1}
    assert client.post(p, json={"nothing": 1}).status_code == 422
    assert client.post("/track/not-a-token", params={"id": "x", "lat": 1, "lon": 1}).status_code == 404

    pos = {f["properties"]["device"]: f["properties"] for f in live(pro, P)["positions"]["features"]}
    assert set(pos) == {"truck-1", "ade/phone", "8661", "drone-7", "boat-3", "osmand"}
    assert pos["osmand"]["speed_kmh"] == pytest.approx(36, abs=0.1)
    assert pos["truck-1"]["speed_kmh"] == pytest.approx(12 * 1.852, abs=0.1)        # knots to km/h, the latest one
    assert pos["8661"]["name"] == "Patrol 2" and pos["8661"]["speed_kmh"] == pytest.approx(5 * 1.852, abs=0.1)
    assert pos["ade/phone"]["speed_kmh"] == 40 and pos["drone-7"]["mission"] == "recce"
    assert pos["truck-1"]["mgrs"].startswith("32P") and pos["truck-1"]["sidc"] == "SFGPU----------"
    trails = live(pro, P)["trails"]["features"]
    assert [x["properties"]["device"] for x in trails] == ["truck-1"]
    # the layer holds one point per device, and keeps a symbol someone changed
    lay = t["layer"]
    rows = pro.post(f"/layers/{lay['id']}/rows", json={"limit": 100}).json()["rows"]
    assert len(rows) == 6
    truck = next(x for x in rows if x["props"]["device"] == "truck-1")
    pro.patch(f"/layers/{lay['id']}/features/{truck['id']}", json={"properties": {"sidc": "SFGPUCA----E---", "designation": "B COY"}})
    client.cookies.clear()
    client.get(p, params={"id": "truck-1", "lat": 9.07, "lon": 7.47, "timestamp": now})
    truck = next(x for x in pro.post(f"/layers/{lay['id']}/rows", json={"limit": 100}).json()["rows"] if x["props"]["device"] == "truck-1")["props"]
    assert truck["sidc"] == "SFGPUCA----E---" and truck["designation"] == "B COY" and truck["time"].startswith(datetime.fromtimestamp(now, timezone.utc).isoformat()[:16])


def test_geofence_alerts(pro, P, client):
    zone = vector(pro, P, "zones", [{"type": "Polygon", "coordinates": [[[7.40, 9.00], [7.50, 9.00], [7.50, 9.10], [7.40, 9.10], [7.40, 9.00]]]}])
    t = new(pro, P, config={"geofence_layer_id": zone["id"], "geofence_field": "n"})
    p, now = path(t), int(time.time())
    client.cookies.clear()
    for k, (lat, lng) in enumerate([(8.95, 7.45), (9.05, 7.45), (9.06, 7.46), (9.20, 7.45)]):
        client.get(p, params={"id": "convoy", "lat": lat, "lon": lng, "timestamp": now - 100 + k * 10})
    alerts = live(pro, P)["alerts"]
    assert [(a["kind"], a["zone"]) for a in reversed(alerts)] == [("enter", "0"), ("exit", "0")]
    pro.post(f"/projects/{P['id']}/tracking/alerts/seen")
    assert live(pro, P)["alerts"] == []
    # tracks as a layer
    r = pro.post(f"/trackers/{t['id']}/history", json={})
    assert r.status_code == 201 and r.json()["tracks"] == 1
    tr = pro.post(f"/layers/{r.json()['layer']['id']}/rows", json={}).json()["rows"][0]["props"]
    assert tr["points"] == 4 and 25 < tr["length_km"] < 30


def test_adsb_feeds(pro, P, monkeypatch):
    now = time.time()
    def answer(req: httpx.Request):
        if req.url.host == "api.adsb.lol":
            return httpx.Response(200, json={"ac": [
                {"hex": "4b1a2c", "flight": "NGA123 ", "r": "5N-ABC", "t": "B738", "lat": 9.0, "lon": 7.3, "alt_baro": 35000, "gs": 450, "track": 45, "seen_pos": 2},
                {"hex": "abcdef", "lat": 20.0, "lon": 20.0, "alt_baro": "ground"}]})                               # outside the area: left out
        if req.url.host == "opensky-network.org":
            assert float(req.url.params["lamin"]) == AREA[1]
            return httpx.Response(200, json={"states": [["3c6444", "DLH4AB  ", "Germany", int(now), int(now), 7.5, 9.1, 10000, False, 230.0, 180.0,
                                                         0, None, 10100, "1000", False, 0]]})
        return httpx.Response(404)
    monkeypatch.setattr(tracking, "TRANSPORT", httpx.MockTransport(answer))
    t = new(pro, P, "adsb", config={"bbox": AREA})
    assert "ingest_url" not in t and t["layer"]["style"]["mil"]["sidc"].startswith("SUAP")
    r = pro.post(f"/trackers/{t['id']}/refresh")
    assert r.status_code == 200 and r.json()["received"] == 1, r.text
    assert pro.post(f"/trackers/{t['id']}/refresh").json()["note"] == "Asked a moment ago."
    a = next(f["properties"] for f in live(pro, P)["positions"]["features"] if f["properties"]["device"] == "4b1a2c")
    assert a["name"] == "NGA123" and a["registration"] == "5N-ABC" and a["altitude"] == pytest.approx(35000 * 0.3048, abs=1)
    assert a["speed_kmh"] == pytest.approx(450 * 1.852, abs=0.5)
    o = new(pro, P, "adsb", config={"bbox": AREA, "source": "opensky"})
    assert pro.post(f"/trackers/{o['id']}/refresh").json()["received"] == 1
    d = next(f["properties"] for f in live(pro, P)["positions"]["features"] if f["properties"]["device"] == "3c6444")
    assert d["name"] == "DLH4AB" and d["speed_kmh"] == pytest.approx(230 * 3.6, abs=0.5)
    # areas are checked
    assert pro.post(f"/projects/{P['id']}/trackers", json={"name": "x", "kind": "adsb", "config": {}}).status_code == 422
    assert pro.post(f"/projects/{P['id']}/trackers", json={"name": "x", "kind": "adsb", "config": {"bbox": [-100, -50, 100, 50]}}).status_code == 422


def test_ais_feed(pro, P, monkeypatch):
    msgs = [{"MessageType": "ShipStaticData", "MetaData": {"MMSI": 657000001}, "Message": {"ShipStaticData": {"CallSign": "5NAB", "Destination": "LAGOS", "Type": 70}}},
            {"MessageType": "PositionReport", "MetaData": {"MMSI": 657000001, "ShipName": "MV ADA  ", "latitude": 6.40, "longitude": 3.35,
                                                           "time_utc": "2026-11-15 10:20:30.123456789 +0000 UTC"},
             "Message": {"PositionReport": {"Sog": 12.5, "Cog": 200.0, "TrueHeading": 198, "NavigationalStatus": 0}}}]
    sent = []

    class WS:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def send(self, m): sent.append(json.loads(m))
        async def recv(self):
            if msgs:
                return json.dumps(msgs.pop(0))
            import asyncio
            await asyncio.sleep(10)
    monkeypatch.setattr(tracking, "WS_CONNECT", lambda url, **kw: WS())
    t = new(pro, P, "ais", config={"bbox": [3.0, 6.0, 4.0, 7.0]})
    r = pro.post(f"/trackers/{t['id']}/refresh")
    assert r.status_code == 422 and "aisstream" in r.json()["detail"]                 # no key yet
    pro.patch(f"/trackers/{t['id']}", json={"secret": {"api_key": "k123"}})
    assert pro.get(f"/projects/{P['id']}/trackers").json()[0]["secret_set"] == ["api_key"]
    monkeypatch.setattr(tracking, "MIN_REFRESH", {**tracking.MIN_REFRESH, "ais": 0})
    import app.tracking as m
    orig = m.ais
    monkeypatch.setattr(tracking, "ais", lambda cfg, sec: orig(cfg, sec, seconds=0.3))
    r = pro.post(f"/trackers/{t['id']}/refresh")
    assert r.status_code == 200 and r.json()["received"] == 1, r.text
    assert sent[0]["APIKey"] == "k123" and sent[0]["BoundingBoxes"] == [[[6.0, 3.0], [7.0, 4.0]]]
    s = live(pro, P)["positions"]["features"][0]["properties"]
    assert s["name"] == "MV ADA" and s["callsign"] == "5NAB" and s["destination"] == "LAGOS" and s["speed_kmh"] == pytest.approx(12.5 * 1.852, abs=0.1)


def test_geohub_form_feed(pro, P):
    form = "11111111-2222-3333-4444-555555555555"
    base = datetime.now(timezone.utc) - timedelta(minutes=30)
    HUB.hub_features[form] = [
        {"id": f"f{k}", "seq": k + 1, "status": "submitted", "created_by_name": who, "created_at": (base + timedelta(minutes=k)).isoformat(),
         "geometry": {"type": "Point", "coordinates": [7.4 + k * 0.01, 9.0]}, "properties": {"report": f"Patrol report {k}", "threat": ["low"]}}
        for k, who in enumerate(["Sgt Musa", "Cpl Ada", "Sgt Musa"])]
    t = new(pro, P, "geohub", config={"form_id": form})
    r = pro.post(f"/trackers/{t['id']}/refresh")
    assert r.status_code == 200 and r.json()["received"] == 3, r.text
    pos = {f["properties"]["device"]: f["properties"] for f in live(pro, P)["positions"]["features"]}
    assert set(pos) == {"Sgt Musa", "Cpl Ada"} and pos["Sgt Musa"]["report"] == "Patrol report 2" and pos["Sgt Musa"]["threat"] == "low"
    # the next ask brings only what is new
    HUB.hub_features[form].append({"id": "f3", "seq": 4, "status": "submitted", "created_by_name": "Cpl Ada", "created_at": datetime.now(timezone.utc).isoformat(),
                                   "geometry": {"type": "Point", "coordinates": [7.5, 9.1]}, "properties": {"report": "At the bridge"}})
    from app.routers import tracking as rt
    import app.tracking as m
    m.MIN_REFRESH["geohub"] = 0
    try:
        assert pro.post(f"/trackers/{t['id']}/refresh").json()["received"] == 1
    finally:
        m.MIN_REFRESH["geohub"] = 15
    del rt


def test_manage_trackers(pro, P, client):
    t = new(pro, P)
    old = path(t)
    t2 = pro.post(f"/trackers/{t['id']}/token").json()
    client.cookies.clear()
    assert client.get(old, params={"id": "a", "lat": 9, "lon": 7}).status_code == 404
    assert client.get(path(t2), params={"id": "a", "lat": 9, "lon": 7}).status_code == 200
    pro.patch(f"/trackers/{t['id']}", json={"active": False})
    assert client.get(path(t2), params={"id": "a", "lat": 9, "lon": 7}).status_code == 403
    assert pro.delete(f"/trackers/{t['id']}").status_code == 204
    assert pro.get(f"/projects/{P['id']}/trackers").json() == []
    assert any(l["id"] == t["layer"]["id"] for l in pro.get(f"/projects/{P['id']}").json()["layers"])    # its layer stays


def test_opensky_unreachable_falls_back_and_failing_feeds_rest(pro, P, monkeypatch):
    def answer(req: httpx.Request):
        if req.url.host == "opensky-network.org":
            raise httpx.ConnectTimeout("no answer", request=req)
        if req.url.host == "api.adsb.lol":
            return httpx.Response(200, json={"ac": [{"hex": "4b1a2c", "flight": "NGA9", "lat": 9.0, "lon": 7.3, "alt_baro": 9000, "gs": 300, "track": 10}]})
        return httpx.Response(404)
    monkeypatch.setattr(tracking, "TRANSPORT", httpx.MockTransport(answer))
    o = new(pro, P, "adsb", config={"bbox": AREA, "source": "opensky"})
    r = pro.post(f"/trackers/{o['id']}/refresh").json()
    assert r["received"] == 1 and "adsb.lol was used" in r["note"]
    assert "adsb.lol was used" in pro.get(f"/projects/{P['id']}/trackers").json()[0]["last_error"]
    # a feed that fails is left alone for a while, not asked every few seconds
    monkeypatch.setattr(tracking, "TRANSPORT", httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectTimeout("x", request=req))))
    a = new(pro, P, "adsb", config={"bbox": AREA})
    r = pro.post(f"/trackers/{a['id']}/refresh")
    assert r.status_code == 502 and "ConnectTimeout" in r.json()["detail"]
    assert pro.post(f"/trackers/{a['id']}/refresh").json()["note"] == "Asked a moment ago."
    t = next(x for x in pro.get(f"/projects/{P['id']}/trackers").json() if x["id"] == a["id"])
    assert "Asked again in 2 minutes" in t["last_error"]
