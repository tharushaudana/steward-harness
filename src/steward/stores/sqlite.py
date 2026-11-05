"""SQLite stores: everything in one file, a persistent default for small deployments. Uses the standard library
(sqlite3 + FTS5 for knowledge search); calls run in a worker thread so the event loop never blocks.

    stores = SqliteStores("assistant.db")             # sessions, records, runtime, config, ledger, plan cache, chat log,
    bot = Steward(config, provider=..., stores=stores) # and the knowledge base (stores.knowledge)
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from steward.errors import RevisionConflict
from steward.knowledge.model import (
    Hit,
    KBMeta,
    KnowledgeSnapshot,
    SupersedeMark,
    SupersedeOverride,
    _to_plain,
    snapshot_from_items,
    snapshot_items,
)
from steward.knowledge.search import DEFAULT_STOPWORDS
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

SCHEMA = """
CREATE TABLE IF NOT EXISTS st_sessions(session TEXT PRIMARY KEY, state TEXT NOT NULL, version INTEGER NOT NULL, updated REAL);
CREATE TABLE IF NOT EXISTS st_records(id TEXT PRIMARY KEY, type TEXT, data TEXT, session TEXT, key TEXT UNIQUE, ts REAL,
    status TEXT DEFAULT 'open', is_test INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS st_records_type ON st_records(type, ts);
CREATE TABLE IF NOT EXISTS st_runtime(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS st_config(slot TEXT PRIMARY KEY, config TEXT, rev TEXT, version INTEGER);
CREATE TABLE IF NOT EXISTS st_config_history(id TEXT PRIMARY KEY, version INTEGER, rev TEXT, ts REAL, note TEXT, config TEXT);
CREATE TABLE IF NOT EXISTS st_ledger(id INTEGER PRIMARY KEY, ts REAL, purpose TEXT, model TEXT, tokens_in INTEGER,
    tokens_out INTEGER, cost REAL, turn_id TEXT, latency_ms REAL, ok INTEGER, error TEXT, cost_source TEXT);
CREATE TABLE IF NOT EXISTS st_plan_cache(signature TEXT PRIMARY KEY, version TEXT, plan TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS st_chatlog(id INTEGER PRIMARY KEY, session TEXT, message TEXT, reply TEXT, status TEXT, ts REAL,
    cost REAL, latency_ms REAL, standalone TEXT, trace TEXT, is_test INTEGER);
CREATE TABLE IF NOT EXISTS st_kb_meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS st_kb_items(kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind, id));
CREATE TABLE IF NOT EXISTS st_kb_overrides(section_id TEXT, line TEXT, action TEXT, by_section TEXT, ts REAL,
    PRIMARY KEY(section_id, line));
CREATE VIRTUAL TABLE IF NOT EXISTS st_kb_fts USING fts5(kind, ref_id, title, body, tokenize='porter unicode61');
"""


class _Db:
    """One sqlite3 connection, serialised by a lock, used from worker threads."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()

    async def run(self, fn: Any, *args: Any) -> Any:
        def call() -> Any:
            with self.lock:
                return fn(self.conn, *args)
        return await asyncio.to_thread(call)


def _tx(conn: sqlite3.Connection, fn: Any) -> Any:
    conn.execute("BEGIN IMMEDIATE")
    try:
        out = fn()
        conn.execute("COMMIT")
        return out
    except BaseException:
        conn.execute("ROLLBACK")
        raise


class SqliteSessionStore:
    def __init__(self, db: _Db) -> None:
        self.db = db

    async def load(self, session: str) -> tuple[dict[str, Any] | None, int]:
        r = await self.db.run(lambda c: c.execute("SELECT state, version FROM st_sessions WHERE session=?", (session,)).fetchone())
        return (json.loads(r["state"]), r["version"]) if r else (None, 0)

    async def compare_and_set(self, session: str, state: dict[str, Any], expected_version: int) -> bool:
        data = json.dumps(state, ensure_ascii=False)

        def cas(c: sqlite3.Connection) -> bool:
            if expected_version == 0:
                cur = c.execute("INSERT OR IGNORE INTO st_sessions VALUES(?,?,1,?)", (session, data, time.time()))
            else:
                cur = c.execute("UPDATE st_sessions SET state=?, version=version+1, updated=? WHERE session=? AND version=?",
                                (data, time.time(), session, expected_version))
            return cur.rowcount == 1
        return bool(await self.db.run(cas))

    async def delete(self, session: str) -> None:
        await self.db.run(lambda c: c.execute("DELETE FROM st_sessions WHERE session=?", (session,)))


class SqliteRecordStore:
    def __init__(self, db: _Db) -> None:
        self.db = db

    async def append(self, record: NewRecord) -> AppendResult:
        def append(c: sqlite3.Connection) -> AppendResult:
            def body() -> AppendResult:
                if record.key is not None:
                    row = c.execute("SELECT id, data FROM st_records WHERE key=?", (record.key,)).fetchone()
                    if row:
                        old = json.loads(row["data"])
                        merged = {**old, **{k: v for k, v in record.data.items() if v not in (None, "")}}
                        if merged != old:
                            c.execute("UPDATE st_records SET data=? WHERE id=?", (json.dumps(merged, ensure_ascii=False), row["id"]))
                        return AppendResult(row["id"], duplicate=True, enriched=merged != old)
                rid = uuid.uuid4().hex[:12]
                c.execute("INSERT INTO st_records(id,type,data,session,key,ts,is_test) VALUES(?,?,?,?,?,?,?)",
                          (rid, record.type, json.dumps(record.data, ensure_ascii=False), record.session, record.key, time.time(),
                           int(record.is_test)))
                return AppendResult(rid)
            return _tx(c, body)
        return await self.db.run(append)

    async def list(self, type: str | None = None, limit: int = 200, include_test: bool = False) -> list[Record]:
        q = "SELECT * FROM st_records WHERE 1=1" + (" AND type=?" if type else "") + ("" if include_test else " AND is_test=0") \
            + " ORDER BY ts DESC LIMIT ?"
        args = (type, limit) if type else (limit,)
        rows = await self.db.run(lambda c: c.execute(q, args).fetchall())
        return [Record(r["id"], r["type"], json.loads(r["data"]), r["session"], r["key"], r["ts"], r["status"], bool(r["is_test"]))
                for r in rows]

    async def set_status(self, record_id: str, status: str) -> None:
        await self.db.run(lambda c: c.execute("UPDATE st_records SET status=? WHERE id=?", (status, record_id)))


class SqliteRuntimeStore:
    def __init__(self, db: _Db) -> None:
        self.db = db

    async def get(self, key: str, default: Any = None) -> Any:
        r = await self.db.run(lambda c: c.execute("SELECT v FROM st_runtime WHERE k=?", (key,)).fetchone())
        return json.loads(r["v"]) if r else default

    async def set(self, key: str, value: Any) -> None:
        await self.db.run(lambda c: c.execute("INSERT OR REPLACE INTO st_runtime VALUES(?,?)", (key, json.dumps(value))))


class SqliteConfigStore:
    def __init__(self, db: _Db) -> None:
        self.db = db

    @staticmethod
    def _get(c: sqlite3.Connection, slot: str) -> ConfigDoc | None:
        r = c.execute("SELECT * FROM st_config WHERE slot=?", (slot,)).fetchone()
        return ConfigDoc(json.loads(r["config"]), r["rev"], r["version"]) if r else None

    async def published(self) -> ConfigDoc | None:
        return await self.db.run(lambda c: self._get(c, "published"))

    async def draft(self) -> ConfigDoc | None:
        return await self.db.run(lambda c: self._get(c, "draft"))

    async def save_draft(self, config: dict[str, Any], base_rev: str) -> ConfigDoc:
        def save(c: sqlite3.Connection) -> ConfigDoc:
            def body() -> ConfigDoc:
                cur = self._get(c, "draft") or self._get(c, "published")
                if (cur.rev if cur else "none") != base_rev:
                    raise RevisionConflict("the configuration changed since it was loaded: reload it, then re-apply your edits")
                pub = self._get(c, "published")
                doc = ConfigDoc(config, config_rev(config), pub.version if pub else 0)
                c.execute("INSERT OR REPLACE INTO st_config VALUES('draft',?,?,?)",
                          (json.dumps(config, ensure_ascii=False), doc.rev, doc.version))
                return doc
            return _tx(c, body)
        return await self.db.run(save)

    async def discard_draft(self) -> None:
        await self.db.run(lambda c: c.execute("DELETE FROM st_config WHERE slot='draft'"))

    async def publish(self, note: str = "") -> ConfigDoc:
        def pub(c: sqlite3.Connection) -> ConfigDoc:
            def body() -> ConfigDoc:
                draft, old = self._get(c, "draft"), self._get(c, "published")
                if draft is None:
                    raise ValueError("there is no draft to publish")
                if old is not None:
                    c.execute("INSERT INTO st_config_history VALUES(?,?,?,?,?,?)", (uuid.uuid4().hex[:12], old.version, old.rev,
                              time.time(), note or "replaced by publish", json.dumps(old.config, ensure_ascii=False)))
                version = (old.version if old else 0) + 1
                cfg = dict(draft.config, version=version)
                doc = ConfigDoc(cfg, config_rev(cfg), version)
                c.execute("INSERT OR REPLACE INTO st_config VALUES('published',?,?,?)",
                          (json.dumps(cfg, ensure_ascii=False), doc.rev, version))
                c.execute("DELETE FROM st_config WHERE slot='draft'")
                return doc
            return _tx(c, body)
        return await self.db.run(pub)

    async def history(self, limit: int = 30) -> list[ConfigVersion]:
        rows = await self.db.run(lambda c: c.execute("SELECT * FROM st_config_history ORDER BY ts DESC LIMIT ?", (limit,)).fetchall())
        return [ConfigVersion(r["id"], r["version"], r["rev"], r["ts"], r["note"]) for r in rows]

    async def get_version(self, version_id: str) -> ConfigDoc | None:
        r = await self.db.run(lambda c: c.execute("SELECT * FROM st_config_history WHERE id=?", (version_id,)).fetchone())
        return ConfigDoc(json.loads(r["config"]), r["rev"], r["version"]) if r else None


class SqliteLedgerStore:
    def __init__(self, db: _Db) -> None:
        self.db = db

    async def record(self, entry: LedgerEntry) -> None:
        e = entry
        await self.db.run(lambda c: c.execute(
            "INSERT INTO st_ledger(ts,purpose,model,tokens_in,tokens_out,cost,turn_id,latency_ms,ok,error,cost_source) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (e.ts or time.time(), e.purpose, e.model, e.tokens_in, e.tokens_out, e.cost_usd,
                                               e.turn_id, e.latency_ms, int(e.ok), e.error, e.cost_source)))

    async def total(self) -> float:
        return float(await self.db.run(lambda c: c.execute("SELECT COALESCE(SUM(cost),0) FROM st_ledger").fetchone()[0]))

    async def summary(self) -> LedgerSummary:
        rows = await self.db.run(lambda c: c.execute("SELECT purpose, model, cost FROM st_ledger").fetchall())
        bp: dict[str, float] = {}
        bm: dict[str, float] = {}
        for r in rows:
            bp[r["purpose"]] = bp.get(r["purpose"], 0.0) + (r["cost"] or 0.0)
            bm[r["model"]] = bm.get(r["model"], 0.0) + (r["cost"] or 0.0)
        return LedgerSummary(round(sum(bp.values()), 8), len(rows), bp, bm)


class SqlitePlanCache:
    def __init__(self, db: _Db) -> None:
        self.db = db

    async def get(self, signature: str, version: str) -> dict[str, Any] | None:
        r = await self.db.run(lambda c: c.execute("SELECT plan FROM st_plan_cache WHERE signature=? AND version=?",
                                                  (signature, version)).fetchone())
        return json.loads(r["plan"]) if r else None

    async def put(self, signature: str, version: str, plan: dict[str, Any]) -> None:
        await self.db.run(lambda c: c.execute("INSERT OR REPLACE INTO st_plan_cache VALUES(?,?,?,?)",
                                              (signature, version, json.dumps(plan), time.time())))

    async def clear(self) -> None:
        await self.db.run(lambda c: c.execute("DELETE FROM st_plan_cache"))


class SqliteChatLog:
    def __init__(self, db: _Db) -> None:
        self.db = db

    async def append(self, turn: TurnLog) -> None:
        t = turn
        await self.db.run(lambda c: c.execute(
            "INSERT INTO st_chatlog(session,message,reply,status,ts,cost,latency_ms,standalone,trace,is_test) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (t.session, t.message, t.reply, t.status, t.ts, t.cost_usd, t.latency_ms, t.standalone,
             json.dumps(t.trace, default=str, ensure_ascii=False), int(t.is_test))))

    async def list(self, limit: int = 50, status: str | None = None) -> list[TurnLog]:
        q = "SELECT * FROM st_chatlog" + (" WHERE status=?" if status else "") + " ORDER BY id DESC LIMIT ?"
        rows = await self.db.run(lambda c: c.execute(q, (status, limit) if status else (limit,)).fetchall())
        return [TurnLog(r["session"], r["message"], r["reply"], r["status"], r["ts"], r["cost"], r["latency_ms"],
                        r["standalone"], json.loads(r["trace"] or "[]"), bool(r["is_test"])) for r in rows]


class SqliteKnowledgeStore:
    """KnowledgeStore + SearchableStore (FTS5, same field weights as the built-in BM25) + KnowledgeWriter."""

    def __init__(self, db: _Db) -> None:
        self.db = db

    async def version(self) -> str:
        r = await self.db.run(lambda c: c.execute("SELECT v FROM st_kb_meta WHERE k='meta'").fetchone())
        return json.loads(r["v"])["version"] if r else "empty"

    async def load(self) -> KnowledgeSnapshot:
        def load(c: sqlite3.Connection) -> KnowledgeSnapshot:
            r = c.execute("SELECT v FROM st_kb_meta WHERE k='meta'").fetchone()
            meta = json.loads(r["v"]) if r else {"version": "empty"}
            items = [(x["kind"], json.loads(x["data"])) for x in c.execute("SELECT kind, data FROM st_kb_items ORDER BY rowid")]
            return snapshot_from_items(meta, items)
        return await self.db.run(load)

    async def search(self, text: str, kind: str, limit: int) -> list[Hit]:
        toks = [t for t in re.findall(r"\w+", text.lower()) if t not in DEFAULT_STOPWORDS and len(t) > 1]
        if not toks:
            return []
        q = " OR ".join(f'"{t}"' for t in dict.fromkeys(toks))
        rows = await self.db.run(lambda c: c.execute(
            "SELECT ref_id, bm25(st_kb_fts, 0, 0, 3.0, 1.0) s FROM st_kb_fts WHERE st_kb_fts MATCH ? AND kind=? ORDER BY s LIMIT ?",
            (q, kind, limit)).fetchall())
        return [Hit(r["ref_id"], -r["s"]) for r in rows]

    async def write_snapshot(self, snapshot: KnowledgeSnapshot) -> None:
        def write(c: sqlite3.Connection) -> None:
            def body() -> None:
                c.execute("DELETE FROM st_kb_items")
                c.execute("DELETE FROM st_kb_fts")
                for kind, iid, data in snapshot_items(snapshot):
                    c.execute("INSERT OR REPLACE INTO st_kb_items VALUES(?,?,?)", (kind, iid, json.dumps(data, ensure_ascii=False)))
                for s in snapshot.sections:
                    c.execute("INSERT INTO st_kb_fts VALUES('section',?,?,?)",
                              (s.id, " ".join([s.title, *s.keywords, *s.questions, s.heading_path]), f"{s.summary} {s.text}"))
                for n in snapshot.nodes:
                    if n.kind in ("domain", "topic"):
                        c.execute("INSERT INTO st_kb_fts VALUES('topic',?,?,?)", (n.id, n.title, n.description))
                for calc in snapshot.calculators:
                    c.execute("INSERT INTO st_kb_fts VALUES('calculator',?,?,?)", (calc.id, calc.title, calc.description))
                c.execute("INSERT OR REPLACE INTO st_kb_meta VALUES('meta',?)", (json.dumps(_to_plain(snapshot.meta)),))
            _tx(c, body)
        await self.db.run(write)

    async def update_marks(self, section_id: str, marks: tuple[SupersedeMark, ...], new_version: str) -> None:
        def upd(c: sqlite3.Connection) -> None:
            def body() -> None:
                r = c.execute("SELECT data FROM st_kb_items WHERE kind='sections' AND id=?", (section_id,)).fetchone()
                if r is None:
                    raise KeyError(section_id)
                data = json.loads(r["data"])
                data["marks"] = _to_plain(list(marks))
                c.execute("UPDATE st_kb_items SET data=? WHERE kind='sections' AND id=?", (json.dumps(data, ensure_ascii=False), section_id))
                m = c.execute("SELECT v FROM st_kb_meta WHERE k='meta'").fetchone()
                meta = json.loads(m["v"]) if m else _to_plain(KBMeta("empty"))
                meta["version"] = new_version
                c.execute("INSERT OR REPLACE INTO st_kb_meta VALUES('meta',?)", (json.dumps(meta),))
            _tx(c, body)
        await self.db.run(upd)

    async def list_overrides(self) -> list[SupersedeOverride]:
        rows = await self.db.run(lambda c: c.execute("SELECT * FROM st_kb_overrides").fetchall())
        return [SupersedeOverride(r["section_id"], r["line"], r["action"], r["by_section"], r["ts"]) for r in rows]

    async def put_override(self, override: SupersedeOverride) -> None:
        o = override
        await self.db.run(lambda c: c.execute("INSERT OR REPLACE INTO st_kb_overrides VALUES(?,?,?,?,?)",
                                              (o.section_id, o.line, o.action, o.by_section, o.ts or time.time())))

    async def delete_override(self, section_id: str, line: str) -> None:
        await self.db.run(lambda c: c.execute("DELETE FROM st_kb_overrides WHERE section_id=? AND line=?", (section_id, line)))


def SqliteStores(path: str | Path) -> Stores:
    """Every store in one SQLite file (created if missing), including the knowledge base."""
    db = _Db(path)
    return Stores(sessions=SqliteSessionStore(db), records=SqliteRecordStore(db), runtime=SqliteRuntimeStore(db),
                  config=SqliteConfigStore(db), ledger=SqliteLedgerStore(db), plan_cache=SqlitePlanCache(db),
                  chatlog=SqliteChatLog(db), knowledge=SqliteKnowledgeStore(db))
