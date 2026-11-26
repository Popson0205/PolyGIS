"""Edition resolution and the offline GEOESTATE-LICENCE-1 path. Pure functions — no DB needed."""
import base64
import json
from datetime import date, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import licensing

FORMAT = "GEOESTATE-LICENCE-1"


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _sign(priv: Ed25519PrivateKey, payload: dict) -> str:
    body = _b64u(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode())
    return f"{FORMAT}.{body}.{_b64u(priv.sign(f'{FORMAT}.{body}'.encode()))}"


def _pub_b64(priv: Ed25519PrivateKey) -> str:
    raw = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return _b64u(raw)


def _install(monkeypatch, priv):
    monkeypatch.setenv("GEOESTATE_LICENCE_PUBLIC_KEY", _pub_b64(priv))
    licensing._trusted_keys.cache_clear()


def _key(priv, *, plan="advanced", seats=None, features=None, ends=None, customer="Osun OSG"):
    gis = {"product": "gis", "plan": plan, "kind": "paid",
           "limits": ({"seats": seats} if seats is not None else {}),
           "features": features or {}, "ends": ends}
    payload = {"format": 1, "id": "KEY-1", "customer": customer,
               "installation": "https://geohub.osun.gov.ng",
               "products": [{"product": "hub", "plan": "standard"}, gis]}
    return _sign(priv, payload)


# ---- edition resolution ------------------------------------------------------------
def test_edition_from_explicit_plan():
    assert licensing.edition_of([], "advanced") == "advanced"
    assert licensing.edition_of([], "basic") == "basic"


def test_edition_derived_from_features():
    assert licensing.edition_of(["maps", "poly_earth", "defence"]) == "advanced"
    assert licensing.edition_of(["maps", "poly_earth"]) == "basic"   # needs BOTH advanced features
    assert licensing.edition_of(["maps", "editing"]) == "basic"


def test_features_for_edition_superset():
    basic, adv = licensing.features_for("basic"), licensing.features_for("advanced")
    assert "poly_earth" not in basic and "defence" not in basic
    assert set(basic) <= set(adv) and {"poly_earth", "defence"} <= set(adv)


def test_gis_features_maps_plan_and_overrides():
    assert "defence" not in licensing._gis_features("standard", None)
    assert {"poly_earth", "defence"} <= set(licensing._gis_features("advanced", None))
    # a per-licence override can switch one on without the Advanced plan
    assert "poly_earth" in licensing._gis_features("standard", {"poly_earth": True})


# ---- offline GEOESTATE-LICENCE-1 ---------------------------------------------------
def test_offline_advanced_is_canonical(monkeypatch):
    priv = Ed25519PrivateKey.generate(); _install(monkeypatch, priv)
    monkeypatch.setenv("POLYGIS_LICENCE", _key(priv, plan="advanced", seats=25))
    ent = licensing.offline_entitlement()
    assert ent and ent["edition"] == "advanced" and ent["limits"]["seats"] == 25
    assert {"poly_earth", "defence"} <= set(ent["features"]) and ent["customer"] == "Osun OSG"


def test_offline_basic_locks_advanced(monkeypatch):
    priv = Ed25519PrivateKey.generate(); _install(monkeypatch, priv)
    monkeypatch.setenv("POLYGIS_LICENCE", _key(priv, plan="standard"))
    ent = licensing.offline_entitlement()
    assert ent["edition"] == "basic" and "defence" not in ent["features"] and ent["limits"]["seats"] == 5


def test_expired_licence_ignored(monkeypatch):
    priv = Ed25519PrivateKey.generate(); _install(monkeypatch, priv)
    past = (date.today() - timedelta(days=1)).isoformat()
    monkeypatch.setenv("POLYGIS_LICENCE", _key(priv, plan="advanced", ends=past))
    assert licensing.offline_entitlement() is None     # -> caller falls back to Basic


def test_tampered_licence_ignored(monkeypatch):
    priv = Ed25519PrivateKey.generate(); _install(monkeypatch, priv)
    tok = _key(priv, plan="advanced")
    a, b, c = tok.split(".")
    monkeypatch.setenv("POLYGIS_LICENCE", f"{a}.{b[:-2]}{'AA' if b[-2:] != 'AA' else 'BB'}.{c}")
    assert licensing.offline_entitlement() is None


def test_wrong_key_ignored(monkeypatch):
    priv = Ed25519PrivateKey.generate(); _install(monkeypatch, priv)
    other = Ed25519PrivateKey.generate()
    monkeypatch.setenv("POLYGIS_LICENCE", _key(other, plan="advanced"))
    assert licensing.offline_entitlement() is None


def test_no_public_key_means_no_offline(monkeypatch):
    priv = Ed25519PrivateKey.generate()
    monkeypatch.delenv("GEOESTATE_LICENCE_PUBLIC_KEY", raising=False)
    licensing._trusted_keys.cache_clear()
    monkeypatch.setenv("POLYGIS_LICENCE", _key(priv, plan="advanced"))
    assert licensing.offline_entitlement() is None


def test_key_without_polygis_is_ignored(monkeypatch):
    priv = Ed25519PrivateKey.generate(); _install(monkeypatch, priv)
    payload = {"format": 1, "id": "K", "customer": "X", "installation": "https://h.example",
               "products": [{"product": "hub", "plan": "standard"}, {"product": "land", "plan": "advanced"}]}
    monkeypatch.setenv("POLYGIS_LICENCE", _sign(priv, payload))
    assert licensing.offline_entitlement() is None


def test_absent_licence_is_none(monkeypatch):
    priv = Ed25519PrivateKey.generate(); _install(monkeypatch, priv)
    monkeypatch.delenv("POLYGIS_LICENCE", raising=False)
    monkeypatch.setenv("POLYGIS_LICENCE_FILE", "/nonexistent/path.lic")
    assert licensing.offline_entitlement() is None
