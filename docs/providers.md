# Providers

A provider makes model calls. The protocol (`steward.providers.base.Provider`) has three methods:

```python
async def chat(messages, *, model=None, max_tokens=600, temperature=0.0, schema=None, schema_name="output", purpose="") -> ChatResult
async def decide(state, questions, *, model=None, purpose="") -> Decisions        # batched yes/no, choice, score questions
def estimate(kind, payload_chars, max_out=0, model=None) -> float                # upper-bound USD, for the spend cap
```

`purpose` labels each call in the ledger (`route`, `answer`, `extract`, `build`, …) and lets a provider pick a model per purpose.

## OpenRouter (built in)

```python
from steward.providers import OpenRouter

provider = OpenRouter.from_env(spend_cap_usd=5)          # OPENROUTER_API_KEY; STEWARD_SPEND_CAP_USD as fallback cap
provider = OpenRouter(api_key, chat_model="z-ai/glm-5.3-flash", decide_model="typesafe/jev-1.14-20261014",
                      models={"extract": "anthropic/claude-haiku-5.5"},       # a model per purpose
                      reasoning={"z-ai/glm-5.3-flash": {"effort": "low", "exclude": True}})
```

- **Decisions** go to the `/api/alpha/decisions` endpoint (a decision model, billed on input only).
- **Structured output** (`schema=`) uses strict JSON Schema. A nullable enum is written as `anyOf: [{enum}, {null}]` so that every vendor accepts it.
- **Errors** are retried with backoff (`retries`, `timeout_s`). The `transport=` argument lets tests inject an `httpx.MockTransport`.

## Spend cap and ledger

`Steward` always wraps the provider in `Metered`. Metered does three things:
- estimates each call before it is made;
- refuses the call with `BudgetExceeded` when the ledger total plus the estimate would pass the cap;
- records the real cost in the `LedgerStore` afterwards.

A reply hit by the cap has status `budget_exceeded` and a safe text. Use `Steward(..., spend_cap_usd=...)` or the provider's `spend_cap_usd`.

## Other vendors

You can implement `Provider` for any vendor. If the vendor has no decision model, wrap a chat provider:

```python
from steward.providers import EmulatedDecider

provider = EmulatedDecider(MyChatProvider(...), threshold_offset=0.0)
```

`EmulatedDecider` asks the chat model to answer the decision batch as JSON scores. Chat models score differently from a trained decision model, so calibrate the trigger thresholds with `steward eval --calibrate` before relying on them.

## Testing providers

| | |
|---|---|
| `FakeProvider` | Scripted. `on_decide("j_lead_capture", {"noul": 0.9})` matches question ids or glob patterns, and the newest rule wins. `on_chat("SECTIONS", "text")` matches on prompt content. `on_chat_json("extract", {...} or fn(messages))` scripts structured output. Unscripted questions get a neutral answer (`noul` 0, `choice` "none"/"other"). `fake.calls` lists every call. |
| `Cassette(inner, path, mode)` | Records real calls to a JSON file and replays them. `mode="auto"` replays what it has and records the rest; `"replay"` fails on any unrecorded call (use it in CI); `"record"` always calls the inner provider. |
| `steward.testing.demo_provider()` | The offline Northwind demo: keyword-scripted decisions plus a regex extractor. |
