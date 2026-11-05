"""OpenRouter provider: chat models via /v1/chat/completions and decision batches via the Jev decision model
(/api/alpha/decisions). Async, with retries on transient errors and cost taken from the API's usage (or computed
from the public model price list).

    provider = OpenRouter.from_env(spend_cap_usd=5.0)            # reads OPENROUTER_API_KEY
    provider = OpenRouter(api_key, chat_model="z-ai/glm-5.3-flash", models={"extract": "anthropic/claude-haiku-5.5"})

`models` maps a call purpose (answer, condense, extract, meta, build, ...) to a model id; chat() picks
models[purpose] when no explicit model is given, else `chat_model`.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any, Literal

import httpx

from steward.errors import ProviderError
from steward.providers.base import Answer, ChatMessage, ChatResult, Decisions, Question, Usage

DEFAULT_BASE = "https://openrouter.ai/api"
DEFAULT_CHAT = "z-ai/glm-5.3-flash"
DEFAULT_DECIDE = "typesafe/jev-1.14-20261014"
# Reasoning settings per model. Default for everything else: reasoning disabled.
DEFAULT_REASONING: dict[str, dict[str, Any] | None] = {"z-ai/glm-5.3-flash": {"effort": "low", "exclude": True}}
# USD per token (input, output), used only when the API does not report a cost and the price list is unavailable.
FALLBACK_PRICES: dict[str, tuple[float, float]] = {
    "z-ai/glm-5.3-flash": (0.031e-6, 0.085e-6),
    "anthropic/claude-haiku-5.5": (0.10e-6, 0.50e-6),
    "typesafe/jev": (0.042e-6, 0.0),
}
_RETRY_STATUS = {408, 429, 500, 502, 503, 504, 524, 529}


class OpenRouter:
    def __init__(self, api_key: str, *, chat_model: str = DEFAULT_CHAT, decide_model: str = DEFAULT_DECIDE,
                 models: Mapping[str, str] | None = None, reasoning: Mapping[str, dict[str, Any] | None] | None = None,
                 base_url: str = DEFAULT_BASE, timeout_s: float = 120.0, retries: int = 2,
                 app_name: str = "steward", transport: httpx.AsyncBaseTransport | None = None,
                 spend_cap_usd: float | None = None, prices: Mapping[str, tuple[float, float]] | None = None) -> None:
        if not api_key:
            raise ProviderError("OpenRouter needs an API key (set OPENROUTER_API_KEY or pass api_key=...)")
        self.chat_model, self.decide_model = chat_model, decide_model
        self.models = dict(models or {})
        self.reasoning = {**DEFAULT_REASONING, **(reasoning or {})}
        self.base_url, self.retries = base_url.rstrip("/"), retries
        self.spend_cap_usd = spend_cap_usd  # read by Steward, which wraps this provider in Metered
        self._prices: dict[str, tuple[float, float]] = dict(prices or {})
        self._prices_loaded = prices is not None
        self._client = httpx.AsyncClient(timeout=timeout_s, transport=transport, headers={
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "X-Title": app_name})

    @classmethod
    def from_env(cls, *, spend_cap_usd: float | None = None, **kw: Any) -> OpenRouter:
        cap = spend_cap_usd
        if cap is None and os.environ.get("STEWARD_SPEND_CAP_USD"):
            cap = float(os.environ["STEWARD_SPEND_CAP_USD"])
        return cls(os.environ.get("OPENROUTER_API_KEY", ""), spend_cap_usd=cap, **kw)

    async def aclose(self) -> None:
        await self._client.aclose()

    # -------------------------------------------------------------------------------- pricing
    async def _load_prices(self) -> None:
        if self._prices_loaded:
            return
        self._prices_loaded = True
        try:
            r = await self._client.get(f"{self.base_url}/v1/models", timeout=20)
            for m in r.json().get("data", []):
                p = m.get("pricing") or {}
                try:
                    self._prices[m["id"]] = (float(p.get("prompt") or 0), float(p.get("completion") or 0))
                except (TypeError, ValueError):
                    continue
        except Exception:  # pricing is only a fallback for missing usage.cost
            pass

    def price(self, model: str) -> tuple[float, float]:
        if model in self._prices:
            return self._prices[model]
        for prefix, p in FALLBACK_PRICES.items():
            if model.lstrip("~").startswith(prefix):
                return p
        return (1e-6, 5e-6)

    def estimate(self, kind: Literal["chat", "decide"], payload_chars: int, max_out: int = 0,
                 model: str | None = None) -> float:
        model = model or (self.decide_model if kind == "decide" else self.chat_model)
        pin, pout = self.price(model)
        return max(((payload_chars / 3) * pin + max_out * pout) * 1.5, 0.00002)

    # -------------------------------------------------------------------------------- http
    async def _post(self, path: str, body: dict[str, Any]) -> tuple[dict[str, Any], float]:
        t0 = time.perf_counter()
        for attempt in range(self.retries + 1):
            try:
                r = await self._client.post(f"{self.base_url}{path}", json=body)
            except httpx.TransportError as e:
                if attempt < self.retries:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                raise ProviderError(f"OpenRouter transport error: {e}") from None
            if r.status_code in _RETRY_STATUS and attempt < self.retries:
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise ProviderError(f"OpenRouter HTTP {r.status_code}: {r.text[:400]}")
            try:
                return r.json(), (time.perf_counter() - t0) * 1000
            except ValueError:
                raise ProviderError(f"OpenRouter returned invalid JSON: {r.text[:200]}") from None
        raise ProviderError("OpenRouter: retries exhausted")  # pragma: no cover

    def _cost(self, usage: Mapping[str, Any], model: str, tin: int, tout: int) -> tuple[float, str]:
        cost = usage.get("cost")
        if cost is not None:
            return float(cost), "api"
        pin, pout = self.price(model)
        return tin * pin + tout * pout, "computed"

    # -------------------------------------------------------------------------------- Provider protocol
    async def chat(self, messages: Sequence[ChatMessage], *, model: str | None = None, max_tokens: int = 600,
                   temperature: float = 0.0, schema: Mapping[str, Any] | None = None, schema_name: str = "output",
                   purpose: str = "") -> ChatResult:
        model = model or self.models.get(purpose) or self.chat_model
        body: dict[str, Any] = {"model": model, "messages": list(messages), "max_tokens": max_tokens,
                                "temperature": temperature, "usage": {"include": True}}
        if schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": schema_name, "strict": True, "schema": dict(schema)}}
        reasoning = self.reasoning.get(model, {"enabled": False})
        if reasoning is not None:
            body["reasoning"] = reasoning
        await self._load_prices()
        data, ms = await self._post("/v1/chat/completions", body)
        u = data.get("usage") or {}
        tin, tout = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        cost, src = self._cost(u, model, tin, tout)
        try:
            choice = data["choices"][0]
            text = (choice["message"].get("content") or "").strip()
        except (KeyError, IndexError, TypeError):
            raise ProviderError(f"OpenRouter: unexpected chat response: {str(data)[:200]}") from None
        usage = Usage(tin, tout, cost, ms, str(data.get("model", model)), src)
        if not text:
            raise ProviderError(f"OpenRouter: empty content from {model} (finish={choice.get('finish_reason')})")
        parsed = parse_json(text) if schema is not None else None
        return ChatResult(text=text, json=parsed, usage=usage)

    async def decide(self, state: Mapping[str, Any], questions: Mapping[str, Question], *,
                     model: str | None = None, purpose: str = "") -> Decisions:
        model = model or self.decide_model
        body = {"model": model, "state": dict(state), "questions": dict(questions)}
        await self._load_prices()
        data, ms = await self._post("/alpha/decisions", body)
        u = data.get("usage") or {}
        tin, tout = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
        cost, src = self._cost(u, model, tin, tout)
        answers: dict[str, Answer] = data.get("answers") or {}
        missing = set(questions) - set(answers)
        if missing:
            raise ProviderError(f"decision response is missing answers for {sorted(missing)}")
        return Decisions(answers=answers, usage=Usage(tin, tout, cost, ms, str(data.get("model", model)), src))


def parse_json(text: str) -> Any:
    """Parse model JSON output, tolerating code fences and leading/trailing prose."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except json.JSONDecodeError:
                pass
        raise ProviderError(f"model output is not valid JSON: {text[:200]}") from None
