"""Firestore stores against the contract suites. Needs the Firestore emulator:

    firebase emulators:start --only firestore --project demo-steward
    FIRESTORE_EMULATOR_HOST=127.0.0.1:8686 uv run pytest tests/test_firestore_stores.py

Skipped when FIRESTORE_EMULATOR_HOST is not set."""

from __future__ import annotations

import os
import uuid

import pytest

from steward.testing.contracts import (
    ConfigStoreContract,
    KnowledgeStoreContract,
    LedgerStoreContract,
    PlanCacheContract,
    RecordStoreContract,
    RuntimeStoreContract,
    SessionStateContract,
)

pytestmark = pytest.mark.skipif(not os.environ.get("FIRESTORE_EMULATOR_HOST"),
                                reason="set FIRESTORE_EMULATOR_HOST to run against the Firestore emulator")


@pytest.fixture
async def stores():
    firestore = pytest.importorskip("google.cloud.firestore")
    from steward.stores.firestore import FirestoreStores

    client = firestore.AsyncClient(project="demo-steward")
    yield FirestoreStores(client, prefix=f"t{uuid.uuid4().hex[:8]}_")
    client.close()


class TestFirestoreSessions(SessionStateContract):
    @pytest.fixture
    def store(self, stores):
        return stores.sessions


class TestFirestoreRecords(RecordStoreContract):
    @pytest.fixture
    def store(self, stores):
        return stores.records


class TestFirestoreRuntime(RuntimeStoreContract):
    @pytest.fixture
    def store(self, stores):
        return stores.runtime


class TestFirestoreConfig(ConfigStoreContract):
    @pytest.fixture
    async def store(self, stores):
        await stores.config.save_draft({"name": "v1"}, "none")
        await stores.config.publish("first")
        return stores.config


class TestFirestoreLedger(LedgerStoreContract):
    @pytest.fixture
    def store(self, stores):
        return stores.ledger


class TestFirestorePlanCache(PlanCacheContract):
    @pytest.fixture
    def store(self, stores):
        return stores.plan_cache


class TestFirestoreKnowledge(KnowledgeStoreContract):
    @pytest.fixture
    def store(self, stores):
        return stores.knowledge
