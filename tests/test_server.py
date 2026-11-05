from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from steward import Steward  # noqa: E402
from steward.knowledge import MemoryKnowledgeStore  # noqa: E402
from steward.server import Principal, create_app, create_router  # noqa: E402
from steward.testing import FakeProvider, memory_stores, tiny_kb  # noqa: E402

CONFIG = json.loads((Path(__file__).resolve().parents[1] / "examples" / "northwind" / "northwind.steward.json")
                    .read_text(encoding="utf-8"))


def bot() -> Steward:
    return Steward(copy.deepcopy(CONFIG), provider=FakeProvider(), stores=memory_stores(),
                   knowledge=MemoryKnowledgeStore(tiny_kb()))


def client_for(roles: set[str] | None) -> TestClient:
    def auth(request):
        if roles is None or request.headers.get("x-token") != "secret":
            return None
        return Principal("tester", frozenset(roles))
    app = FastAPI()
    app.include_router(create_router(bot(), auth=auth), prefix="/steward")
    return TestClient(app, headers={"x-token": "secret"})


def test_router_requires_auth_unless_local():
    with pytest.raises(ValueError, match="auth"):
        create_router(bot())
    assert create_router(bot(), insecure_local=True)


def test_roles_are_enforced():
    viewer = client_for({"viewer"})
    assert viewer.get("/steward/config").status_code == 200
    assert viewer.post("/steward/config/publish", json={}).status_code == 403
    assert viewer.post("/steward/chat", json={"text": "hi"}).status_code == 403
    anon = TestClient(viewer.app)
    assert anon.get("/steward/config").status_code == 401


def test_admin_page_and_assets_are_served():
    c = client_for({"viewer"})
    page = c.get("/steward/admin")
    assert page.status_code == 200 and "Steward" in page.text and "admin/vendor/drawflow.min.js" in page.text
    assert c.get("/steward/admin/vendor/drawflow.min.js").status_code == 200
    assert c.get("/steward/admin/vendor/../app.py").status_code == 404


def test_draft_publish_cycle_over_http():
    c = client_for({"viewer", "editor", "publisher"})
    g = c.get("/steward/config", params={"draft": 1}).json()
    assert g["errors"] == [] and g["has_draft"] is False and g["version"] == 1
    cfg = g["harness"]
    cfg["templates"]["welcome"]["text"]["en"] = "Hello (draft)!"
    r = c.put("/steward/config", json={"config": cfg, "base_rev": g["rev"]})
    assert r.status_code == 200
    assert c.put("/steward/config", json={"config": cfg, "base_rev": g["rev"]}).status_code == 409  # stale
    chat = c.post("/steward/chat", json={"text": "hi", "channel": "comment", "config": "draft"}).json()
    assert chat["status"] == "template" and chat["session"].startswith("test_")
    bad = copy.deepcopy(cfg)
    bad["policies"][0]["action"]["template"] = "nope"
    v = c.post("/steward/config/validate", json=bad).json()
    assert any(e.get("policy") == "careers" for e in v["errors"])
    assert c.post("/steward/config/publish", json={"note": "test"}).json()["version"] == 2
    hist = c.get("/steward/config/history").json()["history"]
    assert c.post("/steward/config/restore", json={"id": hist[0]["id"]}).status_code == 200
    assert c.post("/steward/config/discard").status_code == 200


def test_catalog_records_sessions_agents():
    c = client_for({"viewer", "editor"})
    cat = c.get("/steward/catalog").json()
    assert "extract" in cat["cards"] and "invalid" in cat["cards"]["extract"]["ports"]
    assert {t["id"] for t in cat["tools"]} >= {"records.append", "agents.available"} and cat["languages"] == ["en", "es"]
    assert c.post("/steward/runtime/agents", json={"online": True}).json() == {"online": True}
    assert c.get("/steward/runtime/agents").json()["online"] is True
    assert c.get("/steward/records").json() == {"records": []}
    assert c.get("/steward/records.csv").text.startswith("id,ts,type,session,status")
    assert c.post("/steward/sessions/abc/release").json()["state"]["flags"]["handover_epoch"] == 1
    assert c.get("/steward/sessions/abc").json()["flags"]["handover_active"] is False
    assert c.get("/steward/prompt", params={"draft": 1}).json()["core"].startswith("You are the customer assistant")


def test_create_app_with_prefix():
    app = create_app(bot(), insecure_local=True, prefix="/admin-api")
    assert TestClient(app).get("/admin-api/config").status_code == 200
