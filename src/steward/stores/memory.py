"""In-memory stores: the default for tests, demos and quick starts. Not persistent; safe for concurrent use
within one event loop."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
import uuid
from collections import OrderedDict
from typing import Any

from steward.errors import RevisionConflict
from steward.stores.base import (
    AppendResult,
    ConfigDoc,
    ConfigVersion,
    LedgerEntry,
    LedgerSummary,
    NewRecord,
    Record,
    Stores,
    TurnLog,
)


def config_rev(config: dict[str, Any] | None) -> str:
    """Content hash of a config (stable key order)."""
    if config is None:
        return "none"
    return hashlib.sha1(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


class MemorySessionStore:
    def __init__(self) -> None:
        self._data: dict[str, tuple[dict[str, Any], int]] = {}
        self._lock = asyncio.Lock()

    async def load(self, session: str) -> tuple[dict[str, Any] | None, int]:
        if session not in self._data:
            return None, 0
        state, version = self._data[session]
        return copy.deepcopy(state), version

    async def compare_and_set(self, session: str, state: dict[str, Any], expected_version: int) -> bool:
        async with self._lock:
            current = self._data.get(session, (None, 0))[1]
            if current != expected_version:
                return False
            self._data[session] = (copy.deepcopy(state), expected_version + 1)
            return True

    async def delete(self, session: str) -> None:
        self._data.pop(session, None)


class MemoryRecordStore:
    def __init__(self) -> None:
        self._rows: dict[str, Record] = {}
        self._by_key: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def append(self, record: NewRecord) -> AppendResult:
        async with self._lock:
            if record.key is not None and record.key in self._by_key:
                rid = self._by_key[record.key]
                old = self._rows[rid]
                merged = {**old.data, **{k: v for k, v in record.data.items() if v not in (None, "")}}
                enriched = merged != old.data
                if enriched:
                    self._rows[rid] = Record(old.id, old.type, merged, old.session, old.key, old.ts, old.status, old.is_test)
                return AppendResult(rid, duplicate=True, enriched=enriched)
            rid = uuid.uuid4().hex[:12]
            self._rows[rid] = Record(rid, record.type, dict(record.data), record.session, record.key, time.time(),
                                     "open", record.is_test)
            if record.key is not None:
                self._by_key[record.key] = rid
            return AppendResult(rid)

    async def list(self, type: str | None = None, limit: int = 200, include_test: bool = False) -> list[Record]:
        rows = [r for r in self._rows.values() if (type is None or r.type == type) and (include_test or not r.is_test)]
        return sorted(rows, key=lambda r: -r.ts)[:limit]

    async def set_status(self, record_id: str, status: str) -> None:
        r = self._rows[record_id]
        self._rows[record_id] = Record(r.id, r.type, r.data, r.session, r.key, r.ts, status, r.is_test)


class MemoryRuntimeStore:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = dict(initial or {})

    async def get(self, key: str, default: Any = None) -> Any:
        return copy.deepcopy(self._data.get(key, default))

    async def set(self, key: str, value: Any) -> None:
        self._data[key] = copy.deepcopy(value)


class MemoryConfigStore:
    def __init__(self, published: dict[str, Any] | None = None) -> None:
        self._published: ConfigDoc | None = (
            ConfigDoc(copy.deepcopy(published), config_rev(published), int(published.get("version", 1) or 1))
            if published is not None else None)
        self._draft: ConfigDoc | None = None
        self._history: list[tuple[ConfigVersion, ConfigDoc]] = []
        self._lock = asyncio.Lock()

    async def published(self) -> ConfigDoc | None:
        return copy.deepcopy(self._published)

    async def draft(self) -> ConfigDoc | None:
        return copy.deepcopy(self._draft)

    async def save_draft(self, config: dict[str, Any], base_rev: str) -> ConfigDoc:
        async with self._lock:
            current = self._draft or self._published
            if (current.rev if current else "none") != base_rev:
                raise RevisionConflict("the configuration changed since it was loaded: reload it, then re-apply your edits")
            self._draft = ConfigDoc(copy.deepcopy(config), config_rev(config),
                                    self._published.version if self._published else 0)
            return copy.deepcopy(self._draft)

    async def discard_draft(self) -> None:
        self._draft = None

    async def publish(self, note: str = "") -> ConfigDoc:
        async with self._lock:
            if self._draft is None:
                raise ValueError("there is no draft to publish")
            if self._published is not None:
                v = ConfigVersion(uuid.uuid4().hex[:12], self._published.version, self._published.rev, time.time(),
                                  note or "replaced by publish")
                self._history.append((v, self._published))
            version = (self._published.version if self._published else 0) + 1
            cfg = dict(self._draft.config, version=version)
            self._published = ConfigDoc(cfg, config_rev(cfg), version)
            self._draft = None
            return copy.deepcopy(self._published)

    async def history(self, limit: int = 30) -> list[ConfigVersion]:
        return [v for v, _ in reversed(self._history)][:limit]

    async def get_version(self, version_id: str) -> ConfigDoc | None:
        for v, doc in self._history:
            if v.id == version_id:
                return copy.deepcopy(doc)
        return None


class MemoryLedgerStore:
    def __init__(self) -> None:
        self.entries: list[LedgerEntry] = []

    async def record(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)

    async def total(self) -> float:
        return round(sum(e.cost_usd for e in self.entries), 8)

    async def summary(self) -> LedgerSummary:
        by_purpose: dict[str, float] = {}
        by_model: dict[str, float] = {}
        for e in self.entries:
            by_purpose[e.purpose] = by_purpose.get(e.purpose, 0.0) + e.cost_usd
            by_model[e.model] = by_model.get(e.model, 0.0) + e.cost_usd
        return LedgerSummary(await self.total(), len(self.entries), by_purpose, by_model)


class MemoryPlanCache:
    def __init__(self, max_items: int = 2000) -> None:
        self._data: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
        self._max = max_items

    async def get(self, signature: str, version: str) -> dict[str, Any] | None:
        key = (signature, version)
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return copy.deepcopy(self._data[key])

    async def put(self, signature: str, version: str, plan: dict[str, Any]) -> None:
        self._data[(signature, version)] = copy.deepcopy(plan)
        self._data.move_to_end((signature, version))
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    async def clear(self) -> None:
        self._data.clear()


class MemoryChatLog:
    def __init__(self, max_items: int = 5000) -> None:
        self._rows: list[TurnLog] = []
        self._max = max_items

    async def append(self, turn: TurnLog) -> None:
        self._rows.append(turn)
        del self._rows[:-self._max]

    async def list(self, limit: int = 50, status: str | None = None) -> list[TurnLog]:
        rows = [t for t in reversed(self._rows) if status is None or t.status == status]
        return rows[:limit]


def memory_stores(config: dict[str, Any] | None = None, knowledge: Any = None,
                  runtime: dict[str, Any] | None = None) -> Stores:
    """Every store in memory. `config` becomes the published configuration; `knowledge` is a KnowledgeStore."""
    return Stores(sessions=MemorySessionStore(), records=MemoryRecordStore(), runtime=MemoryRuntimeStore(runtime),
                  config=MemoryConfigStore(config), ledger=MemoryLedgerStore(), plan_cache=MemoryPlanCache(),
                  chatlog=MemoryChatLog(), knowledge=knowledge)
