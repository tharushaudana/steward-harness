"""Scenario files: multi-turn conversations with per-turn expectations.

    {"setup": {"runtime": {"agents_online": false}},          # applied before every scenario (optional)
     "scenarios": [
       {"id": "lead_once", "tags": ["lead"], "channel": "dm",
        "setup": {"runtime": {...}},                          # per scenario (optional)
        "turns": [
          {"user": "I'm Ada, 202 555 0147", "runtime": {},
           "expect": {"status": "answered", "contains": ["Thank you"], "not_contains": ["{{"],
                      "regex": ["\\\\d{10}"], "not_regex": [], "journeys": {"lead_capture": "done"},
                      "no_journeys": ["human_request"], "policies": ["workshop"], "no_policies": [],
                      "tools": ["save_lead"], "no_tools": [], "slots": {"phone": "2025550147"},
                      "pending": null, "max_cost_usd": 0.01, "max_llm_calls": 3}}],
        "records": {"lead": 1}}                               # record counts at the end (test records included)
     ]}

JSON always works; YAML (.yaml/.yml) needs PyYAML installed. Unknown keys are errors (typos must not pass silently)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_UNSET: Any = "__unset__"


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | list[str] | None = None
    contains: list[str] = Field(default_factory=list)
    not_contains: list[str] = Field(default_factory=list)
    regex: list[str] = Field(default_factory=list)
    not_regex: list[str] = Field(default_factory=list)
    journeys: dict[str, str] = Field(default_factory=dict)  # journey id -> outcome status ("*" = any)
    no_journeys: list[str] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    no_policies: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)  # tool instances that ran successfully this turn
    no_tools: list[str] = Field(default_factory=list)
    slots: dict[str, Any] = Field(default_factory=dict)
    pending: Any = _UNSET  # journey id with a pending ask, or null for none
    max_cost_usd: float | None = None
    max_llm_calls: int | None = None


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: str
    expect: Expect = Field(default_factory=lambda: Expect())
    lang: str | None = None
    runtime: dict[str, Any] = Field(default_factory=dict)  # RuntimeStore keys set before this turn (an admin action)


class Setup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime: dict[str, Any] = Field(default_factory=dict)  # RuntimeStore keys (e.g. agents_online)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    channel: str = "dm"
    setup: Setup = Field(default_factory=lambda: Setup())
    turns: list[Turn]
    records: dict[str, int] = Field(default_factory=dict)


class ScenarioFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setup: Setup = Field(default_factory=lambda: Setup())
    scenarios: list[Scenario]


def load_scenarios(path: str | Path) -> ScenarioFile:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as e:  # pragma: no cover - depends on the environment
            raise RuntimeError("YAML scenarios need PyYAML: pip install pyyaml (or use JSON)") from e
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)
    if isinstance(raw, list):
        raw = {"scenarios": raw}
    sf = ScenarioFile.model_validate(raw)
    ids = [s.id for s in sf.scenarios]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate scenario ids: {dupes}")
    return sf
