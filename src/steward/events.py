"""Tracing (one Step per pipeline/engine action) and the event bus for host hooks.

Every turn produces a Trace: which mechanism did what (code, decision model, LLM, tool, store, cache), with tokens,
cost and latency, so any answer can be audited. Host hooks (`bot.on("handover", fn)`) are delivered through an
outbox after the turn's state is committed; a failing hook is logged and never changes the reply.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

log = logging.getLogger("steward.events")

Mechanism = Literal["code", "decision", "llm", "tool", "state", "cache"]


@dataclass
class Step:
    name: str
    mechanism: Mechanism
    input: Any = None
    output: Any = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    part: int | None = None  # message part index, for multi-part messages


@dataclass(frozen=True)
class Totals:
    steps: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: float
    llm_calls: int
    decision_calls: int


@dataclass
class Trace:
    steps: list[Step] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)

    def add(self, name: str, mechanism: Mechanism, input: Any = None, output: Any = None, *, t0: float | None = None,
            tokens_in: int = 0, tokens_out: int = 0, cost_usd: float = 0.0, part: int | None = None) -> Step:
        step = Step(name, mechanism, input, output, tokens_in, tokens_out, cost_usd,
                    round((time.perf_counter() - t0) * 1000, 1) if t0 is not None else 0.0, part)
        self.steps.append(step)
        return step

    @property
    def totals(self) -> Totals:
        s = self.steps
        return Totals(len(s), sum(x.tokens_in for x in s), sum(x.tokens_out for x in s),
                      round(sum(x.cost_usd for x in s), 8), round((time.perf_counter() - self.started) * 1000, 1),
                      sum(x.mechanism == "llm" for x in s), sum(x.mechanism == "decision" for x in s))

    def to_dicts(self) -> list[dict[str, Any]]:
        return [asdict(x) for x in self.steps]

    def to_table(self, width: int = 60) -> str:
        rows = [f"{'step':28} {'mech':9} {'tok in/out':>12} {'cost $':>10} {'ms':>8}  output"]
        for x in self.steps:
            out = str(x.output).replace("\n", " ")
            rows.append(f"{x.name[:28]:28} {x.mechanism:9} {f'{x.tokens_in}/{x.tokens_out}':>12} {x.cost_usd:>10.6f} "
                        f"{x.latency_ms:>8.0f}  {out[:width]}")
        t = self.totals
        rows.append(f"{'TOTAL':28} {'':9} {f'{t.tokens_in}/{t.tokens_out}':>12} {t.cost_usd:>10.6f} {t.latency_ms:>8.0f}")
        return "\n".join(rows)


Handler = Callable[[str, dict[str, Any]], Any] | Callable[[str, dict[str, Any]], Awaitable[Any]]

EVENTS = ("turn_end", "handover", "release", "record", "tool_call", "journey_end", "ask_abandoned", "error",
          "budget_exceeded", "config_published")


class EventBus:
    """bot.on(event, fn): fn(event, payload) may be sync or async. Use "*" to receive every event."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def on(self, event: str, fn: Handler | None = None) -> Any:
        if event != "*" and event not in EVENTS:
            raise ValueError(f"unknown event '{event}'. Known events: {', '.join(EVENTS)} (or '*')")

        def register(f: Handler) -> Handler:
            self._handlers[event].append(f)
            return f

        return register(fn) if fn is not None else register

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        for fn in [*self._handlers.get(event, []), *self._handlers.get("*", [])]:
            try:
                res = fn(event, payload)
                if inspect.isawaitable(res):
                    await res
            except Exception:  # a host hook must never break a conversation
                log.exception("event handler for %r failed", event)


class Outbox:
    """Events collected during a turn, delivered only after the turn's state was saved."""

    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any]]] = []

    def add(self, event: str, payload: dict[str, Any]) -> None:
        self.items.append((event, payload))

    async def flush(self, bus: EventBus) -> None:
        items, self.items = self.items, []
        await asyncio.gather(*(bus.emit(e, p) for e, p in items))
