"""PolyGIS licensing: Basic / Advanced editions, and an optional offline signed licence.

Two tiers:
  * basic    — every tool except the two below.
  * advanced — basic PLUS Poly Earth and Defence.

Where the entitlement comes from (first that is present wins):
  1. An offline signed licence file (canonical) — for on-prem / air-gapped / Defence
     deployments with no connection back to Geohub. Set POLYGIS_LICENSE (the raw token)
     or POLYGIS_LICENSE_FILE (default /etc/geoestate/polygis.lic).
  2. The GeoEstate ID token's `product` claim (cloud) — Geohub mints it per org.
  3. Nothing -> Basic fallback (the app stays usable; Earth and Defence stay locked).

A licence carries only entitlements — edition and seat count. No pricing, ever. An expired
or tampered licence never bricks the app; it falls back to Basic.

The signed token is a compact JWS (EdDSA / Ed25519), verified with the embedded public key
(the GeoEstate Licensing Root). Only the public key lives here; the private key stays with
GeoEstate. Issued by the `geolicense` toolkit.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from functools import lru_cache

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

log = logging.getLogger("licence")

# ---- editions & features -----------------------------------------------------------
ADVANCED_FEATURES: tuple[str, ...] = ("poly_earth", "defence")
# Everything PolyGIS currently gates on is Basic (granted to every licensed org).
BASIC_FEATURES: tuple[str, ...] = (
    "maps", "editing", "export", "raster", "geoprocessing", "layouts", "dxf_import",
)
EDITIONS = ("basic", "advanced")
DEFAULT_FALLBACK_SEATS = int(os.environ.get("POLYGIS_FALLBACK_SEATS", "3"))


def features_for(edition: str) -> list[str]:
    """Authoritative edition -> feature list. Advanced is a superset of Basic."""
    if edition == "advanced":
        return list(BASIC_FEATURES) + list(ADVANCED_FEATURES)
    return list(BASIC_FEATURES)


def edition_of(features: list[str] | None, plan: str | None = None) -> str:
    """Resolve the edition of a claim. An explicit basic/advanced plan wins; otherwise it is
    derived from whether the Advanced features are present."""
    p = (plan or "").strip().lower()
    if p in EDITIONS:
        return p
    feats = set(features or [])
    return "advanced" if set(ADVANCED_FEATURES) <= feats else "basic"


# ---- embedded public key (safe to commit) ------------------------------------------
# Replace with your real GeoEstate Licensing Root public key (keygen output), or supply it
# at runtime via GEOESTATE_LICENSE_PUBKEY (PEM). Offline licences are ignored until one is set.
GEOESTATE_LICENSE_PUBKEY_PEM = os.environ.get("GEOESTATE_LICENSE_PUBKEY", "").strip()
GEOESTATE_LICENSE_KID = os.environ.get("GEOESTATE_LICENSE_KID", "geoestate-root")

_LEEWAY = 300


def _b64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@lru_cache(maxsize=1)
def _public_key() -> Ed25519PublicKey | None:
    pem = GEOESTATE_LICENSE_PUBKEY_PEM
    if not pem:
        return None
    try:
        key = serialization.load_pem_public_key(pem.encode())
        return key if isinstance(key, Ed25519PublicKey) else None
    except Exception as e:  # noqa: BLE001
        log.warning("licence public key did not load: %s", e)
        return None


def verify_token(token: str) -> dict | None:
    """Verify a compact-JWS licence token. Returns the payload, or None if it cannot be
    trusted (no key configured, bad signature, wrong type). Never raises."""
    key = _public_key()
    if key is None:
        return None
    try:
        h_b64, p_b64, s_b64 = token.strip().split(".")
    except ValueError:
        return None
    try:
        key.verify(_b64u(s_b64), f"{h_b64}.{p_b64}".encode("ascii"))
    except (InvalidSignature, Exception):  # noqa: BLE001
        return None
    try:
        hdr = json.loads(_b64u(h_b64))
        payload = json.loads(_b64u(p_b64))
    except Exception:  # noqa: BLE001
        return None
    if hdr.get("typ") != "geolicense+jws" or payload.get("typ") != "license":
        return None
    if payload.get("product") not in (None, "polygis"):
        return None
    return payload


def _offline_token() -> str | None:
    raw = os.environ.get("POLYGIS_LICENSE")
    if raw:
        return raw.strip()
    path = os.environ.get("POLYGIS_LICENSE_FILE", "/etc/geoestate/polygis.lic")
    try:
        return open(path).read().strip()
    except OSError:
        return None


def offline_entitlement(now: float | None = None) -> dict | None:
    """If a valid offline licence is configured, return a product-shaped entitlement that is
    CANONICAL (it overrides the Geohub claim). Returns None when absent/invalid/expired."""
    token = _offline_token()
    if not token:
        return None
    payload = verify_token(token)
    if not payload:
        log.warning("offline PolyGIS licence present but could not be verified; ignoring")
        return None
    now = int(now if now is not None else time.time())
    nbf = int(payload.get("nbf", payload.get("iat", 0)))
    exp = payload.get("exp")
    if now + _LEEWAY < nbf:
        return None
    if exp is not None and now - _LEEWAY > int(exp):
        log.info("offline PolyGIS licence expired; falling back to Basic")
        return None
    edition = payload.get("edition") if payload.get("edition") in EDITIONS else "basic"
    seats = int(payload.get("max_users") or DEFAULT_FALLBACK_SEATS)
    org = payload.get("org") or {}
    return {
        "id": "gis",
        "name": "PolyGIS",
        "plan": edition,
        "plan_name": f"PolyGIS {edition.title()}",
        "status": "active",
        "edition": edition,
        "features": features_for(edition),
        "limits": {"users": seats},
        "source": "offline-licence",
        "licence_id": payload.get("lic_id"),
        "org": org,
    }
