"""Steward: a compliant customer-service harness, powered by DADR (dependency-aware decision retrieval).

The public API is re-exported here. See README.md and AGENTS.md for the integration guide.
"""

from steward.app import Steward
from steward.errors import (
    BudgetExceeded,
    ConfigError,
    Issue,
    ProviderError,
    RevisionConflict,
    StateConflict,
    StewardError,
    StoreError,
    ToolError,
    UnknownTool,
)
from steward.events import EventBus, Step, Trace
from steward.types import Message, Receipt, Reply, Status, ToolCall, ToolResult, TurnInfo, fail, ok

__version__ = "0.1.0"

__all__ = [
    "BudgetExceeded", "ConfigError", "EventBus", "Issue", "Message", "ProviderError", "Receipt", "Reply",
    "RevisionConflict", "StateConflict", "Status", "Step", "Steward", "StewardError", "StoreError", "ToolCall", "ToolError",
    "ToolResult", "Trace", "TurnInfo", "UnknownTool", "__version__", "fail", "ok",
]
