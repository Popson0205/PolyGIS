"""Tests run against a real PostgreSQL with PostGIS (a gis_test database), and a stand-in Geohub that signs real
GeoEstate ID tokens with its own Ed25519 key."""
import base64
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

env_file = Path(__file__).resolve().parent.parent / ".env.test"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from fastapi.testclient import TestClient  # noqa: E402

from app import identity  # noqa: E402
from app.config import settings  # noqa: E402
from app.geoestate_id import GeoEstateID  # noqa: E402
from app.main import app  # noqa: E402

KEY = Ed25519PrivateKey.generate()
KID = "test-key"


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class FakeGeohub:
    """Answers /api/id/*: the key, codes and refresh tokens it issued, and the apps list."""
    def __init__(self):
        self.codes: dict[str, dict] = {}
        self.refresh: dict[str, dict] = {}
        self.calls: list[str] = []
        self.no_product_on_refresh = False
        self.datasets: dict[str, dict] = {}
        self.products: set[str] = set()                   # other apps the organization has (for linked layers)
        self.hub_projects: list[dict] = []
        self.hub_features: dict[str, list[dict]] = {}

    def claims(self, role="owner", org="Abuja Survey Ltd", org_id=None, status="active", features=None, limits=None, staff=None,
               name="Ada Mapper"):
        return {"sub": str(uuid.uuid4()), "email": f"{uuid.uuid4().hex[:6]}@survey.example", "name": name, "mfa": True,
                "org": {"id": org_id or ORG, "name": org, "slug": "abuja-survey", "role": role},
                "product": {"id": "gis", "name": "PolyGIS", "plan": "standard", "plan_name": "PolyGIS Standard",
                            "licence_kind": "paid", "status": status, "status_name": status.title(), "subscription_ends": "2027-11-04",
                            "grace_ends": "2027-11-18", "features": features if features is not None else ["maps", "editing"],
                            "limits": limits or {}},
                "staff_role": staff}

    def token(self, claims: dict, minutes: int = 15) -> str:
        now = int(time.time())
        return jwt.encode({**claims, "aud": "gis", "iss": settings.geohub_url, "typ": "geoestate-id", "iat": now,
                           "exp": now + minutes * 60}, KEY, algorithm="EdDSA", headers={"kid": KID})

    def tokens(self, claims: dict) -> dict:
        rt = uuid.uuid4().hex
        self.refresh[rt] = claims
        return {"access_token": self.token(claims), "refresh_token": rt, "expires_in": 900, "token_type": "Bearer", "user": claims}

    def handle(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/api")
        self.calls.append(path)
        if path == "/id/jwks":
            raw = KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            return httpx.Response(200, json={"keys": [{"kty": "OKP", "crv": "Ed25519", "x": b64(raw), "kid": KID, "alg": "EdDSA"}]})
        if path == "/id/token":
            f = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            if f.get("client_secret") != settings.id_secret:
                return httpx.Response(401, json={"detail": "The app's secret is not right.", "code": "invalid_client"})
            if f["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange":
                who = jwt.decode(f["subject_token"], options={"verify_signature": False})
                if f["audience"] not in self.products:
                    return httpx.Response(403, json={"detail": f"{who['org']['name']} does not have that app.", "code": "no_product"})
                return httpx.Response(200, json={"access_token": f"{f['audience']}-token-for-{who['sub']}", "token_type": "Bearer", "expires_in": 900})
            if f["grant_type"] == "authorization_code":
                c = self.codes.pop(f["code"], None)
                if not c:
                    return httpx.Response(400, json={"detail": "That code is not valid.", "code": "invalid_grant"})
                return httpx.Response(200, json=self.tokens(c))
            c = self.refresh.pop(f["refresh_token"], None)
            if not c:
                return httpx.Response(400, json={"detail": "That refresh token was already used. Sign in again.", "code": "invalid_grant"})
            if self.no_product_on_refresh:
                return httpx.Response(403, json={"detail": "Abuja Survey Ltd does not have PolyGIS.", "code": "no_product"})
            return httpx.Response(200, json=self.tokens(c))
        if path == "/id/revoke":
            return httpx.Response(200, json={"revoked": True})
        if path.startswith("/id/data/datasets"):
            return self.hub(req, path)
        if path == "/id/apps":
            return httpx.Response(200, json=[{"product": "hub", "name": "Geohub", "status": "active", "url": settings.geohub_url, "current": False},
                                             {"product": "gis", "name": "PolyGIS", "status": "active", "url": settings.app_url, "current": True},
                                             *[{"product": p, "name": n, "status": "active", "url": f"http://{p}.test", "current": False}
                                               for p, n in (("land", "Land Registry"), ("farm", "FarmScan")) if p in self.products]])
        if path == "/id/data/projects":
            return httpx.Response(200, json=self.hub_projects)
        if path.startswith("/id/data/forms/"):
            feats = self.hub_features.get(path.split("/")[4], [])
            after = int(req.url.params.get("after") or 0)
            if req.url.params.get("status"):
                feats = [x for x in feats if x["status"] == req.url.params["status"]]
            page = feats[after:after + int(req.url.params.get("limit") or 1000)]
            nxt = after + len(page)
            return httpx.Response(200, json={"features": page, "next": str(nxt) if nxt < len(feats) else None})
        return httpx.Response(404, json={"detail": "Not found."})


    def hub(self, req: httpx.Request, path: str) -> httpx.Response:
        """A stand-in Data hub: datasets published from PolyGIS, as Geohub's /id/data/datasets keeps them."""
        who = jwt.decode(req.headers["authorization"][7:], options={"verify_signature": False})
        if req.method == "GET":
            ref = req.url.params.get("ref")
            return httpx.Response(200, json=[d for d in self.datasets.values() if d["by"] == who["sub"] and (not ref or d["source"]["ref"] == ref)])
        body = json.loads(req.content)
        if path == "/id/data/datasets":
            if body["visibility"] == "public" and body["license"] == "Private":
                return httpx.Response(422, json={"detail": "A Private licence cannot be published publicly.", "code": "validation"})
            slug = f"{body['title'].lower().replace(' ', '-')}-{len(self.datasets) + 1}"
            self.datasets[slug] = {"slug": slug, "title": body["title"], "visibility": body["visibility"], "latest_version": 1, "by": who["sub"],
                                   "source": {"app": who["aud"], "name": "PolyGIS", "ref": body["ref"], "label": body["label"]},
                                   "feature_count": len(body["features"]), "body": body}
            return httpx.Response(201, json={"dataset": self.datasets[slug], "version": 1, "feature_count": len(body["features"])})
        slug = path.split("/")[4]
        d = self.datasets.get(slug)
        if not d or d["by"] != who["sub"]:
            return httpx.Response(404, json={"detail": "Dataset not found.", "code": "not_found"})
        d["latest_version"] += 1; d["feature_count"] = len(body["features"]); d["body"] = body
        return httpx.Response(201, json={"dataset": d, "version": d["latest_version"], "feature_count": len(body["features"])})


ORG = str(uuid.uuid4())
HUB = FakeGeohub()


@pytest.fixture(scope="session")
def client():
    identity.gid = GeoEstateID(geohub=settings.geohub_url, api=settings.geohub_api, client_id="gis", client_secret=settings.id_secret,
                               transport=httpx.MockTransport(HUB.handle))
    import psycopg
    with psycopg.connect(settings.database_url, autocommit=True) as c:
        c.execute(f'DROP SCHEMA IF EXISTS "{settings.db_schema}" CASCADE')
    with TestClient(app) as c:
        yield c


class Person:
    """Someone signed in to PolyGIS (their tokens in the cookies), sending changes from the app's own pages."""
    def __init__(self, client, **claims):
        self.c = client
        self.claims = HUB.claims(**claims)
        t = HUB.tokens(self.claims)
        self.cookies = {identity.AT: t["access_token"], identity.RT: t["refresh_token"]}

    def _go(self, method, url, **kw):
        self.c.cookies.clear()
        for k, v in self.cookies.items():
            self.c.cookies.set(k, v)
        r = self.c.request(method, url, headers={"X-GeoEstate": "1", **kw.pop("headers", {})}, **kw)
        for k in (identity.AT, identity.RT):                 # keep refreshed tokens, as a browser would
            if k in r.cookies:
                self.cookies[k] = r.cookies[k]
        return r

    def get(self, url, **kw): return self._go("GET", url, **kw)
    def post(self, url, **kw): return self._go("POST", url, **kw)
    def put(self, url, **kw): return self._go("PUT", url, **kw)
    def patch(self, url, **kw): return self._go("PATCH", url, **kw)
    def delete(self, url, **kw): return self._go("DELETE", url, **kw)


@pytest.fixture
def person(client):
    def make(**claims):
        return Person(client, **claims)
    return make
