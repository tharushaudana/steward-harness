"""The DADR engine end to end on the fictional knowledge base, with scripted decisions (no network)."""

from __future__ import annotations

import pytest

from steward.engine import Engine, EngineSettings
from steward.engine.calc import calc_lines, safe_eval
from steward.engine.generate import number_check
from steward.engine.preprocess import parse_condense
from steward.knowledge import MemoryKnowledgeStore
from steward.stores.memory import MemoryPlanCache
from steward.testing import FakeProvider, tiny_kb


def contains(*needles, yes=0.9, no=0.05):
    """A scripted decision: high probability when the question text mentions any needle."""
    def answer(state, q):
        return {"noul": yes if any(n.lower() in q["instructions"].lower() for n in needles) else no}
    return answer


def provider_for(topic: str, section_title: str, entity: str | None = None) -> FakeProvider:
    fake = FakeProvider()
    fake.on_decide("t*", contains(topic)).on_decide("u*", contains(section_title)).on_decide("sufficient", {"noul": 0.9})
    fake.on_decide("e*", contains(f"'{entity}'") if entity else {"noul": 0.0})
    fake.on_decide("c*", {"noul": 0.0})
    return fake


async def test_answer_from_approved_section():
    fake = provider_for("Prices", "Approved price list", "Volt")
    fake.on_chat("SECTIONS", "The Volt e-bike costs $2,290 [S9].")
    cache = MemoryPlanCache()
    engine = Engine(MemoryKnowledgeStore(tiny_kb()), fake, plan_cache=cache)
    r = await engine.answer("How much is the Volt e-bike these days?")
    assert r.status == "answered" and "S9" in r.units and "$2,290" in r.text
    steps = [s.name for s in r.trace.steps]
    assert {"shortlist", "route", "gate", "answer", "answer_check"} <= set(steps)
    answer_prompt = next(c for c in fake.calls if c.kind == "chat").payload[1]["content"]
    assert "APPROVED" in answer_prompt and "$2,490" not in answer_prompt  # the superseded line never reaches the model
    r2 = await engine.answer("How much is the Volt e-bike these days?")  # confident plan -> cached
    assert any(s.name == "plan_cache" for s in r2.trace.steps)


async def test_gate_failure_uses_about_fallback():
    fake = FakeProvider().on_decide("t*", contains("Orders and returns")).on_decide("sufficient", {"noul": 0.1})
    fake.on_decide("u*", {"noul": 0.1}).on_decide("u*a", contains("Returns and warranty", yes=0.8, no=0.1))  # newest rule wins
    fake.on_chat("SECTIONS", "Returns are possible within 30 days [S7].")
    r = await Engine(MemoryKnowledgeStore(tiny_kb()), fake).answer("can I send back 14 bikes after 2 months of use?")
    assert "S7" in r.units and any(s.name == "about_fallback" for s in r.trace.steps)


async def test_entity_coverage_adds_missing_entity_evidence():
    fake = provider_for("Models", "Trail 500 mountain bike")
    fake.on_decide("e*", contains("'Trail 500'", "'City Cruiser'"))
    fake.on_chat("SECTIONS", "The Trail 500 weighs 13.2 kg and the City Cruiser weighs 15.8 kg.")
    r = await Engine(MemoryKnowledgeStore(tiny_kb()), fake).answer("Which is lighter, the Trail 500 or the City Cruiser?")
    cov = next(s for s in r.trace.steps if s.name == "coverage")
    assert len(cov.output) == 1 and r.units[0] == "S1"


async def test_calculator_binds_numbers_and_uses_approved_price():
    fake = FakeProvider().on_decide("c*", {"noul": 0.9}).on_decide("e*", contains("'Volt'"))
    fake.on_decide("n*", {"type": "choice", "choice": "quantity", "confidence": 0.9})
    r = await Engine(MemoryKnowledgeStore(tiny_kb()), fake).answer("What is the total for 3 Volt bikes with delivery?")
    assert r.results and r.results[0]["value"] == 2290 * 3 + 49
    assert r.text.startswith("Order total for Volt: 6919")  # single calculation: template answer, no LLM
    assert not [c for c in fake.calls if c.kind == "chat"]


async def test_partial_calculation_asks_for_the_missing_input():
    fake = FakeProvider().on_decide("c*", {"noul": 0.9}).on_decide("e*", contains("'Trail 500'"))
    fake.on_chat("SECTIONS", "The total is $1,290 per bike plus $49 delivery. How many bikes would you like?")
    r = await Engine(MemoryKnowledgeStore(tiny_kb()), fake).answer("What would an order of Trail 500 bikes cost me?")
    assert r.status == "needs_input" and r.results[0]["status"] == "PARTIAL"
    assert "quantity" in calc_lines(r.results)[0]


async def test_unsupported_numbers_are_rewritten_or_withheld():
    fake = provider_for("Workshop", "Workshop services")
    fake.on_chat("SECTIONS", "A full service costs $99 [S5].")  # wrong number
    fake.on_chat("Rewrite the answer", "A full service costs $149 [S5].")  # still wrong
    r = await Engine(MemoryKnowledgeStore(tiny_kb()), fake).answer("What does a full service in the workshop cost?")
    assert "$99" not in r.text and "$149" not in r.text and "Workshop services" in r.text  # code fallback listing


async def test_chitchat_and_insufficient():
    fake = FakeProvider().on_decide("kind", {"type": "choice", "choice": "chitchat", "confidence": 0.9})
    r = await Engine(MemoryKnowledgeStore(tiny_kb()), fake).answer("hi there", profile={"greeting": "Hello from Northwind!"})
    assert r.status == "chitchat" and r.text == "Hello from Northwind!"
    fake2 = FakeProvider()  # nothing relevant anywhere
    r2 = await Engine(MemoryKnowledgeStore(tiny_kb()), fake2).answer("do you sell kayaks or canoes?",
                                                                       profile={"escalation": "Call +1 555 0100."})
    assert r2.status == "insufficient" and "Call +1 555 0100." in r2.text


async def test_multi_part_message_routes_parts_in_parallel():
    fake = FakeProvider().on_chat_json("condense", {
        "standalone": "How much is the Volt and when are you open?", "search": "How much is the Volt and when are you open?",
        "parts": ["How much is the Volt?", "When are you open?"]})
    fake.on_decide("t*", contains("Prices", "Orders and returns"))
    fake.on_decide("u*", contains("Approved price list", "Contact"))
    fake.on_decide("sufficient", {"noul": 0.9})
    fake.on_chat("SECTIONS", "The Volt costs $2,290 [S9]. We are open Mon-Sat 9:00-18:00 [S8].")
    r = await Engine(MemoryKnowledgeStore(tiny_kb()), fake).answer("How much is the Volt, and when are you open?")
    assert {"S9", "S8"} <= set(r.units) and any(s.name == "merge_parts" for s in r.trace.steps)
    assert any(s.name == "route[2]" for s in r.trace.steps)


def test_number_check_rules():
    allowed = {1290.0, 49.0}
    assert number_check("It costs $1,290 plus $49 [S4].", allowed) == []
    assert number_check("$1,290 + $49 = $1,339", allowed) == []  # a sum of supported numbers it states
    assert number_check("Together $1,339.", allowed) == ["$1,339"]  # an unexplained total is not supported
    assert number_check("Only $999!", allowed) == ["$999"]
    assert number_check("We have 3 sizes.", allowed) == []  # small counts are exempt


def test_safe_eval_whitelist():
    assert safe_eval("max(a, b) * 2", {"a": 1, "b": 3}) == 6
    for bad in ["__import__('os')", "a ** 2", "open('x')", "'text'"]:
        with pytest.raises(ValueError):
            safe_eval(bad, {"a": 1})


def test_parse_condense_variants():
    assert parse_condense({"standalone": "A?", "parts": []}, "a") == ("A?", [])
    _q, parts = parse_condense({"standalone": "a and b", "parts": ["a?", "b?"]}, "a and b")
    assert parts == ["a?", "b?"]


def test_settings_digest_changes_cache_version():
    assert EngineSettings().digest() != EngineSettings(unit=0.6).digest()
