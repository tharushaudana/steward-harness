from __future__ import annotations

import json
from pathlib import Path

from steward.knowledge import Bm25Index, KnowledgeIndex, KnowledgeSnapshot, MemoryKnowledgeStore, script_ratio
from steward.knowledge.search import stem
from steward.testing import tiny_kb

ROOT = Path(__file__).resolve().parents[1]


def test_example_kb_file_matches_fixture():
    on_disk = json.loads((ROOT / "examples" / "northwind" / "kb.json").read_text(encoding="utf-8"))
    assert KnowledgeSnapshot.from_dict(on_disk) == tiny_kb()


def test_snapshot_round_trip():
    s = tiny_kb()
    assert KnowledgeSnapshot.from_dict(json.loads(json.dumps(s.to_dict()))) == s


def test_supersede_marks_remove_outdated_lines_and_link_approved():
    idx = KnowledgeIndex(tiny_kb())
    assert "Volt e-bike: $2,490" not in idx.text("S4") and "Trail 500: $1,290" in idx.text("S4")
    assert idx.superseded_by["S4"] == ["S9"] and idx.with_authority(["S4"]) == ["S4", "S9"]
    assert not idx.hidden


def test_value_prefers_approved_sources():
    idx = KnowledgeIndex(tiny_kb())
    assert idx.value("bike:volt", "price").num == 2290
    assert idx.value("fee:delivery", "amount").num == 49
    assert idx.value("bike:volt", "weight") is None


def test_entity_sections_and_coverage():
    idx = KnowledgeIndex(tiny_kb())
    assert idx.entity_sections("bike:city_cruiser")[0] == "S9"  # approved first
    assert idx.covers("S2", "bike:city_cruiser")  # named in the text
    assert not idx.covers("S1", "bike:city_cruiser")
    assert "Trail 500" in idx.entity_names(["bike"])


def test_topics_and_calculators():
    idx = KnowledgeIndex(tiny_kb())
    assert [t.id for t in idx.topics] == ["T1.1", "T1.2", "T2.1", "T2.2"] and idx.topics[0].domain_title == "Bikes"
    assert list(idx.calcs) == ["order_total"] and "bike:volt" in idx.calc_entities


async def test_bm25_search():
    idx = KnowledgeIndex(tiny_kb())
    hits = await idx.search("how much does the volt e-bike cost", "section", 3)
    assert {h.id for h in hits} & {"S3", "S4", "S9"}
    assert (await idx.search("warranty on frames", "section", 1))[0].id == "S7"
    assert await idx.search("the of and", "section") == []
    assert stem("pricing") == stem("prices") or stem("prices") == "pric"
    b = Bm25Index()
    b.add("section", "x", "Ünïcode títle", "körper text")
    assert b.search("körper")[0].id == "x"


async def test_hidden_sections_are_never_returned():
    from dataclasses import replace

    from steward.knowledge.model import SupersedeMark

    snap = tiny_kb()
    sections = tuple(replace(s, marks=(SupersedeMark("S9", ("x",), full=True),)) if s.id == "S4" else s for s in snap.sections)
    idx = KnowledgeIndex(replace(snap, sections=sections))
    assert "S4" in idx.hidden and all(h.id != "S4" for h in await idx.search("price list", "section", 10))
    assert idx.value("bike:volt", "price").section_id == "S9"


async def test_memory_store_writer():
    store = MemoryKnowledgeStore(tiny_kb())
    assert await store.version() == "nw-1"
    from steward.knowledge.model import SupersedeMark, SupersedeOverride

    await store.update_marks("S5", (SupersedeMark("S9", ("Basic tune-up: $59.",)),), "nw-2")
    snap = await store.load()
    assert snap.meta.version == "nw-2" and next(s for s in snap.sections if s.id == "S5").marks
    await store.put_override(SupersedeOverride("S5", "x", "reject"))
    assert len(await store.list_overrides()) == 1
    await store.delete_override("S5", "x")
    assert await store.list_overrides() == []


def test_script_ratio():
    assert script_ratio("hello") == 1.0 and script_ratio("مرحبا") == 0.0 and script_ratio("123") == 1.0
