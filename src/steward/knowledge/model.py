"""The knowledge data model: a navigation tree of topics over section-sized units, plus numeric values, entities and
calculators. Storage-independent: any KnowledgeStore produces a KnowledgeSnapshot.

    root -> domain -> topic -> Section (one section of a source document)
    Section.values -> Value (numeric parameter: price, limit, rate...) of an Entity (plan:pro, product:x)
    Calculator: a formula over values + user inputs (e.g. monthly total for N seats)

Authority: sections from management-approved documents have authority "approved" and outrank "source" sections.
A source section may carry SupersedeMarks: lines an approved section contradicts; they are removed from its text.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Literal

Authority = Literal["source", "approved"]


@dataclass(frozen=True)
class TreeNode:
    id: str
    parent_id: str | None
    kind: Literal["root", "domain", "topic"]
    title: str
    description: str = ""
    order: int = 0


@dataclass(frozen=True)
class SupersedeMark:
    by: str  # id of the approved section that makes these lines outdated
    lines: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()  # the approved line contradicting each outdated line
    rejected: tuple[str, ...] = ()  # lines an admin decided to keep
    full: bool = False  # the whole section is outdated: hide it
    manual: bool = False  # added by an admin
    why: str = ""
    p: float | None = None

    @property
    def active_lines(self) -> tuple[str, ...]:
        return tuple(x for x in self.lines if x not in self.rejected)


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    text: str
    topic_id: str
    doc_id: str = ""
    url: str = ""
    heading_path: str = ""
    summary: str = ""
    keywords: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()  # customer questions this section answers (doc2query)
    tokens: int = 0
    authority: Authority = "source"
    marks: tuple[SupersedeMark, ...] = ()


@dataclass(frozen=True)
class Value:
    id: str
    section_id: str
    entity: str
    attribute: str
    num: float
    raw: str = ""
    unit: str | None = None
    conditions: tuple[str, ...] = ()
    source_quote: str = ""
    verified: bool = True
    conflict_group: str | None = None
    n_sources: int = 1
    rank: int = 0


@dataclass(frozen=True)
class Entity:
    id: str
    type: str
    title: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalcInput:
    var: str
    attribute: str
    entity: str = "$entity"  # "$entity" = the entity the customer means; else a fixed entity id
    fallback_entity: str | None = None


@dataclass(frozen=True)
class UserInput:
    var: str
    description: str
    ask: str = ""
    unit: str | None = None
    default: float | None = None
    assumption: str | None = None


@dataclass(frozen=True)
class Calculator:
    id: str
    title: str
    expression: str
    entity_type: str
    description: str = ""
    inputs: tuple[CalcInput, ...] = ()
    user_inputs: tuple[UserInput, ...] = ()
    applies_to: tuple[str, ...] = ()
    validated: bool = True


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    type: str
    note: str = ""


@dataclass(frozen=True)
class SupersedeOverride:
    section_id: str
    line: str
    action: Literal["reject", "add"]
    by_section: str | None = None
    ts: float = 0.0


@dataclass(frozen=True)
class KBMeta:
    version: str
    name: str = ""
    summary: str = ""
    built_at: float = 0.0


@dataclass(frozen=True)
class KnowledgeSnapshot:
    meta: KBMeta
    nodes: tuple[TreeNode, ...] = ()
    sections: tuple[Section, ...] = ()
    values: tuple[Value, ...] = ()
    entities: tuple[Entity, ...] = ()
    calculators: tuple[Calculator, ...] = ()
    edges: tuple[Edge, ...] = ()

    # ------------------------------------------------------------------ plain-data round trip (JSON files, document DBs)
    def to_dict(self) -> dict[str, Any]:
        return _to_plain(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> KnowledgeSnapshot:
        def secs(x: dict[str, Any]) -> Section:
            x = dict(x)
            x["keywords"], x["questions"] = tuple(x.get("keywords", ())), tuple(x.get("questions", ()))
            x["marks"] = tuple(_mark(m) for m in x.get("marks", ()))
            return Section(**x)

        def calc(x: dict[str, Any]) -> Calculator:
            x = dict(x)
            x["inputs"] = tuple(CalcInput(**i) for i in x.get("inputs", ()))
            x["user_inputs"] = tuple(UserInput(**i) for i in x.get("user_inputs", ()))
            x["applies_to"] = tuple(x.get("applies_to", ()))
            return Calculator(**x)

        return cls(
            meta=KBMeta(**d["meta"]),
            nodes=tuple(TreeNode(**n) for n in d.get("nodes", ())),
            sections=tuple(secs(s) for s in d.get("sections", ())),
            values=tuple(Value(**{**v, "conditions": tuple(v.get("conditions", ()))}) for v in d.get("values", ())),
            entities=tuple(Entity(**{**e, "aliases": tuple(e.get("aliases", ()))}) for e in d.get("entities", ())),
            calculators=tuple(calc(c) for c in d.get("calculators", ())),
            edges=tuple(Edge(**e) for e in d.get("edges", ())),
        )


ITEM_KINDS = ("nodes", "sections", "values", "entities", "calculators", "edges")


def snapshot_items(snap: KnowledgeSnapshot) -> list[tuple[str, str, dict[str, Any]]]:
    """(kind, id, data) per item: how document databases store a knowledge base (one document per item)."""
    d = snap.to_dict()
    out: list[tuple[str, str, dict[str, Any]]] = []
    for kind in ITEM_KINDS:
        for i, item in enumerate(d[kind]):
            out.append((kind, str(item.get("id") or f"{kind}-{i}"), item))
    return out


def snapshot_from_items(meta: dict[str, Any], items: list[tuple[str, dict[str, Any]]]) -> KnowledgeSnapshot:
    d: dict[str, Any] = {"meta": meta, **{k: [] for k in ITEM_KINDS}}
    for kind, data in items:
        d[kind].append(data)
    return KnowledgeSnapshot.from_dict(d)


def _mark(m: dict[str, Any]) -> SupersedeMark:
    return SupersedeMark(by=str(m["by"]), lines=tuple(m.get("lines", ())), evidence=tuple(m.get("evidence", ())),
                         rejected=tuple(m.get("rejected", ())), full=bool(m.get("full", False)),
                         manual=bool(m.get("manual", False)), why=str(m.get("why", "")), p=m.get("p"))


def _to_plain(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {f.name: _to_plain(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, tuple | list):
        return [_to_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    return obj


@dataclass(frozen=True)
class Hit:
    id: str
    score: float


@dataclass
class TopicView:
    """A topic with its parent domain, as the router sees it."""

    id: str
    title: str
    description: str
    domain_title: str = ""
    sections: list[str] = field(default_factory=list)
