"""Edition resolution and the offline signed-licence path. Pure functions — no DB needed."""
import base64
import json
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import licensing


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _make_token(priv, payload, typ="geolicense+jws", kid="test"):
    header = {"alg": "EdDSA", "typ": typ, "kid": kid}
    hb = _b64u(json.dumps(header, sort_keys=True, separators=(",", ":")).encode())
    pb = _b64u(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    sig = priv.sign(f"{hb}.{pb}".encode("ascii"))
    return f"{hb}.{pb}.{_b64u(sig)}"


def _install_key(monkeypatch):
    priv = Ed25519PrivateKey.generate()
    pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    monkeypatch.setattr(licensing, "GEOESTATE_LICENSE_PUBKEY_PEM", pem)
    licensing._public_key.cache_clear()
    return priv


def _lic_payload(edition="advanced", seats=25, **kw):
    now = int(time.time())
    return {"v": 1, "typ": "license", "product": "polygis", "edition": edition,
            "max_users": seats, "iat": now, "nbf": now, "exp": None,
            "lic_id": "GE-LIC-TEST", "org": {"id": "o1", "name": "Org"}, **kw}


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
    assert set(basic) <= set(adv)
    assert {"poly_earth", "defence"} <= set(adv)


# ---- offline licence ---------------------------------------------------------------
def test_offline_advanced_licence_is_canonical(monkeypatch):
    priv = _install_key(monkeypatch)
    monkeypatch.setenv("POLYGIS_LICENSE", _make_token(priv, _lic_payload("advanced", 25)))
    ent = licensing.offline_entitlement()
    assert ent and ent["edition"] == "advanced" and ent["limits"]["users"] == 25
    assert "defence" in ent["features"] and "poly_earth" in ent["features"]


def test_offline_basic_licence_locks_advanced(monkeypatch):
    priv = _install_key(monkeypatch)
    monkeypatch.setenv("POLYGIS_LICENSE", _make_token(priv, _lic_payload("basic", 5)))
    ent = licensing.offline_entitlement()
    assert ent["edition"] == "basic" and "defence" not in ent["features"]


def test_expired_offline_licence_ignored(monkeypatch):
    priv = _install_key(monkeypatch)
    past = int(time.time()) - 10 * 86400
    payload = _lic_payload("advanced", 25)
    payload.update(iat=past - 86400, nbf=past - 86400, exp=past)
    monkeypatch.setenv("POLYGIS_LICENSE", _make_token(priv, payload))
    assert licensing.offline_entitlement() is None     # -> caller falls back to Basic


def test_tampered_offline_licence_ignored(monkeypatch):
    priv = _install_key(monkeypatch)
    tok = _make_token(priv, _lic_payload("advanced", 25))
    h, p, s = tok.split(".")
    tampered = f"{h}.{p[:-2]}{'AA' if p[-2:] != 'AA' else 'BB'}.{s}"
    monkeypatch.setenv("POLYGIS_LICENSE", tampered)
    assert licensing.offline_entitlement() is None


def test_licence_from_wrong_key_ignored(monkeypatch):
    _install_key(monkeypatch)
    other = Ed25519PrivateKey.generate()
    monkeypatch.setenv("POLYGIS_LICENSE", _make_token(other, _lic_payload("advanced", 25)))
    assert licensing.offline_entitlement() is None


def test_no_key_configured_means_no_offline(monkeypatch):
    monkeypatch.setattr(licensing, "GEOESTATE_LICENSE_PUBKEY_PEM", "")
    licensing._public_key.cache_clear()
    priv = Ed25519PrivateKey.generate()
    monkeypatch.setenv("POLYGIS_LICENSE", _make_token(priv, _lic_payload()))
    assert licensing.offline_entitlement() is None


def test_absent_licence_is_none(monkeypatch):
    _install_key(monkeypatch)
    monkeypatch.delenv("POLYGIS_LICENSE", raising=False)
    monkeypatch.setenv("POLYGIS_LICENSE_FILE", "/nonexistent/path.lic")
    assert licensing.offline_entitlement() is None
