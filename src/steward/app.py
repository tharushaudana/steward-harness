"""Steward: the facade a host system uses.

    from steward import Steward, Message, ok
    from steward.providers import OpenRouter

    bot = Steward.from_file("assistant.json", knowledge=my_store, provider=OpenRouter.from_env(spend_cap_usd=5))

    @bot.tool("crm.save_lead")
    async def save_lead(call):
        ...
        return ok({"id": ref}, receipt={"ref": ref})

    bot.check()                                     # fail fast: invalid config, unregistered tools
    reply = bot.handle(Message(session="u1", text="Hi"))          # sync hosts
    reply = await bot.ahandle(Message(session="u1", text="Hi"))   # async hosts

Configuration lives in a ConfigStore: the file/dict you pass seeds it as the first published version when the store
is empty; admin edits (draft -> publish) are kept there. Conversations always use the published config; pass
config="draft" to test the draft.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, Literal, TypeVar

from steward.config.loader import load_dict
from steward.config.models import Config
from steward.config.validate import validate
from steward.engine.common import EngineSettings
from steward.engine.core import Engine, profile_block
from steward.engine.generate import SYS_ANSWER
from steward.engine.preprocess import SYS_CONDENSE, SYS_META
from steward.errors import ConfigError, Issue
from steward.events import EventBus
from steward.harness.expr import ExprEngine, ExprFunction
from steward.harness.pipeline import Pipeline
from steward.harness.rules import rules_block
from steward.harness.turn import new_state
from steward.knowledge.store import KnowledgeStore, MemoryKnowledgeStore
from steward.providers.base import Provider
from steward.providers.metered import Metered
from steward.stores.base import ConfigDoc, ConfigVersion, Stores
from steward.stores.memory import memory_stores
from steward.tools.builtins import register_builtins
from steward.tools.registry import ToolFn, ToolRegistry
from steward.types import Message, Reply

T = TypeVar("T")
Which = Literal["published", "draft"]


class Steward:
    def __init__(self, config: Config | dict[str, Any] | None = None, *, provider: Provider,
                 stores: Stores | None = None, knowledge: KnowledgeStore | None = None,
                 engine: EngineSettings | None = None, spend_cap_usd: float | None = None) -> None:
        self.stores = stores or memory_stores()
        if knowledge is not None:
            self.stores.knowledge = knowledge
        if self.stores.knowledge is None:
            self.stores.knowledge = MemoryKnowledgeStore()
        cap = spend_cap_usd if spend_cap_usd is not None else getattr(provider, "spend_cap_usd", None)
        self.provider: Provider = provider if isinstance(provider, Metered) else Metered(provider, self.stores.ledger, cap)
        self.tools = ToolRegistry()
        self.expr = ExprEngine()
        self.events = EventBus()
        self.engine = Engine(self.stores.knowledge, self.provider, engine, self.stores.plan_cache)
        self.pipeline = Pipeline(self.engine, self.stores, self.tools, self.expr, self.events)
        self._seed: dict[str, Any] | None = (config.to_dict() if isinstance(config, Config) else
                                             (load_dict(config)[0].to_dict() if isinstance(config, dict) else None))
        self._cache: dict[str, Config] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_lock = threading.Lock()
        register_builtins(self.tools, self.stores)
        self.admin = Admin(self)
        self.sessions = Sessions(self)

    # ----------------------------------------------------------------------------------------- construction
    @classmethod
    def from_file(cls, path: str | Path, **kw: Any) -> Steward:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(raw, **kw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], **kw: Any) -> Steward:
        return cls(raw, **kw)

    # ----------------------------------------------------------------------------------------- registries
    def tool(self, impl_id: str, **kw: Any) -> Callable[[ToolFn], ToolFn]:
        """Register a tool implementation: @bot.tool("crm.save_lead") (side_effect=, timeout_s=, mock=)."""
        return self.tools.tool(impl_id, **kw)

    def expr_function(self, name: str) -> Callable[[ExprFunction], ExprFunction]:
        """Add a function to the expression language: @bot.expr_function("open_now") def f(ctx): ..."""
        def deco(fn: ExprFunction) -> ExprFunction:
            return self.expr.register(name, fn)
        return deco

    def on(self, event: str, fn: Any = None) -> Any:
        """Subscribe to events: handover, record, turn_end, tool_call, journey_end, ask_abandoned, error, ..."""
        return self.events.on(event, fn)

    # ----------------------------------------------------------------------------------------- configuration
    async def config(self, which: Which = "published") -> Config:
        doc = await self._doc(which)
        if doc is None:
            raise ConfigError("no configuration: pass one to Steward(...) or publish one through bot.admin")
        if doc.rev not in self._cache:
            self._cache[doc.rev] = load_dict(doc.config)[0]
            while len(self._cache) > 8:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[doc.rev]

    async def ensure_config(self) -> ConfigDoc | None:
        """Seed the ConfigStore with the configuration given at construction (first start); return the published doc."""
        return await self._doc("published")

    async def _doc(self, which: Which) -> ConfigDoc | None:
        cs = self.stores.config
        pub = await cs.published()
        if pub is None and self._seed is not None:  # first start: the given config becomes version 1
            await cs.save_draft(dict(self._seed, version=0), "none")
            pub = await cs.publish("initial configuration")
        if which == "draft":
            return (await cs.draft()) or pub
        return pub

    async def acheck(self, which: Which = "published") -> list[Issue]:
        """Validate the configuration against the registered tools; raises ConfigError on errors, returns warnings."""
        cfg = await self.config(which)
        issues = validate(cfg, tool_impls=self.tools.ids(), expr=self.expr)
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            raise ConfigError(issues)
        return issues

    def check(self, which: Which = "published") -> list[Issue]:
        return self._sync(self.acheck(which))

    # ----------------------------------------------------------------------------------------- conversations
    async def ahandle(self, msg: Message, *, config: Which = "published") -> Reply:
        return await self.pipeline.handle(await self.config(config), msg)

    def handle(self, msg: Message, *, config: Which = "published") -> Reply:
        """Synchronous version of ahandle() for non-async hosts (Django, Celery, scripts)."""
        return self._sync(self.ahandle(msg, config=config))

    async def prompt_preview(self, which: Which = "published") -> dict[str, str]:
        cfg = await self.config(which)
        return {"profile": profile_block(cfg.profile), "client_rules": rules_block(cfg), "core": SYS_ANSWER,
                "core_condense": SYS_CONDENSE, "core_meta": SYS_META}

    # ----------------------------------------------------------------------------------------- sync bridge
    def _sync(self, coro: Coroutine[Any, Any, T]) -> T:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            coro.close()
            raise RuntimeError("this event loop is already running: use the async method (e.g. await bot.ahandle(...))")
        with self._loop_lock:
            if self._loop is None:  # one private loop in a background thread, reused (clients stay bound to it)
                self._loop = asyncio.new_event_loop()
                threading.Thread(target=self._loop.run_forever, name="steward-loop", daemon=True).start()
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def close(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop = None


class Sessions:
    def __init__(self, bot: Steward) -> None:
        self._bot = bot

    async def aget(self, session: str) -> dict[str, Any]:
        state, _ = await self._bot.stores.sessions.load(session)
        return state or new_state()

    async def areset(self, session: str) -> None:
        await self._bot.stores.sessions.delete(session)

    async def arelease(self, session: str, by: str = "") -> dict[str, Any]:
        """A human agent hands the session back to the assistant after a handover."""
        store = self._bot.stores.sessions
        for _ in range(3):
            state, version = await store.load(session)
            state = state or new_state()
            flags = state.setdefault("flags", {})
            flags["handover_active"] = False
            flags["handover_epoch"] = int(flags.get("handover_epoch", 0) or 0) + 1  # a later handover is a new record
            if await store.compare_and_set(session, state, version):
                await self._bot.events.emit("release", {"session": session, "by": by})
                return state
        raise RuntimeError("could not release the session (concurrent updates)")

    def get(self, session: str) -> dict[str, Any]:
        return self._bot._sync(self.aget(session))

    def reset(self, session: str) -> None:
        self._bot._sync(self.areset(session))

    def release(self, session: str, by: str = "") -> dict[str, Any]:
        return self._bot._sync(self.arelease(session, by))


class Admin:
    """Draft -> validate -> publish, with history. Conversations always use the published configuration."""

    def __init__(self, bot: Steward) -> None:
        self._bot = bot

    async def adraft(self) -> ConfigDoc:
        doc = await self._bot._doc("draft")
        if doc is None:
            raise ConfigError("no configuration yet")
        return doc

    async def asave_draft(self, config: dict[str, Any], base_rev: str) -> tuple[ConfigDoc, list[Issue]]:
        cfg, _ = load_dict(config)  # structural errors raise ConfigError
        doc = await self._bot.stores.config.save_draft(cfg.to_dict(), base_rev)
        return doc, validate(cfg, tool_impls=self._bot.tools.ids(), expr=self._bot.expr)

    async def avalidate(self, config: dict[str, Any]) -> list[Issue]:
        try:
            cfg, _ = load_dict(config)
        except ConfigError as e:
            return e.issues
        return validate(cfg, tool_impls=self._bot.tools.ids(), expr=self._bot.expr)

    async def apublish(self, note: str = "") -> ConfigDoc:
        draft = await self._bot.stores.config.draft()
        if draft is None:
            raise ValueError("there is no draft to publish")
        issues = await self.avalidate(draft.config)
        if any(i.severity == "error" for i in issues):
            raise ConfigError(issues)
        doc = await self._bot.stores.config.publish(note)
        await self._bot.events.emit("config_published", {"version": doc.version, "note": note})
        return doc

    async def adiscard(self) -> None:
        await self._bot.stores.config.discard_draft()

    async def ahistory(self, limit: int = 30) -> list[ConfigVersion]:
        return await self._bot.stores.config.history(limit)

    async def arestore(self, version_id: str) -> ConfigDoc:
        """Copy an older published version into the draft (publish it to make it live)."""
        cs = self._bot.stores.config
        old = await cs.get_version(version_id)
        if old is None:
            raise ValueError(f"unknown version {version_id!r}")
        current = (await cs.draft()) or (await cs.published())
        return await cs.save_draft(old.config, current.rev if current else "none")

    # sync versions
    def draft(self) -> ConfigDoc:
        return self._bot._sync(self.adraft())

    def save_draft(self, config: dict[str, Any], base_rev: str) -> tuple[ConfigDoc, list[Issue]]:
        return self._bot._sync(self.asave_draft(config, base_rev))

    def validate(self, config: dict[str, Any]) -> list[Issue]:
        return self._bot._sync(self.avalidate(config))

    def publish(self, note: str = "") -> ConfigDoc:
        return self._bot._sync(self.apublish(note))

    def discard(self) -> None:
        self._bot._sync(self.adiscard())

    def history(self, limit: int = 30) -> list[ConfigVersion]:
        return self._bot._sync(self.ahistory(limit))

    def restore(self, version_id: str) -> ConfigDoc:
        return self._bot._sync(self.arestore(version_id))
