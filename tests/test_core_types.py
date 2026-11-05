from __future__ import annotations

import pytest

from steward import Message, ToolResult
from steward.errors import ConfigError, Issue, UnknownTool
from steward.events import EventBus, Outbox, Trace
from steward.providers.fake import FakeProvider


def test_tool_result_coercion():
    assert ToolResult.coerce(None).ok
    r = ToolResult.coerce({"ok": False, "error": "down"})
    assert not r.ok and r.error == "down" and r.data == {}
    assert ToolResult.coerce({"data": {"id": 1}}).ok
    with pytest.raises(TypeError):
        ToolResult.coerce(42)
    assert ToolResult.success({"id": "x"}, receipt={"ref": "x"}).receipt == {"ref": "x"}


def test_unknown_tool_suggests_the_closest_id():
    e = UnknownTool("crm.save_led", ["crm.save_lead", "staff.available"])
    assert "Did you mean 'crm.save_lead'" in str(e) and "@bot.tool('crm.save_led')" in str(e)


def test_config_error_lists_issues():
    e = ConfigError([Issue("unknown template 'hi'", path="journeys[0].nodes[2].template", hint="add it under templates"),
                     Issue("missing 'bn' text", severity="warning")])
    assert "journeys[0].nodes[2].template" in str(e) and "add it under templates" in str(e) and "bn" not in str(e)


def test_trace_totals_and_table():
    t = Trace()
    t.add("route", "decision", "q", {"t0": 0.9}, tokens_in=100, cost_usd=0.00001)
    t.add("answer", "llm", "q", "text", tokens_in=50, tokens_out=20, cost_usd=0.0002)
    tot = t.totals
    assert tot.steps == 2 and tot.llm_calls == 1 and tot.decision_calls == 1 and abs(tot.cost_usd - 0.00021) < 1e-9
    assert "TOTAL" in t.to_table()


async def test_event_bus_isolates_failing_handlers():
    bus, got = EventBus(), []
    bus.on("handover", lambda e, p: 1 / 0)

    @bus.on("handover")
    async def ok(event, payload):
        got.append((event, payload["session"]))

    bus.on("*", lambda e, p: got.append(("any", e)))
    out = Outbox()
    out.add("handover", {"session": "s1"})
    await out.flush(bus)
    assert ("handover", "s1") in got and ("any", "handover") in got and not out.items
    with pytest.raises(ValueError):
        bus.on("not_an_event", lambda e, p: None)


async def test_fake_provider_scripting_and_defaults():
    fake = FakeProvider()
    fake.on_decide("j_*", {"noul": 0.9}).on_decide("kind", {"type": "choice", "choice": "request", "confidence": 0.8})
    d = await fake.decide({"q": "x"}, {
        "j_lead": {"type": "noul", "instructions": "?"},
        "kind": {"type": "choice", "instructions": "?", "criteria": {"request": "", "chitchat": ""}},
        "p_other": {"type": "noul", "instructions": "?"},
        "t": {"type": "choice", "instructions": "?", "criteria": {"complaint": "", "none": ""}},
    })
    a = d.answers
    assert a["j_lead"]["noul"] == 0.9 and a["kind"]["choice"] == "request" and a["p_other"]["noul"] == 0.0
    assert a["t"]["choice"] == "none"
    fake.on_chat("price", "It costs 10.").on_chat_json("extract", {"name": "Ada"})
    r = await fake.chat([{"role": "user", "content": "What is the PRICE?"}])
    assert r.text == "It costs 10."
    j = await fake.chat([{"role": "user", "content": "x"}], schema={"type": "object"}, purpose="extract")
    assert j.json == {"name": "Ada"}
    assert fake.decisions_asked() == ["j_lead", "kind", "p_other", "t"]


def test_message_defaults():
    m = Message(session="s1", text="hi")
    assert m.channel == "dm" and not m.is_test
