"""The card catalog: every journey card type with its output ports and help text. Drives validation and the admin
canvas. Host card types (steward.harness.cards, milestone M4) are added with `register_card`."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CardInfo:
    type: str
    label: str
    ports: tuple[str, ...]
    help: str
    mechanism: str = "code"  # code | decision | llm | tool | state
    dynamic_ports: Callable[[Mapping[str, Any]], list[str]] | None = field(default=None, compare=False)


def _decision_ports(extra: tuple[str, ...]) -> Callable[[Mapping[str, Any]], list[str]]:
    def ports(node: Mapping[str, Any]) -> list[str]:
        q = node.get("decision") or node.get("jev") or {}
        if q.get("type") == "choice":
            return [k for k in (q.get("criteria") or {}) if k != "none"] + list(extra)
        return []

    return ports


BUILTIN_CARDS: dict[str, CardInfo] = {c.type: c for c in [
    CardInfo("trigger", "Trigger", ("next",), "When the journey applies: a decision question (yes/no, or a choice with one "
             "port per option) plus an optional code `when`.", "decision", _decision_ports(())),
    CardInfo("condition", "Condition", ("true", "false"), "Branch on slots, vars, state, trigger or turn with a safe "
             "expression: has(), valid(x, 'validator'), len(), lower(), comparisons."),
    CardInfo("decision", "Decision", ("yes", "no"), "A judgement by the decision model: yes/no, or a choice with one port "
             "per option plus 'unsure'.", "decision", _decision_ports(("unsure",))),
    CardInfo("extract", "LLM extract", ("ok", "partial", "invalid", "error"), "The LLM fills slots from the latest message "
             "or the whole conversation; code validates them. 'invalid': a required value was given but failed validation "
             "(see vars.invalid).", "llm"),
    CardInfo("tool", "Tool", ("ok", "error", "timeout"), "Calls a tool instance; every call leaves a receipt.", "tool"),
    CardInfo("ask", "Ask (wait)", ("resume", "gave_up"), "Optionally sends a template, then waits: the journey resumes "
             "here on the customer's reply, or takes gave_up.", "state"),
    CardInfo("set", "Set", ("next",), "Assigns values: keys like state.x, vars.x or slots.x; values are expressions."),
    CardInfo("emit", "Output", ("next",), "Sends an approved template to the output collector (before, body, replace, suffix)."),
    CardInfo("end", "End", (), "Ends the journey with a status."),
]}

_CUSTOM: dict[str, CardInfo] = {}


def register_card(info: CardInfo) -> None:
    if info.type in BUILTIN_CARDS:
        raise ValueError(f"'{info.type}' is a built-in card type")
    _CUSTOM[info.type] = info


def card(card_type: str) -> CardInfo | None:
    return BUILTIN_CARDS.get(card_type) or _CUSTOM.get(card_type)


def all_cards() -> dict[str, CardInfo]:
    return {**BUILTIN_CARDS, **_CUSTOM}


def node_ports(node: Any) -> list[str]:
    """Output ports of a node (a model or a dict)."""
    d = node if isinstance(node, Mapping) else node.model_dump(by_alias=True)
    info = card(str(d.get("type")))
    if info is None:
        return []
    if info.dynamic_ports is not None:
        dyn = info.dynamic_ports(d)
        if dyn:
            return dyn
    return list(info.ports)


def catalog_json() -> dict[str, Any]:
    """For the admin UI."""
    return {t: {"label": c.label, "ports": list(c.ports), "help": c.help, "mechanism": c.mechanism}
            for t, c in all_cards().items()}
