"""Engine: knowledge question answering (DADR) without the harness - used by the harness for its knowledge path,
by evaluations, and by hosts that only want grounded answers.

    engine = Engine(knowledge_store, provider)
    result = await engine.answer("How much is the pro plan?", history=[...], profile=config.profile)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from steward.engine.calc import calc_lines, fmt, run_calcs
from steward.engine.common import ENGINE_V, EngineSettings, Run, signature
from steward.engine.generate import GenerateOptions, generate, keyword_answer
from steward.engine.preprocess import meta_answer, understand
from steward.engine.route import merge_part_plans, route, route_parts
from steward.events import Trace
from steward.knowledge.index import KnowledgeIndex, script_ratio
from steward.knowledge.search import Lexicon
from steward.knowledge.store import KnowledgeStore
from steward.providers.base import Provider
from steward.stores.base import PlanCache

PROFILE_LABELS = (("name", "Name"), ("identity", "Who you are"), ("purpose", "Purpose"), ("tone", "Tone and style"),
                  ("goals", "Goals"), ("escalation", "When you can't help"))


def profile_block(profile: Any) -> str:
    """The ASSISTANT PROFILE block at the top of every answer prompt (from config.profile, a model or a dict)."""
    if profile is None:
        return ""
    get = (lambda k: profile.get(k)) if isinstance(profile, dict) else (lambda k: getattr(profile, k, None))
    lines = [f"{label}: {get(k)}" for k, label in PROFILE_LABELS if get(k)]
    return "ASSISTANT PROFILE (who you are; follow it):\n" + "\n".join(lines) + "\n\n" if lines else ""


def greeting_text(profile: Any, lang: str = "en") -> str:
    g = (profile.get("greeting") if isinstance(profile, dict) else getattr(profile, "greeting", None)) or "Hello!"
    return g if isinstance(g, str) else (g.get(lang) or next(iter(g.values()), "Hello!"))


@dataclass
class EngineResult:
    status: str  # answered | needs_input | insufficient | chitchat | meta | error
    text: str
    standalone: str | None = None
    units: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] = field(default_factory=dict)
    trace: Trace = field(default_factory=Trace)


class Engine:
    def __init__(self, knowledge: KnowledgeStore, provider: Provider, settings: EngineSettings | None = None,
                 plan_cache: PlanCache | None = None, lexicon: Lexicon | None = None) -> None:
        self.knowledge, self.provider = knowledge, provider
        self.settings = settings or EngineSettings()
        self.plan_cache, self.lexicon = plan_cache, lexicon
        self._index: KnowledgeIndex | None = None
        self._lock = asyncio.Lock()

    async def index(self) -> KnowledgeIndex:
        """The current knowledge index; rebuilt when the store's version changes (hot reload)."""
        v = await self.knowledge.version()
        if self._index is None or self._index.version != v:
            async with self._lock:
                if self._index is None or self._index.version != v:
                    self._index = KnowledgeIndex(await self.knowledge.load(), self.knowledge, self.lexicon)
        return self._index

    def run(self, trace: Trace | None = None) -> Run:
        return Run(self.provider, self.settings, trace)

    # ---------------------------------------------------------------------------------------- plan cache
    def cache_version(self, index: KnowledgeIndex) -> str:
        return f"{index.version}:{ENGINE_V}:{self.settings.digest()}"

    async def cached_plan(self, index: KnowledgeIndex, q: str) -> dict[str, Any] | None:
        return await self.plan_cache.get(signature(q), self.cache_version(index)) if self.plan_cache else None

    async def store_plan(self, index: KnowledgeIndex, q: str, plan: dict[str, Any]) -> None:
        if self.plan_cache and plan.get("confident") and plan.get("units"):
            keep = {k: plan[k] for k in ("topics", "units", "calcs", "entities", "bind") if k in plan}
            await self.plan_cache.put(signature(q), self.cache_version(index), keep)

    # ---------------------------------------------------------------------------------------- plain answering
    async def answer(self, query: str, *, history: list[dict[str, str]] | None = None, profile: Any = None,
                     mode: Literal["dadr", "keyword"] = "dadr", trace: Trace | None = None,
                     opts: GenerateOptions | None = None) -> EngineResult:
        run = self.run(trace)
        index = await self.index()
        pblock = profile_block(profile)
        opts = opts or GenerateOptions(profile_block=pblock)
        u = await understand(run, index, query, history)
        standalone = u.standalone if u.standalone != query else None
        if u.kind == "chitchat":
            text = greeting_text(profile)
            run.step("answer", "code", "small talk -> profile greeting, no retrieval", text)
            return EngineResult("chitchat", text, standalone, trace=run.trace)
        if u.kind in ("about_assistant", "about_conversation"):
            return EngineResult("meta", await meta_answer(run, pblock, query, history), standalone, trace=run.trace)
        if mode == "keyword":
            text, hits = await keyword_answer(run, index, u.search, pblock)
            return EngineResult("answered", text, standalone, hits, trace=run.trace)
        hit = await self.cached_plan(index, u.search)
        cached = hit is not None
        if hit is not None:
            plan = hit
            run.step("plan_cache", "cache", "plan cache hit", {k: plan[k] for k in ("units", "calcs", "entities", "bind") if k in plan})
        elif u.parts:
            plan = merge_part_plans(run, u.parts, await route_parts(run, index, u.parts))
        else:
            plan = await route(run, index, u.search, original=query if u.kind is None else None)
        if plan.get("kind") == "chitchat":
            return EngineResult("chitchat", greeting_text(profile), standalone, trace=run.trace)
        if plan.get("kind") in ("about_assistant", "about_conversation"):
            return EngineResult("meta", await meta_answer(run, pblock, query, history), standalone, trace=run.trace)
        units = plan["units"]
        results, asks = run_calcs(run, index, plan) if plan.get("calcs") else ([], [])
        status = "answered"
        if any(r["status"] == "PARTIAL" for r in results) or (results and not any(r["entity_known"] for r in results)):
            status = "needs_input"
        if not units and not results:
            status = "insufficient"
            escalation = (profile.get("escalation") if isinstance(profile, dict) else getattr(profile, "escalation", "")) or ""
            if abs(script_ratio(query) - script_ratio("not found")) > 0.5:  # the customer writes in another script
                text = await generate(run, index, u.standalone, [], [], [], original=query, history=history, opts=opts) or escalation
            else:
                text = ("I couldn't find that in the information I have, so I can't answer it reliably. " + escalation).strip()
                run.step("answer", "code", "no evidence -> escalate", text)
        elif not units and len(results) == 1 and results[0]["status"] == "OK" and not asks:
            r = results[0]
            text = (f"{r['title']} for {r['entity_title']}: {fmt(r['value'])}. Working: {r['steps']}"
                    + (f" (assuming {', '.join(r['assumptions'])})" if r["assumptions"] else "") + f" [C:{r['calc']}:{r['entity']}]")
            run.step("answer", "code", "template (single calculation)", text)
        else:
            text = await generate(run, index, u.standalone, units, results, asks, original=query, history=history, opts=opts)
            if text is None:
                text = ("Here is what I found:\n" + "\n".join(calc_lines(results)) + "\n"
                        + "\n".join(f"- {index.sections[s].title} [{s}]" for s in units) + ("\n\n" + " ".join(asks) if asks else ""))
                run.step("answer", "code", "fallback (number check failed twice)", text)
        if not cached and status == "answered":
            await self.store_plan(index, u.search, plan)
        for s in units:
            run.retrieved.add(f"S:{s}")
        return EngineResult(status, text, standalone, units, results, plan, run.trace)


def elapsed_ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)
