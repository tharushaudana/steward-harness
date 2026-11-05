"""Error types. Programmer errors (invalid config, unregistered tools) raise; runtime failures during a turn
(provider down, tool timeout, budget) are turned into a safe Reply instead, so a customer never sees a traceback."""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Issue:
    """One configuration problem, precise enough for a human or an agent to fix it."""

    msg: str
    path: str = ""  # JSON path in the config, e.g. "journeys[2].nodes[4].template"
    code: str = ""  # stable machine-readable code, e.g. "unknown_template"
    severity: str = "error"  # "error" blocks publishing; "warning" does not
    hint: str = ""  # how to fix it
    journey: str | None = None
    node: str | None = None
    port: str | None = None

    def __str__(self) -> str:
        where = self.path or ".".join(x for x in (self.journey, self.node, self.port) if x)
        return f"[{self.severity}] {where + ': ' if where else ''}{self.msg}" + (f" (hint: {self.hint})" if self.hint else "")


class StewardError(Exception):
    """Base class of every error steward raises."""


class ConfigError(StewardError):
    def __init__(self, issues: Sequence[Issue] | str):
        self.issues: list[Issue] = [Issue(issues)] if isinstance(issues, str) else list(issues)
        errors = [i for i in self.issues if i.severity == "error"] or self.issues
        head = f"{len(errors)} configuration problem(s):\n" if len(errors) > 1 else ""
        super().__init__(head + "\n".join(f"  - {i}" for i in errors[:20]))


class UnknownTool(StewardError):
    def __init__(self, tool_id: str, known: Iterable[str]):
        self.tool_id = tool_id
        self.known = sorted(known)
        close = difflib.get_close_matches(tool_id, self.known, n=1)
        hint = f" Did you mean '{close[0]}'?" if close else ""
        listing = ", ".join(self.known) or "none"
        super().__init__(f"tool '{tool_id}' is not registered.{hint} Registered: {listing}. "
                         f"Register it with @bot.tool('{tool_id}') or bot.tools.register('{tool_id}', fn).")


class StateConflict(StewardError):
    """The session state changed concurrently (compare-and-set failed twice)."""


class RevisionConflict(StewardError):
    """The draft configuration changed since it was loaded (optimistic locking)."""


class BudgetExceeded(StewardError):
    def __init__(self, spent: float, cap: float, estimate: float = 0.0):
        self.spent, self.cap, self.estimate = spent, cap, estimate
        super().__init__(f"spend cap reached: spent ${spent:.4f} + estimated ${estimate:.4f} > cap ${cap:.2f}")


class ProviderError(StewardError):
    """A model provider call failed (network, HTTP error, malformed response)."""


class ToolError(StewardError):
    """A tool failed in a way that is a programming error (e.g. it returned a wrong type)."""


class StoreError(StewardError):
    """A storage adapter failed."""
