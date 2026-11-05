"""Generate docs/config-reference.md from the configuration models (so the reference can never drift from the code).

    uv run steward schema --markdown -o docs/config-reference.md"""

from __future__ import annotations

import json
from typing import Annotated, Any, get_args, get_origin

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from steward.config import models as m
from steward.config.catalog import BUILTIN_CARDS

SECTIONS: list[tuple[str, type[BaseModel], str]] = [
    ("Config (root)", m.Config, "The whole file. Unknown keys are errors everywhere (typos never pass silently)."),
    ("profile", m.Profile, "Who the assistant is: a small system prompt shown to the answer model."),
    ("prompts", m.Prompts, "Extra prompt text."),
    ("settings", m.Settings, "Runtime behaviour."),
    ("validators.<id>", m.Validator, "Normalise then match a value (phone numbers, emails, ids)."),
    ("slots.<name>", m.Slot, "What the assistant may learn about the customer (extract cards fill them)."),
    ("templates.<id>", m.Template, "Approved texts, one per language. `{{slots.name|prefix:', '}}` placeholders."),
    ("rules", m.Rules, "Checks applied to every generated reply."),
    ("rules.required_wording[]", m.RequiredWording, ""),
    ("rules.forbidden_phrases[]", m.ForbiddenPhrase, ""),
    ("rules.claims_without_receipt", m.ClaimsRule, "Phrases that claim an action; only allowed when that tool succeeded."),
    ("channels.<name>", m.Channel, "Per channel (Message.channel) behaviour."),
    ("flow", m.Flow, "Conversation-level steps: welcome, asking for the contact, thanking."),
    ("flow.ask_contact", m.AskContactStep, ""),
    ("policies[]", m.Policy, "A decision question plus an action (template or prompt injection)."),
    ("policies[].action", m.PolicyAction, ""),
    ("DecisionQuestion (trigger / decision)", m.DecisionQuestion, "A question for the decision model."),
    ("tools.<id>", m.ToolSpec, "A tool instance. `impl` defaults to the instance id; register it with @bot.tool(impl)."),
    ("journeys[]", m.Journey, "A graph of cards (nodes) joined by edges from '<node>.<port>' to '<node>'."),
    ("journeys[].edges[]", m.Edge, ""),
    ("card: trigger", m.TriggerNode, "Starts the journey when the decision model says so (and `when` is true)."),
    ("card: condition", m.ConditionNode, "Branches on a safe expression."),
    ("card: decision", m.DecisionNode, "Branches on a decision-model question."),
    ("card: extract", m.ExtractNode, "Fills slots with the extract model; validators decide ok / partial / invalid."),
    ("card: tool", m.ToolNode, "Calls a tool instance by id."),
    ("card: ask", m.AskNode, "Asks the customer and waits; the next message resumes the journey."),
    ("card: set", m.SetNode, "Assigns expressions to state.x / vars.x / slots.x."),
    ("card: emit", m.EmitNode, "Adds a template to the reply."),
    ("card: end", m.EndNode, "Ends the journey with a status."),
]


def _type(ann: Any) -> str:
    origin = get_origin(ann)
    if origin is Annotated:
        return _type(get_args(ann)[0])
    args = [a for a in get_args(ann) if a is not type(None)]
    name = getattr(ann, "__name__", None)
    if origin is None:
        return name or str(ann).replace("typing.", "")
    if str(origin) == "typing.Literal":
        return " \\| ".join(json.dumps(a) for a in get_args(ann))
    if origin in (list, tuple, set):
        return f"list[{_type(args[0])}]" if args else "list"
    if origin is dict:
        return f"dict[{_type(args[0])}, {_type(args[1])}]" if len(args) == 2 else "dict"
    parts = [_type(a) for a in args]
    if len(parts) == 1:
        return parts[0] + " \\| null"
    return " \\| ".join(parts)


def _default(field: Any) -> str:
    if field.is_required():
        return "**required**"
    if field.default_factory is not None:
        try:
            v = field.default_factory()
        except TypeError:
            return ""
        if isinstance(v, BaseModel):
            return "object (see its section)"
        return f"`{json.dumps(v)}`"
    if field.default is PydanticUndefined:
        return ""
    return f"`{json.dumps(field.default)}`"


def config_reference() -> str:
    out = ["# Configuration reference", "",
           "Generated from `steward.config.models` by `steward schema --markdown`; do not edit by hand. The JSON Schema",
           "(`schema/steward.schema.json`) has the same information for editors and agents.", ""]
    for title, model, intro in SECTIONS:
        out += [f"## {title}", ""]
        if intro:
            out += [intro, ""]
        out += ["| field | type | default | description |", "|---|---|---|---|"]
        for name, f in model.model_fields.items():
            key = f.alias or name
            if key in ("$schema",):
                continue
            desc = (f.description or "").replace("|", "\\|").replace("\n", " ")
            out.append(f"| `{key}` | {_type(f.annotation)} | {_default(f)} | {desc} |")
        out.append("")
    out += ["## Card ports", "", "Edges leave a card through one of its ports. A choice question gives the trigger and",
            "decision cards one port per option instead.", "", "| card | ports | what it does |", "|---|---|---|"]
    for c in BUILTIN_CARDS.values():
        out.append(f"| `{c.type}` | {', '.join(f'`{p}`' for p in c.ports) or '(none)'} | {c.help.replace('|', '/')} |")
    return "\n".join(out) + "\n"
