"""The journey executor: walks a journey graph card by card.

Limits: at most 25 steps per journey and 2 visits per card (no infinite loops). An Ask card suspends the journey
(outcome.pending); the next turn resumes it at '<ask>.resume' (or takes '<ask>.gave_up').
"""

from __future__ import annotations

import time
from typing import Any

from steward.config.models import (
    AskNode,
    ConditionNode,
    CustomNode,
    DecisionNode,
    EmitNode,
    EndNode,
    ExtractNode,
    Journey,
    SetNode,
    ToolNode,
    TriggerNode,
)
from steward.harness.expr import ExprError
from steward.harness.slots import validate_value
from steward.harness.triggers import Fired, as_question
from steward.harness.turn import Outcome, TurnContext

MAX_STEPS = 25
MAX_VISITS = 2


async def extract(ctx: TurnContext, node: ExtractNode) -> tuple[str, dict[str, Any]]:
    """The LLM fills slots (strict JSON) from the message or the conversation; code validates every value.
    Ports: ok | partial (a required slot is missing) | invalid (a required value was given but failed validation)."""
    cfg, env = ctx.cfg, ctx.env
    props: dict[str, Any] = {}
    lines = []
    for s in node.slots:
        d = cfg.slots.get(s)
        if d and d.type == "enum":
            # anyOf (not a ["string","null"] type with a null in the enum): accepted by every provider's strict mode
            props[s] = {"anyOf": [{"type": "string", "enum": list(d.values)}, {"type": "null"}]}
            meanings = "\n".join(f"    * {v}: {d.value_descriptions.get(v, '')}".rstrip(": ") for v in d.values) \
                if d.value_descriptions else ""
            lines.append(f"- {s}: {d.description or s}" + (" Options (choose the MOST SPECIFIC one that fits the whole "
                                                           f"conversation):\n{meanings}" if meanings else ""))
        else:
            props[s] = {"type": ["string", "null"]}
            lines.append(f"- {s}: {(d.description if d else '') or s}")
    schema = {"type": "object", "additionalProperties": False, "required": list(node.slots), "properties": props}
    hist = env["turn"]["history"][-6:] if node.source == "conversation" else []
    convo = "\n".join(f"Customer: {h['q']}\nAssistant: {h['a'][:300]}" for h in hist)
    msgs: list[Any] = [
        {"role": "system", "content": "Extract the requested fields from the customer's messages. Use null for anything the "
                                      "customer has not given. Never invent or guess values. " + node.instructions +
                                      "\nFIELDS:\n" + "\n".join(lines)},
        {"role": "user", "content": (f"CONVERSATION:\n{convo}\n\n" if convo else "") + f"LATEST MESSAGE: {env['turn']['message']}"}]
    r = await ctx.run.chat("extract", msgs, purpose="extract", max_tokens=400, schema=schema, schema_name="extract")
    data = r.json if isinstance(r.json, dict) else {}
    got: dict[str, str] = {}
    rejected: dict[str, str] = {}
    for s in node.slots:
        v = data.get(s)
        d = cfg.slots.get(s)
        if v in (None, "", "null"):
            continue
        if d and d.validator:
            raw, v = v, validate_value(cfg.validators, v, d.validator)
            if v is None:
                rejected[s] = str(raw).strip()
                continue
        if d and d.type == "enum" and v not in d.values:
            continue
        got[s] = str(v).strip()
    contact_slot = cfg.settings.contact_slot
    if contact_slot in node.slots and env["turn"].get("contact_in_message"):  # code-captured contact wins
        got[contact_slot] = env["turn"]["contact_in_message"]
        rejected.pop(contact_slot, None)
    for s, v in got.items():
        d = cfg.slots.get(s)
        if d and d.overwrite == "keep_first" and env["slots"].get(s):
            continue
        env["slots"][s] = v
    missing = [s for s in node.require if not env["slots"].get(s)]
    bad = {s: v for s, v in rejected.items() if s in missing}
    env["vars"]["invalid"] = bad
    port = "invalid" if bad else ("partial" if missing else "ok")
    return port, {"extracted": got, "missing": missing, **({"invalid": bad} if bad else {})}


async def run_journey(ctx: TurnContext, j: Journey, outcome: Outcome, *, start: str | None = None,
                      trigger: Fired | None = None) -> Outcome:
    nodes = {n.id: n for n in j.nodes}
    edges = {e.from_: e.to for e in j.edges}
    env = ctx.env
    if trigger is not None:
        env["trigger"] = trigger.info()
    t = j.trigger()
    cur: str | None = start or (t.id if t else None)
    visits: dict[str, int] = {}
    steps = 0
    while cur and steps < MAX_STEPS:
        steps += 1
        node = nodes.get(cur)
        if node is None:
            outcome.status = "error"
            break
        visits[cur] = visits.get(cur, 0) + 1
        if visits[cur] > MAX_VISITS:
            outcome.status = "error:loop"
            break
        t0 = time.perf_counter()
        port, info, mech = "next", {}, "code"
        try:
            if isinstance(node, TriggerNode):
                port = env["trigger"].get("choice") or "next"
            elif isinstance(node, ConditionNode):
                port = "true" if ctx.expr.eval(node.expr, ctx.eval_ctx()) else "false"
            elif isinstance(node, DecisionNode):
                mech = "decision"
                a = (await ctx.run.decide(f"j:{j.id}.{cur}", {"latest_message": env["turn"]["message"],
                                                              "standalone": env["turn"]["standalone"]},
                                          {"d": as_question(node.decision)}, purpose="route"))["d"]
                if a.get("type") == "choice":
                    confident = float(a.get("confidence", 1) or 0) >= node.threshold
                    port = (a.get("choice") or "unsure") if confident else "unsure"
                else:
                    port = "yes" if float(a.get("noul", 0) or 0) >= node.threshold else "no"
            elif isinstance(node, ExtractNode):
                mech = "llm"
                port, info = await extract(ctx, node)
                if port == "invalid" and f"{cur}.invalid" not in edges:  # journeys drawn without an 'invalid' arrow
                    port = "partial"
            elif isinstance(node, ToolNode):
                mech = "tool"
                res, receipt = await ctx.tools.call(ctx.cfg, node.tool, env, ctx.turn_info(), mode=ctx.tools_mode)
                outcome.receipts.append(receipt)
                ctx.outbox.add("tool_call", {"session": ctx.msg.session, "tool": node.tool, "impl": receipt.impl,
                                             "ok": res.ok, "error": res.error, "journey": j.id})
                if receipt.impl == "records.append" and res.ok:
                    ctx.outbox.add("record", {"session": ctx.msg.session, "tool": node.tool, "id": res.data.get("id"),
                                              "duplicate": res.duplicate, "args": dict(ctx.cfg.tools[node.tool].args)})
                if node.save_as:
                    root, _, name = node.save_as.partition(".")
                    env.setdefault(root, {})[name] = res.data
                port = "ok" if res.ok else ("timeout" if res.error == "timeout" else "error")
                info = {"ok": res.ok, "error": res.error, "duplicate": res.duplicate}
            elif isinstance(node, AskNode):
                outcome.asks.append({"slots": list(node.require), "template": node.template})
                outcome.status = "suspended"
                outcome.pending = {"journey": j.id, "node": cur, "require": list(node.require), "asked_turn": None,
                                   "tries": 0}
                outcome.path.append({"node": cur, "port": "suspend"})
                ctx.run.step(f"j:{j.id}.{cur}", "state", node.template, {"ask": node.require}, t0)
                return outcome
            elif isinstance(node, SetNode):
                for key, val in node.assign.items():
                    root, _, name = key.partition(".")
                    env.setdefault(root, {})[name] = ctx.expr.eval(val, ctx.eval_ctx()) if isinstance(val, str) else val
                info = dict(node.assign)
            elif isinstance(node, EmitNode):
                outcome.emits.append({"template": node.template, "position": node.position})
            elif isinstance(node, EndNode):
                outcome.status = node.status
                outcome.path.append({"node": cur, "port": None})
                ctx.run.step(f"j:{j.id}.{cur}", "code", "end", {"status": outcome.status}, t0)
                return outcome
            elif isinstance(node, CustomNode):
                port, info = "error", {"error": f"no runtime registered for card type '{node.type}'"}
        except ExprError as e:
            port, info = "error", {"error": str(e)}
        outcome.path.append({"node": cur, "port": port})
        ctx.run.step(f"j:{j.id}.{cur}", mech, type(node).__name__.removesuffix("Node").lower(),
                     {"port": port, **({"info": info} if info else {})}, t0)
        cur = edges.get(f"{cur}.{port}")
    if outcome.status == "running":
        outcome.status = "done" if steps < MAX_STEPS else "error:steps"
    return outcome
