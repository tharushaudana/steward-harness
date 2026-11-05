"""Cassette: record provider responses once, replay them for free (evals, regression tests, demos).

    provider = Cassette(OpenRouter.from_env(), "tests/cassettes/scenario1.json", mode="auto")

Modes:
  record  always call the inner provider and store the result
  replay  only use stored results; a miss raises (strict)
  auto    replay when stored, else call and record

The key is a hash of the call (kind, model, purpose, messages or state+questions, schema), so any change to a
prompt or question is a miss and gets re-recorded in auto mode. Replayed results report cost 0 ("cache").
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from steward.errors import ProviderError
from steward.providers.base import ChatMessage, ChatResult, Decisions, Provider, Question, Usage

Mode = Literal["record", "replay", "auto"]


class CassetteMiss(ProviderError):
    """A replay-mode cassette has no recording for this call."""


class Cassette:
    def __init__(self, inner: Provider | None, path: str | Path, mode: Mode = "auto") -> None:
        if inner is None and mode != "replay":
            raise ValueError("a cassette without an inner provider can only replay")
        self.inner, self.path, self.mode = inner, Path(path), mode
        self._data: dict[str, Any] = (json.loads(self.path.read_text(encoding="utf-8"))
                                      if self.path.exists() else {})
        self._lock = asyncio.Lock()
        self.hits = self.misses = 0

    @staticmethod
    def key(kind: str, payload: Any) -> str:
        return hashlib.sha256(json.dumps([kind, payload], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]

    def estimate(self, kind: Literal["chat", "decide"], payload_chars: int, max_out: int = 0,
                 model: str | None = None) -> float:
        return self.inner.estimate(kind, payload_chars, max_out, model) if self.inner and self.mode != "replay" else 0.0

    async def _save(self) -> None:
        async with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def _lookup(self, k: str) -> Any:
        if self.mode != "record" and k in self._data:
            self.hits += 1
            return self._data[k]
        if self.mode == "replay":
            raise CassetteMiss(f"cassette {self.path.name}: no recording for this call (key {k}); "
                               "re-record with mode='auto' or 'record'")
        self.misses += 1
        return None

    async def chat(self, messages: Sequence[ChatMessage], *, model: str | None = None, max_tokens: int = 600,
                   temperature: float = 0.0, schema: Mapping[str, Any] | None = None, schema_name: str = "output",
                   purpose: str = "") -> ChatResult:
        k = self.key("chat", {"model": model, "purpose": purpose, "messages": list(messages), "schema": schema})
        hit = self._lookup(k)
        if hit is not None:
            return ChatResult(text=hit["text"], json=hit.get("json"), usage=Usage(model=hit.get("model", ""), cost_source="cache"))
        assert self.inner is not None
        r = await self.inner.chat(messages, model=model, max_tokens=max_tokens, temperature=temperature, schema=schema,
                                  schema_name=schema_name, purpose=purpose)
        self._data[k] = {"text": r.text, "json": r.json, "model": r.usage.model, "usage": asdict(r.usage)}
        await self._save()
        return r

    async def decide(self, state: Mapping[str, Any], questions: Mapping[str, Question], *,
                     model: str | None = None, purpose: str = "") -> Decisions:
        k = self.key("decide", {"model": model, "purpose": purpose, "state": dict(state), "questions": dict(questions)})
        hit = self._lookup(k)
        if hit is not None:
            return Decisions(answers=hit["answers"], usage=Usage(model=hit.get("model", ""), cost_source="cache"))
        assert self.inner is not None
        r = await self.inner.decide(state, questions, model=model, purpose=purpose)
        self._data[k] = {"answers": r.answers, "model": r.usage.model, "usage": asdict(r.usage)}
        await self._save()
        return r
