"""A knowledge store over your own database: implement two async methods.

The engine loads the whole snapshot into memory and reloads it when version() changes, so version() must be cheap
(a counter or a hash kept next to the data). Implement `search()` too if your database has full-text search; without
it the engine builds its own BM25 index.

Here the "database" is a dict of JSON documents, standing in for a Firestore collection or a MongoDB collection."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from steward.knowledge import KnowledgeIndex, KnowledgeSnapshot
from steward.knowledge.model import snapshot_from_items, snapshot_items


class DocumentKnowledgeStore:
    """One document per item (section, node, value, calculator, ...), plus a meta document holding the version."""

    def __init__(self, documents: dict[str, dict]) -> None:
        self.docs = documents  # your collection

    async def version(self) -> str:
        return self.docs["meta"]["version"]

    async def load(self) -> KnowledgeSnapshot:
        return snapshot_from_items(self.docs["meta"], [(d["kind"], d["data"]) for k, d in self.docs.items() if k != "meta"])

    @classmethod
    def import_snapshot(cls, snap: KnowledgeSnapshot) -> DocumentKnowledgeStore:
        docs: dict[str, dict] = {"meta": snap.to_dict()["meta"]}
        for kind, item_id, data in snapshot_items(snap):
            docs[f"{kind}:{item_id}"] = {"kind": kind, "data": data}
        return cls(docs)


async def main() -> None:
    snap = KnowledgeSnapshot.from_dict(json.loads((Path(__file__).parent / "northwind" / "kb.json").read_text(encoding="utf-8")))
    store = DocumentKnowledgeStore.import_snapshot(snap)
    loaded = await store.load()
    index = KnowledgeIndex(loaded)
    print(f"version {await store.version()}: {len(loaded.sections)} sections, {len(loaded.values)} values")
    print("search 'trail weight':", [h.id for h in await index.search("trail weight", "section", 3)])


if __name__ == "__main__":
    asyncio.run(main())
