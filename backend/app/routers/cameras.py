"""Live cameras (Defence › Cameras): a phone opens the camera's secret address and sends pictures and its position;
people of the organization watch it live and see it on the map. A demo of live surveillance: pictures as MJPEG, a few a
second, through PolyGIS itself; IP cameras and recordings can come in later through a media server."""
import secrets
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import cameras as hub
from ..audit import audit
from ..config import settings
from ..db import get_conn, pool
from ..errors import Problem
from ..identity import need
from .projects import project_of

router = APIRouter(tags=["cameras"])
EDIT = need("editor", write=True)
WATCH_MAX_S = 30 * 60                       # one watching connection lasts at most this long (the page reconnects)
BOUNDARY = "polygisframe"


def _public(c: dict) -> dict:
    out = {k: c[k] for k in ("id", "project_id", "name", "last_at", "lng", "lat", "accuracy", "heading", "located_at", "created_at")}
    out["live"] = hub.live(str(c["id"]))
    out["send_url"] = f"{settings.app_url.rstrip('/')}/cam/{c['token']}"
    return out


async def camera_of(conn, cid, user) -> dict:
    cur = await conn.execute("SELECT * FROM cameras WHERE id = %s AND org_id = %s", (cid, user["org_id"]))
    c = await cur.fetchone()
    if not c:
        raise Problem(404, "That camera was not found.", code="not_found")
    await project_of(conn, c["project_id"], user)
    return c


class CameraIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.get("/projects/{project_id}/cameras", summary="The project's cameras, live or not")
async def list_cameras(project_id: UUID, user=Depends(need()), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    cur = await conn.execute("SELECT * FROM cameras WHERE project_id = %s ORDER BY created_at", (project_id,))
    return [_public(c) for c in await cur.fetchall()]


@router.post("/projects/{project_id}/cameras", status_code=201, summary="Add a camera: its secret address is opened on the phone")
async def add_camera(project_id: UUID, body: CameraIn, user=Depends(EDIT), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    cur = await conn.execute("INSERT INTO cameras (org_id, project_id, name, token, created_by) VALUES (%s, %s, %s, %s, %s) RETURNING *",
                             (user["org_id"], project_id, body.name.strip(), secrets.token_urlsafe(24), user["id"]))
    c = await cur.fetchone()
    await audit(conn, user, "camera.create", "camera", c["id"], name=c["name"])
    return _public(c)


@router.patch("/cameras/{camera_id}", summary="Rename a camera, or give it a new secret address")
async def update_camera(camera_id: UUID, body: dict, user=Depends(EDIT), conn=Depends(get_conn)):
    c = await camera_of(conn, camera_id, user)
    if body.get("name") and str(body["name"]).strip():
        await conn.execute("UPDATE cameras SET name = %s WHERE id = %s", (str(body["name"]).strip()[:120], camera_id))
    if body.get("new_address"):                   # the old address stops working at once
        await conn.execute("UPDATE cameras SET token = %s WHERE id = %s", (secrets.token_urlsafe(24), camera_id))
        hub.drop(str(camera_id))
    return _public(await camera_of(conn, c["id"], user))


@router.delete("/cameras/{camera_id}", status_code=204, summary="Remove a camera")
async def delete_camera(camera_id: UUID, user=Depends(EDIT), conn=Depends(get_conn)):
    c = await camera_of(conn, camera_id, user)
    await conn.execute("DELETE FROM cameras WHERE id = %s", (camera_id,))
    hub.drop(str(camera_id))
    await audit(conn, user, "camera.delete", "camera", c["id"], name=c["name"])
    return Response(status_code=204)


@router.get("/cameras/{camera_id}/snapshot.jpg", summary="The camera's latest picture")
async def snapshot(camera_id: UUID, user=Depends(need())):
    async with pool.connection() as conn:
        await camera_of(conn, camera_id, user)
    got = hub.latest(str(camera_id))
    if not got:
        raise Problem(404, "This camera has not sent a picture yet.", code="not_found")
    return Response(got[0], media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/cameras/{camera_id}/live.mjpg", summary="Watch the camera live (MJPEG: plays in an <img>)")
async def watch(camera_id: UUID, request: Request, user=Depends(need())):
    async with pool.connection() as conn:          # checked, then the connection goes back: watching holds none
        await camera_of(conn, camera_id, user)
    cid = str(camera_id)

    async def pictures():
        start, seq = time.monotonic(), 0
        got = hub.latest(cid)
        if got:                                      # what it last showed, at once
            yield _part(got[0])
        while time.monotonic() - start < WATCH_MAX_S:
            if await request.is_disconnected():
                return
            nxt = await hub.next_frame(cid, seq, timeout=max(0.05, min(10, WATCH_MAX_S - (time.monotonic() - start))))
            if nxt is None:
                continue
            frame, seq = nxt
            yield _part(frame)
    return StreamingResponse(pictures(), media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def _part(frame: bytes) -> bytes:
    return b"--" + BOUNDARY.encode() + b"\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"


# ---------- the camera's side: its secret address, no sign-in ----------

async def _by_token(token: str) -> dict:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT id, name, project_id FROM cameras WHERE token = %s", (token,))
        c = await cur.fetchone()
    if not c:
        raise Problem(404, "This camera address is not in use any more.", code="not_found")
    return c


@router.get("/cam/{token}", summary="The camera's name, for the page that sends from the phone")
async def cam_info(token: str):
    c = await _by_token(token)
    return {"name": c["name"], "max_bytes": hub.MAX_BYTES}


_last_seen: dict[str, float] = {}


@router.post("/cam/{token}/frame", summary="Send one picture (image/jpeg body), with the phone's position if known")
async def cam_frame(token: str, request: Request, lat: float | None = None, lng: float | None = None,
                    accuracy: float | None = None, heading: float | None = None):
    body = await request.body()
    if not body or len(body) > hub.MAX_BYTES or body[:2] != b"\xff\xd8":
        raise Problem(422, "Send one JPEG picture of at most 600 KB.", code="validation")
    c = await _by_token(token)
    cid = str(c["id"])
    await hub.put(cid, body)
    now = time.monotonic()
    if now - _last_seen.get(cid, 0) > 5:            # the database hears of it every few seconds, not every picture
        _last_seen[cid] = now
        ok = lat is not None and lng is not None and -90 <= lat <= 90 and -180 <= lng <= 180
        async with pool.connection() as conn:
            if ok:
                await conn.execute("UPDATE cameras SET last_at = now(), lat = %s, lng = %s, accuracy = %s, heading = %s, located_at = now() WHERE id = %s",
                                   (lat, lng, accuracy, heading if heading is not None and 0 <= heading <= 360 else None, c["id"]))
            else:
                await conn.execute("UPDATE cameras SET last_at = now() WHERE id = %s", (c["id"],))
    return {"ok": True}

