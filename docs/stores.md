# Stores

All persistence goes through small async protocols (`steward/stores/base.py`), bundled in a `Stores` object:

| store | holds | key operations |
|---|---|---|
| `SessionStore` | Per-conversation state: slots, flags, the pending ask, history | `load` returns (state, version); `compare_and_set(session, state, expected_version)`; `delete` |
| `RecordStore` | Leads, handovers, anything a tool appends | `append(NewRecord)`, idempotent by key with an atomic merge; `list(type, include_test=)`; `set_status` |
| `RuntimeStore` | Live switches shared by all sessions (`agents_online`) | `get`, `set` |
| `ConfigStore` | The draft, the published config and every published version | `save_draft(config, base_rev)` (raises `RevisionConflict` on a stale rev); `publish`; `history`; `get_version` |
| `LedgerStore` | The cost of every model call | `record`, `total`, `summary` |
| `PlanCache` | Retrieval plans for repeated questions | `get` / `put` by (signature, version) |
| `ChatLog` | Optional full turn logs for debugging | `append`, `list(status=)` |
| `KnowledgeStore` | The knowledge base | `version()`, `load()`; optional `search()`; `KnowledgeWriter` for builds |

## Adapters

```python
from steward.stores.memory import memory_stores             # tests, demos
from steward.stores.sqlite import SqliteStores              # one file, one process (WAL)
from steward.stores.mongo import MongoStores                # pip install "steward-harness[mongo]"
from steward.stores.firestore import FirestoreStores        # pip install "steward-harness[firestore]"

stores = SqliteStores("assistant.db")
stores = await MongoStores.create(AsyncMongoClient(uri)["mydb"], prefix="steward_")   # creates the indexes
stores = FirestoreStores(firestore.AsyncClient(project="my-project"), prefix="steward_")
```

How each adapter implements compare-and-set and the idempotent merge:
- **SQLite:** a version column; `BEGIN IMMEDIATE` transactions; FTS5 for knowledge search.
- **MongoDB:** conditional updates on a version field; a unique sparse index on the record key; the knowledge base is written as a new generation and then switched over atomically.
- **Firestore:** precondition writes (`create()`, then `update()` with the last update time). This avoids the lock contention of transactions under concurrent messages. Record ids are hashes of the key, and records are merged in a transaction.

You can mix adapters, for example sessions in Firestore and knowledge in a JSON file:

```python
stores = FirestoreStores(client)
bot = Steward(config, provider=provider, stores=stores, knowledge=MemoryKnowledgeStore.from_file("kb.json"))
```

## Writing an adapter

Implement the protocols you need, put them in a `Stores(...)`, and run the contract suites against them:

```python
# tests/test_my_store.py
import pytest
from steward.testing.contracts import RecordStoreContract, SessionStateContract

class TestMySessions(SessionStateContract):
    @pytest.fixture
    async def store(self):
        return MySessionStore(...)

class TestMyRecords(RecordStoreContract):
    @pytest.fixture
    async def store(self):
        return MyRecordStore(...)
```

The suites check:
- compare-and-set under concurrency;
- idempotent merges;
- that test records stay hidden;
- revision conflicts on drafts;
- version history;
- ledger totals;
- that knowledge round-trips with its supersede overrides.

The built-in adapters pass the suites on memory, SQLite, MongoDB (mongomock, or a real server via `STEWARD_TEST_MONGO_URI`) and the Firestore emulator (`FIRESTORE_EMULATOR_HOST`).

For a knowledge base kept in your own database, you only need `version()` and `load()`. See `examples/custom_knowledge_store.py`.
