"""The harness end to end (Steward facade + pipeline + journeys + composer + tools) on the Northwind example,
with scripted decisions: no network, no credentials."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from steward import ConfigError, Message, Steward, ToolCall, ok
from steward.config import load_dict
from steward.harness.rules import make_checker, make_fixer
from steward.harness.templates import render
from steward.knowledge import KnowledgeIndex, MemoryKnowledgeStore
from steward.testing import FakeProvider, memory_stores, tiny_kb
from steward.types import Receipt

CONFIG = json.loads((Path(__file__).resolve().parents[1] / "examples" / "northwind" / "northwind.steward.json")
                    .read_text(encoding="utf-8"))
PHONE = "202 555 0147"


def make_bot(fake: FakeProvider, config: dict | None = None) -> Steward:
    return Steward(config or copy.deepcopy(CONFIG), provider=fake, stores=memory_stores(),
                   knowledge=MemoryKnowledgeStore(tiny_kb()))


def texts(fake: FakeProvider) -> list[str]:
    return [c.purpose for c in fake.calls if c.kind == "chat"]


def test_render_filters():
    env = {"slots": {"name": "Ada"}, "vars": {}}
    assert render("Thanks{{slots.name|prefix:', '}}!", env) == "Thanks, Ada!"
    assert render("Thanks{{slots.missing|prefix:', '}}!", env) == "Thanks!"
    assert render("{{slots.missing|default:'friend'}} {{slots.name|upper}}", env) == "friend ADA"


async def test_first_turn_welcome_answer_and_contact_request():
    fake = FakeProvider().on_decide("t*", lambda s, q: {"noul": 0.9 if "Prices" in q["instructions"] else 0.0})
    fake.on_decide("u*", lambda s, q: {"noul": 0.9 if "Approved price list" in q["instructions"] else 0.0})
    fake.on_decide("sufficient", {"noul": 0.9}).on_chat("SECTIONS", "The Volt costs $2,290 [S9].")
    r = await make_bot(fake).ahandle(Message("s1", "How much is the Volt e-bike these days?"))
    assert r.status == "answered"
    assert r.text.startswith("Hi! Welcome to Northwind Bikes.") and "$2,290" in r.text
    assert r.text.endswith("Share your phone number and we will get in touch.")  # flow: ask for the contact once
    assert r.evidence["sections"] == ["S9"]


async def test_lead_capture_saves_once_and_thanks_once():
    fake = FakeProvider().on_decide("j_lead_capture", {"noul": 0.95})
    fake.on_chat_json("extract", {"name": "Ada", "phone": PHONE, "category": "Bike Purchase", "interest": "Volt",
                                  "requirement": "wants a Volt"})
    bot = make_bot(fake)
    got = []
    bot.on("record", lambda e, p: got.append(p))
    r = await bot.ahandle(Message("s2", f"I'm Ada, {PHONE}"))
    assert "Thank you, Ada! Our team will contact you soon." in r.text and r.journeys[0]["status"] == "done"
    leads = await bot.stores.records.list("lead")
    assert len(leads) == 1 and leads[0].data["phone"] == "2025550147" and leads[0].data["category"] == "Bike Purchase"
    assert got and got[0]["tool"] == "save_lead"
    r2 = await bot.ahandle(Message("s2", f"again: {PHONE}"))
    assert "Our team will contact you soon" not in r2.text and len(await bot.stores.records.list("lead")) == 1


async def test_test_conversations_never_touch_real_records():
    fake = FakeProvider().on_decide("j_lead_capture", {"noul": 0.95}).on_chat_json("extract", {"phone": PHONE})
    bot = make_bot(fake)
    await bot.ahandle(Message("t1", PHONE, is_test=True))
    assert await bot.stores.records.list("lead") == []
    test_rows = await bot.stores.records.list("lead", include_test=True)
    assert test_rows[0].key.startswith("test:lead:")


async def test_human_request_waits_then_reports_unavailable():
    fake = FakeProvider().on_decide("j_human_request", {"noul": 0.9})
    fake.on_chat_json("extract", {"name": None, "phone": None})
    bot = make_bot(fake)
    r = await bot.ahandle(Message("s3", "I want to talk to a person"))
    assert r.status == "needs_input" and "may I have your name and phone number" in r.text
    assert r.state["pending"]["journey"] == "human_request"
    fake.on_decide("r_resume", {"noul": 0.9}).on_chat_json("extract", {"name": "Ada", "phone": PHONE})
    r2 = await bot.ahandle(Message("s3", f"Ada {PHONE}"))
    assert "Nobody from the team is available to chat right now" in r2.text and "2025550147" in r2.text
    assert len(await bot.stores.records.list("lead")) == 1 and r2.state["pending"] is None


async def test_handover_pauses_until_release():
    fake = FakeProvider().on_decide("j_human_request", {"noul": 0.9})
    fake.on_chat_json("extract", {"name": "Ada", "phone": PHONE})
    bot = make_bot(fake)
    await bot.stores.runtime.set("agents_online", True)
    events = []
    bot.on("handover", lambda e, p: events.append(p))
    r = await bot.ahandle(Message("s4", f"connect me to a person please, I'm Ada {PHONE}"))
    assert r.status == "handover" and "connecting you with a member of our team" in r.text and events
    assert len(await bot.stores.records.list("handover")) == 1
    r2 = await bot.ahandle(Message("s4", "hello?"))
    assert r2.status == "paused" and "will reply to you here shortly" in r2.text
    assert not [c for c in fake.calls[-3:] if c.kind == "decide" and c.payload["questions"].get("j_human_request")
                and r2.trace.steps[0].name != "paused"]
    await bot.sessions.arelease("s4", by="agent-7")
    assert (await bot.sessions.aget("s4"))["flags"]["handover_epoch"] == 1


async def test_invalid_contact_gets_a_message_and_the_loop_is_capped():
    fake = FakeProvider().on_decide("j_human_request", {"noul": 0.9})
    fake.on_chat_json("extract", {"name": "Ada", "phone": "+44 7700 900123"})
    bot = make_bot(fake)
    r = await bot.ahandle(Message("s5", "I need a person, Ada +44 7700 900123"))
    assert "does not look like a valid phone number" in r.text and "+44 7700 900123" in r.text
    fake.on_decide("r_resume", {"noul": 0.9})
    for _ in range(3):  # re-asked twice (settings.max_reasks = 2), the third invalid reply takes gave_up
        r = await bot.ahandle(Message("s5", "+44 7700 900123"))
    assert "You can also reach us directly" in r.text and r.state["pending"] is None  # gave up: contacts, no loop


async def test_refusal_gets_contacts_instead_of_another_ask():
    fake = FakeProvider().on_decide("j_human_request", {"noul": 0.9}).on_chat_json("extract", {})
    bot = make_bot(fake)
    await bot.ahandle(Message("s6", "I want a person"))
    fake.on_decide("r_resume", {"noul": 0.9}).on_decide("r_refuse", {"noul": 0.9})
    r = await bot.ahandle(Message("s6", "I won't give you my number"))
    assert "You can also reach us directly" in r.text and "may I have your name" not in r.text


async def test_complaint_takes_over_the_turn():
    fake = FakeProvider().on_decide("j_immediate_handover", {"type": "choice", "choice": "complaint", "confidence": 0.9,
                                                              "probabilities": {"complaint": 0.9}})
    r = await make_bot(fake).ahandle(Message("s7", "This is the worst service ever!"))
    assert r.status == "handover"
    assert r.text.startswith("We are sorry for the trouble") and "will reply to you here shortly" in r.text
    assert "answer" not in texts(fake)  # no knowledge answer on a takeover


async def test_template_only_channel_makes_no_model_calls():
    fake = FakeProvider()
    r = await make_bot(fake).ahandle(Message("s8", "price?", channel="comment"))
    assert r.status == "template" and "sent you a private message" in r.text and fake.calls == []


async def test_exclusive_policy_replaces_the_answer():
    fake = FakeProvider().on_decide("p_careers", {"noul": 0.9})
    r = await make_bot(fake).ahandle(Message("s9", "Are you hiring mechanics? Where do I send my CV?"))
    assert "jobs@northwind.example" in r.text and r.policies == ["careers"] and "answer" not in texts(fake)
    assert "Share your phone number" not in r.text  # no_ask


async def test_chitchat_greets_then_answers_small_talk_in_context():
    fake = FakeProvider().on_decide("kind", {"type": "choice", "choice": "chitchat", "confidence": 0.9})
    fake.on_chat("LATEST MESSAGE", "You're welcome!")
    bot = make_bot(fake)
    assert (await bot.ahandle(Message("s10", "hi"))).text == "Hi! Welcome to Northwind Bikes. How can I help you today?"
    r = await bot.ahandle(Message("s10", "thanks!"))
    assert r.status == "chitchat" and r.text == "You're welcome!"


def test_rules_checker_and_fixer():
    cfg, _ = load_dict(copy.deepcopy(CONFIG))
    idx = KnowledgeIndex(tiny_kb())
    probs, det = make_checker(cfg, idx, "en", [])("A cheap bike! We have forwarded your request.")
    assert det["forbidden"] == ["cheap"] and det["claims"] == ["forwarded"] and probs
    fixed = make_fixer(cfg, idx, "en")("A cheap bike! We have forwarded your request.", det)
    assert "great value" in fixed and "forwarded" not in fixed
    _, det2 = make_checker(cfg, idx, "en", [Receipt("create_handover", "records.append", True)])("We have forwarded it.")
    assert "claims" not in det2


async def test_tools_registry_custom_impl_timeout_and_check():
    raw = copy.deepcopy(CONFIG)
    raw["tools"]["save_lead"]["impl"] = "crm.save_lead"
    fake = FakeProvider().on_decide("j_lead_capture", {"noul": 0.95}).on_chat_json("extract", {"phone": PHONE})
    bot = make_bot(fake, raw)
    with pytest.raises(ConfigError, match=r"crm.save_lead"):
        await bot.acheck()
    seen = []

    @bot.tool("crm.save_lead")
    def save(call: ToolCall):  # a sync tool
        seen.append((call.args["record"]["phone"], call.idempotency_key))
        return ok({"id": "CRM-1"}, receipt={"ref": "CRM-1"})

    await bot.acheck()
    r = await bot.ahandle(Message("s11", PHONE))
    assert seen == [("2025550147", f"lead:2025550147:{r.journeys[0]['receipts'][0]['key'].split(':')[-1]}")]
    assert "Our team will contact you soon" in r.text


async def test_failing_tool_never_breaks_the_conversation():
    raw = copy.deepcopy(CONFIG)
    raw["tools"]["save_lead"]["impl"] = "crm.down"
    fake = FakeProvider().on_decide("j_lead_capture", {"noul": 0.95}).on_chat_json("extract", {"phone": PHONE})
    bot = make_bot(fake, raw)
    bot.tools.register("crm.down", lambda call: 1 / 0)
    r = await bot.ahandle(Message("s12", PHONE))
    assert r.status != "error" and "Our team will contact you soon" not in r.text  # no receipt -> no thank-you claim
    assert r.journeys[0]["receipts"][0]["ok"] is False


async def test_admin_draft_publish_history_restore():
    bot = make_bot(FakeProvider())
    doc = await bot.admin.adraft()
    cfg = copy.deepcopy(doc.config)
    cfg["templates"]["welcome"]["text"]["en"] = "Hello from the draft!"
    _, issues = await bot.admin.asave_draft(cfg, doc.rev)
    assert not [i for i in issues if i.severity == "error"]
    assert (await bot.config("draft")).templates["welcome"].text["en"] == "Hello from the draft!"
    assert (await bot.config()).templates["welcome"].text["en"] != "Hello from the draft!"  # live config untouched
    pub = await bot.admin.apublish("new welcome")
    assert pub.version == 2 and (await bot.config()).templates["welcome"].text["en"] == "Hello from the draft!"
    hist = await bot.admin.ahistory()
    await bot.admin.arestore(hist[0].id)
    assert (await bot.config("draft")).templates["welcome"].text["en"] == "Hi! Welcome to Northwind Bikes."
    bad = copy.deepcopy(cfg)
    bad["policies"][0]["action"]["template"] = "nope"
    draft = await bot.admin.adraft()
    await bot.admin.asave_draft(bad, draft.rev)
    with pytest.raises(ConfigError):
        await bot.admin.apublish()


def test_sync_facade():
    fake = FakeProvider()
    bot = make_bot(fake)
    r = bot.handle(Message("s13", "price?", channel="comment"))
    assert r.status == "template"
    assert bot.sessions.get("s13")["turn"] == 1
    bot.close()


async def test_sync_facade_refuses_inside_a_running_loop():
    with pytest.raises(RuntimeError, match="ahandle"):
        make_bot(FakeProvider()).handle(Message("s14", "hi"))


async def test_costs_go_to_the_ledger():
    class Priced(FakeProvider):
        async def chat(self, *a, **k):
            r = await super().chat(*a, **k)
            r.usage.cost_usd = 0.001
            return r

    fake = Priced().on_decide("kind", {"type": "choice", "choice": "about_assistant", "confidence": 0.9})
    bot = make_bot(fake)
    await bot.ahandle(Message("s15", "who are you?"))
    assert abs(await bot.stores.ledger.total() - 0.001) < 1e-9
