"""A scripted provider for tests, demos and agents: no network, no credentials, deterministic.

    fake = FakeProvider()
    fake.on_decide("j_lead*", {"type": "noul", "noul": 0.9})          # question ids, fnmatch patterns
    fake.on_decide("kind", {"type": "choice", "choice": "request", "confidence": 0.9})
    fake.on_chat(lambda msgs: "Hello!" if "hi" in msgs[-1]["content"].lower() else None)
    fake.on_chat("price", "Our price is listed in the catalogue.")  # substring of the last message -> text
    fake.on_chat_json("extract", {"name": "Ada", "phone": None})     # purpose -> JSON (for schema calls)

Unscripted decisions default to "no" (noul 0.0), the neutral option ("none" or "other") for choices that have one
(else the first option with confidence 0.0),
and score 0.0. Unscripted chats return `default_text`. Every call is recorded in `fake.calls`.
"""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from steward.providers.base import Answer, ChatMessage, ChatResult, Decisions, Question, Usage

ChatRule = Callable[[Sequence[ChatMessage]], Any]


@dataclass
class FakeCall:
    kind: Literal["chat", "decide"]
    purpose: str
    payload: Any
    result: Any


@dataclass
class FakeProvider:
    default_text: str = "OK."
    calls: list[FakeCall] = field(default_factory=list)
    _decide_rules: list[tuple[str, Any]] = field(default_factory=list)
    _chat_rules: list[ChatRule] = field(default_factory=list)
    _json_by_purpose: dict[str, Any] = field(default_factory=dict)

    # -------------------------------------------------------------------------------- scripting
    def on_decide(self, pattern: str, answer: Answer | Callable[[Mapping[str, Any], Question], Answer]) -> FakeProvider:
        """Answer question ids matching `pattern` (fnmatch). The answer may be a callable(state, question)."""
        self._decide_rules.append((pattern, answer))
        return self

    def on_chat(self, rule: ChatRule | str, reply: Any = None) -> FakeProvider:
        """A callable(messages) returning text/dict/None, or a substring of the last message plus the reply."""
        if isinstance(rule, str):
            needle, value = rule.lower(), reply
            self._chat_rules.append(lambda msgs: value if needle in msgs[-1]["content"].lower() else None)
        else:
            self._chat_rules.append(rule)
        return self

    def on_chat_json(self, purpose: str, value: Any) -> FakeProvider:
        """Structured (schema) calls with this purpose return `value` as JSON."""
        self._json_by_purpose[purpose] = value
        return self

    # -------------------------------------------------------------------------------- Provider protocol
    async def chat(self, messages: Sequence[ChatMessage], *, model: str | None = None, max_tokens: int = 600,
                   temperature: float = 0.0, schema: Mapping[str, Any] | None = None, schema_name: str = "output",
                   purpose: str = "") -> ChatResult:
        value: Any = None
        if schema is not None and purpose in self._json_by_purpose:
            value = self._json_by_purpose[purpose]
        else:
            for rule in reversed(self._chat_rules):
                value = rule(messages)
                if value is not None:
                    break
        if value is None:
            value = {} if schema is not None else self.default_text
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        parsed = (value if not isinstance(value, str) else _try_json(value)) if schema is not None else None
        result = ChatResult(text=text, json=parsed, usage=Usage(model=model or "fake-chat", cost_source="computed"))
        self.calls.append(FakeCall("chat", purpose, list(messages), result))
        return result

    async def decide(self, state: Mapping[str, Any], questions: Mapping[str, Question], *,
                     model: str | None = None, purpose: str = "") -> Decisions:
        answers: dict[str, Answer] = {}
        for qid, q in questions.items():
            answers[qid] = self._answer(qid, q, state)
        result = Decisions(answers=answers, usage=Usage(model=model or "fake-decide", cost_source="computed"))
        self.calls.append(FakeCall("decide", purpose, {"state": dict(state), "questions": dict(questions)}, result))
        return result

    def estimate(self, kind: Literal["chat", "decide"], payload_chars: int, max_out: int = 0,
                 model: str | None = None) -> float:
        return 0.0

    # -------------------------------------------------------------------------------- helpers
    def _answer(self, qid: str, q: Question, state: Mapping[str, Any]) -> Answer:
        for pattern, answer in reversed(self._decide_rules):
            if fnmatch.fnmatchcase(qid, pattern):
                a = cast(Mapping[str, Any], answer(state, q) if callable(answer) else answer)
                return cast(Answer, {"type": q["type"], **a})
        if q["type"] == "choice":
            criteria = q.get("criteria") or {}
            for neutral in ("none", "other"):  # unscripted: the neutral option when there is one
                if neutral in criteria:
                    return {"type": "choice", "choice": neutral, "confidence": 1.0, "probabilities": {neutral: 1.0}}
            options = list(criteria)
            return {"type": "choice", "choice": options[0] if options else "", "confidence": 0.0}
        if q["type"] == "score":
            return {"type": "score", "score": 0.0}
        return {"type": "noul", "noul": 0.0}

    def decisions_asked(self, purpose: str | None = None) -> list[str]:
        """All question ids asked so far (optionally for one purpose): handy in assertions."""
        return [qid for c in self.calls if c.kind == "decide" and (purpose is None or c.purpose == purpose)
                for qid in c.payload["questions"]]


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None
