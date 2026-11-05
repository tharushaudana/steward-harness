from __future__ import annotations

import json

import httpx
import pytest

from steward.errors import BudgetExceeded, ProviderError
from steward.providers import Cassette, CassetteMiss, EmulatedDecider, FakeProvider, Metered, OpenRouter, parse_json
from steward.stores.memory import MemoryLedgerStore


def router(handler, **kw):
    return OpenRouter("test-key", transport=httpx.MockTransport(handler), prices={}, retries=2, **kw)


async def test_openrouter_chat_with_schema_and_reasoning():
    seen = {}

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        seen.update(body)
        assert req.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"model": body["model"], "choices": [{"message": {"content": '{"a": 1}'}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost": 0.0001}})

    p = router(handler, models={"extract": "vendor/extractor"})
    r = await p.chat([{"role": "user", "content": "hi"}], schema={"type": "object"}, purpose="extract")
    assert r.json == {"a": 1} and r.usage.cost_usd == 0.0001 and r.usage.cost_source == "api"
    assert seen["model"] == "vendor/extractor" and seen["response_format"]["json_schema"]["strict"] is True
    assert seen["reasoning"] == {"enabled": False}
    await p.chat([{"role": "user", "content": "hi"}])
    assert seen["model"] == "z-ai/glm-5.3-flash" and seen["reasoning"]["effort"] == "low"


async def test_openrouter_retries_then_computes_cost_when_missing():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                         "usage": {"prompt_tokens": 1000, "completion_tokens": 100}})

    p = router(handler)
    p.retries = 1
    import steward.providers.openrouter as orm
    orig = orm.asyncio.sleep

    async def no_sleep(_):
        return None

    orm.asyncio.sleep = no_sleep  # type: ignore[assignment]
    try:
        r = await p.chat([{"role": "user", "content": "x"}])
    finally:
        orm.asyncio.sleep = orig  # type: ignore[assignment]
    assert calls["n"] == 2 and r.usage.cost_source == "computed" and r.usage.cost_usd > 0


async def test_openrouter_errors():
    p = router(lambda req: httpx.Response(400, text="bad model"))
    with pytest.raises(ProviderError, match="HTTP 400"):
        await p.chat([{"role": "user", "content": "x"}])
    p2 = router(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}))
    with pytest.raises(ProviderError, match="empty content"):
        await p2.chat([{"role": "user", "content": "x"}])
    with pytest.raises(ProviderError):
        OpenRouter("")


async def test_openrouter_decide():
    def handler(req):
        body = json.loads(req.content)
        assert req.url.path.endswith("/alpha/decisions") and body["model"].startswith("typesafe/jev")
        return httpx.Response(200, json={"answers": {"q1": {"type": "noul", "noul": 0.8}},
                                         "usage": {"input_tokens": 500, "cost": 0.00002}})

    d = await router(handler).decide({"m": "x"}, {"q1": {"type": "noul", "instructions": "?"}})
    assert d.answers["q1"]["noul"] == 0.8 and d.usage.tokens_in == 500
    bad = router(lambda req: httpx.Response(200, json={"answers": {}}))
    with pytest.raises(ProviderError, match="missing answers"):
        await bad.decide({}, {"q1": {"type": "noul", "instructions": "?"}})


def test_parse_json_tolerant():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure: {"a": 2} done') == {"a": 2}
    with pytest.raises(ProviderError):
        parse_json("no json here")


class Priced(FakeProvider):
    def estimate(self, kind, payload_chars, max_out=0, model=None):
        return 0.01


async def test_metered_records_and_caps():
    ledger = MemoryLedgerStore()
    fake = Priced()
    m = Metered(fake, ledger, cap_usd=0.025)
    await m.chat([{"role": "user", "content": "a"}], purpose="answer")
    await m.decide({}, {"q": {"type": "noul", "instructions": "?"}}, purpose="route")
    s = await ledger.summary()
    assert s.calls == 2 and set(s.by_purpose) == {"answer", "route"}
    with pytest.raises(BudgetExceeded):  # 0 spent (fake costs 0) but 0.01 + 0.01 reserved concurrently > cap?
        m.cap_usd = 0.005
        await m.chat([{"role": "user", "content": "b"}])
    assert (await ledger.summary()).calls == 2  # nothing was sent


async def test_metered_records_failures():
    class Boom(FakeProvider):
        async def chat(self, *a, **k):
            raise ProviderError("down")

    ledger = MemoryLedgerStore()
    with pytest.raises(ProviderError):
        await Metered(Boom(), ledger).chat([{"role": "user", "content": "x"}], purpose="answer")
    assert ledger.entries[0].ok is False and "down" in (ledger.entries[0].error or "")


async def test_emulated_decider():
    fake = FakeProvider().on_chat_json("route", {"q1": 0.7, "k": {"choice": "request", "confidence": 0.9}, "s": 3})
    em = EmulatedDecider(fake, threshold_offset=0.1)
    d = await em.decide({"m": "hi"}, {
        "q1": {"type": "noul", "instructions": "?"},
        "k": {"type": "choice", "instructions": "?", "criteria": {"request": "", "chitchat": ""}},
        "s": {"type": "score", "instructions": "?"},
    }, purpose="route")
    assert abs(d.answers["q1"]["noul"] - 0.8) < 1e-9 and d.answers["k"]["choice"] == "request" and d.answers["s"]["score"] == 3
    schema = fake.calls[0].payload  # the schema was strict JSON with one property per question
    assert schema


async def test_cassette_record_replay(tmp_path):
    path = tmp_path / "c.json"
    fake = FakeProvider().on_chat("hello", "Hi there").on_decide("*", {"noul": 0.6})
    rec = Cassette(fake, path, mode="auto")
    r1 = await rec.chat([{"role": "user", "content": "hello"}], purpose="answer")
    d1 = await rec.decide({"m": 1}, {"q": {"type": "noul", "instructions": "?"}})
    assert rec.misses == 2 and path.exists()
    play = Cassette(None, path, mode="replay")
    r2 = await play.chat([{"role": "user", "content": "hello"}], purpose="answer")
    d2 = await play.decide({"m": 1}, {"q": {"type": "noul", "instructions": "?"}})
    assert r2.text == r1.text and d2.answers == d1.answers and r2.usage.cost_source == "cache" and play.hits == 2
    with pytest.raises(CassetteMiss):
        await play.chat([{"role": "user", "content": "something else"}])
    with pytest.raises(ValueError):
        Cassette(None, path, mode="auto")
