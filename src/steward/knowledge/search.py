"""Built-in keyword search (BM25, pure Python): used when the knowledge store has no full-text search of its own
(Firestore, MongoDB without Atlas Search, memory). Two weighted fields, like the reference implementation:
  title field (weight 3): title + keywords + questions + heading path
  body field  (weight 1): summary + text
A light suffix stemmer makes "plans"/"plan" and "pricing"/"price" meet. Any script works (Unicode word tokens).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from steward.knowledge.model import Hit, KnowledgeSnapshot

DEFAULT_STOPWORDS = frozenset(["a", "an", "the", "is", "are", "was", "were", "be", "been", "do", "does", "did", "can", "could", "i", "we", "you", "my", "our", "your", "it", "its", "of", "to", "in", "on", "for", "with", "and", "or", "if", "what", "which", "how", "much", "many", "any", "there", "this", "that", "these", "those", "me", "us", "per", "about", "from", "at", "by", "as", "will", "would", "should", "get", "have", "has", "need", "want", "use", "using", "like", "than", "then", "also", "just", "please", "tell", "know"])

_TOKEN = re.compile(r"\w+", re.UNICODE)


def stem(t: str) -> str:
    if len(t) <= 3 or not t.isascii():
        return t
    for suf, rep in (("ingly", ""), ("ations", "ate"), ("ation", "ate"), ("ings", ""), ("ing", ""), ("ies", "y"),
                     ("edly", ""), ("ed", ""), ("ly", ""), ("es", ""), ("s", "")):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            t = t[: len(t) - len(suf)] + rep
            break
    return t[:-1] if t.endswith("e") and len(t) > 4 else t


@dataclass
class Lexicon:
    stopwords: frozenset[str] = DEFAULT_STOPWORDS

    def tokens(self, text: str) -> list[str]:
        return [stem(t) for t in _TOKEN.findall(text.lower()) if len(t) > 1 and t not in self.stopwords]


@dataclass
class _Doc:
    id: str
    fields: dict[str, Counter[str]]
    lengths: dict[str, int]


@dataclass
class Bm25Index:
    lexicon: Lexicon = field(default_factory=Lexicon)
    weights: dict[str, float] = field(default_factory=lambda: {"title": 3.0, "body": 1.0})
    k1: float = 1.2
    b: float = 0.75
    _docs: dict[str, list[_Doc]] = field(default_factory=dict)  # kind -> docs
    _df: dict[str, Counter[str]] = field(default_factory=dict)
    _avg: dict[str, dict[str, float]] = field(default_factory=dict)

    def add(self, kind: str, doc_id: str, title: str, body: str) -> None:
        toks = {"title": self.lexicon.tokens(title), "body": self.lexicon.tokens(body)}
        doc = _Doc(doc_id, {f: Counter(t) for f, t in toks.items()}, {f: len(t) for f, t in toks.items()})
        self._docs.setdefault(kind, []).append(doc)
        df = self._df.setdefault(kind, Counter())
        for term in set(toks["title"]) | set(toks["body"]):
            df[term] += 1
        self._avg.pop(kind, None)

    def _averages(self, kind: str) -> dict[str, float]:
        if kind not in self._avg:
            docs = self._docs.get(kind, [])
            n = max(1, len(docs))
            self._avg[kind] = {f: max(1.0, sum(d.lengths[f] for d in docs) / n) for f in self.weights}
        return self._avg[kind]

    def search(self, text: str, kind: str = "section", limit: int = 20, exclude: Iterable[str] = ()) -> list[Hit]:
        terms = list(dict.fromkeys(self.lexicon.tokens(text)))
        docs = self._docs.get(kind, [])
        if not terms or not docs:
            return []
        n, df, avg, skip = len(docs), self._df[kind], self._averages(kind), set(exclude)
        idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in terms if df.get(t)}
        hits = []
        for d in docs:
            if d.id in skip:
                continue
            score = 0.0
            for t, w in idf.items():
                for f, weight in self.weights.items():
                    tf = d.fields[f].get(t, 0)
                    if tf:
                        norm = tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * d.lengths[f] / avg[f]))
                        score += weight * w * norm
            if score > 0:
                hits.append(Hit(d.id, round(score, 4)))
        hits.sort(key=lambda h: (-h.score, h.id))
        return hits[:limit]

    @classmethod
    def from_snapshot(cls, snap: KnowledgeSnapshot, lexicon: Lexicon | None = None) -> Bm25Index:
        idx = cls(lexicon=lexicon or Lexicon())
        for s in snap.sections:
            idx.add("section", s.id, " ".join([s.title, *s.keywords, *s.questions, s.heading_path]), f"{s.summary} {s.text}")
        for n in snap.nodes:
            if n.kind in ("domain", "topic"):
                idx.add("topic", n.id, n.title, n.description)
        for c in snap.calculators:
            idx.add("calculator", c.id, c.title, c.description)
        return idx
