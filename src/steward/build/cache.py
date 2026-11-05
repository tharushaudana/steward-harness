"""Cached model calls for builds: every response is stored under a hash of (prompt version, pass label, inputs), so
re-running a build is free and only changed inputs cost money. A per-run budget stops runaway spend."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from steward.build.prompts import PROMPT_VERSION
from steward.errors import BudgetExceeded
from steward.providers.base import Answer, ChatMessage, Provider, Question


class BuildCache(Protocol):
    def get(self, key: str) -> Any | None: ...

    def put(self, key: str, value: Any) -> None: ...


class FileCache:
    """One JSON file per response in a directory."""

    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory)

    def get(self, key: str) -> Any | None:
        f = self.dir / f"{key}.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    def put(self, key: str, value: Any) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{key}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self.data.get(key)

    def put(self, key: str, value: Any) -> None:
        self.data[key] = value


class BuildLLM:
    def __init__(self, provider: Provider, cache: BuildCache | None = None, budget_usd: float = 0.25,
                 model: str | None = None) -> None:
        self.provider, self.cache = provider, cache or MemoryCache()
        self.budget, self.model = budget_usd, model
        self.spent = 0.0
        self.calls = self.hits = 0

    @staticmethod
    def key(parts: list[Any]) -> str:
        return hashlib.sha1(json.dumps([PROMPT_VERSION, *parts], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]

    def _check_budget(self) -> None:
        if self.spent > self.budget:
            raise BudgetExceeded(self.spent, self.budget)

    async def json(self, key_parts: list[Any], system: str, user: str, schema: Mapping[str, Any] | None, name: str,
                   max_tokens: int) -> Any:
        """Structured output (schema) or {'text': ...} for plain text; cached."""
        k = self.key(["chat", *key_parts])
        hit = self.cache.get(k)
        if hit is not None:
            self.hits += 1
            return hit
        self._check_budget()
        msgs: list[ChatMessage] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        r = await self.provider.chat(msgs, model=self.model, max_tokens=max_tokens, schema=schema, schema_name=name,
                                     purpose="build")
        self.spent += r.usage.cost_usd
        self.calls += 1
        out = r.json if schema is not None else {"text": r.text}
        if schema is not None and not isinstance(out, dict):
            raise ValueError(f"build pass '{name}' returned no JSON object")
        self.cache.put(k, out)
        return out

    async def decide(self, key_parts: list[Any], state: Mapping[str, Any], questions: Mapping[str, Question]) -> dict[str, Answer]:
        k = self.key(["decide", *key_parts])
        hit = self.cache.get(k)
        if hit is not None:
            self.hits += 1
            return hit
        self._check_budget()
        r = await self.provider.decide(state, questions, purpose="build")
        self.spent += r.usage.cost_usd
        self.calls += 1
        self.cache.put(k, r.answers)
        return r.answers
