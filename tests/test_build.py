"""steward.build end to end with a scripted provider that plays each pass (no network)."""

from __future__ import annotations

import re

import pytest

from steward.build import SourceDocument, build, overlay_approved, set_override, split_markdown
from steward.build.passes import quote_check
from steward.build.prompts import SYS_B, SYS_B2, SYS_D, SYS_S, SYS_SUP
from steward.knowledge import KnowledgeIndex, MemoryKnowledgeStore
from steward.knowledge.model import SupersedeOverride
from steward.testing import FakeProvider

PRICES = """# Prices
## Bike prices
Trail 500: $1,290
City Cruiser: $690
Kids Bike: $290
Delivery fee: $49 per order.
## Contact
Call us on +1 555 0100. Open Mon-Sat.
"""
MODELS = """# Bikes
## Trail 500
The Trail 500 is a mountain bike. Weight 13.2 kg, 12 gears, sizes S to XL, built for forest trails and rough terrain.
## City Cruiser
The City Cruiser is a commuter bike with mudguards, a rack and lights. Weight 15.8 kg, 7 gears.
"""
APPROVED = """# Approved
## Approved prices 2026
Trail 500: $1,190
City Cruiser: $690
"""


def scripted() -> FakeProvider:
    fake = FakeProvider()

    def chat(msgs):
        system, user = msgs[0]["content"], msgs[-1]["content"]
        if system.startswith(SYS_S[:40]):
            return {"summary": "A fictional bicycle shop", "entity_types": [{"type": "bike", "description": "bike models"}],
                    "entities": [{"id": "bike:trail_500", "type": "bike", "title": "Trail 500", "aliases": ["trail"]},
                                 {"id": "bike:city_cruiser", "type": "bike", "title": "City Cruiser", "aliases": ["cruiser"]},
                                 {"id": "fee:delivery", "type": "fee", "title": "Delivery fee", "aliases": []}],
                    "attributes": [{"name": "price", "entity_type": "bike", "unit": "USD", "description": "price"},
                                   {"name": "amount", "entity_type": "fee", "unit": "USD", "description": "fee"}]}
        if system.startswith(SYS_D[:40]):
            return {"calculators": [{"id": "order_total", "title": "Order total", "description": "N bikes plus delivery",
                                     "entity_type": "bike", "expression": "price * quantity + fee",
                                     "inputs": [{"var": "price", "attribute": "price", "entity": "$entity", "fallback_entity": None},
                                                {"var": "fee", "attribute": "amount", "entity": "fee:delivery", "fallback_entity": None}],
                                     "user_inputs": [{"var": "quantity", "description": "bikes", "unit": None, "ask": "How many?",
                                                      "default": None, "assumption": None}],
                                     "example": {"entity": "bike:city_cruiser", "user_values": [{"var": "quantity", "value": 2}],
                                                 "expected": 1429}},
                                    {"id": "broken", "title": "Broken", "description": "x", "entity_type": "bike",
                                     "expression": "__import__('os')", "inputs": [], "user_inputs": [],
                                     "example": {"entity": "bike:trail_500", "user_values": [], "expected": 0}}]}
        if system.startswith(SYS_SUP[:40]):
            return {"outdated": [{"source_line": "Trail 500: $1,290", "approved_line": "Trail 500: $1,190"},
                                 {"source_line": "Open Mon-Sat.", "approved_line": "City Cruiser: $690"}],  # bogus pair
                    "full": False, "why": "the approved price list changed the Trail 500 price"}
        if system.startswith(SYS_B[:40]):
            return ("DOMAIN: Bikes | models and prices\nTOPIC: Models | specs of each bike\nTOPIC: Prices | bike prices and fees\n"
                    "DOMAIN: Shop | contact\nTOPIC: Contact | phone and hours")
        if system.startswith(SYS_B2[:40]):
            lines = []
            for sid, title in re.findall(r"^([\w.#:-]+) \| [^|]*\| ([^|]*)\|", user, re.M):
                topic = "T1.2" if "price" in title.lower() else "T2.1" if "contact" in title.lower() else "T1.1"
                lines.append(f"{sid} -> {topic}")
            return "\n".join(lines)
        if "SECTION" in user:  # Pass U
            units, values = [], []
            for sid, head, body in re.findall(r"=== SECTION (\S+) \(([^)]*)\)\n(.*?)(?=\n\n=== SECTION|\Z)", user, re.S):
                units.append({"id": sid, "title": head.split(" > ")[-1], "summary": f"About {head}", "keywords": [],
                              "questions": [f"What about {head.split(' > ')[-1]}?"]})
                for name, ent in (("Trail 500", "bike:trail_500"), ("City Cruiser", "bike:city_cruiser"), ("Kids Bike", "bike:kids")):
                    m = re.search(rf"{name}: \$([\d,]+)", body)
                    if m:
                        values.append({"unit_id": sid, "entity": ent, "attribute": "price", "value": f"${m.group(1)}",
                                       "num": float(m.group(1).replace(",", "")), "unit": "USD", "conditions": [],
                                       "source_quote": m.group(0)})
                m = re.search(r"Delivery fee: \$(\d+) per order", body)
                if m:
                    values.append({"unit_id": sid, "entity": "fee:delivery", "attribute": "amount", "value": "$49", "num": 49,
                                   "unit": "USD", "conditions": [], "source_quote": m.group(0)})
                values.append({"unit_id": sid, "entity": "bike:x", "attribute": "price", "value": "$5", "num": 5, "unit": None,
                               "conditions": [], "source_quote": "not in the text $5"})  # must be dropped by verification
            return {"units": units, "values": values}
        return None

    fake.on_chat(chat)
    fake.on_decide("w*", {"noul": 0.9})  # step 1: candidate pairs
    fake.on_decide("l0", {"noul": 0.8}).on_decide("l1", {"noul": 0.1})  # step 3: the real pair passes, the bogus one does not
    return fake


def docs() -> list[SourceDocument]:
    return [SourceDocument.from_markdown(PRICES, doc_id="prices", url="https://shop.example/prices", id_prefix="P01"),
            SourceDocument.from_markdown(MODELS, doc_id="models", url="https://shop.example/bikes", id_prefix="P02")]


def test_split_markdown_merges_and_folds():
    secs = split_markdown(["## A", "short", "## B", "x" * 400, "### B1", "y" * 300], "Doc")
    assert secs[0][1].startswith("## A\nshort\n## B")  # the tiny leading fragment folds into the next section
    d = SourceDocument.from_markdown("<!-- hidden -->\n# Title\n## One\n" + "text " * 60, doc_id="d")
    assert d.title == "Title" and d.sections[0].id == "d_00" and "hidden" not in d.sections[0].text
    assert quote_check("Trail 500: $1,290", "x Trail  500: $1,290 y") == 1.0


async def test_full_build(tmp_path):
    fake = scripted()
    store = MemoryKnowledgeStore()
    report = await build(docs(), fake, writer=store, description="a bicycle shop", cache_dir=tmp_path / "cache")
    snap = report.snapshot
    assert await store.version() == snap.meta.version != "pending"
    assert {n.id for n in snap.nodes if n.kind == "topic"} == {"T1.1", "T1.2", "T2.1"}
    sec = {s.id: s for s in snap.sections}
    assert sec["P01_00"].topic_id == "T1.2" and sec["P02_00"].topic_id == "T1.1" and sec["P01_00"].questions
    assert {(v.entity, v.num) for v in snap.values} == {("bike:trail_500", 1290), ("bike:city_cruiser", 690), ("bike:kids", 290),
                                                         ("fee:delivery", 49)}
    assert report.dropped and all("bike:x" not in v.entity for v in snap.values)
    calcs = {c.id: c for c in snap.calculators}
    assert calcs["order_total"].validated
    assert set(calcs["order_total"].applies_to) == {"bike:trail_500", "bike:city_cruiser", "bike:kids"}
    assert not calcs["broken"].validated
    # re-running hits the cache: no new model calls
    calls = len(fake.calls)
    again = await build(docs(), fake, description="a bicycle shop", cache_dir=tmp_path / "cache")
    assert len(fake.calls) == calls and again.cache_hits > 0 and again.snapshot.meta.version == snap.meta.version


async def test_overlay_approved_supersedes_and_overrides(tmp_path):
    fake = scripted()
    store = MemoryKnowledgeStore()
    await build(docs(), fake, writer=store, description="a bicycle shop")
    approved = SourceDocument.from_markdown(APPROVED, doc_id="approved", kind="approved", id_prefix="A01")
    report = await overlay_approved(store, [approved], fake)
    snap = await store.load()
    idx = KnowledgeIndex(snap)
    assert idx.sections["A01_00"].authority == "approved" and idx.sections["A01_00"].topic_id == "T1.2"
    assert idx.value("bike:trail_500", "price").num == 1190  # approved outranks the source price
    assert "Trail 500: $1,290" in idx.outdated["P01_00"]  # the evidence + per-line check kept the real conflict
    assert "Open Mon-Sat." not in idx.outdated.get("P01_00", set())  # the bogus pair was rejected
    assert report.notes and report.snapshot.meta.version == snap.meta.version
    # admin rejects the mark -> the line is shown again; clearing restores it
    v1 = await set_override(store, SupersedeOverride("P01_00", "Trail 500: $1,290", "reject"))
    assert "Trail 500: $1,290" not in KnowledgeIndex(await store.load()).outdated.get("P01_00", set())
    v2 = await set_override(store, clear=("P01_00", "Trail 500: $1,290"))
    assert v1 != v2 and "Trail 500: $1,290" in KnowledgeIndex(await store.load()).outdated["P01_00"]
    # re-running the overlay keeps admin overrides
    await set_override(store, SupersedeOverride("P01_00", "Delivery fee: $49 per order.", "add", "A01_00"))
    await overlay_approved(store, [approved], fake)
    assert "Delivery fee: $49 per order." in KnowledgeIndex(await store.load()).outdated["P01_00"]


async def test_overlay_rejects_clashing_ids():
    fake = scripted()
    store = MemoryKnowledgeStore()
    await build(docs(), fake, writer=store)
    clash = SourceDocument.from_markdown(APPROVED, doc_id="x", kind="approved", id_prefix="P01")
    with pytest.raises(ValueError, match="clash"):
        await overlay_approved(store, [clash], fake)


async def test_knowledge_admin_endpoints():
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from steward import Steward
    from steward.build import KnowledgeAdmin
    from steward.server import create_router
    from steward.testing import memory_stores, northwind_config

    fake = scripted()
    store = MemoryKnowledgeStore()
    await build(docs(), fake, writer=store)
    bot = Steward(northwind_config(), provider=fake, stores=memory_stores(), knowledge=store)
    app = FastAPI()
    app.include_router(create_router(bot, insecure_local=True, knowledge_admin=KnowledgeAdmin(bot)), prefix="/s")
    c = TestClient(app)
    assert c.get("/s/catalog").json()["knowledge_admin"] is True
    assert c.put("/s/knowledge/approved", json={"text": APPROVED}).json() == {"ok": True}
    ov = c.get("/s/knowledge/approved").json()
    assert ov["text"] == APPROVED and ov["sources"] and ov["build"]["running"] is False
    sec = c.get("/s/knowledge/section/P01_00").json()
    assert sec["lines"] and c.get("/s/knowledge/section/nope").status_code == 404
    assert c.post("/s/knowledge/mark", json={"unit": "P01_00", "line": "x", "action": "add", "by": "P02_00"}).status_code == 400
