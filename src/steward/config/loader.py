"""Load a configuration from a file or dict: migrate older formats, then parse into typed models.
Pydantic errors become ConfigError issues with JSON paths, so humans and agents can fix them directly."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from steward.config.migrate import migrate
from steward.config.models import Config
from steward.errors import ConfigError, Issue

log = logging.getLogger("steward.config")


def _path(loc: tuple[Any, ...]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        elif part in ("trigger", "condition", "decision", "extract", "tool", "ask", "set", "emit", "end", "custom") \
                and out.endswith("]"):
            continue  # discriminated-union tag, not part of the user's JSON path
        else:
            out += ("." if out else "") + str(part)
    return out


def load_dict(raw: dict[str, Any]) -> tuple[Config, list[str]]:
    """Returns (config, migration notes). Raises ConfigError with one Issue per problem."""
    if not isinstance(raw, dict):
        raise ConfigError("a configuration must be a JSON object")
    try:
        data, notes = migrate(raw)
    except ValueError as e:
        raise ConfigError(str(e)) from None
    for n in notes:
        log.warning("config migration: %s", n)
    try:
        return Config.model_validate(data), notes
    except ValidationError as e:
        issues = [Issue(err["msg"], _path(tuple(err["loc"])), err["type"]) for err in e.errors()]
        raise ConfigError(issues) from None


def load_file(path: str | Path) -> tuple[Config, list[str]]:
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"configuration file not found: {p}") from None
    except json.JSONDecodeError as e:
        raise ConfigError(f"{p}: invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}") from None
    return load_dict(raw)


def schema() -> dict[str, Any]:
    """The JSON Schema of the configuration (what `steward schema` writes)."""
    s = Config.model_json_schema(by_alias=True)
    s["$id"] = "urn:steward:config:2"
    return s
