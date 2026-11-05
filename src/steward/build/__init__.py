"""Build a knowledge base from source documents (steward.build).

    from steward.build import SourceDocument, build, overlay_approved
    docs = [SourceDocument.from_markdown(text, doc_id="pricing", url="https://shop.example/pricing"), ...]
    report = await build(docs, provider, writer=stores.knowledge, description="a bicycle shop", cache_dir=".steward-cache")

    # management-approved information that outranks the other sources (prices, contacts, policies):
    approved = SourceDocument.from_markdown(approved_md, doc_id="approved", kind="approved", id_prefix="A01")
    await overlay_approved(stores.knowledge, [approved], provider)          # cheap: only the approved part is processed

Every model response is cached (cache_dir), so re-running is free and only changed inputs cost money. Crawling a
website or exporting documents is the host's job.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from steward.build.cache import BuildLLM, FileCache, MemoryCache
from steward.build.documents import SourceDocument, SourceSection, split_markdown
from steward.build.passes import (
    Unit,
    Vocab,
    assign,
    pass_b,
    pass_d,
    pass_s,
    pass_u,
    rank_values,
    section_lines,
    verify_values,
)
from steward.build.supersede import apply_overrides, supersede, with_marks
from steward.knowledge.index import KnowledgeIndex
from steward.knowledge.model import (
    Entity,
    KBMeta,
    KnowledgeSnapshot,
    Section,
    SupersedeOverride,
    TreeNode,
    Value,
    _to_plain,
)
from steward.knowledge.store import KnowledgeStore, KnowledgeWriter
from steward.providers.base import Provider


class TextIn(BaseModel):
    text: str


class MarkIn(BaseModel):
    unit: str
    line: str
    action: str
    by: str | None = None

__all__ = ["BuildReport", "KnowledgeAdmin", "SourceDocument", "SourceSection", "build", "overlay_approved", "set_override",
           "split_markdown", "version_of"]


@dataclass
class BuildReport:
    snapshot: KnowledgeSnapshot
    cost_usd: float
    calls: int
    cache_hits: int
    notes: list[str] = field(default_factory=list)
    dropped: list[tuple[str, Any]] = field(default_factory=list)


def version_of(snap: KnowledgeSnapshot) -> str:
    """Content hash: values, placement and supersede marks. A new version invalidates cached retrieval plans."""
    data = [[(v.id, v.num) for v in snap.values], [(s.id, s.topic_id, _to_plain(s.marks)) for s in snap.sections]]
    return hashlib.sha1(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:10]


def _llm(provider: Provider, cache_dir: str | Path | None, budget_usd: float, model: str | None) -> BuildLLM:
    return BuildLLM(provider, FileCache(cache_dir) if cache_dir else MemoryCache(), budget_usd, model)


def _section(u: Unit, m: dict[str, Any], topic: str) -> Section:
    return Section(id=u.id, title=m.get("title") or u.section.heading_path.split(" > ")[-1], text=u.text, topic_id=topic,
                   doc_id=u.doc.id, url=u.doc.url, heading_path=u.section.heading_path, summary=m.get("summary", ""),
                   keywords=tuple(m.get("keywords", [])[:8]), questions=tuple(q for q in m.get("questions", []) if q.strip())[:6],
                   tokens=u.section.tokens, authority=u.doc.kind)


def _entities(vocab: Vocab, values: list[Value]) -> tuple[Entity, ...]:
    out = {eid: Entity(eid, e.get("type") or eid.split(":")[0], e.get("title") or eid, tuple(sorted(set(e.get("aliases", [])))))
           for eid, e in vocab.entities.items()}
    for v in values:
        if v.entity not in out:
            out[v.entity] = Entity(v.entity, v.entity.split(":")[0], v.entity.split(":", 1)[-1].replace("_", " ").title())
    return tuple(out.values())


async def build(documents: list[SourceDocument], provider: Provider, *, writer: KnowledgeWriter | None = None,
                description: str = "a business", cache_dir: str | Path | None = None, budget_usd: float = 0.25,
                model: str | None = None, name: str = "", calculators: bool = True,
                overrides: list[SupersedeOverride] | None = None) -> BuildReport:
    """Full build: S (vocabulary) -> U (sections + values) -> verify -> B (tree) -> D (calculators) -> supersede."""
    llm = _llm(provider, cache_dir, budget_usd, model)
    units = [Unit(s, d) for d in documents for s in d.sections]
    by_id = {u.id: u for u in units}
    if len(by_id) != len(units):
        raise ValueError("section ids must be unique across documents (use id_prefix in SourceDocument.from_markdown)")
    vocab = await pass_s(llm, units, description)
    results = await pass_u(llm, units, vocab)
    meta, verified, dropped = verify_values(results, by_id, vocab)
    values = rank_values(verified)
    domains, placed = await pass_b(llm, units, meta)
    nodes = [TreeNode("ROOT", None, "root", name or description, vocab.summary)]
    for di, d in enumerate(domains, 1):
        nodes.append(TreeNode(f"D{di}", "ROOT", "domain", d["title"], d["description"], di))
        for ti, t in enumerate(d["topics"], 1):
            nodes.append(TreeNode(f"T{di}.{ti}", f"D{di}", "topic", t["title"], t["description"], ti))
    if any(u.id not in placed for u in units):
        nodes += [TreeNode("D0", "ROOT", "domain", "Other", "Sections not placed by the tree builder", 99),
                  TreeNode("T0.1", "D0", "topic", "Other", "Unplaced sections", 1)]
    sections = tuple(_section(u, meta.get(u.id, {}), placed.get(u.id, "T0.1")) for u in units)
    snap = KnowledgeSnapshot(KBMeta("pending", name or description, vocab.summary, time.time()), tuple(nodes), sections,
                             tuple(values), _entities(vocab, values))
    notes = [f"{len(documents)} documents, {len(units)} sections, {len(values)} values, {len(dropped)} values dropped"]
    if calculators:
        idx = KnowledgeIndex(snap)
        calcs, cnotes = await pass_d(llm, values, {e.id: e for e in snap.entities}, by_id,
                                     lambda e, i: next((idx.value(x, i.attribute) for x in
                                                        ([e] if i.entity == "$entity" else [i.entity]) + ([i.fallback_entity] if i.fallback_entity else [])
                                                        if idx.value(x, i.attribute)), None))
        snap = replace(snap, calculators=tuple(calcs))
        notes += cnotes
    marks, snotes = await supersede(llm, snap)
    snap = with_marks(snap, marks, overrides or [])
    notes += snotes
    snap = replace(snap, meta=replace(snap.meta, version=version_of(snap)))
    if writer is not None:
        await writer.write_snapshot(snap)
    return BuildReport(snap, round(llm.spent, 6), llm.calls, llm.hits, notes, dropped)


async def overlay_approved(store: Any, approved: list[SourceDocument], provider: Provider, *,
                           cache_dir: str | Path | None = None, budget_usd: float = 0.1, model: str | None = None) -> BuildReport:
    """(Re)load management-approved documents into an EXISTING knowledge base, without rebuilding the rest: sections and
    values (ranked above other sources), topic assignment into the existing tree, then the supersede pass. `store` must
    be a KnowledgeStore and KnowledgeWriter."""
    llm = _llm(provider, cache_dir, budget_usd, model)
    base: KnowledgeSnapshot = await store.load()
    keep_sections = [s for s in base.sections if s.authority != "approved"]
    approved_ids = {s.id for s in base.sections if s.authority == "approved"}
    keep_values = [v for v in base.values if v.section_id not in approved_ids]
    units = [Unit(s, replace(d, kind="approved")) for d in approved for s in d.sections]
    by_id = {u.id: u for u in units}
    clash = {u.id for u in units} & {s.id for s in keep_sections}
    if clash:
        raise ValueError(f"approved section ids clash with existing sections: {sorted(clash)[:5]} (use id_prefix)")
    attrs: dict[str, set[str]] = defaultdict(set)
    for v in base.values:
        attrs[v.entity.split(":")[0]].add(v.attribute)
    from steward.build.passes import vocab_from

    vocab = vocab_from(base.meta.summary, [{"id": e.id, "type": e.type, "title": e.title, "aliases": list(e.aliases)} for e in base.entities],
                       [{"name": a, "entity_type": t, "unit": None, "description": ""} for t, xs in attrs.items() for a in sorted(xs)])
    results = await pass_u(llm, units, vocab)
    meta, verified, dropped = verify_values(results, by_id, vocab)
    topics = {n.id: (n.title, n.description) for n in base.nodes if n.kind == "topic" and not n.id.startswith("T0.")}
    placed = await assign(llm, topics, section_lines(units, meta)) if topics and units else {}
    fallback = max(topics, key=lambda t: sum(s.topic_id == t for s in keep_sections)) if topics else "T0.1"
    new_sections = [_section(u, meta.get(u.id, {}), placed.get(u.id, fallback)) for u in units]
    # values: approved first in each (entity, attribute, conditions) group; the others shift down
    from steward.build.passes import VerifiedValue

    old_as_verified = [VerifiedValue(v.section_id, v.entity, v.attribute, v.raw, v.num, v.unit, list(v.conditions), v.source_quote,
                                     v.verified, "", 0, False) for v in keep_values]
    values = rank_values([*verified, *old_as_verified])
    entities = {e.id: e for e in base.entities}
    for v in values:
        if v.entity not in entities:
            entities[v.entity] = Entity(v.entity, v.entity.split(":")[0], v.entity.split(":", 1)[-1].replace("_", " ").title())
    snap = replace(base, sections=tuple([*keep_sections, *new_sections]), values=tuple(values), entities=tuple(entities.values()))
    overrides = await store.list_overrides() if hasattr(store, "list_overrides") else []
    marks, notes = await supersede(llm, snap)
    snap = with_marks(snap, marks, overrides)
    snap = replace(snap, meta=replace(snap.meta, version=version_of(snap), built_at=time.time()))
    await store.write_snapshot(snap)
    return BuildReport(snap, round(llm.spent, 6), llm.calls, llm.hits,
                       [f"{len(new_sections)} approved sections, {len(verified)} approved values", *notes], dropped)


async def set_override(store: Any, override: SupersedeOverride | None = None, *, clear: tuple[str, str] | None = None) -> str:
    """Admin review of supersede marks: add/replace an override (or clear one), re-apply it to that section, new version."""
    if override is not None:
        await store.put_override(override)
        section_id = override.section_id
    elif clear is not None:
        await store.delete_override(*clear)
        section_id = clear[0]
    else:
        raise ValueError("pass an override or clear=(section_id, line)")
    snap: KnowledgeSnapshot = await store.load()
    sec = next((s for s in snap.sections if s.id == section_id), None)
    if sec is None:
        raise KeyError(section_id)
    marks = apply_overrides(sec.marks, await store.list_overrides(), section_id)
    snap = replace(snap, sections=tuple(replace(s, marks=marks) if s.id == section_id else s for s in snap.sections))
    version = version_of(snap)
    await store.update_marks(section_id, marks, version)
    return version


class KnowledgeAdmin:
    """Endpoints for the admin page's approved-knowledge tab: the approved Markdown (kept in the RuntimeStore), a
    background overlay build, and the review of supersede marks. Pass it to create_router(..., knowledge_admin=...)."""

    def __init__(self, bot: Any, provider: Provider | None = None, *, cache_dir: str | Path | None = None,
                 budget_usd: float = 0.1, id_prefix: str = "A01") -> None:
        self.bot, self.provider = bot, provider or bot.provider
        self.cache_dir, self.budget, self.id_prefix = cache_dir, budget_usd, id_prefix
        self.build_state: dict[str, Any] = {"running": False, "log": "", "error": None, "finished": None}

    @property
    def store(self) -> KnowledgeStore:
        return self.bot.stores.knowledge

    async def text(self) -> str:
        return str(await self.bot.stores.runtime.get("knowledge.approved_markdown", "") or "")

    async def save_text(self, text: str) -> None:
        await self.bot.stores.runtime.set("knowledge.approved_markdown", text)

    async def rebuild(self) -> BuildReport:
        doc = SourceDocument.from_markdown(await self.text(), doc_id="approved", kind="approved", id_prefix=self.id_prefix)
        return await overlay_approved(self.store, [doc] if doc.sections else [], self.provider, cache_dir=self.cache_dir,
                                      budget_usd=self.budget)

    async def overview(self) -> dict[str, Any]:
        index = await self.bot.engine.index()
        marks = []
        for sid, s in index.sections.items():
            for m in s.marks:
                for i, line in enumerate(m.lines):
                    marks.append({"unit": sid, "unit_title": s.title, "line": line, "by": m.by,
                                  "by_title": index.sections[m.by].title if m.by in index.sections else "",
                                  "evidence": m.evidence[i] if i < len(m.evidence) else "", "why": m.why,
                                  "manual": m.manual, "rejected": line in m.rejected})
        return {"file": "approved information", "text": await self.text(), "kb_version": index.version,
                "approved": [{"id": s.id, "title": s.title, "topic": s.topic_id} for s in index.sections.values() if s.authority == "approved"],
                "sources": [{"id": s.id, "title": s.title} for s in index.sections.values() if s.authority != "approved"],
                "marks": marks, "build": self.build_state}

    def mount(self, router: Any, *, viewer: Any, editor: Any) -> None:
        import asyncio

        from fastapi import HTTPException

        @router.get("/knowledge/approved")
        async def get_approved(_: Any = viewer) -> dict[str, Any]:
            return await self.overview()

        @router.put("/knowledge/approved")
        async def put_approved(body: TextIn, _: Any = editor) -> dict[str, Any]:
            await self.save_text(body.text)
            return {"ok": True}

        @router.post("/knowledge/approved/build")
        async def build_approved(_: Any = editor) -> dict[str, Any]:
            if self.build_state["running"]:
                raise HTTPException(409, "a build is already running")
            self.build_state.update(running=True, log="", error=None)

            async def work() -> None:
                try:
                    report = await self.rebuild()
                    self.build_state["log"] = "\n".join(report.notes) + f"\ncost ${report.cost_usd:.4f}"
                except Exception as e:
                    self.build_state["error"] = str(e)[:500]
                finally:
                    self.build_state.update(running=False, finished=time.time())

            asyncio.get_running_loop().create_task(work())
            return {"started": True}

        @router.post("/knowledge/mark")
        async def mark(body: MarkIn, _: Any = editor) -> dict[str, Any]:
            index = await self.bot.engine.index()
            if body.unit not in index.sections:
                raise HTTPException(400, "unknown section")
            if body.action == "clear":
                version = await set_override(self.store, clear=(body.unit, body.line))
            elif body.action in ("reject", "add"):
                if body.action == "add" and (body.by not in index.sections or index.sections[body.by].authority != "approved"):
                    raise HTTPException(400, "'by' must be an approved section")
                if body.action == "add" and body.line.strip() not in [ln.strip() for ln in index.sections[body.unit].text.splitlines()]:
                    raise HTTPException(400, "that line is not in the section")
                version = await set_override(self.store, SupersedeOverride(body.unit, body.line.strip(), body.action,  # type: ignore[arg-type]
                                                                           body.by, time.time()))
            else:
                raise HTTPException(400, "action must be reject | add | clear")
            return {"kb_version": version}

        @router.get("/knowledge/section/{section_id}")
        async def section(section_id: str, _: Any = viewer) -> dict[str, Any]:
            index = await self.bot.engine.index()
            if section_id not in index.sections:
                raise HTTPException(404, "unknown section")
            s = index.sections[section_id]
            return {"id": s.id, "title": s.title, "authority": s.authority,
                    "lines": [ln for ln in s.text.splitlines() if ln.strip()],
                    "outdated": sorted(index.outdated.get(section_id, set()))}
