"""MongoDB stores against the contract suites.

By default they run on mongomock behind a small async shim (the adapter's real filters, updates and unique-index
handling are exercised). Set STEWARD_TEST_MONGO_URI (e.g. mongodb://localhost:27017) to run them on a real server."""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

from steward.stores.mongo import MongoStores
from steward.testing.contracts import (
    ConfigStoreContract,
    KnowledgeStoreContract,
    LedgerStoreContract,
    PlanCacheContract,
    RecordStoreContract,
    RuntimeStoreContract,
    SessionStateContract,
)

URI = os.environ.get("STEWARD_TEST_MONGO_URI")


class _Cursor:
    def __init__(self, items: list[Any]) -> None:
        self.items = items

    def sort(self, key: Any, direction: int = 1) -> _Cursor:
        if isinstance(key, list):
            key, direction = key[0]
        self.items = sorted(self.items, key=lambda d: d.get(key), reverse=direction < 0)
        return self

    def limit(self, n: int) -> _Cursor:
        self.items = self.items[:n] if n else self.items
        return self

    async def to_list(self, length: int | None = None) -> list[Any]:
        return list(self.items)


class _Coll:
    """Async facade over a mongomock collection, mirroring PyMongo's async API surface the adapter uses."""

    def __init__(self, coll: Any) -> None:
        self.c = coll

    def find(self, *a: Any, **k: Any) -> _Cursor:
        return _Cursor(list(self.c.find(*a, **k)))

    async def aggregate(self, pipeline: Any) -> _Cursor:
        return _Cursor(list(self.c.aggregate(pipeline)))

    def __getattr__(self, name: str) -> Any:
        fn = getattr(self.c, name)

        async def call(*a: Any, **k: Any) -> Any:
            return fn(*a, **k)
        return call


class _Db:
    def __init__(self, db: Any) -> None:
        self.db = db

    def __getitem__(self, name: str) -> _Coll:
        return _Coll(self.db[name])


@pytest.fixture
async def stores():
    if URI:
        from pymongo import AsyncMongoClient

        client: Any = AsyncMongoClient(URI)
        name = f"steward_test_{uuid.uuid4().hex[:8]}"
        yield await MongoStores.create(client[name])
        await client.drop_database(name)
        await client.close()
    else:
        mongomock = pytest.importorskip("mongomock")
        yield await MongoStores.create(_Db(mongomock.MongoClient()["steward_test"]))


class TestMongoSessions(SessionStateContract):
    @pytest.fixture
    def store(self, stores):
        return stores.sessions


class TestMongoRecords(RecordStoreContract):
    @pytest.fixture
    def store(self, stores):
        return stores.records


class TestMongoRuntime(RuntimeStoreContract):
    @pytest.fixture
    def store(self, stores):
        return stores.runtime


class TestMongoConfig(ConfigStoreContract):
    @pytest.fixture
    async def store(self, stores):
        await stores.config.save_draft({"name": "v1"}, "none")
        await stores.config.publish("first")
        return stores.config


class TestMongoLedger(LedgerStoreContract):
    @pytest.fixture
    def store(self, stores):
        return stores.ledger


class TestMongoPlanCache(PlanCacheContract):
    @pytest.fixture
    def store(self, stores):
        return stores.plan_cache


class TestMongoKnowledge(KnowledgeStoreContract):
    @pytest.fixture
    def store(self, stores):
        return stores.knowledge
