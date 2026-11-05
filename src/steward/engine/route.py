"""Dependency-aware decision retrieval (DADR): navigate the knowledge tree with batches of bounded decisions.

  shortlist   keyword search (top N sections)
  route       ONE decision call: topic relevance, shortlisted sections (two views + 'about'), calculators,
              entities, number bindings (+ any extra questions the harness lets ride along)
  select      sections inside the chosen topics
  gate        do the selected sections contain what is needed? if not: expand once (next topics + more search hits)
  about       gate failed -> add sections that describe the subject the message mentions
  soft floor  nothing passed -> best weak candidates
  coverage    every entity the question refers to must be covered by a selected section
  authority   a selected source section brings the approved section that corrects it
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from steward.engine.common import Run, extract_numbers
from steward.knowledge.index import KnowledgeIndex
from steward.knowledge.model import TopicView
from steward.providers.base import Answer, Question

SECTION_Q = "Does the section '{}' contain information needed to answer the question?"
ABOUT_Q = "Does the section '{}' describe the product, service or topic that the customer's message mentions?"
# The 'about' wording and criteria were measured: right sections 0.64-0.92, wrong ones <= 0.41. A phrasing like
# "is it about what the question asks?" made the decision model read 'about' as 'answers the exact detail'.
ABOUT_CRIT = {"true": "The section is about the same product, service or topic the message mentions, even if it does not "
                      "answer the exact detail asked (stock, quantity, delivery time, a comparison).",
              "false": "The section is about a different product, service or topic."}
GATE_Q: dict[str, Question] = {"sufficient": {
    "type": "noul",
    "instructions": "Do these sections, taken together, contain the specific information needed to answer the customer's question?",
    "criteria": {"true": "The answer (or a clear statement of the relevant rule/price/step) is present in the sections.",
                 "false": "The specific information asked about is missing from these sections."}}}
KIND_Q: dict[str, Question] = {"kind": {
    "type": "choice", "instructions": "What kind of message is the customer's latest message?",
    "criteria": {"request": "Asks for information or help about the business, its products, prices, policies or setup "
                            "(including short follow-ups).",
                 "chitchat": "Only a greeting, thanks, goodbye or small talk; no information request.",
                 "about_assistant": "Asks about the assistant itself: who/what it is, its name, what it can do, how it works.",
                 "about_conversation": "Asks about this conversation itself, e.g. summarise or repeat what was said earlier."}}}


def topic_label(t: TopicView) -> str:
    return f"{t.domain_title} › {t.title}: {t.description}"


def section_label(index: KnowledgeIndex, sid: str, q: str | None = None) -> str:
    """Title + summary + likely questions; with a question, also the 2 lines with most word overlap (facts in passing)."""
    s = index.sections[sid]
    base = f"{s.title}: {s.summary}"
    if s.questions:
        base += " | answers questions like: " + "; ".join(s.questions[:6])
    if not q:
        return base
    qt = set(index.lexicon.tokens(q))
    lines = [ln.strip() for ln in index.text(sid).splitlines() if len(ln.strip()) > 15]
    scored = sorted(((len(qt & set(index.lexicon.tokens(ln))), i, ln) for i, ln in enumerate(lines)), reverse=True)
    snip = [ln[:220] for n, _, ln in scored[:2] if n > 0]
    return base + (" | relevant lines: " + " / ".join(snip) if snip else "")


def section_questions(index: KnowledgeIndex, ids: list[str], q: str, prefix: str = "u") -> dict[str, Question]:
    qs: dict[str, Question] = {}
    for i, sid in enumerate(ids):
        plain = section_label(index, sid)
        qs[f"{prefix}{i}"] = {"type": "noul", "instructions": SECTION_Q.format(plain)}
        focused = section_label(index, sid, q)
        if focused != plain:
            qs[f"{prefix}{i}s"] = {"type": "noul", "instructions": SECTION_Q.format(focused)}
        qs[f"{prefix}{i}a"] = {"type": "noul", "instructions": ABOUT_Q.format(plain), "criteria": ABOUT_CRIT}
    return qs


def _noul(a: dict[str, Answer], key: str) -> float:
    return float((a.get(key) or {}).get("noul", 0.0) or 0.0)


def section_scores(a: dict[str, Answer], ids: list[str], prefix: str = "u") -> dict[str, float]:
    return {sid: max(_noul(a, f"{prefix}{i}"), _noul(a, f"{prefix}{i}s")) for i, sid in enumerate(ids)}


def about_scores(a: dict[str, Answer], ids: list[str], prefix: str = "u") -> dict[str, float]:
    return {sid: _noul(a, f"{prefix}{i}a") for i, sid in enumerate(ids)}


def pick_sections(run: Run, index: KnowledgeIndex, scores: dict[str, float]) -> list[str]:
    """Topic-diverse selection: best section of each topic first (round-robin), so a multi-part question keeps one
    section per part instead of several from the same topic."""
    st = run.settings
    passing = sorted(((s, p) for s, p in scores.items() if p >= st.unit), key=lambda x: -x[1])
    by_topic: dict[str, list[str]] = {}
    for sid, _ in passing:
        by_topic.setdefault(index.sections[sid].topic_id, []).append(sid)
    out, rnd = [], 0
    while len(out) < st.max_units and any(len(v) > rnd for v in by_topic.values()):
        for t in by_topic:
            if rnd < len(by_topic[t]) and len(out) < st.max_units:
                out.append(by_topic[t][rnd])
        rnd += 1
    return out


async def route(run: Run, index: KnowledgeIndex, q: str, *, original: str | None = None, tag: str = "",
                extra_qs: dict[str, Question] | None = None) -> dict[str, Any]:
    """Plan for one question: {topics, units, calcs, entities, bind, confident, kind?, extra}.
    `original`: ask the message kind in the same call (the caller has not classified it yet).
    `extra_qs`: questions from the harness that ride in the route call; their answers come back in plan["extra"]."""
    st = run.settings
    t0 = time.perf_counter()
    nums = extract_numbers(q)
    short = await index.search(q, "section", st.shortlist)
    run.step("shortlist" + tag, "code", q, {"sections": [(h.id, round(h.score, 2)) for h in short]}, t0)
    calcs = list(index.calcs.values())
    ents = index.calc_entities
    ui_vars: dict[str, str] = {}
    for c in calcs:
        for ui in c.user_inputs:
            ui_vars.setdefault(ui.var, ui.description)

    qs: dict[str, Question] = {}
    for i, t in enumerate(index.topics):
        qs[f"t{i}"] = {"type": "noul",
                       "instructions": f"Could information needed to answer the question be found under the topic '{topic_label(t)}'?"}
    short_ids = [h.id for h in short]
    qs.update(section_questions(index, short_ids, q))
    for i, c in enumerate(calcs):
        need = ", ".join(ui.description for ui in c.user_inputs) or "no extra inputs"
        qs[f"c{i}"] = {"type": "noul",
                       "instructions": f"Does answering the question require computing: '{c.title}' ({c.description}; uses: {need})?",
                       "criteria": {"true": "The customer asks for a cost/total/amount this calculation produces, even if some inputs are missing.",
                                    "false": "The calculation is not what the customer asks for."}}
    for i, e in enumerate(ents):
        x = index.entities[e]
        al = ", ".join(x.aliases[:4])
        qs[f"e{i}"] = {"type": "noul", "instructions": f"Does the customer's question refer to the {x.type.replace('_', ' ')} "
                                                       f"'{x.title}'" + (f" (also called: {al})" if al else "") + "?"}
    if ui_vars and nums:
        crit = dict(ui_vars)
        crit["other"] = "Something else (a date, an id, a price the customer quotes, etc.)."
        for j, n in enumerate(nums):
            qs[f"n{j}"] = {"type": "choice", "instructions": f"In the question, what does the number {n['raw']} (in '{n['context']}') specify?",
                           "criteria": crit}
    state: dict[str, Any] = {"question": q}
    if original is not None:
        qs["kind"] = KIND_Q["kind"]
        state["latest_message"] = original
    if extra_qs:
        qs.update(extra_qs)
    if not qs:
        return {"topics": [], "units": [], "calcs": [], "entities": [], "bind": {}, "confident": False, "extra": {}}
    a = await run.decide("route" + tag, state, qs, summary=f"{len(index.topics)} topics, {len(short)} sections, {len(calcs)} calcs, "
                         f"{len(ents)} entities, {len(nums)} numbers" + (f", {len(extra_qs)} harness triggers" if extra_qs else ""))
    extra = {key: a[key] for key in (extra_qs or {}) if key in a}
    if "kind" in qs and float(a["kind"].get("confidence", 0) or 0) >= 0.6 and a["kind"].get("choice") != "request":
        return {"kind": a["kind"].get("choice"), "topics": [], "units": [], "calcs": [], "entities": [], "bind": {},
                "confident": False, "extra": extra}

    topic_p = sorted(((index.topics[i].id, _noul(a, f"t{i}")) for i in range(len(index.topics))), key=lambda x: -x[1])
    unit_p, about_p = section_scores(a, short_ids), about_scores(a, short_ids)
    calc_p = {c.id: _noul(a, f"c{i}") for i, c in enumerate(calcs)}
    ent_p = {e: _noul(a, f"e{i}") for i, e in enumerate(ents)}
    bind: dict[str, float] = {}
    for j, n in enumerate(nums):
        ch = (a.get(f"n{j}") or {}).get("choice")
        if ch and ch != "other":
            bind.setdefault(ch, n["value"])
    chosen = [t for t, p in topic_p if p >= st.topic][: st.max_topics] or ([topic_p[0][0]] if topic_p else [])

    # select: sections inside the chosen topics (ranked by keyword search within a large topic)
    cand: list[str] = []
    for t in chosen:
        ids = [s for s in index.sections_by_topic.get(t, []) if s not in unit_p]
        if len(ids) > st.units_per_topic:
            ranked = [h.id for h in await index.search(q, "section", 300) if h.id in ids]
            ids = (ranked + [s for s in ids if s not in ranked])[: st.units_per_topic]
        cand += ids
    if cand:
        a2 = await run.decide("select" + tag, {"question": q}, section_questions(index, cand, q),
                              summary=f"{len(cand)} sections in topics {chosen}")
        unit_p.update(section_scores(a2, cand))
        about_p.update(about_scores(a2, cand))
    units = pick_sections(run, index, unit_p)
    calcs_sel = [c for c, p in calc_p.items() if p >= st.calc]
    ents_sel = [e for e, p in ent_p.items() if p >= st.entity]

    # gate + one expansion round
    confident = True
    if units or not calcs_sel:
        gstate = {"question": q, "sections": [f"{index.sections[s].title}: {index.text(s)[:1200]}" for s in units] or ["(none)"]}
        g = await run.decide("gate" + tag, gstate, GATE_Q, summary=f"{len(units)} sections")
        if _noul(g, "sufficient") < st.sufficient:
            confident = False
            more = [t for t, p in topic_p[len(chosen):len(chosen) + 3] if p >= 0.15]
            extra_ids = [h.id for h in await index.search(q, "section", 20) if h.id not in unit_p]
            for t in more:
                ranked = [h.id for h in await index.search(q, "section", 300) if h.id in index.sections_by_topic.get(t, [])]
                extra_ids += [s for s in (ranked + index.sections_by_topic.get(t, []))[:6] if s not in unit_p and s not in extra_ids]
            extra_ids = extra_ids[:16]
            if extra_ids:
                a3 = await run.decide("expand" + tag, {"question": q}, section_questions(index, extra_ids, q),
                                      summary=f"{len(extra_ids)} more sections (next topics {more} + search ranks 9-20)")
                unit_p.update(section_scores(a3, extra_ids))
                about_p.update(about_scores(a3, extra_ids))
                units = pick_sections(run, index, unit_p)
                chosen += more
    if not confident:  # about-fallback: approved sections about the subject first, then the rest by score
        about = [s for s, p in sorted(about_p.items(), key=lambda x: (index.sections[x[0]].authority != "approved", -x[1]))
                 if p >= st.about and s not in units][: st.about_max]
        if about:
            run.step("about_fallback" + tag, "code", f"gate failed; sections about the subject (>= {st.about}) added",
                     {s: round(about_p[s], 2) for s in about})
            units = units + about
    if not units and not calcs_sel and unit_p:  # soft floor
        best = sorted(unit_p.items(), key=lambda x: -x[1])[:2]
        units = [s for s, p in best if p >= st.unit_floor]
        confident = False
        if units:
            run.step("soft_floor" + tag, "code", f"no section >= {st.unit}; using best >= {st.unit_floor}",
                     {s: round(unit_p[s], 2) for s in units})
    run.selected.update(chosen)
    added: list[str] = []  # entity coverage (an entity a selected calculator computes with is covered by the calculation)
    calc_covered = {e for c in calcs_sel for e in index.calcs[c].applies_to}
    for e in sorted(ents_sel, key=lambda x: -ent_p[x])[:4]:
        if e in calc_covered:
            continue
        # approved knowledge about the entity always comes along, even when a source section already mentions it:
        # the approved section is where the current figures are
        approved = next((s for s in index.approved_sections_for(e) if s not in units + added), None)
        if approved and not any(index.sections[s].authority == "approved" and index.covers(s, e) for s in units + added):
            added.append(approved)
        elif not any(index.covers(s, e) for s in units + added):
            best_s = next((s for s in index.entity_sections(e) if s not in units + added), None)
            if best_s:
                added.append(best_s)
    if added:
        run.step("coverage" + tag, "code", "entities in the question without (approved) evidence: their best sections added",
                 {s: index.sections[s].title for s in added})
        units = units + added
        confident = False
    followed = index.with_authority(units)
    if len(followed) > len(units):
        run.step("authority" + tag, "code", "source sections superseded by approved sections: approved sections added",
                 {"added": followed[len(units):]})
        units = followed
    return {"topics": chosen, "units": units, "unit_p": unit_p, "calcs": calcs_sel, "entities": ents_sel, "bind": bind,
            "confident": confident, "extra": extra}


async def route_parts(run: Run, index: KnowledgeIndex, parts: list[str],
                      extra_qs: dict[str, Question] | None = None) -> list[dict[str, Any]]:
    """Route every part of a multi-part message in parallel (each part also gets its own kind question)."""
    return list(await asyncio.gather(*(route(run, index, p, original=p, tag=f"[{i + 1}]", extra_qs=extra_qs)
                                       for i, p in enumerate(parts[: run.settings.max_parts]))))


def merge_part_plans(run: Run, parts: list[str], plans: list[dict[str, Any]], claimed: set[int] | None = None) -> dict[str, Any]:
    """Merge per-part plans; each request part contributes its best sections round-robin. Claimed parts (handled by a
    journey or policy) contribute nothing."""
    st, claimed = run.settings, claimed or set()
    merged: dict[str, Any] = {"topics": [], "units": [], "calcs": [], "entities": [], "bind": {}, "confident": True, "parts": []}
    per_part: list[list[str]] = []
    for i, (p_text, pl) in enumerate(zip(parts, plans, strict=False)):
        kind = pl.get("kind", "request")
        merged["parts"].append({"q": p_text, "kind": kind, "units": pl["units"][: st.units_per_part], "claimed": i in claimed})
        if kind != "request" or i in claimed:
            continue
        per_part.append(pl["units"][: st.units_per_part])
        for key in ("topics", "calcs", "entities"):
            merged[key] += [x for x in pl[key] if x not in merged[key]]
        for k, v in pl["bind"].items():
            merged["bind"].setdefault(k, v)
        merged["confident"] = merged["confident"] and pl.get("confident", False)
    for rnd in range(st.units_per_part):
        for us in per_part:
            if rnd < len(us) and us[rnd] not in merged["units"] and len(merged["units"]) < st.max_units_multi:
                merged["units"].append(us[rnd])
    if not per_part and not claimed:
        merged["kind"] = "about_assistant" if any(p["kind"].startswith("about") for p in merged["parts"]) else "chitchat"
    run.step("merge_parts", "code", f"{len(parts)} parts, claimed: {sorted(claimed)}", merged["parts"])
    return merged


def strip_citations(text: str) -> str:
    return re.sub(r"\s*\[[^\]]*\]", "", text)
