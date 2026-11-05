"""The build passes.

  S  vocabulary: entity types, entities (+aliases), numeric attributes - from the most number-dense source sections
  U  per section: title, routing summary, keywords, likely customer questions, and commercial numeric values
     (batched per document, <= ~5,000 chars per call; per-section retry on bad JSON; focused recall retry)
  verify (code): the quote must be in the section, the number in the quote; aliases normalised; conflicts grouped
  B  the navigation tree (domains -> topics) + assignment of sections to topics (batches of 60)
  D  calculators from the value catalog; validated in code (AST whitelist, inputs resolve, example evaluates)
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from steward.build.cache import BuildLLM
from steward.build.documents import SourceDocument, SourceSection
from steward.build.prompts import RECALL_NOTE, SCHEMA_D, SCHEMA_S, SCHEMA_U, SYS_B, SYS_B2, SYS_D, SYS_S, SYS_U
from steward.engine.calc import expr_vars, safe_eval
from steward.errors import ProviderError
from steward.knowledge.model import CalcInput, Calculator, Entity, UserInput, Value

WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30}
_PARAM = re.compile(r"\$\s?\d|\d[\d,.]*\s?%|\d[\d,.]*\+?\s*(?:/|per\b|a\s+(?:month|year)|minutes?|mins?|contacts?|days?|"
                    r"hours?|seats?|lines?|numbers?|users?|gb|mb|months?|years?)", re.I)


@dataclass
class Unit:
    """A source section during the build."""

    section: SourceSection
    doc: SourceDocument

    @property
    def id(self) -> str:
        return self.section.id

    @property
    def text(self) -> str:
        return self.section.text


@dataclass
class Vocab:
    summary: str = ""
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    attributes: list[dict[str, Any]] = field(default_factory=list)
    alias: dict[str, str] = field(default_factory=dict)
    attrs_by_type: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def text(self) -> str:
        return ("ENTITIES: " + "; ".join(f"{eid} ({e['title']})" for eid, e in self.entities.items()) + "\nATTRIBUTES: " +
                "; ".join(f"{a['entity_type']}.{a['name']} [{a.get('unit') or ''}] {a.get('description', '')}" for a in self.attributes))


# ------------------------------------------------------------------------------------------------ helpers
def canon(s: str) -> str:
    s = re.sub(r"[^a-z0-9:_]+", "_", s.strip().lower()).strip("_")
    return re.sub(r"_+", "_", s)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    for a, b in (("—", "-"), ("–", "-"), ("’", "'"), ("“", '"'), ("”", '"'), ("|", " "), ("$ ", "$"), ("•", " ")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def quote_check(quote: str, text: str) -> float:
    """How well a quote matches a span of the text (1.0 = verbatim after normalisation)."""
    q, p = norm(quote), norm(text)
    if not q:
        return 0.0
    if q in p:
        return 1.0
    sm = difflib.SequenceMatcher(None, p, q, autojunk=False)
    best = 0.0
    for b in sm.get_matching_blocks():
        win = p[max(0, b.a - b.b):max(0, b.a - b.b) + len(q) + 10]
        best = max(best, difflib.SequenceMatcher(None, win, q, autojunk=False).ratio())
    return best


def nums_in(s: str) -> set[float]:
    out = {float(v) for w, v in WORDNUM.items() if re.search("(?<![a-z])" + w + "(?![a-z])", s.lower())}
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", s.replace("$ ", "$")):
        with contextlib.suppress(ValueError):
            out.add(round(float(m.replace(",", "")), 6))
    return out


def path_depth(url: str) -> int:
    return len([p for p in re.sub(r"^\w+://[^/]+", "", url).split("/") if p])


def numeric_lines(units: list[Unit], limit_chars: int = 40000) -> list[str]:
    def score(u: Unit) -> float:
        return len(re.findall(r"\d[\d,.]*", u.text)) / (1 + len(u.text) / 1000)
    out, total = [], 0
    for u in sorted(units, key=score, reverse=True):
        if score(u) < 2:
            break
        t = f"=== {u.section.heading_path}\n{u.text}"
        if total + len(t) <= limit_chars:
            out.append(t)
            total += len(t)
    return out


# ------------------------------------------------------------------------------------------------ Pass S
async def pass_s(llm: BuildLLM, units: list[Unit], description: str) -> Vocab:
    lines = numeric_lines([u for u in units if u.doc.kind != "approved"])
    out = await llm.json(["S", description, lines], SYS_S, f"BUSINESS: {description}\nNUMERIC LINES:\n" + "\n".join(lines),
                         SCHEMA_S, "schema", 8000)
    return vocab_from(out.get("summary", ""), out.get("entities", []), out.get("attributes", []))


def vocab_from(summary: str, entities: list[dict[str, Any]], attributes: list[dict[str, Any]]) -> Vocab:
    v = Vocab(summary=summary)
    v.entities = {canon(e["id"]): e for e in entities}
    for eid, e in v.entities.items():
        for a in [*e.get("aliases", []), e.get("title", ""), eid.split(":", 1)[-1]]:
            v.alias.setdefault(canon(a), eid)
        v.alias[eid] = eid
    for a in attributes:
        a = dict(a)
        a["name"] = re.sub(rf"^{canon(a['entity_type'])}_", "", canon(a["name"]))
        v.attributes.append(a)
        v.attrs_by_type[canon(a["entity_type"])].add(a["name"])
    return v


# ------------------------------------------------------------------------------------------------ Pass U
async def pass_u(llm: BuildLLM, units: list[Unit], vocab: Vocab, concurrency: int = 6) -> list[tuple[list[Unit], dict[str, Any]]]:
    by_doc: dict[str, list[Unit]] = defaultdict(list)
    for u in units:
        by_doc[u.doc.id].append(u)
    batches: list[list[Unit]] = []
    for us in by_doc.values():
        cur: list[Unit] = []
        size = 0
        for u in us:
            if cur and size + len(u.text) > 5000:
                batches.append(cur)
                cur, size = [], 0
            cur.append(u)
            size += len(u.text)
        if cur:
            batches.append(cur)
    sem = asyncio.Semaphore(concurrency)
    vtext = vocab.text()

    async def do(us: list[Unit]) -> tuple[list[Unit], dict[str, Any]]:
        body = "\n\n".join(f"=== SECTION {u.id} ({u.section.heading_path})\n{u.text}" for u in us)
        try:
            async with sem:
                out = await llm.json(["U", vtext, body], SYS_U.format(vocab=vtext),
                                     f"DOCUMENT: {us[0].doc.url or us[0].doc.id} - {us[0].doc.title}\n\n{body}", SCHEMA_U, "sections", 12000)
            return us, out
        except (ValueError, ProviderError):
            if len(us) == 1:
                return us, {"units": [], "values": []}
            merged: dict[str, Any] = {"units": [], "values": []}
            for u in us:
                _, o = await do([u])
                merged["units"] += o["units"]
                merged["values"] += o["values"]
            return us, merged

    results = list(await asyncio.gather(*(do(b) for b in batches)))
    got: dict[str, int] = defaultdict(int)
    for _, out in results:
        for v in out["values"]:
            got[v["unit_id"]] += 1
    weak = [u for u in units if len(_PARAM.findall(u.text)) >= 4 and got[u.id] < 0.6 * len(_PARAM.findall(u.text))]

    async def redo(u: Unit) -> tuple[list[Unit], dict[str, Any]]:
        async with sem:
            out = await llm.json(["U-redo", vtext, u.id, u.text], SYS_U.format(vocab=vtext) + RECALL_NOTE,
                                 f"=== SECTION {u.id} ({u.section.heading_path})\n{u.text}", SCHEMA_U, "sections", 6000)
        return [u], {"units": [], "values": out.get("values", [])}  # keep the first pass's titles/summaries

    results += list(await asyncio.gather(*(redo(u) for u in weak)))
    return results


@dataclass
class VerifiedValue:
    section_id: str
    entity: str
    attribute: str
    raw: str
    num: float
    unit: str | None
    conditions: list[str]
    source_quote: str
    verified: bool
    url: str
    depth: int
    approved: bool


def verify_values(results: list[tuple[list[Unit], dict[str, Any]]], by_id: dict[str, Unit],
                  vocab: Vocab) -> tuple[dict[str, dict[str, Any]], list[VerifiedValue], list[tuple[str, Any]]]:
    meta: dict[str, dict[str, Any]] = {}
    vals: list[VerifiedValue] = []
    dropped: list[tuple[str, Any]] = []
    for us, out in results:
        for m in out.get("units", []):
            if m.get("id") in by_id:
                meta[m["id"]] = m
        for v in out.get("values", []):
            u = by_id.get(v["unit_id"]) or next((x for x in us if quote_check(v["source_quote"], x.text) >= 0.95), None)
            if u is None:
                dropped.append(("unknown section", v))
                continue
            q = quote_check(v["source_quote"], u.text)
            if q < 0.85:
                dropped.append((f"quote {q:.2f}", v))
                continue
            if not any(abs(v["num"] - x) < 1e-6 for x in nums_in(v["source_quote"]) | nums_in(v["value"])):
                dropped.append((f"num {v['num']} not in quote", v))
                continue
            e = canon(v["entity"])
            e = vocab.alias.get(e) or vocab.alias.get(e.split(":", 1)[-1]) or e
            if ":" not in e:
                e = "thing:" + e
            etype = e.split(":", 1)[0]
            a = re.sub(rf"^{etype}_", "", canon(v["attribute"]).replace(":", "_"))
            known = vocab.attrs_by_type.get(etype, set())
            if a not in known and known:
                close = difflib.get_close_matches(a, list(known), n=1, cutoff=0.85)
                a = close[0] if close else a
            conds = sorted(canon(x).replace("=", "_") for x in v.get("conditions", []) if x.strip())
            for cd in list(conds):  # "plan=pro" on an add-on value belongs to that plan
                cand = (vocab.alias.get(cd.replace("_", ":", 1)) or vocab.alias.get(cd.split("_", 1)[1])) if "_" in cd else None
                if cand and cand != e and cand.split(":")[0] != etype:
                    e = cand
                    conds.remove(cd)
                    break
            conds = [x for x in conds if vocab.alias.get(x.replace("_", ":", 1)) != e]
            vals.append(VerifiedValue(u.id, e, a, v["value"].strip(), float(v["num"]), v.get("unit"), conds, v["source_quote"].strip(),
                                      q >= 0.999, u.doc.url, path_depth(u.doc.url), u.doc.kind == "approved"))
    return meta, vals, dropped


def rank_values(vals: list[VerifiedValue], start: int = 1) -> list[Value]:
    """Group by (entity, attribute, conditions); approved first, then the number of documents stating a value."""
    groups: dict[tuple[str, str, tuple[str, ...]], list[VerifiedValue]] = defaultdict(list)
    for v in vals:
        groups[(v.entity, v.attribute, tuple(v.conditions))].append(v)
    out: list[Value] = []
    n = start
    for key, vs in sorted(groups.items()):
        byval: dict[float, list[VerifiedValue]] = defaultdict(list)
        for v in vs:
            byval[round(v.num, 6)].append(v)
        variants = []
        for same in byval.values():
            best = min(same, key=lambda x: (not x.approved, x.depth, not x.verified))
            variants.append((best, len({s.url or s.section_id for s in same})))
        variants.sort(key=lambda x: (not x[0].approved, -x[1], x[0].depth))
        cg = "CG" + hashlib.sha1(repr(key).encode()).hexdigest()[:6] if len(variants) > 1 else None
        for rank, (v, n_sources) in enumerate(variants):
            out.append(Value(f"V{n:04d}", v.section_id, v.entity, v.attribute, v.num, v.raw, v.unit, tuple(v.conditions),
                             v.source_quote, v.verified, cg, n_sources, rank))
            n += 1
    return out


# ------------------------------------------------------------------------------------------------ Pass B
def parse_tree(text: str) -> list[dict[str, Any]]:
    domains: list[dict[str, Any]] = []
    d = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("DOMAIN:"):
            p = [x.strip() for x in line[7:].split("|")] + [""]
            d = {"title": p[0], "description": p[1], "topics": []}
            domains.append(d)
        elif line.startswith("TOPIC:") and d is not None:
            p = [x.strip() for x in line[6:].split("|")] + [""]
            d["topics"].append({"title": p[0], "description": p[1], "units": []})
    return domains


def section_lines(units: list[Unit], meta: dict[str, dict[str, Any]]) -> list[str]:
    return [f"{u.id} | {u.doc.title[:40]} | {meta.get(u.id, {}).get('title', u.section.heading_path[:50])} | "
            f"{meta.get(u.id, {}).get('summary', '')[:160]}" for u in units]


async def assign(llm: BuildLLM, topics: dict[str, tuple[str, str]], lines: list[str]) -> dict[str, str]:
    """B2: section id -> topic id, batches of 60."""
    tlist = "\n".join(f"{tid}: {title} - {desc}" for tid, (title, desc) in topics.items())
    placed: dict[str, str] = {}
    for i in range(0, len(lines), 60):
        chunk = "\n".join(lines[i:i + 60])
        out = await llm.json(["B2", tlist, chunk], SYS_B2, f"TOPICS:\n{tlist}\n\nSECTIONS (id | document | title | summary):\n{chunk}",
                             None, "assign", 2000)
        for m in re.finditer(r"^\s*([\w.#:-]+)\s*->\s*([\w.]+)\s*$", out["text"], re.M):
            if m.group(2) in topics:
                placed.setdefault(m.group(1), m.group(2))
    return placed


async def pass_b(llm: BuildLLM, units: list[Unit], meta: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    lines = section_lines(units, meta)
    raw = await llm.json(["B", "\n".join(lines)], SYS_B, "SECTIONS (id | document | title | summary):\n" + "\n".join(lines),
                         None, "tree", 6000)
    domains = parse_tree(raw["text"])
    topics = {f"T{di}.{ti}": (t["title"], t["description"]) for di, d in enumerate(domains, 1)
              for ti, t in enumerate(d["topics"], 1)}
    placed = await assign(llm, topics, lines) if topics else {}
    by_doc: dict[str, list[str]] = defaultdict(list)
    for u in units:
        by_doc[u.doc.id].append(u.id)
    for u in units:  # unplaced -> the most common topic among its document's sections
        if u.id not in placed:
            sib = [placed[s] for s in by_doc[u.doc.id] if s in placed]
            if sib:
                placed[u.id] = max(set(sib), key=sib.count)
    return domains, placed


# ------------------------------------------------------------------------------------------------ Pass D
async def pass_d(llm: BuildLLM, values: list[Value], entities: dict[str, Entity], units: dict[str, Unit],
                 resolve: Any) -> tuple[list[Calculator], list[str]]:
    """Calculators proposed by the model, validated in code. `resolve(entity, CalcInput)` -> Value | None."""
    cat: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    units_of: dict[str, str | None] = {}
    for v in values:
        if v.rank == 0:
            cat[v.entity.split(":")[0]][v.attribute].append(f"{v.entity}={v.num:g}" + (f"({','.join(v.conditions)})" if v.conditions else ""))
            units_of.setdefault(v.attribute, v.unit)
    catalog = "\n".join(f"TYPE {t}:\n" + "\n".join(f"  {a} [{units_of.get(a) or ''}]: {', '.join(vs[:8])}" for a, vs in sorted(attrs.items()))
                        for t, attrs in cat.items() if sum(len(x) for x in attrs.values()) >= 3)
    if not catalog:
        return [], ["no numeric catalog: no calculators"]
    rank: dict[str, int] = defaultdict(int)
    for v in values:
        rank[v.section_id] += 1
    rules, total = [], 0
    for sid, _ in sorted(rank.items(), key=lambda x: -x[1]):
        t = f"--- {units[sid].section.heading_path}\n{units[sid].text[:2500]}" if sid in units else ""
        if total + len(t) > 14000:
            break
        rules.append(t)
        total += len(t)
    out = await llm.json(["D", catalog, rules], SYS_D, f"CATALOG:\n{catalog}\n\nRULE TEXT:\n" + "\n".join(rules), SCHEMA_D,
                         "calculators", 6000)
    calcs: list[Calculator] = []
    notes: list[str] = []
    all_entities = {v.entity for v in values} | set(entities)
    for c in out.get("calculators", []):
        expression = c["expression"].replace("$", "")
        inputs = tuple(CalcInput(i["var"].lstrip("$"), i["attribute"], i.get("entity") or "$entity", i.get("fallback_entity"))
                       for i in c["inputs"])
        user_inputs = tuple(UserInput(u["var"].lstrip("$"), u["description"], u.get("ask") or "", u.get("unit"), u.get("default"),
                                      u.get("assumption")) for u in c["user_inputs"])
        problems: list[str] = []
        try:
            used = expr_vars(expression)
            safe_eval(expression, dict.fromkeys(used, 1))
        except Exception as e:
            problems.append(f"bad expression: {e}")
            used = set()
        declared = {i.var for i in inputs} | {u.var for u in user_inputs}
        if used - declared:
            problems.append(f"undeclared variables {sorted(used - declared)}")
        etype = canon(c["entity_type"])
        applies = sorted(e for e in all_entities if e.split(":")[0] == etype and all(resolve(e, i) for i in inputs))
        if not applies:
            problems.append("its inputs resolve for no entity")
        if not problems:
            try:
                for e in applies:  # sanity: finite, non-negative for every entity, with the example's user values
                    env = {i.var: Decimal(str(resolve(e, i).num)) for i in inputs}
                    for uv in c["example"]["user_values"]:
                        env[uv["var"].lstrip("$")] = Decimal(str(uv["value"]))
                    for u in user_inputs:
                        env.setdefault(u.var, Decimal(str(u.default if u.default is not None else 1)))
                    r = float(safe_eval(expression, env))
                    if not (r == r and r >= 0):
                        raise ValueError(f"result {r} for {e}")
            except Exception as e:
                problems.append(f"example failed: {e}")
        cid = canon(c["id"])
        calcs.append(Calculator(cid, c["title"], expression, etype, c["description"], inputs, user_inputs, tuple(applies),
                                validated=not problems))
        notes.append(f"calculator {cid}: {'OK' if not problems else 'REJECTED (' + '; '.join(problems) + ')'}")
    return calcs, notes
