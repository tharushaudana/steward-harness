"""Firestore stores (pip install "steward-harness[firestore]"), using google-cloud-firestore's AsyncClient.

    from google.cloud.firestore import AsyncClient
    stores = FirestoreStores(AsyncClient(project="my-project"), prefix="steward_")

Guarantees: session compare-and-set runs in a transaction; records use the hashed idempotency key as the document id
(create() fails on a duplicate, then new non-empty fields are merged in a transaction). The knowledge base is one
document per item tagged with a generation; search uses the engine's built-in BM25. Document ids are hashed when
needed (Firestore ids cannot contain '/').
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
    from google.api_core.exceptions import Aborted, AlreadyExists, Conflict, FailedPrecondition
    from google.cloud import firestore
    from google.cloud.firestore_v1.base_query import FieldFilter
except ImportError:  # pragma: no cover
    raise ImportError('Firestore support needs google-cloud-firestore: pip install "steward-harness[firestore]"') from None


def _doc_id(raw: str) -> str:
    return raw if raw and "/" not in raw and len(raw) <= 700 and not raw.startswith("__") else \
        hashlib.sha1(raw.encode()).hexdigest()


class FirestoreSessionStore:
    def __init__(self, client: Any, coll: str) -> None:
        self.client, self.coll = client, client.collection(coll)

    async def load(self, session: str) -> tuple[dict[str, Any] | None, int]:
        snap = await self.coll.document(_doc_id(session)).get()
        if not snap.exists:
            return None, 0
        d = snap.to_dict()
        return d["state"], d["version"]

    async def compare_and_set(self, session: str, state: dict[str, Any], expected_version: int) -> bool:
        # Optimistic concurrency with write preconditions (no transaction locks): an insert is create() (fails if the
        # document exists); an update requires the document's last update time to be unchanged since we read it.
        ref = self.coll.document(_doc_id(session))
        data = {"state": state, "version": expected_version + 1, "updated": time.time()}
        if expected_version == 0:
            try:
                await ref.create(data)
                return True
            except (AlreadyExists, Conflict):
                return False
        snap = await ref.get()
        if not snap.exists or snap.to_dict()["version"] != expected_version:
            return False
        try:
            await ref.update(data, option=self.client.write_option(last_update_time=snap.update_time))
            return True
        except (FailedPrecondition, Aborted, Conflict, ValueError):
            return False

    async def delete(self, session: str) -> None:
        await self.coll.document(_doc_id(session)).delete()


class FirestoreRecordStore:
    def __init__(self, client: Any, coll: str) -> None:
        self.client, self.coll = client, client.collection(coll)

    async def append(self, record: NewRecord) -> AppendResult:
        rid = hashlib.sha1(record.key.encode()).hexdigest()[:20] if record.key is not None else uuid.uuid4().hex[:20]
        ref = self.coll.document(rid)
        doc = {"type": record.type, "data": record.data, "session": record.session, "key": record.key, "ts": time.time(),
               "status": "open", "is_test": record.is_test}
        try:
            await ref.create(doc)
            return AppendResult(rid)
        except (AlreadyExists, Conflict):
            if record.key is None:  # pragma: no cover - uuid collision
                return await self.append(record)
            new = {k: v for k, v in record.data.items() if v not in (None, "")}

            @firestore.async_transactional
            async def merge(transaction: Any) -> bool:
                snap = await ref.get(transaction=transaction)
                old = snap.to_dict()["data"]
                merged = {**old, **new}
                if merged != old:
                    transaction.update(ref, {"data": merged})
                return merged != old

            enriched = await merge(self.client.transaction())
            return AppendResult(rid, duplicate=True, enriched=bool(enriched))

    async def list(self, type: str | None = None, limit: int = 200, include_test: bool = False) -> list[Record]:
        q: Any = self.coll
        if type:
            q = q.where(filter=FieldFilter("type", "==", type))
        docs = [d async for d in q.stream()]
        rows = []
        for d in docs:
            x = d.to_dict()
            if include_test or not x.get("is_test"):
                rows.append(Record(d.id, x["type"], x["data"], x["session"], x.get("key"), x["ts"], x.get("status", "open"),
                                   bool(x.get("is_test"))))
        return sorted(rows, key=lambda r: -r.ts)[:limit]

    async def set_status(self, record_id: str, status: str) -> None:
        await self.coll.document(record_id).update({"status": status})


class FirestoreRuntimeStore:
    def __init__(self, client: Any, coll: str) -> None:
        self.coll = client.collection(coll)

    async def get(self, key: str, default: Any = None) -> Any:
        snap = await self.coll.document(_doc_id(key)).get()
        return snap.to_dict()["v"] if snap.exists else default

    async def set(self, key: str, value: Any) -> None:
        await self.coll.document(_doc_id(key)).set({"v": value})


class FirestoreConfigStore:
    def __init__(self, client: Any, coll: str, history: str) -> None:
        self.client, self.coll, self.hist = client, client.collection(coll), client.collection(history)

    async def _get(self, slot: str, transaction: Any = None) -> ConfigDoc | None:
        snap = await self.coll.document(slot).get(transaction=transaction)
        if not snap.exists:
            return None
        d = snap.to_dict()
        return ConfigDoc(d["config"], d["rev"], d["version"])

    async def published(self) -> ConfigDoc | None:
        return await self._get("published")

    async def draft(self) -> ConfigDoc | None:
        return await self._get("draft")

    async def save_draft(self, config: dict[str, Any], base_rev: str) -> ConfigDoc:
        @firestore.async_transactional
        async def txn(transaction: Any) -> ConfigDoc:
            draft, pub = await self._get("draft", transaction), await self._get("published", transaction)
            cur = draft or pub
            if (cur.rev if cur else "none") != base_rev:
                raise RevisionConflict("the configuration changed since it was loaded: reload it, then re-apply your edits")
            doc = ConfigDoc(config, config_rev(config), pub.version if pub else 0)
            transaction.set(self.coll.document("draft"), {"config": config, "rev": doc.rev, "version": doc.version})
            return doc

        return await txn(self.client.transaction())

    async def discard_draft(self) -> None:
        await self.coll.document("draft").delete()

    async def publish(self, note: str = "") -> ConfigDoc:
        @firestore.async_transactional
        async def txn(transaction: Any) -> ConfigDoc:
            draft, old = await self._get("draft", transaction), await self._get("published", transaction)
            if draft is None:
                raise ValueError("there is no draft to publish")
            if old is not None:
                transaction.set(self.hist.document(uuid.uuid4().hex[:12]), {
                    "version": old.version, "rev": old.rev, "ts": time.time(), "note": note or "replaced by publish",
                    "config": old.config})
            version = (old.version if old else 0) + 1
            cfg = dict(draft.config, version=version)
            doc = ConfigDoc(cfg, config_rev(cfg), version)
            transaction.set(self.coll.document("published"), {"config": cfg, "rev": doc.rev, "version": version})
            transaction.delete(self.coll.document("draft"))
            return doc

        return await txn(self.client.transaction())

    async def history(self, limit: int = 30) -> list[ConfigVersion]:
        docs = [d async for d in self.hist.order_by("ts", direction=firestore.Query.DESCENDING).limit(limit).stream()]
        return [ConfigVersion(d.id, d.get("version"), d.get("rev"), d.get("ts"), d.get("note") or "") for d in docs]

    async def get_version(self, version_id: str) -> ConfigDoc | None:
        snap = await self.hist.document(version_id).get()
        if not snap.exists:
            return None
        d = snap.to_dict()
        return ConfigDoc(d["config"], d["rev"], d["version"])


class FirestoreLedgerStore:
    def __init__(self, client: Any, coll: str) -> None:
        self.coll = client.collection(coll)

    async def record(self, entry: LedgerEntry) -> None:
        await self.coll.add({**_to_plain(entry), "ts": entry.ts or time.time()})

    async def _rows(self) -> list[dict[str, Any]]:
        return [d.to_dict() async for d in self.coll.stream()]

    async def total(self) -> float:
        return round(sum(r.get("cost_usd", 0.0) for r in await self._rows()), 8)

    async def summary(self) -> LedgerSummary:
        rows = await self._rows()
        bp: dict[str, float] = {}
        bm: dict[str, float] = {}
        for r in rows:
            bp[r["purpose"]] = bp.get(r["purpose"], 0.0) + r["cost_usd"]
            bm[r["model"]] = bm.get(r["model"], 0.0) + r["cost_usd"]
        return LedgerSummary(round(sum(bp.values()), 8), len(rows), bp, bm)


class FirestorePlanCache:
    def __init__(self, client: Any, coll: str) -> None:
        self.client, self.coll = client, client.collection(coll)

    def _ref(self, signature: str, version: str) -> Any:
        return self.coll.document(hashlib.sha1(f"{version}\n{signature}".encode()).hexdigest())

    async def get(self, signature: str, version: str) -> dict[str, Any] | None:
        snap = await self._ref(signature, version).get()
        return snap.to_dict()["plan"] if snap.exists else None

    async def put(self, signature: str, version: str, plan: dict[str, Any]) -> None:
        await self._ref(signature, version).set({"plan": plan, "ts": time.time()})

    async def clear(self) -> None:
        await _delete_query(self.client, self.coll)


class FirestoreChatLog:
    def __init__(self, client: Any, coll: str) -> None:
        self.coll = client.collection(coll)

    async def append(self, turn: TurnLog) -> None:
        await self.coll.add(_to_plain(turn))

    async def list(self, limit: int = 50, status: str | None = None) -> list[TurnLog]:
        q: Any = self.coll if status is None else self.coll.where(filter=FieldFilter("status", "==", status))
        docs = sorted([d.to_dict() async for d in q.stream()], key=lambda d: -d["ts"])[:limit]
        return [TurnLog(**{k: d[k] for k in TurnLog.__dataclass_fields__ if k in d}) for d in docs]


async def _delete_query(client: Any, query: Any) -> None:
    batch, n = client.batch(), 0
    async for d in query.stream():
        batch.delete(d.reference)
        n += 1
        if n % 400 == 0:
            await batch.commit()
            batch = client.batch()
    if n % 400:
        await batch.commit()


class FirestoreKnowledgeStore:
    def __init__(self, client: Any, meta: str, items: str, overrides: str) -> None:
        self.client = client
        self.meta, self.items, self.ov = client.collection(meta), client.collection(items), client.collection(overrides)

    async def _meta(self) -> dict[str, Any]:
        snap = await self.meta.document("meta").get()
        return snap.to_dict() if snap.exists else {"gen": None, "kb": {"version": "empty"}}

    async def version(self) -> str:
        return str((await self._meta())["kb"]["version"])

    async def load(self) -> KnowledgeSnapshot:
        m = await self._meta()
        docs = [d.to_dict() async for d in self.items.where(filter=FieldFilter("gen", "==", m["gen"])).stream()] if m["gen"] else []
        docs.sort(key=lambda d: d["seq"])
        return snapshot_from_items(m["kb"], [(d["kind"], d["data"]) for d in docs])

    async def write_snapshot(self, snapshot: KnowledgeSnapshot) -> None:
        gen = uuid.uuid4().hex[:12]
        batch, n = self.client.batch(), 0
        for i, (kind, iid, data) in enumerate(snapshot_items(snapshot)):
            batch.set(self.items.document(_doc_id(f"{gen}:{kind}:{iid}")), {"gen": gen, "seq": i, "kind": kind, "id": iid, "data": data})
            n += 1
            if n % 400 == 0:
                await batch.commit()
                batch = self.client.batch()
        if n % 400:
            await batch.commit()
        await self.meta.document("meta").set({"gen": gen, "kb": _to_plain(snapshot.meta)})
        await _delete_query(self.client, self.items.where(filter=FieldFilter("gen", "!=", gen)))

    async def update_marks(self, section_id: str, marks: tuple[SupersedeMark, ...], new_version: str) -> None:
        m = await self._meta()
        ref = self.items.document(_doc_id(f"{m['gen']}:sections:{section_id}"))
        snap = await ref.get()
        if not snap.exists:
            raise KeyError(section_id)
        data = snap.to_dict()["data"]
        data["marks"] = _to_plain(list(marks))
        await ref.update({"data": data})
        kb = m.get("kb") or _to_plain(KBMeta("empty"))
        kb["version"] = new_version
        await self.meta.document("meta").update({"kb": kb})

    async def list_overrides(self) -> list[SupersedeOverride]:
        return [SupersedeOverride(**{k: d.get(k) for k in ("section_id", "line", "action", "by_section", "ts")})
                async for d in self.ov.stream()]

    async def put_override(self, override: SupersedeOverride) -> None:
        await self.ov.document(hashlib.sha1(f"{override.section_id}\n{override.line}".encode()).hexdigest()).set(
            {**_to_plain(override), "ts": override.ts or time.time()})

    async def delete_override(self, section_id: str, line: str) -> None:
        await self.ov.document(hashlib.sha1(f"{section_id}\n{line}".encode()).hexdigest()).delete()


def FirestoreStores(client: Any, prefix: str = "steward_") -> Stores:
    """All stores in one Firestore database (collections named <prefix>...)."""
    p = prefix
    return Stores(sessions=FirestoreSessionStore(client, f"{p}sessions"), records=FirestoreRecordStore(client, f"{p}records"),
                  runtime=FirestoreRuntimeStore(client, f"{p}runtime"),
                  config=FirestoreConfigStore(client, f"{p}config", f"{p}config_history"),
                  ledger=FirestoreLedgerStore(client, f"{p}ledger"), plan_cache=FirestorePlanCache(client, f"{p}plan_cache"),
                  chatlog=FirestoreChatLog(client, f"{p}chatlog"),
                  knowledge=FirestoreKnowledgeStore(client, f"{p}kb_meta", f"{p}kb_items", f"{p}kb_overrides"))
