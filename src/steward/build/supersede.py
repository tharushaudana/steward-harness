"""Supersede pass: which source statements do the approved sections correct or replace?

  1 decision model, one batch per approved section: does it make a statement in each candidate source section wrong?
    (candidates: same topic + keyword search, at most 8)
  2 LLM, only for the hits: the outdated source lines WITH the approved line contradicting each; both are verified
    verbatim in code (no evidence -> not superseded)
  3 decision model, per (source line, approved line) pair, with both section names (a spec line of one product must not
    be "contradicted" by another product's): kept at >= 0.5

Measured on a real knowledge base: the LLM alone over-marked; evidence + the calibrated per-line check left only real
conflicts. Marks are proposals: admins review them (overrides survive re-runs)."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import replace
from typing import Any

from steward.build.cache import BuildLLM
from steward.build.passes import quote_check
from steward.build.prompts import SCHEMA_SUP, SUPERSEDE_LINE_CRIT, SUPERSEDE_LINE_Q, SUPERSEDE_Q, SYS_SUP
from steward.knowledge.model import KnowledgeSnapshot, Section, SupersedeMark, SupersedeOverride
from steward.knowledge.search import Bm25Index


async def supersede(llm: BuildLLM, snap: KnowledgeSnapshot, *, per_approved: int = 8, threshold: float = 0.5,
                    line_threshold: float = 0.5) -> tuple[dict[str, tuple[SupersedeMark, ...]], list[str]]:
    """Returns {source section id: marks} for every source section an approved section supersedes, plus log notes."""
    sections = {s.id: s for s in snap.sections}
    approved = [s for s in snap.sections if s.authority == "approved"]
    notes: list[str] = []
    if not approved:
        return {}, notes
    index = Bm25Index.from_snapshot(snap)

    def candidates(a: Section) -> list[str]:
        same = [s.id for s in snap.sections if s.topic_id == a.topic_id and s.authority != "approved"]
        ranked = [h.id for h in index.search(a.title + " " + a.text, "section", 30) if sections[h.id].authority != "approved"]
        return list(dict.fromkeys([*[x for x in ranked if x in same], *[x for x in ranked if x not in same], *same]))[:per_approved]

    async def step1(a: Section) -> list[tuple[Section, str, float]]:
        cand = candidates(a)
        if not cand:
            return []
        state = {"approved_section": f"{a.title}\n{a.text}",
                 "source_sections": [f"[{w}] {sections[w].title}\n{sections[w].text[:1500]}" for w in cand]}
        qs: dict[str, Any] = {f"w{i}": {"type": "noul", "instructions": SUPERSEDE_Q.format(f"[{w}] {sections[w].title}")}
                              for i, w in enumerate(cand)}
        ans = await llm.decide(["SUP", a.text, [sections[w].text for w in cand]], state, qs)
        return [(a, w, float(ans[f"w{i}"].get("noul", 0) or 0)) for i, w in enumerate(cand)]

    hits = [h for res in await asyncio.gather(*(step1(a) for a in approved)) for h in res if h[2] >= threshold]
    notes.append(f"supersede step 1: {len(approved)} approved sections, {len(hits)} candidate pairs")

    async def step2(a: Section, w: str, p: float) -> tuple[str, SupersedeMark] | None:
        src = sections[w]
        out = await llm.json(["SUP2", a.text, src.text], SYS_SUP, f"APPROVED SECTION:\n{a.text}\n\nSOURCE SECTION [{w}]:\n{src.text}",
                             SCHEMA_SUP, "superseded", 1500)
        wl = [ln.strip() for ln in src.text.splitlines() if ln.strip()]
        lines: list[str] = []
        evidence: list[str] = []
        for o in out.get("outdated", []):
            sl, al = str(o.get("source_line", "")).strip(), str(o.get("approved_line", "")).strip()
            if not sl or not al or not wl:
                continue
            best = max(wl, key=lambda x: quote_check(sl, x))
            if quote_check(sl, best) >= 0.9 and quote_check(al, a.text) >= 0.9 and best not in lines:
                lines.append(best)
                evidence.append(al)
        if lines:
            wsec, asec = f"{src.heading_path} / {src.title}", f"{a.heading_path} / {a.title}"
            qs: dict[str, Any] = {f"l{i}": {"type": "noul", "instructions": SUPERSEDE_LINE_Q.format(wsec, ln, asec, ev),
                                            "criteria": SUPERSEDE_LINE_CRIT} for i, (ln, ev) in enumerate(zip(lines, evidence, strict=True))}
            ans = await llm.decide(["SUP-line", wsec, asec, lines, evidence], {"comparison": "source line vs approved line"}, qs)
            keep = [i for i in range(len(lines)) if float(ans[f"l{i}"].get("noul", 0) or 0) >= line_threshold]
            lines, evidence = [lines[i] for i in keep], [evidence[i] for i in keep]
        if not lines:
            return None
        full = bool(out.get("full")) and len(lines) >= 0.8 * len(wl)
        return w, SupersedeMark(a.id, tuple(lines), tuple(evidence), (), full, False, str(out.get("why", "")), round(p, 2))

    marks: dict[str, list[SupersedeMark]] = defaultdict(list)
    for res in await asyncio.gather(*(step2(a, w, p) for a, w, p in hits)):
        if res is not None:
            marks[res[0]].append(res[1])
    notes.append(f"supersede: {len(marks)} source sections marked")
    return {k: tuple(v) for k, v in marks.items()}, notes


def apply_overrides(marks: tuple[SupersedeMark, ...], overrides: list[SupersedeOverride], section_id: str) -> tuple[SupersedeMark, ...]:
    """Admin review on top of automatic marks: 'reject' keeps a line (listed in mark.rejected); 'add' marks a line by hand."""
    out = [replace(m, rejected=()) for m in marks if not m.manual]
    for o in overrides:
        if o.section_id != section_id:
            continue
        if o.action == "reject":
            out = [replace(m, rejected=(*m.rejected, o.line)) if o.line in m.lines else m for m in out]
        elif o.action == "add" and o.by_section:
            out.append(SupersedeMark(o.by_section, (o.line,), ("(marked by an admin)",), (), False, True, "marked outdated by an admin"))
    return tuple(out)


def with_marks(snap: KnowledgeSnapshot, marks: dict[str, tuple[SupersedeMark, ...]], overrides: list[SupersedeOverride]) -> KnowledgeSnapshot:
    sections = tuple(replace(s, marks=apply_overrides(marks.get(s.id, ()), overrides, s.id)) if s.authority != "approved" else s
                     for s in snap.sections)
    return replace(snap, sections=sections)
