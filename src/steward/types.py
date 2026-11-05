"""Public value types: what a host sends in (Message), what it gets back (Reply), and what tools see (ToolCall/ToolResult)."""

from __future__ import annotations

import datetime as _dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from steward.events import Trace

Status = Literal[
    "answered",  # a knowledge answer (possibly with journey/template parts)
    "needs_input",  # a journey is waiting for the customer (an Ask card)
    "insufficient",  # the knowledge sources do not contain the answer
    "chitchat",  # greeting / small talk
    "meta",  # a question about the assistant or the conversation
    "template",  # a template-only reply (e.g. a public comment channel)
    "handover",  # a handover to a human was just made
    "paused",  # the session is with a human; the assistant only sends the holding message
    "error",  # a runtime failure (provider down, ...); the reply holds a safe fallback text
    "budget_exceeded",  # the spend cap was reached
]


@dataclass(frozen=True)
class Message:
    """One customer message.

    session: a stable id for the conversation (your chat/thread id). channel: any name your config's
    `channels` section knows (e.g. "dm", "comment", "whatsapp"). is_test: test conversations never touch real
    records (their idempotency keys are namespaced) and are hidden from record listings by default.
    """

    session: str
    text: str
    channel: str = "dm"
    is_test: bool = False
    lang: str | None = None  # force a reply language; normally detected from the text
    metadata: Mapping[str, Any] = field(default_factory=dict)  # free host data, visible to tools as call.turn.metadata


@dataclass(frozen=True)
class TurnInfo:
    """What tools, expressions and templates know about the current turn."""

    session: str
    channel: str
    message: str
    standalone: str = ""  # the message rewritten to be understandable without the conversation
    lang: str = "en"
    turn_no: int = 0
    date: str = field(default_factory=lambda: _dt.date.today().isoformat())
    is_test: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCall:
    """Everything a tool callback receives. `args` are already rendered from the config's tool instance
    (e.g. {"phone": "{{slots.phone}}"} -> {"phone": "+15550100"})."""

    tool_id: str  # the config tool instance id (what journeys reference)
    impl: str  # the registered implementation id (defaults to tool_id)
    args: Mapping[str, Any]
    turn: TurnInfo
    slots: Mapping[str, Any] = field(default_factory=dict)
    vars: Mapping[str, Any] = field(default_factory=dict)
    state: Mapping[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None  # rendered and (for test turns) namespaced by the runtime
    mode: Literal["live", "mock"] = "live"
    timeout_s: float = 10.0
    settings: Mapping[str, Any] = field(default_factory=dict)  # the active config's settings (e.g. allow_hosts)


@dataclass
class ToolResult:
    """What a tool returns. A plain dict {"ok": ..., "data": ...} is accepted too; raising an exception means ok=False.

    receipt: what the assistant may claim happened (e.g. {"ref": "CRM-123"}). Templates that claim an action
    (`requires_receipt`) and the "no claims without a receipt" check only pass when a tool returned ok.
    """

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duplicate: bool = False
    retryable: bool = False
    receipt: dict[str, Any] | None = None

    @classmethod
    def success(cls, data: Mapping[str, Any] | None = None, receipt: Mapping[str, Any] | None = None, **kw: Any) -> ToolResult:
        return cls(ok=True, data=dict(data or {}), receipt=dict(receipt) if receipt is not None else None, **kw)

    @classmethod
    def failure(cls, error: str, retryable: bool = False, data: Mapping[str, Any] | None = None) -> ToolResult:
        return cls(ok=False, error=error, retryable=retryable, data=dict(data or {}))

    @classmethod
    def coerce(cls, value: Any) -> ToolResult:
        """Accept a ToolResult, a dict ({"ok": bool, "data": ..., "error": ...}) or None (= ok, no data)."""
        if isinstance(value, ToolResult):
            return value
        if value is None:
            return cls(ok=True)
        if isinstance(value, Mapping):
            known = {k: value[k] for k in ("ok", "data", "error", "duplicate", "retryable", "receipt") if k in value}
            known.setdefault("ok", True)
            known["data"] = dict(known.get("data") or {})
            return cls(**known)
        raise TypeError(f"a tool must return ToolResult, dict or None, got {type(value).__name__}")


# Short aliases used in docs: ToolResult.ok(...) reads well but would shadow the `ok` field, so they are functions.
def ok(data: Mapping[str, Any] | None = None, receipt: Mapping[str, Any] | None = None) -> ToolResult:
    return ToolResult.success(data, receipt)


def fail(error: str, retryable: bool = False) -> ToolResult:
    return ToolResult.failure(error, retryable)


@dataclass(frozen=True)
class Receipt:
    """One tool call as recorded in the turn: the evidence for `requires_receipt` and claim checks."""

    tool: str
    impl: str
    ok: bool
    duplicate: bool = False
    key: str | None = None
    receipt: Mapping[str, Any] | None = None
    error: str | None = None


@dataclass
class Reply:
    """The assistant's answer to one Message, with everything needed to audit it."""

    text: str
    status: Status
    session: str
    trace: Trace = field(default_factory=Trace)
    standalone: str | None = None  # how the message was understood, when it differs from the text
    state: dict[str, Any] = field(default_factory=dict)  # slots, flags, pending ask after this turn
    journeys: list[dict[str, Any]] = field(default_factory=list)  # outcomes: journey, status, path, receipts, emits
    policies: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)  # sections and values used by the knowledge answer
