"""MongoDB stores (pip install "steward-harness[mongo]"), using PyMongo's async API.

    from pymongo import AsyncMongoClient
    stores = await MongoStores.create(AsyncMongoClient(uri)["my_app"], prefix="steward_")

Guarantees: session state uses a version field (compare-and-set via a filtered update; inserts rely on the _id
uniqueness); records use a unique index on `key` (a duplicate append merges non-empty fields with $set). No
multi-document transactions are needed, so a standalone server works. The knowledge base is stored one document per
item and replaced generation by generation; search uses the engine's built-in BM25 (no Atlas Search required).
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

from steward.errors import RevisionConflict
from steward.knowledge.model import (
    KBMeta,
    KnowledgeSnapshot,
    SupersedeMark,
    SupersedeOverride,
    _to_plain,
    snapshot_from_items,
    snapshot_items,
)
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
from steward.stores.memory import config_rev

try:
    from pymongo.errors import DuplicateKeyError
except ImportError:  # pragma: no cover
    raise ImportError('MongoDB support needs pymongo: pip install "steward-harness[mongo]"') from None


async def _to_list(cursor: Any) -> list[Any]:
    return await cursor.to_list(length=None)


class MongoSessionStore:
    def __init__(self, coll: Any) -> None:
        self.c = coll

    async def load(self, session: str) -> tuple[dict[str, Any] | None, int]:
        d = await self.c.find_one({"_id": session})
        return (d["state"], d["version"]) if d else (None, 0)

    async def compare_and_set(self, session: str, state: dict[str, Any], expected_version: int) -> bool:
        if expected_version == 0:
            try:
                await self.c.insert_one({"_id": session, "state": state, "version": 1, "updated": time.time()})
                return True
            except DuplicateKeyError:
                return False
        r = await self.c.update_one({"_id": session, "version": expected_version},
                                    {"$set": {"state": state, "updated": time.time()}, "$inc": {"version": 1}})
        return r.matched_count == 1

    async def delete(self, session: str) -> None:
        await self.c.delete_one({"_id": session})


class MongoRecordStore:
    def __init__(self, coll: Any) -> None:
        self.c = coll

    async def append(self, record: NewRecord) -> AppendResult:
        doc = {"_id": uuid.uuid4().hex[:12], "type": record.type, "data": record.data, "session": record.session,
               "ts": time.time(), "status": "open", "is_test": record.is_test}
        if record.key is not None:
            doc["key"] = record.key
        try:
            await self.c.insert_one(doc)
            return AppendResult(doc["_id"])
        except DuplicateKeyError:
            old = await self.c.find_one({"key": record.key})
            if old is None:  # pragma: no cover - deleted in between
                return await self.append(record)
            new = {k: v for k, v in record.data.items() if v not in (None, "")}
            enriched = any(old["data"].get(k) != v for k, v in new.items())
            if new:
                await self.c.update_one({"_id": old["_id"]}, {"$set": {f"data.{k}": v for k, v in new.items()}})
            return AppendResult(old["_id"], duplicate=True, enriched=enriched)

    async def list(self, type: str | None = None, limit: int = 200, include_test: bool = False) -> list[Record]:
        q: dict[str, Any] = {}
        if type:
            q["type"] = type
        if not include_test:
            q["is_test"] = False
        docs = await _to_list(self.c.find(q).sort("ts", -1).limit(limit))
        return [Record(d["_id"], d["type"], d["data"], d["session"], d.get("key"), d["ts"], d.get("status", "open"),
                       bool(d.get("is_test"))) for d in docs]

    async def set_status(self, record_id: str, status: str) -> None:
        await self.c.update_one({"_id": record_id}, {"$set": {"status": status}})


class MongoRuntimeStore:
    def __init__(self, coll: Any) -> None:
        self.c = coll

    async def get(self, key: str, default: Any = None) -> Any:
        d = await self.c.find_one({"_id": key})
        return d["v"] if d else default

    async def set(self, key: str, value: Any) -> None:
        await self.c.replace_one({"_id": key}, {"_id": key, "v": value}, upsert=True)


class MongoConfigStore:
    """published / draft documents plus a history collection. Optimistic locking on the draft's rev."""

    def __init__(self, coll: Any, history: Any) -> None:
        self.c, self.h = coll, history

    async def _get(self, slot: str) -> ConfigDoc | None:
        d = await self.c.find_one({"_id": slot})
        return ConfigDoc(d["config"], d["rev"], d["version"]) if d else None

    async def published(self) -> ConfigDoc | None:
        return await self._get("published")

    async def draft(self) -> ConfigDoc | None:
        return await self._get("draft")

    async def save_draft(self, config: dict[str, Any], base_rev: str) -> ConfigDoc:
        draft, pub = await self._get("draft"), await self._get("published")
        doc = ConfigDoc(config, config_rev(config), pub.version if pub else 0)
        new = {"_id": "draft", "config": config, "rev": doc.rev, "version": doc.version}
        if draft is not None:
            if draft.rev != base_rev:
                raise RevisionConflict("the configuration changed since it was loaded: reload it, then re-apply your edits")
            r = await self.c.replace_one({"_id": "draft", "rev": base_rev}, new)
            if r.matched_count != 1:
                raise RevisionConflict("the draft changed while saving: reload it")
        else:
            if (pub.rev if pub else "none") != base_rev:
                raise RevisionConflict("the configuration changed since it was loaded: reload it, then re-apply your edits")
            try:
                await self.c.insert_one(new)
            except DuplicateKeyError:
                raise RevisionConflict("someone else saved a draft meanwhile: reload it") from None
        return doc

    async def discard_draft(self) -> None:
        await self.c.delete_one({"_id": "draft"})

    async def publish(self, note: str = "") -> ConfigDoc:
        draft, old = await self._get("draft"), await self._get("published")
        if draft is None:
            raise ValueError("there is no draft to publish")
        if old is not None:
            await self.h.insert_one({"_id": uuid.uuid4().hex[:12], "version": old.version, "rev": old.rev, "ts": time.time(),
                                     "note": note or "replaced by publish", "config": old.config})
        version = (old.version if old else 0) + 1
        cfg = dict(draft.config, version=version)
        doc = ConfigDoc(cfg, config_rev(cfg), version)
        await self.c.replace_one({"_id": "published"}, {"_id": "published", "config": cfg, "rev": doc.rev, "version": version},
                                 upsert=True)
        await self.c.delete_one({"_id": "draft", "rev": draft.rev})
        return doc

    async def history(self, limit: int = 30) -> list[ConfigVersion]:
        docs = await _to_list(self.h.find({}).sort("ts", -1).limit(limit))
        return [ConfigVersion(d["_id"], d["version"], d["rev"], d["ts"], d.get("note", "")) for d in docs]

    async def get_version(self, version_id: str) -> ConfigDoc | None:
        d = await self.h.find_one({"_id": version_id})
        return ConfigDoc(d["config"], d["rev"], d["version"]) if d else None


class MongoLedgerStore:
    def __init__(self, coll: Any) -> None:
        self.c = coll

    async def record(self, entry: LedgerEntry) -> None:
        await self.c.insert_one({**_to_plain(entry), "ts": entry.ts or time.time()})

    async def total(self) -> float:
        rows = await _to_list(await self.c.aggregate([{"$group": {"_id": None, "t": {"$sum": "$cost_usd"}}}]))
        return round(float(rows[0]["t"]) if rows else 0.0, 8)

    async def summary(self) -> LedgerSummary:
        docs = await _to_list(self.c.find({}, {"purpose": 1, "model": 1, "cost_usd": 1}))
        bp: dict[str, float] = {}
        bm: dict[str, float] = {}
        for d in docs:
            bp[d["purpose"]] = bp.get(d["purpose"], 0.0) + d["cost_usd"]
            bm[d["model"]] = bm.get(d["model"], 0.0) + d["cost_usd"]
        return LedgerSummary(round(sum(bp.values()), 8), len(docs), bp, bm)


class MongoPlanCache:
    def __init__(self, coll: Any) -> None:
        self.c = coll

    @staticmethod
    def _id(signature: str, version: str) -> str:
        return hashlib.sha1(f"{version}\n{signature}".encode()).hexdigest()

    async def get(self, signature: str, version: str) -> dict[str, Any] | None:
        d = await self.c.find_one({"_id": self._id(signature, version)})
        return d["plan"] if d else None

    async def put(self, signature: str, version: str, plan: dict[str, Any]) -> None:
        _id = self._id(signature, version)
        await self.c.replace_one({"_id": _id}, {"_id": _id, "plan": plan, "ts": time.time()}, upsert=True)

    async def clear(self) -> None:
        await self.c.delete_many({})


class MongoChatLog:
    def __init__(self, coll: Any) -> None:
        self.c = coll

    async def append(self, turn: TurnLog) -> None:
        await self.c.insert_one(_to_plain(turn))

    async def list(self, limit: int = 50, status: str | None = None) -> list[TurnLog]:
        docs = await _to_list(self.c.find({"status": status} if status else {}).sort("ts", -1).limit(limit))
        return [TurnLog(**{k: d[k] for k in TurnLog.__dataclass_fields__ if k in d}) for d in docs]


class MongoKnowledgeStore:
    """One document per knowledge item, tagged with a generation; meta.gen points at the live generation, so a
    snapshot write is atomic from the reader's point of view (old generations are deleted afterwards)."""

    def __init__(self, meta: Any, items: Any, overrides: Any) -> None:
        self.meta, self.items, self.ov = meta, items, overrides

    async def _meta(self) -> dict[str, Any]:
        return await self.meta.find_one({"_id": "meta"}) or {"_id": "meta", "gen": None, "kb": {"version": "empty"}}

    async def version(self) -> str:
        return str((await self._meta())["kb"]["version"])

    async def load(self) -> KnowledgeSnapshot:
        m = await self._meta()
        docs = await _to_list(self.items.find({"gen": m["gen"]}).sort("seq", 1)) if m["gen"] else []
        return snapshot_from_items(m["kb"], [(d["kind"], d["data"]) for d in docs])

    async def write_snapshot(self, snapshot: KnowledgeSnapshot) -> None:
        gen = uuid.uuid4().hex[:12]
        docs = [{"_id": f"{gen}:{kind}:{iid}", "gen": gen, "seq": i, "kind": kind, "id": iid, "data": data}
                for i, (kind, iid, data) in enumerate(snapshot_items(snapshot))]
        if docs:
            await self.items.insert_many(docs)
        await self.meta.replace_one({"_id": "meta"}, {"_id": "meta", "gen": gen, "kb": _to_plain(snapshot.meta)}, upsert=True)
        await self.items.delete_many({"gen": {"$ne": gen}})

    async def update_marks(self, section_id: str, marks: tuple[SupersedeMark, ...], new_version: str) -> None:
        m = await self._meta()
        r = await self.items.update_one({"gen": m["gen"], "kind": "sections", "id": section_id},
                                        {"$set": {"data.marks": _to_plain(list(marks))}})
        if r.matched_count != 1:
            raise KeyError(section_id)
        kb = m.get("kb") or _to_plain(KBMeta("empty"))
        kb["version"] = new_version
        await self.meta.update_one({"_id": "meta"}, {"$set": {"kb": kb}})

    async def list_overrides(self) -> list[SupersedeOverride]:
        docs = await _to_list(self.ov.find({}))
        return [SupersedeOverride(d["section_id"], d["line"], d["action"], d.get("by_section"), d.get("ts", 0.0)) for d in docs]

    async def put_override(self, override: SupersedeOverride) -> None:
        _id = hashlib.sha1(f"{override.section_id}\n{override.line}".encode()).hexdigest()
        await self.ov.replace_one({"_id": _id}, {"_id": _id, **_to_plain(override), "ts": override.ts or time.time()}, upsert=True)

    async def delete_override(self, section_id: str, line: str) -> None:
        await self.ov.delete_one({"_id": hashlib.sha1(f"{section_id}\n{line}".encode()).hexdigest()})


class MongoStores:
    @staticmethod
    async def create(db: Any, prefix: str = "steward_") -> Stores:
        """All stores in one MongoDB database (collections named <prefix>...); creates the needed indexes."""
        col = lambda name: db[f"{prefix}{name}"]  # noqa: E731
        await col("records").create_index("key", unique=True, sparse=True)
        await col("records").create_index([("type", 1), ("ts", -1)])
        return Stores(sessions=MongoSessionStore(col("sessions")), records=MongoRecordStore(col("records")),
                      runtime=MongoRuntimeStore(col("runtime")), config=MongoConfigStore(col("config"), col("config_history")),
                      ledger=MongoLedgerStore(col("ledger")), plan_cache=MongoPlanCache(col("plan_cache")),
                      chatlog=MongoChatLog(col("chatlog")),
                      knowledge=MongoKnowledgeStore(col("kb_meta"), col("kb_items"), col("kb_overrides")))
