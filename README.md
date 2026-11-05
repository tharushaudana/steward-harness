# Steward

**A compliant customer-service harness, powered by DADR.**

Steward turns a knowledge base plus an admin-editable configuration into a customer-service assistant that:

- **answers from your knowledge only.** DADR (decision-guided retrieval) navigates a knowledge tree with a cheap decision model. Numbers and wording are checked in code, and management-approved information outranks other sources.
- **follows your rules.** It uses approved templates (multi-language), forbidden and required wording, and policies, and it never claims an action that no tool performed.
- **runs journeys:** lead capture, human handover with availability checks, or any multi-step flow, drawn as graphs of cards.
- **calls your tools** as callbacks identified by a string id: your CRM, ticketing system or staffing system.
- **stores data where you want it:** memory, SQLite, MongoDB, Firestore, or your own adapter.

> Status: **pre-alpha, under construction.** The architecture and milestones are in [docs/PLAN.md](docs/PLAN.md).
> Milestone M0 (skeleton: types, errors, tracing, events, store protocols with in-memory stores, a scripted
> provider, contract tests) is in place.

## Install (development)

```bash
uv sync --extra dev
uv run pytest
```

## Planned usage

```python
from steward import Steward, Message, ToolCall, ok
from steward.providers import OpenRouter
from steward.stores.sqlite import SqliteStores

bot = Steward.from_file("northwind.steward.json",
                        stores=SqliteStores("northwind.db"),
                        provider=OpenRouter.from_env(spend_cap_usd=5.0))

@bot.tool("crm.save_lead")
async def save_lead(call: ToolCall):
    ref = await crm.upsert(call.args, key=call.idempotency_key)
    return ok({"id": ref}, receipt={"ref": ref})

reply = bot.handle(Message(session="u-42", text="Do you have a 52 cm frame?"))
print(reply.text, reply.status, reply.trace.totals.cost_usd)
```

## License

Apache-2.0. See [LICENSE](LICENSE).
