"""KnowledgeIndex: the in-memory view of a KnowledgeSnapshot that the engine works on.

Applies authority and supersede marks (outdated lines removed, fully superseded sections hidden, "superseded by"
links), picks values (approved first, then the most authoritative document, then unconditional, then rank), and
answers keyword searches (the store's own search when it has one, else the built-in BM25 index).
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from steward.knowledge.model import Calculator, Entity, Hit, KnowledgeSnapshot, Section, TopicView, Value
from steward.knowledge.search import Bm25Index, Lexicon
from steward.knowledge.store import KnowledgeStore, SearchKind


def script_ratio(text: str) -> float:
    """Share of letters that are Latin (a-z). Distinguishes a message's script from the knowledge base's."""
    letters = [ch for ch in text if ch.isalpha()]
    return sum(1 for ch in letters if ch.isascii()) / len(letters) if letters else 1.0


class KnowledgeIndex:
    def __init__(self, snapshot: KnowledgeSnapshot, store: KnowledgeStore | None = None,
                 lexicon: Lexicon | None = None) -> None:
        self.snapshot = snapshot
        self.version = snapshot.meta.version
        self.store = store
        self.lexicon = lexicon or Lexicon()
        self.sections: dict[str, Section] = {s.id: s for s in snapshot.sections}
        self.outdated: dict[str, set[str]] = {}
        self.superseded_by: dict[str, list[str]] = {}
        self.hidden: set[str] = set()
        for s in snapshot.sections:
            lines = {ln for m in s.marks for ln in m.active_lines}
            if lines:
                self.outdated[s.id] = lines
                self.superseded_by[s.id] = [m.by for m in s.marks if m.by in self.sections and m.active_lines]
            if any(m.full for m in s.marks):
                self.hidden.add(s.id)
        self.sections_by_topic: dict[str, list[str]] = defaultdict(list)
        for s in snapshot.sections:
            if s.id not in self.hidden:
                self.sections_by_topic[s.topic_id].append(s.id)
        nodes = {n.id: n for n in snapshot.nodes}
        self.topics: list[TopicView] = [
            TopicView(n.id, n.title, n.description, nodes[n.parent_id].title if n.parent_id in nodes else "",
                      list(self.sections_by_topic[n.id]))
            for n in sorted(snapshot.nodes, key=lambda x: (x.parent_id or "", x.order, x.id))
            if n.kind == "topic" and self.sections_by_topic.get(n.id)]
        self.values: dict[str, Value] = {v.id: v for v in snapshot.values}
        self.doc_weight: dict[str, int] = defaultdict(int)  # values per source document = how authoritative its numbers are
        for v in snapshot.values:
            sec = self.sections.get(v.section_id)
            if sec:
                self.doc_weight[sec.doc_id or sec.url or sec.id] += 1
        self.calcs: dict[str, Calculator] = {c.id: c for c in snapshot.calculators if c.validated}
        self.entities: dict[str, Entity] = {e.id: e for e in snapshot.entities}
        calc_types = {c.entity_type for c in self.calcs.values()}
        self.calc_entities = sorted(e for e, x in self.entities.items() if x.type in calc_types)
        sample = " ".join(s.text[:400] for s in snapshot.sections[:60])
        self.latin = script_ratio(sample)
        self.sample_text = next((re.sub(r"\s+", " ", s.text)[:160] for s in snapshot.sections
                                 if abs(script_ratio(s.text) - self.latin) < 0.2), "")
        self._ent_sections: dict[str, dict[str, int]] | None = None
        self._bm25: Bm25Index | None = None

    # ---------------------------------------------------------------------------------------------- search
    async def search(self, text: str, kind: SearchKind = "section", limit: int = 20) -> list[Hit]:
        if self.store is not None and hasattr(self.store, "search"):
            hits = await self.store.search(text, kind, limit + len(self.hidden))  # type: ignore[attr-defined]
        else:
            if self._bm25 is None:
                self._bm25 = Bm25Index.from_snapshot(self.snapshot, self.lexicon)
            hits = self._bm25.search(text, kind, limit + len(self.hidden))
        return [h for h in hits if not (kind == "section" and h.id in self.hidden)][:limit]

    # ---------------------------------------------------------------------------------------------- sections
    def text(self, section_id: str) -> str:
        """Section text without the lines an approved section superseded (they never reach a model)."""
        s = self.sections[section_id]
        out = self.outdated.get(section_id)
        if not out:
            return s.text
        return "\n".join(ln for ln in s.text.splitlines() if ln.strip() not in out)

    def with_authority(self, section_ids: list[str], cap: int = 2) -> list[str]:
        """Follow 'superseded by' links: a selected source section brings the approved section that corrects it."""
        out = list(section_ids)
        for sid in section_ids:
            for a in self.superseded_by.get(sid, [])[:cap]:
                if a not in out:
                    out.append(a)
        return out

    # ---------------------------------------------------------------------------------------------- values, entities
    def value(self, entity: str, attribute: str) -> Value | None:
        best: tuple[tuple[Any, ...], Value] | None = None
        for v in self.values.values():
            if v.entity != entity or v.attribute != attribute:
                continue
            sec = self.sections.get(v.section_id)
            if sec is None or sec.id in self.hidden:
                continue
            key = (sec.authority != "approved", -self.doc_weight.get(sec.doc_id or sec.url or sec.id, 0),
                   bool(v.conditions), v.rank)
            if best is None or key < best[0]:
                best = (key, v)
        return best[1] if best else None

    def entity_sections(self, entity: str) -> list[str]:
        """Sections holding values of the entity, best first: approved, then most values."""
        if self._ent_sections is None:
            self._ent_sections = defaultdict(dict)
            for v in self.values.values():
                if v.section_id in self.sections and v.section_id not in self.hidden:
                    d = self._ent_sections[v.entity]
                    d[v.section_id] = d.get(v.section_id, 0) + 1
        d = self._ent_sections.get(entity, {})
        return sorted(d, key=lambda s: (self.sections[s].authority != "approved", -d[s], s))

    def covers(self, section_id: str, entity: str) -> bool:
        """Does the section speak about the entity (its values, or its title/alias in the text)?"""
        if section_id in self.entity_sections(entity):
            return True
        e = self.entities.get(entity)
        if e is None:
            return False
        low = self.text(section_id).lower()
        return any(re.search(rf"(?<!\w){re.escape(a.lower())}(?!\w)", low) for a in (e.title, *e.aliases) if len(a) >= 3)

    def entity_names(self, types: list[str]) -> list[str]:
        """Titles and aliases of entities of these types, longest first (for wording rules)."""
        names: set[str] = set()
        for e in self.entities.values():
            if e.type in types:
                names.update(n for n in (e.title, *e.aliases) if len(n) >= 3)
                toks = e.title.split()
                if len(toks) > 1 and toks[-1].isupper() and len(toks[-1]) <= 4:  # "Model X HV" -> also "Model X"
                    names.add(" ".join(toks[:-1]))
        return sorted(names, key=len, reverse=True)

    def approved_titles(self) -> list[str]:
        return [s.title for s in self.sections.values() if s.authority == "approved"]
