"""Knowledge store interfaces.

The engine loads a whole KnowledgeSnapshot into memory (fine up to ~10k sections) and reloads it when version()
changes. Search is optional on the store: a store that has full-text search (e.g. SQLite FTS5, Atlas Search)
implements SearchableStore; otherwise the engine builds its own BM25 index from the snapshot.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from steward.knowledge.model import Hit, KnowledgeSnapshot, SupersedeMark, SupersedeOverride

SearchKind = Literal["section", "topic", "calculator"]


@runtime_checkable
class KnowledgeStore(Protocol):
    async def version(self) -> str:
        """Cheap; the engine polls it to hot-reload the snapshot."""
        ...

    async def load(self) -> KnowledgeSnapshot: ...


@runtime_checkable
class SearchableStore(KnowledgeStore, Protocol):
    async def search(self, text: str, kind: SearchKind, limit: int) -> list[Hit]:
        """Keyword search (best first). Hidden (fully superseded) sections are filtered by the engine."""
        ...


@runtime_checkable
class KnowledgeWriter(Protocol):
    """Used by steward.build and the admin's supersede review."""

    async def write_snapshot(self, snapshot: KnowledgeSnapshot) -> None:
        """Atomically replace the whole knowledge base."""
        ...

    async def update_marks(self, section_id: str, marks: tuple[SupersedeMark, ...], new_version: str) -> None: ...

    async def list_overrides(self) -> list[SupersedeOverride]: ...

    async def put_override(self, override: SupersedeOverride) -> None: ...

    async def delete_override(self, section_id: str, line: str) -> None: ...


class MemoryKnowledgeStore:
    """A knowledge base held in memory; also a KnowledgeWriter. Load one from a JSON file with from_file()."""

    def __init__(self, snapshot: KnowledgeSnapshot | None = None) -> None:
        from steward.knowledge.model import KBMeta

        self._snap = snapshot or KnowledgeSnapshot(meta=KBMeta(version="empty"))
        self._overrides: dict[tuple[str, str], SupersedeOverride] = {}

    @classmethod
    def from_file(cls, path: str | Path) -> MemoryKnowledgeStore:
        return cls(KnowledgeSnapshot.from_dict(json.loads(Path(path).read_text(encoding="utf-8"))))

    def to_file(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self._snap.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")

    async def version(self) -> str:
        return self._snap.meta.version

    async def load(self) -> KnowledgeSnapshot:
        return self._snap

    async def write_snapshot(self, snapshot: KnowledgeSnapshot) -> None:
        self._snap = snapshot

    async def update_marks(self, section_id: str, marks: tuple[SupersedeMark, ...], new_version: str) -> None:
        from dataclasses import replace

        sections = tuple(replace(s, marks=marks) if s.id == section_id else s for s in self._snap.sections)
        self._snap = replace(self._snap, sections=sections, meta=replace(self._snap.meta, version=new_version))

    async def list_overrides(self) -> list[SupersedeOverride]:
        return list(copy.deepcopy(list(self._overrides.values())))

    async def put_override(self, override: SupersedeOverride) -> None:
        self._overrides[(override.section_id, override.line)] = override

    async def delete_override(self, section_id: str, line: str) -> None:
        self._overrides.pop((section_id, line), None)
