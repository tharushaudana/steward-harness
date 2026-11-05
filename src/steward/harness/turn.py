"""Per-turn context: session state, the expression environment, and the shared runtime services."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

from steward.config.models import Config
from steward.engine.common import Run
from steward.events import Outbox
from steward.harness.expr import EvalContext, ExprEngine
from steward.harness.slots import find_contact, number_like, validate_value
from steward.tools.registry import ToolRegistry
from steward.types import Message, Receipt, TurnInfo

HISTORY_TURNS = 6


def new_state() -> dict[str, Any]:
    """slots: what we know about the customer; flags: journey state (state.x in expressions); pending: a waiting Ask;
    history: the last turns {q, a}; asks_made: how often the contact was requested."""
    return {"slots": {}, "flags": {}, "pending": None, "turn": 0, "asks_made": 0, "history": []}


def contact_validator(cfg: Config) -> str | None:
    slot = cfg.slots.get(cfg.settings.contact_slot)
    return slot.validator if slot else None


def make_env(cfg: Config, state: dict[str, Any], msg: Message, lang: str) -> dict[str, Any]:
    contact = find_contact(cfg.validators, contact_validator(cfg), msg.text)
    turn = {"message": msg.text, "standalone": msg.text, "channel": msg.channel, "lang": lang, "session": msg.session,
            "date": _dt.date.today().isoformat(), "contact_in_message": contact,
            "phone_in_message": contact,  # alias kept for configs written against the prototype
            "number_in_message": number_like(msg.text), "history": state.get("history", []),
            "is_test": msg.is_test, "turn_no": state.get("turn", 0), "metadata": dict(msg.metadata)}
    return {"slots": state["slots"], "state": state["flags"], "vars": {}, "trigger": {}, "contacts": cfg.contacts, "turn": turn}


@dataclass
class Outcome:
    journey: str
    part: int | None = None
    status: str = "running"
    emits: list[dict[str, Any]] = field(default_factory=list)
    asks: list[dict[str, Any]] = field(default_factory=list)
    receipts: list[Receipt] = field(default_factory=list)
    path: list[dict[str, Any]] = field(default_factory=list)
    pending: dict[str, Any] | None = None

    def summary(self) -> dict[str, Any]:
        return {"journey": self.journey, "part": self.part, "status": self.status, "path": self.path,
                "receipts": [r.__dict__ for r in self.receipts], "emits": self.emits, "asks": self.asks}


@dataclass
class TurnContext:
    cfg: Config
    msg: Message
    state: dict[str, Any]
    env: dict[str, Any]
    lang: str
    run: Run
    tools: ToolRegistry
    expr: ExprEngine
    outbox: Outbox
    tools_mode: str = "live"
    outcomes: list[Outcome] = field(default_factory=list)

    def eval_ctx(self) -> EvalContext:
        return EvalContext(self.env, validate=lambda v, name: validate_value(self.cfg.validators, v, name))

    def turn_info(self) -> TurnInfo:
        t = self.env["turn"]
        return TurnInfo(session=self.msg.session, channel=self.msg.channel, message=self.msg.text,
                        standalone=t.get("standalone", ""), lang=self.lang, turn_no=self.state.get("turn", 0),
                        date=t["date"], is_test=self.msg.is_test, metadata=self.msg.metadata)

    @property
    def receipts(self) -> list[Receipt]:
        return [r for o in self.outcomes for r in o.receipts]
