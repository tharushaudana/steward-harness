"""The smallest complete integration: a config, a knowledge base, a provider and one custom tool.

    uv run python examples/minimal.py            # offline (scripted decisions)
    OPENROUTER_API_KEY=... uv run python examples/minimal.py --live

The Northwind config saves leads with the built-in `records.append`. Here the `save_lead` instance is pointed at a
custom implementation instead ("crm.save_lead"), which is how you connect your own CRM."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from steward import Message, Steward, ToolCall, ok
from steward.knowledge import MemoryKnowledgeStore

HERE = Path(__file__).parent / "northwind"


def make_bot(live: bool = False) -> Steward:
    config = json.loads((HERE / "northwind.steward.json").read_text(encoding="utf-8"))
    config["tools"]["save_lead"]["impl"] = "crm.save_lead"  # use our own implementation for this tool instance
    if live:
        from steward.providers import OpenRouter

        provider = OpenRouter.from_env(spend_cap_usd=0.10)
    else:
        from steward.testing import demo_provider

        provider = demo_provider()
    bot = Steward(config, provider=provider, knowledge=MemoryKnowledgeStore.from_file(HERE / "kb.json"))

    crm: dict[str, dict] = {}

    @bot.tool("crm.save_lead")
    def save_lead(call: ToolCall):  # sync or async; return ok(...) / fail(...) / a dict / None
        key = call.idempotency_key or call.turn.session
        crm[key] = {**crm.get(key, {}), **{k: v for k, v in call.args["record"].items() if v}}
        return ok({"id": key}, receipt={"crm_id": key})

    bot.on("tool_call", lambda event, payload: print(f"  [event {event}] {payload.get('tool')}"))
    bot.check()  # raises ConfigError if the config is invalid or a tool implementation is missing
    return bot


def main() -> None:
    bot = make_bot(live="--live" in sys.argv)
    for text in ("hello", "How heavy is the Trail 500?", "I'm Ada, 202 555 0147"):
        reply = bot.handle(Message(session="demo-1", text=text))
        print(f"> {text}\n[{reply.status}] {reply.text}\n")
    bot.close()


if __name__ == "__main__":
    main()
