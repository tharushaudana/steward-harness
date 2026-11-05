"""Tools: host callbacks identified by a string id.

Config declares tool INSTANCES (what journeys reference): args with {{placeholders}}, an idempotency key, a timeout,
a mock result. Each instance points to an IMPLEMENTATION id (`impl`, defaulting to the instance id) that the host
registers in code:

    @bot.tool("crm.save_lead")
    async def save_lead(call: ToolCall) -> ToolResult:      # sync functions work too
        ref = await crm.upsert(call.args, key=call.idempotency_key)
        return ok({"id": ref}, receipt={"ref": ref})

A tool never breaks a conversation: exceptions and timeouts become a failed result (the journey takes its error /
timeout port). Every call leaves a Receipt, the only evidence the assistant may use to claim an action happened.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from steward.config.models import Config
from steward.errors import UnknownTool
from steward.harness.templates import render, render_obj
from steward.types import Receipt, ToolCall, ToolResult, TurnInfo

log = logging.getLogger("steward.tools")

ToolFn = Callable[[ToolCall], Any]  # returns ToolResult | dict | None (or an awaitable of those)


@dataclass
class ToolImpl:
    impl_id: str
    fn: ToolFn
    side_effect: bool = True
    timeout_s: float | None = None
    mock: ToolFn | Mapping[str, Any] | None = None
    description: str = ""


class ToolRegistry:
    def __init__(self) -> None:
        self._impls: dict[str, ToolImpl] = {}

    def register(self, impl_id: str, fn: ToolFn, *, side_effect: bool = True, timeout_s: float | None = None,
                 mock: ToolFn | Mapping[str, Any] | None = None, description: str = "") -> ToolFn:
        if not impl_id or not isinstance(impl_id, str):
            raise ValueError("a tool implementation id must be a non-empty string")
        self._impls[impl_id] = ToolImpl(impl_id, fn, side_effect, timeout_s, mock, description or (fn.__doc__ or "").strip())
        return fn

    def tool(self, impl_id: str, **kw: Any) -> Callable[[ToolFn], ToolFn]:
        """Decorator form of register()."""
        def deco(fn: ToolFn) -> ToolFn:
            return self.register(impl_id, fn, **kw)
        return deco

    def ids(self) -> set[str]:
        return set(self._impls)

    def get(self, impl_id: str) -> ToolImpl:
        if impl_id not in self._impls:
            raise UnknownTool(impl_id, self._impls)
        return self._impls[impl_id]

    def describe(self) -> list[dict[str, Any]]:
        return [{"id": t.impl_id, "side_effect": t.side_effect, "description": t.description} for t in self._impls.values()]

    async def call(self, cfg: Config, tool_id: str, env: Mapping[str, Any], turn: TurnInfo, *,
                   mode: str = "live") -> tuple[ToolResult, Receipt]:
        """Run a config tool instance. Never raises for runtime failures."""
        spec = cfg.tools.get(tool_id)
        if spec is None:
            res = ToolResult.failure(f"unknown tool instance '{tool_id}'")
            return res, Receipt(tool_id, "", False, error=res.error)
        impl_id = spec.impl or tool_id
        args = render_obj(spec.args, env)
        key = render(spec.idempotency_key, env) if spec.idempotency_key else None
        if key is not None and turn.is_test:
            key = f"test:{key}"  # a test conversation must never touch a real customer's record
        call = ToolCall(tool_id=tool_id, impl=impl_id, args=args, turn=turn, slots=dict(env.get("slots") or {}),
                        vars=dict(env.get("vars") or {}), state=dict(env.get("state") or {}), idempotency_key=key,
                        mode="mock" if mode == "mock" else "live", timeout_s=spec.timeout_ms / 1000,
                        settings=cfg.settings.model_dump())
        impl = self._impls.get(impl_id)
        try:
            if call.mode == "mock":
                if impl is not None and impl.mock is not None:
                    res = ToolResult.coerce(await _invoke(impl.mock, call, call.timeout_s))
                elif spec.mock is not None:
                    res = ToolResult.coerce(spec.mock)
                else:
                    res = ToolResult.failure(f"tool '{tool_id}' has no mock result")
            elif impl is None:
                res = ToolResult.failure(f"implementation '{impl_id}' is not registered")
            else:
                res = ToolResult.coerce(await _invoke(impl.fn, call, impl.timeout_s or call.timeout_s))
        except TimeoutError:
            res = ToolResult.failure("timeout", retryable=True)
        except Exception as e:
            log.exception("tool %s (%s) failed", tool_id, impl_id)
            res = ToolResult.failure(f"{type(e).__name__}: {e}"[:300])
        return res, Receipt(tool_id, impl_id, res.ok, res.duplicate, key, res.receipt, res.error)


async def _invoke(fn: Any, call: ToolCall, timeout_s: float) -> Any:
    if not callable(fn):
        return fn  # a static mock result
    if inspect.iscoroutinefunction(fn):
        return await asyncio.wait_for(fn(call), timeout_s)
    result = await asyncio.wait_for(asyncio.to_thread(fn, call), timeout_s)
    return await result if inspect.isawaitable(result) else result
