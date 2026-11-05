"""Metered: wraps any provider with a cost ledger and a hard spend cap.

Before each call a pessimistic estimate is reserved (under a lock, so concurrent calls cannot overshoot together);
if ledger total + reserved + estimate > cap, BudgetExceeded is raised and nothing is sent. After the call the real
cost is recorded in the LedgerStore and the reservation released. Failed calls are recorded with ok=False.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from steward.errors import BudgetExceeded
from steward.providers.base import ChatMessage, ChatResult, Decisions, Provider, Question, Usage
from steward.stores.base import LedgerEntry, LedgerStore


class Metered:
    def __init__(self, inner: Provider, ledger: LedgerStore, cap_usd: float | None = None) -> None:
        self.inner, self.ledger, self.cap_usd = inner, ledger, cap_usd
        self._lock = asyncio.Lock()
        self._reserved = 0.0
        self._spent: float | None = None  # cached ledger total (this process adds to it)

    async def spent(self) -> float:
        if self._spent is None:
            self._spent = await self.ledger.total()
        return self._spent

    async def _reserve(self, est: float) -> None:
        async with self._lock:
            spent = await self.spent()
            if self.cap_usd is not None and spent + self._reserved + est > self.cap_usd:
                raise BudgetExceeded(spent + self._reserved, self.cap_usd, est)
            self._reserved += est

    async def _settle(self, est: float, purpose: str, usage: Usage | None, turn_id: str | None, error: str | None) -> None:
        async with self._lock:
            self._reserved = max(0.0, self._reserved - est)
            cost = usage.cost_usd if usage else 0.0
            self._spent = (await self.spent()) + cost
        await self.ledger.record(LedgerEntry(
            purpose=purpose or "other", model=usage.model if usage else "", tokens_in=usage.tokens_in if usage else 0,
            tokens_out=usage.tokens_out if usage else 0, cost_usd=cost, ts=time.time(), turn_id=turn_id,
            latency_ms=usage.latency_ms if usage else 0.0, ok=error is None, error=error,
            cost_source=usage.cost_source if usage else "api"))

    def estimate(self, kind: Literal["chat", "decide"], payload_chars: int, max_out: int = 0,
                 model: str | None = None) -> float:
        return self.inner.estimate(kind, payload_chars, max_out, model)

    async def chat(self, messages: Sequence[ChatMessage], *, model: str | None = None, max_tokens: int = 600,
                   temperature: float = 0.0, schema: Mapping[str, Any] | None = None, schema_name: str = "output",
                   purpose: str = "", turn_id: str | None = None) -> ChatResult:
        est = self.inner.estimate("chat", sum(len(m["content"]) for m in messages) + len(json.dumps(schema or {})),
                                  max_tokens, model)
        await self._reserve(est)
        try:
            r = await self.inner.chat(messages, model=model, max_tokens=max_tokens, temperature=temperature,
                                      schema=schema, schema_name=schema_name, purpose=purpose)
        except BaseException as e:
            await self._settle(est, purpose, None, turn_id, f"{type(e).__name__}: {e}"[:300])
            raise
        await self._settle(est, purpose, r.usage, turn_id, None)
        return r

    async def decide(self, state: Mapping[str, Any], questions: Mapping[str, Question], *,
                     model: str | None = None, purpose: str = "", turn_id: str | None = None) -> Decisions:
        est = self.inner.estimate("decide", len(json.dumps(state, ensure_ascii=False)) + len(json.dumps(questions)), 0, model)
        await self._reserve(est)
        try:
            r = await self.inner.decide(state, questions, model=model, purpose=purpose)
        except BaseException as e:
            await self._settle(est, purpose, None, turn_id, f"{type(e).__name__}: {e}"[:300])
            raise
        await self._settle(est, purpose, r.usage, turn_id, None)
        return r
