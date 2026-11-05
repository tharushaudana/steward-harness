from __future__ import annotations

import pytest

from steward.stores.memory import (
    MemoryConfigStore,
    MemoryLedgerStore,
    MemoryPlanCache,
    MemoryRecordStore,
    MemoryRuntimeStore,
    MemorySessionStore,
    memory_stores,
)
from steward.stores.sync import wrap
from steward.testing.contracts import (
    ConfigStoreContract,
    LedgerStoreContract,
    PlanCacheContract,
    RecordStoreContract,
    RuntimeStoreContract,
    SessionStateContract,
)


class TestMemorySessions(SessionStateContract):
    @pytest.fixture
    def store(self):
        return MemorySessionStore()


class TestMemoryRecords(RecordStoreContract):
    @pytest.fixture
    def store(self):
        return MemoryRecordStore()


class TestMemoryRuntime(RuntimeStoreContract):
    @pytest.fixture
    def store(self):
        return MemoryRuntimeStore()


class TestMemoryConfig(ConfigStoreContract):
    @pytest.fixture
    def store(self):
        return MemoryConfigStore({"name": "v1", "version": 1})


class TestMemoryLedger(LedgerStoreContract):
    @pytest.fixture
    def store(self):
        return MemoryLedgerStore()


class TestMemoryPlanCache(PlanCacheContract):
    @pytest.fixture
    def store(self):
        return MemoryPlanCache()


class _SyncRuntime:
    """A plain synchronous store, adapted with wrap()."""

    def __init__(self):
        self.d = {}

    def get(self, key, default=None):
        return self.d.get(key, default)

    def set(self, key, value):
        self.d[key] = value


class TestWrappedSyncRuntime(RuntimeStoreContract):
    @pytest.fixture
    def store(self):
        return wrap(_SyncRuntime())


def test_memory_stores_bundle():
    s = memory_stores(config={"name": "x"}, runtime={"agents_online": True})
    assert s.chatlog is not None and s.knowledge is None


async def test_plan_cache_is_bounded():
    c = MemoryPlanCache(max_items=2)
    for i in range(3):
        await c.put(f"q{i}", "v", {"i": i})
    assert await c.get("q0", "v") is None and await c.get("q2", "v") == {"i": 2}
