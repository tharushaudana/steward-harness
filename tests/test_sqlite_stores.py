from __future__ import annotations

import pytest

from steward import Message, Steward
from steward.knowledge import MemoryKnowledgeStore
from steward.stores.sqlite import SqliteStores
from steward.testing import FakeProvider, tiny_kb
from steward.testing.contracts import (
    ConfigStoreContract,
    KnowledgeStoreContract,
    LedgerStoreContract,
    PlanCacheContract,
    RecordStoreContract,
    RuntimeStoreContract,
    SessionStateContract,
)


@pytest.fixture
def stores(tmp_path):
    return SqliteStores(tmp_path / "steward.db")


class TestSqliteSessions(SessionStateContract):
    @pytest.fixture
    def store(self, stores):
        return stores.sessions


class TestSqliteRecords(RecordStoreContract):
    @pytest.fixture
    def store(self, stores):
        return stores.records


class TestSqliteRuntime(RuntimeStoreContract):
    @pytest.fixture
    def store(self, stores):
        return stores.runtime


class TestSqliteConfig(ConfigStoreContract):
    @pytest.fixture
    async def store(self, stores):
        await stores.config.save_draft({"name": "v1"}, "none")
        await stores.config.publish("first")
        return stores.config


class TestSqliteLedger(LedgerStoreContract):
    @pytest.fixture
    def store(self, stores):
        return stores.ledger


class TestSqlitePlanCache(PlanCacheContract):
    @pytest.fixture
    def store(self, stores):
        return stores.plan_cache


class TestSqliteKnowledge(KnowledgeStoreContract):
    @pytest.fixture
    def store(self, stores):
        return stores.knowledge


class TestMemoryKnowledge(KnowledgeStoreContract):
    @pytest.fixture
    def store(self):
        return MemoryKnowledgeStore()


async def test_everything_persists_across_restarts(tmp_path):
    path = tmp_path / "bot.db"
    s1 = SqliteStores(path)
    await s1.knowledge.write_snapshot(tiny_kb())
    bot = Steward({"templates": {"c": {"text": {"en": "See you inbox!"}}}, "channels": {"comment": {"mode": "template_only", "template": "c"}}},
                  provider=FakeProvider(), stores=s1)
    r = await bot.ahandle(Message("u1", "hi", channel="comment"))
    assert r.text == "See you inbox!"
    s2 = SqliteStores(path)  # a new process
    bot2 = Steward(None, provider=FakeProvider(), stores=s2)
    assert (await bot2.config()).templates["c"].text["en"] == "See you inbox!"
    assert (await bot2.sessions.aget("u1"))["turn"] == 1
    assert (await s2.knowledge.load()) == tiny_kb()
    assert (await s2.chatlog.list())[0].reply == "See you inbox!"
