"""EmulatedDecider: answer decision batches with any chat model (one strict-JSON call per batch).

Use it when no dedicated decision model is available:

    provider = EmulatedDecider(my_chat_provider)            # chat() is passed through; decide() is emulated

Chat models are less calibrated than a decision model; `threshold_offset` lets you shift probabilities uniformly
(measure it with `steward eval calibrate`). Decision batches with many questions are split into chunks.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from steward.errors import ProviderError
from steward.providers.base import Answer, ChatMessage, ChatResult, Decisions, Provider, Question, Usage

SYSTEM = """You answer a batch of independent decision questions about the STATE (a customer message and context).
For each question id return:
- noul questions: the probability (0..1) that the statement is true, judged with the given criteria.
- choice questions: the best option and your confidence (0..1). Only use the listed options.
- score questions: a number.
Judge each question on its own; be calibrated (0.5 = unsure)."""


def _schema(questions: Mapping[str, Question]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for qid, q in questions.items():
        if q["type"] == "choice":
            opts = list((q.get("criteria") or {}).keys()) or ["none"]
            props[qid] = {"type": "object", "additionalProperties": False, "required": ["choice", "confidence"],
                          "properties": {"choice": {"type": "string", "enum": opts}, "confidence": {"type": "number"}}}
        else:
            props[qid] = {"type": "number"}
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


class EmulatedDecider:
    def __init__(self, chat_provider: Provider, *, model: str | None = None, max_questions: int = 40,
                 threshold_offset: float = 0.0) -> None:
        self.inner, self.model, self.max_questions, self.offset = chat_provider, model, max_questions, threshold_offset

    def estimate(self, kind: Literal["chat", "decide"], payload_chars: int, max_out: int = 0,
                 model: str | None = None) -> float:
        return self.inner.estimate("chat", payload_chars, max_out or 40 * self.max_questions, model or self.model)

    async def chat(self, messages: Sequence[ChatMessage], **kw: Any) -> ChatResult:
        return await self.inner.chat(messages, **kw)

    async def decide(self, state: Mapping[str, Any], questions: Mapping[str, Question], *,
                     model: str | None = None, purpose: str = "") -> Decisions:
        answers: dict[str, Answer] = {}
        total = Usage(model=model or self.model or "")
        items = list(questions.items())
        for i in range(0, len(items), self.max_questions):
            chunk = dict(items[i:i + self.max_questions])
            qtext = "\n".join(
                f"- {qid} ({q['type']}): {q['instructions']}"
                + (f"\n  criteria: {json.dumps(q.get('criteria'), ensure_ascii=False)}" if q.get("criteria") else "")
                for qid, q in chunk.items())
            r = await self.inner.chat(
                [{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": f"STATE:\n{json.dumps(dict(state), ensure_ascii=False)}\n\nQUESTIONS:\n{qtext}"}],
                model=model or self.model, max_tokens=40 * len(chunk) + 50, schema=_schema(chunk),
                schema_name="decisions", purpose=purpose or "decide")
            data = r.json
            if not isinstance(data, dict):
                raise ProviderError("emulated decision: the chat model did not return a JSON object")
            for qid, q in chunk.items():
                v = data.get(qid)
                if q["type"] == "choice":
                    v = v if isinstance(v, dict) else {}
                    conf = _clip(float(v.get("confidence") or 0.0) + self.offset)
                    answers[qid] = {"type": "choice", "choice": str(v.get("choice", "")), "confidence": conf,
                                    "probabilities": {str(v.get("choice", "")): conf}}
                elif q["type"] == "score":
                    answers[qid] = {"type": "score", "score": float(v or 0.0)}
                else:
                    answers[qid] = {"type": "noul", "noul": _clip(float(v or 0.0) + self.offset)}
            u = r.usage
            total = Usage(total.tokens_in + u.tokens_in, total.tokens_out + u.tokens_out, total.cost_usd + u.cost_usd,
                          total.latency_ms + u.latency_ms, u.model or total.model, u.cost_source)
        return Decisions(answers=answers, usage=total)


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))
