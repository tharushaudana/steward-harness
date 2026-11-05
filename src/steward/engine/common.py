"""Shared engine pieces: thresholds (EngineSettings), the per-turn Run (trace + metered provider calls), and
generic text helpers (numbers, signatures). Nothing here knows any domain."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from steward.events import Trace
from steward.providers.base import Answer, ChatMessage, ChatResult, Provider, Question

ENGINE_V = "2.12"  # bump when routing/selection logic changes: invalidates cached retrieval plans


@dataclass(frozen=True)
class EngineSettings:
    """Retrieval thresholds and limits (code-side; not edited by admins). Defaults are the measured reference values."""

    topic: float = 0.5  # a topic is explored when P(answer under it) >= topic
    unit: float = 0.5  # a section is selected when P(contains needed info) >= unit
    unit_floor: float = 0.25  # soft floor: best weak sections when nothing passes
    calc: float = 0.5
    entity: float = 0.5
    sufficient: float = 0.5  # gate: below this, expand the search once
    about: float = 0.6  # about-fallback: sections describing the subject the message mentions
    about_max: int = 2
    max_topics: int = 3
    max_units: int = 4
    units_per_topic: int = 12
    units_per_part: int = 2
    max_units_multi: int = 6
    max_parts: int = 4
    shortlist: int = 8
    section_chars: int = 3000  # characters of each section given to the answer model
    models: Mapping[str, str] = field(default_factory=dict)  # purpose -> model id (answer, condense, extract, meta)

    def digest(self) -> str:
        d = {k: v for k, v in asdict(self).items() if k != "models"}
        return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:8]


class Run:
    """One turn's engine run: every provider call goes through here and lands in the trace."""

    def __init__(self, provider: Provider, settings: EngineSettings | None = None, trace: Trace | None = None) -> None:
        self.provider = provider
        self.settings = settings or EngineSettings()
        self.trace = trace or Trace()
        self.selected: set[str] = set()
        self.retrieved: set[str] = set()
        self.followed: set[tuple[str, str]] = set()

    def step(self, name: str, mechanism: Any, input: Any, output: Any, t0: float | None = None, **kw: Any) -> None:
        self.trace.add(name, mechanism, str(input)[:500], output, t0=t0 if t0 is not None else time.perf_counter(), **kw)

    async def decide(self, step: str, state: Mapping[str, Any], questions: Mapping[str, Question],
                     summary: str | None = None, purpose: str = "route") -> dict[str, Answer]:
        t0 = time.perf_counter()
        r = await self.provider.decide(state, questions, purpose=purpose)
        out = {k: (a.get("choice") if a.get("type") == "choice" else round(float(a.get("noul", a.get("score", 0)) or 0), 2))
               for k, a in r.answers.items()}
        u = r.usage
        self.trace.add(step, "decision", f"{len(questions)} questions | " + (summary or json.dumps(dict(state))[:200]), out,
                       t0=t0, tokens_in=u.tokens_in, tokens_out=u.tokens_out, cost_usd=u.cost_usd)
        return r.answers

    async def chat(self, step: str, messages: Sequence[ChatMessage], *, purpose: str = "answer",
                   **kw: Any) -> ChatResult:
        t0 = time.perf_counter()
        model = kw.pop("model", None) or self.settings.models.get(purpose)
        r = await self.provider.chat(messages, model=model, purpose=purpose, **kw)
        u = r.usage
        self.trace.add(step, "llm", messages[-1]["content"][:400], r.text[:600], t0=t0, tokens_in=u.tokens_in,
                       tokens_out=u.tokens_out, cost_usd=u.cost_usd)
        return r


# --------------------------------------------------------------------------------------------- text helpers
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
NUM_RE = re.compile(r"(?<![\w.])\$?(\d[\d,]*(?:\.\d+)?)\s*(k\b)?|(?<![a-z])(one|two|three|four|five|six|seven|eight|nine|ten)(?![a-z])",
                    re.I)


def extract_numbers(q: str) -> list[dict[str, Any]]:
    """Numbers in a question with their surrounding words (the decision model binds them to calculator inputs)."""
    out = []
    for m in NUM_RE.finditer(q):
        v = float(WORDNUM[m.group(3).lower()]) if m.group(3) else float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
        before = re.findall(r"[\w$%-]+", q[:m.start()])[-4:]
        after = re.findall(r"[\w$%-]+", q[m.end():])[:5]
        out.append({"raw": m.group(0).strip(), "value": v, "context": " ".join([*before, "[" + m.group(0).strip() + "]", *after])})
    return out[:4]


def signature(q: str) -> str:
    """Normalised question text for the plan cache."""
    return " ".join(re.findall(r"\w+", q.lower()))


def looks_multi(text: str) -> bool:
    """Cheap grammar-only hint (no domain words) that a first message may contain several questions."""
    return bool(re.search(r"\band\b|\balso\b|[,;]|\?.*\w.*\?", text.lower()))
