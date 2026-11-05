"""Model provider interface.

Steward needs two capabilities:
  chat()   - a chat model (answers, rewrites, extraction), optionally with a strict JSON schema
  decide() - a batch of bounded decisions answered in ONE call: yes/no probabilities ("noul"), choices, scores.
             DADR's routing asks dozens of these per turn, so they must be cheap and parallel.

The built-in OpenRouter provider uses the Jev decision model for decide(); EmulatedDecider lets any chat model
answer decision batches. Tests use steward.providers.fake.FakeProvider.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, NotRequired, Protocol, TypedDict, runtime_checkable


class Question(TypedDict):
    """One decision question. noul = probability that the statement is true; choice = one of the criteria keys;
    score = a number. criteria: for noul {"true": ..., "false": ...}; for choice {option: description}."""

    type: Literal["noul", "choice", "score"]
    instructions: str
    criteria: NotRequired[dict[str, str]]


class Answer(TypedDict, total=False):
    type: Literal["noul", "choice", "score"]
    noul: float  # probability of "true"
    choice: str
    probabilities: dict[str, float]
    confidence: float
    score: float


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    model: str = ""
    cost_source: str = "api"  # api | computed | estimated | cache


@dataclass
class ChatResult:
    text: str
    json: Any = None  # parsed object when a schema was requested
    usage: Usage = field(default_factory=Usage)


@dataclass
class Decisions:
    answers: dict[str, Answer]
    usage: Usage = field(default_factory=Usage)


@runtime_checkable
class Provider(Protocol):
    async def chat(self, messages: Sequence[ChatMessage], *, model: str | None = None, max_tokens: int = 600,
                   temperature: float = 0.0, schema: Mapping[str, Any] | None = None, schema_name: str = "output",
                   purpose: str = "") -> ChatResult: ...

    async def decide(self, state: Mapping[str, Any], questions: Mapping[str, Question], *,
                     model: str | None = None, purpose: str = "") -> Decisions: ...

    def estimate(self, kind: Literal["chat", "decide"], payload_chars: int, max_out: int = 0,
                 model: str | None = None) -> float:
        """Upper-bound cost estimate in USD, used by the spend cap before a call is made."""
        ...
