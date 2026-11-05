"""Regression tests for the retrieval and conversation-flow fixes found by a multi-turn accuracy comparison:
approved-first coverage and fallback, aliases from approved text, translated parts, a decided reply language,
plain text, contextual small talk, after-handover modes, asking only for what is missing, once-per-session
templates, and config checks for flags nobody sets."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

from steward import Message, Steward
from steward.config import load_dict, validate
from steward.config.migrate import migrate
from steward.engine import Engine
from steward.engine.common import Run
from steward.engine.generate import GenerateOptions, generate
from steward.engine.preprocess import understand
from steward.events import Trace
from steward.harness.rules import strip_markdown
from steward.knowledge import KnowledgeIndex, MemoryKnowledgeStore
from steward.testing import FakeProvider, memory_stores, tiny_kb

CONFIG = json.loads((Path(__file__).resolve().parents[1] / "examples" / "northwind" / "northwind.steward.json")
                    .read_text(encoding="utf-8"))
PHONE = "202 555 0147"


def contains(*needles, yes=0.9, no=0.05):
    def answer(state, q):
        return {"noul": yes if any(n.lower() in q["instructions"].lower() for n in needles) else no}
    return answer


def bot(fake: FakeProvider, **settings) -> Steward:
    cfg = copy.deepcopy(CONFIG)
    cfg["settings"].update(settings)
    return Steward(cfg, provider=fake, stores=memory_stores(), knowledge=MemoryKnowledgeStore(tiny_kb()))


# ------------------------------------------------------------------------------------------------ retrieval
async def test_coverage_brings_the_approved_section_even_when_a_source_mentions_the_entity():
    fake = FakeProvider().on_decide("t*", contains("Models")).on_decide("u*", contains("range, battery"))  # S3 only
    fake.on_decide("sufficient", {"noul": 0.9}).on_decide("c*", {"noul": 0.0}).on_decide("e*", contains("'Volt'"))
    fake.on_chat("SECTIONS", "The Volt costs $2,290 and goes 90 km [S3][S9].")
    r = await Engine(MemoryKnowledgeStore(tiny_kb()), fake).answer("How far does the Volt go, and what does it cost?")
    assert "S3" in r.units and "S9" in r.units  # the source mentions the Volt; the approved price section comes too
    assert any(s.name == "coverage" and "S9" in s.output for s in r.trace.steps)


async def test_about_fallback_ranks_approved_sections_first():
    fake = FakeProvider().on_decide("t*", contains("Prices", "Models")).on_decide("sufficient", {"noul": 0.1})
    fake.on_decide("u*", {"noul": 0.1}).on_decide("c*", {"noul": 0.0}).on_decide("e*", {"noul": 0.0})
    fake.on_decide("u*a", contains("Volt e-bike", yes=0.9, no=0.1)).on_decide("u*a", contains("Approved price", yes=0.7, no=0.1))
    fake.on_chat("SECTIONS", "Please ask our team about Volt delivery dates [S9].")
    r = await Engine(MemoryKnowledgeStore(tiny_kb()), fake).answer("When can 3 Volt e-bikes be delivered to my office?")
    fb = next(s for s in r.trace.steps if s.name.startswith("about_fallback"))
    assert next(iter(fb.output)) == "S9"  # approved (0.7) before the source section (0.9)


def test_aliases_from_approved_text():
    kb = tiny_kb()
    sections = tuple(replace(s, text=s.text.replace("Volt e-bike: $2,290", "Volt (Spark): $2,290")) if s.id == "S9" else s
                     for s in kb.sections)
    ix = KnowledgeIndex(replace(kb, sections=sections))
    assert "Spark" in ix.entities["bike:volt"].aliases
    assert ix.approved_sections_for("bike:volt")[0] == "S9"


async def test_parts_are_translated_for_routing():
    kb = tiny_kb()
    ix = KnowledgeIndex(kb)

    def condense(msgs):
        if "Translate each question" in msgs[0]["content"]:
            return {"parts": ["Is the Volt in stock?", "What is the Volt price?"]}
        return {"standalone": "ভোল্ট আছে? ভোল্টের দাম কত?", "search": "Is the Volt in stock? Volt price?",
                "parts": ["ভোল্ট আছে?", "ভোল্টের দাম কত?"]}

    fake = FakeProvider().on_chat_json("condense", condense)
    u = await understand(Run(fake, trace=Trace()), ix, "ভোল্ট আছে? আর ভোল্টের দাম কত?", None)
    assert u.parts == ["Is the Volt in stock?", "What is the Volt price?"]


# ------------------------------------------------------------------------------------------------ answer form
async def test_decided_reply_language_overrides_mirroring():
    fake = FakeProvider().on_chat("SECTIONS", "The Volt costs $2,290 [S9].")
    ix = KnowledgeIndex(tiny_kb())
    await generate(Run(fake, trace=Trace()), ix, "Volt dam koto?", ["S9"], [], [], original="Volt dam koto?",
                   opts=GenerateOptions(reply_like="Volt dam koto?", reply_language="English", reply_latin=True))
    prompt = next(c for c in fake.calls if c.kind == "chat").payload[1]["content"]
    assert prompt.rstrip().endswith("write the whole reply in English, whatever language or script the customer and the SECTIONS use.")


def test_strip_markdown():
    text = "**E-bikes (2 sizes):**\n- Volt: $2,290\n* Spark: $1,990\n## Next\n1. Visit us\nWeight 2.5 kg, *really*."
    assert strip_markdown(text) == "E-bikes (2 sizes):\nVolt: $2,290\nSpark: $1,990\nNext\nVisit us\nWeight 2.5 kg, really."


async def test_plain_text_rule_strips_markdown_from_replies():
    cfg = copy.deepcopy(CONFIG)
    cfg["rules"]["plain_text"] = True
    fake = FakeProvider().on_decide("t*", contains("Prices")).on_decide("u*", contains("Approved price list"))
    fake.on_decide("sufficient", {"noul": 0.9}).on_chat("SECTIONS", "**Volt:** $2,290 [S9]")
    b = Steward(cfg, provider=fake, stores=memory_stores(), knowledge=MemoryKnowledgeStore(tiny_kb()))
    r = await b.ahandle(Message("p1", "How much is the Volt?"))
    assert "**" not in r.text and "Volt: $2,290" in r.text


# ------------------------------------------------------------------------------------------------ flow
async def test_after_handover_continue_keeps_answering():
    fake = FakeProvider().on_decide("j_immediate_handover", {"type": "choice", "choice": "complaint", "confidence": 0.9})
    b = bot(fake, after_handover="continue")
    r1 = await b.ahandle(Message("h1", "Your workshop broke my bike, I am furious!"))
    assert r1.status == "handover"
    fake.on_decide("j_immediate_handover", {"type": "choice", "choice": "none", "confidence": 0.9})
    fake.on_decide("t*", contains("Prices")).on_decide("u*", contains("Approved price list"))
    fake.on_decide("sufficient", {"noul": 0.9}).on_chat("SECTIONS", "The Volt costs $2,290 [S9].")
    r2 = await b.ahandle(Message("h1", "Meanwhile, how much is the Volt?"))
    assert r2.status == "answered" and "$2,290" in r2.text  # not paused, and no second handover event


async def test_contact_shared_while_paused_is_recorded():
    fake = FakeProvider().on_decide("j_immediate_handover", {"type": "choice", "choice": "complaint", "confidence": 0.9})
    b = bot(fake, paused_contact_tool="save_lead")
    await b.ahandle(Message("h2", "Your workshop broke my bike, I am furious!"))
    r = await b.ahandle(Message("h2", f"call me on {PHONE}"))
    assert r.status == "paused" and r.state["slots"]["phone"] == "2025550147"
    assert len(await b.stores.records.list("lead")) == 1
    assert r.text == "Thank you. Our team will contact you soon." or "Thank you" in r.text


async def test_ask_only_for_what_is_missing():
    cfg = copy.deepcopy(CONFIG)
    cfg["templates"]["ask_name_only"] = {"text": {"en": "May I have your name?", "es": "¿Me dice su nombre?"}}
    hr = next(j for j in cfg["journeys"] if j["id"] == "human_request")
    ask = next(n for n in hr["nodes"] if n["type"] == "ask" and n.get("template"))
    ask["templates_by_missing"] = {"name": "ask_name_only"}
    fake = FakeProvider().on_decide("j_human_request", {"noul": 0.9}).on_chat_json("extract", {"name": None, "phone": PHONE})
    b = Steward(cfg, provider=fake, stores=memory_stores(), knowledge=MemoryKnowledgeStore(tiny_kb()))
    r = await b.ahandle(Message("m1", f"please have someone call me on {PHONE}"))
    assert "May I have your name?" in r.text and "phone number" not in r.text.lower()


async def test_once_per_session_template_is_not_repeated():
    cfg = copy.deepcopy(CONFIG)
    tid = next(p for p in cfg["policies"] if p["id"] == "workshop")["action"]["template"]
    cfg["templates"][tid]["once_per_session"] = True
    fake = FakeProvider().on_decide("p_workshop", {"noul": 0.9})
    b = Steward(cfg, provider=fake, stores=memory_stores(), knowledge=MemoryKnowledgeStore(tiny_kb()))
    first = await b.ahandle(Message("o1", "Can you service my brakes?"))
    second = await b.ahandle(Message("o1", "And can you also service the gears?"))
    tpl = cfg["templates"][tid]["text"]["en"]
    assert tpl in first.text and tpl not in second.text


async def test_small_talk_mid_conversation_is_contextual_not_a_greeting():
    fake = FakeProvider().on_decide("kind", {"type": "choice", "choice": "request", "confidence": 0.9})
    fake.on_decide("t*", contains("Prices")).on_decide("u*", contains("Approved price list"))
    fake.on_decide("sufficient", {"noul": 0.9}).on_chat("SECTIONS", "The Volt costs $2,290 [S9].")
    b = bot(fake)
    await b.ahandle(Message("c1", "How much is the Volt e-bike these days?"))
    fake.on_decide("t*", {"noul": 0.0}).on_decide("route", None)
    fake.on_decide("kind", {"type": "choice", "choice": "chitchat", "confidence": 0.9})
    fake.on_chat("LATEST MESSAGE", "You are welcome, see you on Friday!")
    r = await b.ahandle(Message("c1", "great, thank you. see you friday then, I will come by"))
    assert "Welcome to Northwind Bikes" not in r.text


# ------------------------------------------------------------------------------------------------ config checks
def test_flag_read_but_never_set_is_reported():
    cfg = copy.deepcopy(CONFIG)
    hr = next(j for j in cfg["journeys"] if j["id"] == "human_request")
    cond = next(n for n in hr["nodes"] if n["type"] == "condition" and "contact_refused" in n.get("expr", ""))
    cond["expr"] = "state.phone_refused == True"
    issues = validate(load_dict(cfg)[0])
    assert any(i.code == "flag_never_set" and "phone_refused" in i.msg and "contact_refused" in i.hint for i in issues)


def test_migration_renames_the_old_refusal_flag():
    v1 = {"schema_version": 1, "journeys": [{"id": "j", "nodes": [{"id": "c", "type": "condition",
                                                                    "expr": "state.phone_refused == True"}]}]}
    out, notes = migrate(v1)
    assert out["journeys"][0]["nodes"][0]["expr"] == "state.contact_refused == True"
    assert any("phone_refused" in n for n in notes)
