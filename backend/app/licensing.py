"""PolyGIS licensing: Basic / Advanced editions, and an optional offline signed licence.

Two tiers:
  * basic    — every tool except the two below.
  * advanced — basic PLUS Poly Earth and Defence.

Where the entitlement comes from (first that is present wins):
  1. An offline signed licence key (canonical) — for on-prem / air-gapped / Defence
     deployments with no connection back to Geohub. This is the SAME
     `GEOESTATE-LICENCE-1` key the rest of the GeoEstate suite uses (issued by Geohub,
     signed with GeoEstate's Ed25519 key). Set POLYGIS_LICENCE (the key) or
     POLYGIS_LICENCE_FILE (default /etc/geoestate/polygis.lic), and the trusted public
     key in GEOESTATE_LICENCE_PUBLIC_KEY (raw Ed25519, base64url — the same value Geohub
     installs as LICENCE_PUBLIC_KEY).
  2. The GeoEstate ID token's `product` claim (cloud) — Geohub mints it per org.
  3. Nothing -> Basic fallback (the app stays usable; Earth and Defence stay locked).

A licence carries only entitlements — plan, seat caps, feature switches. No pricing, ever.
An expired or tampered licence never bricks the app; it falls back to Basic.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from datetime import date
from functools import lru_cache

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

log = logging.getLogger("licence")

# ---- editions & features -----------------------------------------------------------
ADVANCED_FEATURES: tuple[str, ...] = ("poly_earth", "defence")
# Everything PolyGIS currently gates on is Basic (granted to every licensed org).
BASIC_FEATURES: tuple[str, ...] = (
    "maps", "editing", "export", "raster", "geoprocessing", "layouts", "dxf_import",
)
EDITIONS = ("basic", "advanced")
# Geohub plan keys -> edition. 'standard' is the Basic tier; 'custom' gets everything.
PLAN_EDITION = {"standard": "basic", "basic": "basic", "advanced": "advanced", "custom": "advanced"}
# Default seat caps per edition (match Geohub products.LIMITS["gis"]); overridden by the key.
PLAN_SEATS = {"basic": 5, "advanced": 25}
DEFAULT_FALLBACK_SEATS = int(os.environ.get("POLYGIS_FALLBACK_SEATS", "3"))

FORMAT = "GEOESTATE-LICENCE-1"
# Pinned trusted public keys (raw Ed25519, base64url). Set in the build to stop a deployment
# being pointed at another key via a setting. Otherwise GEOESTATE_LICENCE_PUBLIC_KEY is used.
PINNED: tuple[str, ...] = ()
_LEEWAY_DAYS = 0


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


def _gis_features(plan: str, overrides: dict | None) -> list[str]:
    """Effective PolyGIS feature list for a plan + per-licence overrides (mirrors Geohub's
    products.features for 'gis')."""
    edition = PLAN_EDITION.get((plan or "").strip().lower(), "basic")
    on = set(features_for(edition))
    for k, v in (overrides or {}).items():
        (on.add if v else on.discard)(k)
    return sorted(on)


# ---- trusted public keys -----------------------------------------------------------
def _b64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@lru_cache(maxsize=1)
def _trusted_keys() -> tuple[Ed25519PublicKey, ...]:
    raw = list(PINNED) or [k.strip() for k in os.environ.get("GEOESTATE_LICENCE_PUBLIC_KEY", "").split(",") if k.strip()]
    keys = []
    for k in raw:
        try:
            keys.append(Ed25519PublicKey.from_public_bytes(_b64u(k)))
        except (ValueError, Exception):  # noqa: BLE001
            log.warning("a configured licence public key did not load; ignoring it")
    return tuple(keys)


def verify_key(key: str) -> dict | None:
    """Verify a GEOESTATE-LICENCE-1 key and return its payload, or None if it cannot be
    trusted (no key configured, wrong format, bad signature). Never raises."""
    trusted = _trusted_keys()
    if not trusted:
        return None
    parts = "".join(key.split()).split(".")
    if len(parts) != 3 or parts[0] != FORMAT:
        return None
    try:
        sig = _b64u(parts[2])
        signed = f"{parts[0]}.{parts[1]}".encode()
    except (ValueError, Exception):  # noqa: BLE001
        return None
    for pub in trusted:
        try:
            pub.verify(sig, signed)
            break
        except InvalidSignature:
            continue
        except Exception:  # noqa: BLE001
            continue
    else:
        return None
    try:
        return json.loads(_b64u(parts[1]))
    except Exception:  # noqa: BLE001
        return None


def _offline_key() -> str | None:
    raw = os.environ.get("POLYGIS_LICENCE")
    if raw:
        return raw.strip()
    path = os.environ.get("POLYGIS_LICENCE_FILE", "/etc/geoestate/polygis.lic")
    try:
        return open(path).read().strip()
    except OSError:
        return None


def _not_expired(ends: str | None, today: date | None = None) -> bool:
    if not ends:
        return True
    try:
        return (today or date.today()) <= date.fromisoformat(ends)
    except ValueError:
        return True


def offline_entitlement(today: date | None = None) -> dict | None:
    """If a valid offline GEOESTATE-LICENCE-1 key granting PolyGIS is configured, return a
    product-shaped entitlement that is CANONICAL (it overrides the Geohub claim). Returns None
    when absent / unverifiable / PolyGIS not in the key / expired."""
    key = _offline_key()
    if not key:
        return None
    payload = verify_key(key)
    if not payload:
        log.warning("offline PolyGIS licence present but could not be verified; ignoring")
        return None
    gis = next((p for p in payload.get("products", []) if p.get("product") == "gis"), None)
    if not gis:
        return None
    if not _not_expired(gis.get("ends"), today):
        log.info("offline PolyGIS licence expired; falling back to Basic")
        return None
    plan = (gis.get("plan") or "standard").strip().lower()
    feats = _gis_features(plan, gis.get("features"))
    edition = edition_of(feats, plan)
    limits = dict(gis.get("limits") or {})
    if "seats" not in limits:
        limits["seats"] = PLAN_SEATS.get(edition, DEFAULT_FALLBACK_SEATS)
    return {
        "id": "gis",
        "name": "PolyGIS",
        "plan": plan,
        "plan_name": f"PolyGIS {'Advanced' if edition == 'advanced' else 'Basic'}",
        "status": "active",
        "edition": edition,
        "features": feats,
        "limits": limits,
        "source": "offline-licence",
        "licence_id": payload.get("id"),
        "customer": payload.get("customer"),
    }
