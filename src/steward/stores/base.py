"""Storage interfaces. Steward never talks to a database directly: it uses these protocols, so any backend works
(memory, SQLite, MongoDB, Firestore, or your own). Every adapter must pass the contract tests in
`steward.testing.contracts`.

All methods are async. A synchronous implementation (e.g. a Django ORM store) can be adapted with
`steward.stores.sync.wrap(...)`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ------------------------------------------------------------------------------------------------ session state
@runtime_checkable
class SessionStateStore(Protocol):
    """Per-conversation state (slots, flags, pending ask, turn counter) with optimistic concurrency.

    load() returns (state, version); version 0 means "no state yet".
    compare_and_set() writes only if the stored version still equals `expected_version` and returns False otherwise
    (never overwrite silently). After a successful write the stored version is expected_version + 1.
    """

    async def load(self, session: str) -> tuple[dict[str, Any] | None, int]: ...

    async def compare_and_set(self, session: str, state: dict[str, Any], expected_version: int) -> bool: ...

    async def delete(self, session: str) -> None: ...


# ------------------------------------------------------------------------------------------------ records
@dataclass(frozen=True)
class NewRecord:
    type: str  # e.g. "lead", "handover", "ticket"
    data: dict[str, Any]
    session: str
    key: str | None = None  # idempotency key: a second append with the same key merges instead of inserting
    is_test: bool = False


@dataclass(frozen=True)
class Record:
    id: str
    type: str
    data: dict[str, Any]
    session: str
    key: str | None
    ts: float
    status: str = "open"
    is_test: bool = False


@dataclass(frozen=True)
class AppendResult:
    id: str
    duplicate: bool = False  # the key already existed
    enriched: bool = False  # ...and new non-empty fields were merged into it


@runtime_checkable
class RecordStore(Protocol):
    """Things the assistant created (leads, handovers, ...). append() is idempotent by key: on a duplicate key the
    non-empty fields of the new data are merged into the existing record atomically."""

    async def append(self, record: NewRecord) -> AppendResult: ...

    async def list(self, type: str | None = None, limit: int = 200, include_test: bool = False) -> list[Record]: ...

    async def set_status(self, record_id: str, status: str) -> None: ...


# ------------------------------------------------------------------------------------------------ runtime switches
@runtime_checkable
class RuntimeStore(Protocol):
    """Small live settings shared by all sessions (e.g. "agents_online"). Last write wins."""

    async def get(self, key: str, default: Any = None) -> Any: ...

    async def set(self, key: str, value: Any) -> None: ...


# ------------------------------------------------------------------------------------------------ configuration
@dataclass(frozen=True)
class ConfigDoc:
    config: dict[str, Any]
    rev: str  # content hash, used for optimistic locking of drafts
    version: int = 0  # published version number


@dataclass(frozen=True)
class ConfigVersion:
    id: str
    version: int
    rev: str
    ts: float
    note: str = ""


@runtime_checkable
class ConfigStore(Protocol):
    """Published configuration + one working draft + history of replaced versions.

    save_draft() raises steward.errors.RevisionConflict when `base_rev` is not the current draft's rev (or the
    published rev when there is no draft). publish() archives the current published config, bumps the version
    and removes the draft, atomically. Validation is the caller's job (the core validates before publishing).
    """

    async def published(self) -> ConfigDoc | None: ...

    async def draft(self) -> ConfigDoc | None: ...

    async def save_draft(self, config: dict[str, Any], base_rev: str) -> ConfigDoc: ...

    async def discard_draft(self) -> None: ...

    async def publish(self, note: str = "") -> ConfigDoc: ...

    async def history(self, limit: int = 30) -> list[ConfigVersion]: ...

    async def get_version(self, version_id: str) -> ConfigDoc | None: ...


# ------------------------------------------------------------------------------------------------ cost ledger
@dataclass(frozen=True)
class LedgerEntry:
    purpose: str  # route, answer, extract, eval, build, ...
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    ts: float = 0.0
    turn_id: str | None = None
    latency_ms: float = 0.0
    ok: bool = True
    error: str | None = None
    cost_source: str = "api"  # api | computed | estimated


@dataclass(frozen=True)
class LedgerSummary:
    total_usd: float
    calls: int
    by_purpose: dict[str, float] = field(default_factory=dict)
    by_model: dict[str, float] = field(default_factory=dict)


@runtime_checkable
class LedgerStore(Protocol):
    """Append-only record of paid calls; the spend cap is enforced against total()."""

    async def record(self, entry: LedgerEntry) -> None: ...

    async def total(self) -> float: ...

    async def summary(self) -> LedgerSummary: ...


# ------------------------------------------------------------------------------------------------ plan cache
@runtime_checkable
class PlanCache(Protocol):
    """Best-effort cache of retrieval plans, keyed by a question signature and a version string
    (knowledge version + engine version + thresholds). A miss is always safe."""

    async def get(self, signature: str, version: str) -> dict[str, Any] | None: ...

    async def put(self, signature: str, version: str, plan: dict[str, Any]) -> None: ...

    async def clear(self) -> None: ...


# ------------------------------------------------------------------------------------------------ chat log
@dataclass(frozen=True)
class TurnLog:
    session: str
    message: str
    reply: str
    status: str
    ts: float
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    standalone: str | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    is_test: bool = False


@runtime_checkable
class ChatLog(Protocol):
    """Optional: every turn with its trace, to diagnose answers later."""

    async def append(self, turn: TurnLog) -> None: ...

    async def list(self, limit: int = 50, status: str | None = None) -> list[TurnLog]: ...


# ------------------------------------------------------------------------------------------------ bundle
@dataclass
class Stores:
    """All stores a Steward instance uses. `knowledge` is typed loosely here to avoid an import cycle; it must
    implement steward.knowledge.store.KnowledgeStore."""

    sessions: SessionStateStore
    records: RecordStore
    runtime: RuntimeStore
    config: ConfigStore
    ledger: LedgerStore
    plan_cache: PlanCache
    chatlog: ChatLog | None = None
    knowledge: Any = None
