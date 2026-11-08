"""Signing in and out, through GeoEstate ID (Geohub)."""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from .. import identity
from ..config import settings
from ..geoestate_id import GeoEstateIDError
from ..identity import current_user

router = APIRouter(tags=["signing in"])


def _safe(path: str | None) -> str:
    return path if path and path.startswith("/") and not path.startswith("//") else "/"


@router.get("/auth/sign-in", summary="Off to Geohub to sign in; back to `next` afterwards")
async def sign_in(next: str | None = None):
    verifier, state = identity.new_state()
    r = RedirectResponse(identity.client().authorize_url(identity.callback_url(), state, verifier), status_code=303)
    opts = {"httponly": True, "secure": settings.cookie_secure, "samesite": "lax", "max_age": 600, "path": "/"}
    r.set_cookie("gis_pkce", verifier, **opts)
    r.set_cookie("gis_state", state, **opts)
    r.set_cookie("gis_next", _safe(next), **opts)
    return r


@router.get("/auth/callback", summary="Back from Geohub with a one-time code")
async def callback(request: Request, code: str = "", state: str = ""):
    if not code or not state or state != request.cookies.get("gis_state"):
        return RedirectResponse(f"{settings.app_url}/signed-out?reason={quote('This sign-in did not start here. Sign in again.')}", 303)
    try:
        tokens = identity.client().exchange(code, identity.callback_url(), request.cookies.get("gis_pkce", ""))
    except GeoEstateIDError as e:
        return RedirectResponse(f"{settings.app_url}/signed-out?reason={quote(str(e))}", 303)
    r = RedirectResponse(f"{settings.app_url}{_safe(request.cookies.get('gis_next'))}", 303)
    identity.keep(r, tokens)
    for k in ("gis_pkce", "gis_state", "gis_next"):
        r.delete_cookie(k, path="/")
    return r


@router.post("/auth/sign-out", status_code=204, summary="Sign out of PolyGIS (Geohub stays signed in)")
async def sign_out(request: Request):
    from fastapi import Response
    r = Response(status_code=204)
    rt = request.cookies.get(identity.RT)
    if rt:
        try:
            identity.client().sign_out(rt)
        except GeoEstateIDError:
            pass
    identity.forget(r)
    return r


@router.get("/me", summary="Who is signed in, their role, and the licence")
async def me(user=Depends(current_user)):
    return {k: v for k, v in user.items() if k != "claims"} | {"geohub_url": settings.geohub_url}


@router.get("/apps", summary="The GeoEstate apps the organization has, for the app switcher")
async def apps(request: Request, user=Depends(current_user)):
    token = request.state.at or request.cookies.get(identity.AT)
    try:
        return identity.client().apps(token) if token else []
    except GeoEstateIDError:
        return []
