"""steward.evals: scenario loading, the runner, invariants, reports, calibrate and the CLI, on the Northwind example."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from steward import Steward
from steward.cli import main
from steward.evals import RunReport, ScenarioFile, calibrate, invariants, load_scenarios, run_scenarios
from steward.knowledge import MemoryKnowledgeStore
from steward.testing import demo_provider, memory_stores, tiny_kb
from steward.types import Reply

NW = Path(__file__).resolve().parents[1] / "examples" / "northwind"
CONFIG = json.loads((NW / "northwind.steward.json").read_text(encoding="utf-8"))


def factory() -> Steward:
    return Steward(copy.deepcopy(CONFIG), provider=demo_provider(), stores=memory_stores(),
                   knowledge=MemoryKnowledgeStore(tiny_kb()))


async def test_northwind_scenarios_pass_offline(tmp_path):
    report = await run_scenarios(factory, load_scenarios(NW / "scenarios.json"), out_dir=tmp_path)
    assert report.ok, report.to_markdown()
    saved = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert saved["passed"] == saved["total"] == len(report.results)
    assert "PASS" in (tmp_path / "report.md").read_text(encoding="utf-8")


async def test_failures_are_reported_not_raised(tmp_path):
    sf = ScenarioFile.model_validate({"scenarios": [
        {"id": "wrong", "turns": [{"user": "hello", "expect": {"status": "handover", "contains": ["nope"], "pending": "x",
                                                                "journeys": {"lead_capture": "done"}}}],
         "records": {"lead": 2}}]})
    report = await run_scenarios(factory, sf, out_dir=tmp_path)
    r = report.results[0]
    assert not report.ok and not r.passed and r.failures == ["0 'lead' records, expected 2"]
    joined = " | ".join(r.turns[0].failures)
    for part in ("status 'chitchat'", "missing text 'nope'", "pending ask None", "journey lead_capture did not run"):
        assert part in joined
    assert "## wrong" in report.to_markdown()


async def test_resume_skips_passed_scenarios(tmp_path):
    sf = load_scenarios(NW / "scenarios.json")
    await run_scenarios(factory, sf, out_dir=tmp_path, only=["greeting"])
    made = []

    def counting() -> Steward:
        made.append(1)
        return factory()

    report = await run_scenarios(counting, sf, out_dir=tmp_path, only=["greeting", "careers_policy"], resume=True)
    assert len(made) == 1 and report.ok and [r.id for r in report.results] == ["greeting", "careers_policy"]


async def test_mock_tools_write_nothing():
    sf = ScenarioFile.model_validate({"scenarios": [
        {"id": "lead", "turns": [{"user": "I'm Ada, 202 555 0147", "expect": {"tools": ["save_lead"]}}], "records": {"lead": 0}}]})
    assert (await run_scenarios(factory, sf, mock_tools=True)).ok


def test_invariants():
    assert invariants(Reply("Fine. Fine.", "answered", "s"), no_emoji=True) == []
    bad = Reply("Hello {{slots.name}} \U0001F600. This sentence is long enough to count. This sentence is long enough to count.",
                "error", "s")
    got = " ".join(invariants(bad, no_emoji=True))
    for part in ("placeholder", "emoji", "status error", "repeated"):
        assert part in got
    assert invariants(Reply(" ", "answered", "s"), no_emoji=False) == ["invariant: empty reply"]


def test_scenario_files_reject_typos(tmp_path):
    with pytest.raises(ValidationError):
        ScenarioFile.model_validate({"scenarios": [{"id": "a", "turns": [{"user": "x", "expect": {"contain": ["typo"]}}]}]})
    f = tmp_path / "s.json"
    f.write_text(json.dumps([{"id": "a", "turns": [{"user": "x"}]}, {"id": "a", "turns": [{"user": "y"}]}]), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_scenarios(f)


async def test_calibrate_suggests_a_threshold():
    sf = ScenarioFile.model_validate({"scenarios": [
        {"id": "pos", "turns": [{"user": "Are you hiring?", "expect": {"policies": ["careers"]}}]},
        {"id": "neg", "turns": [{"user": "How heavy is the Volt?", "expect": {"no_policies": ["careers"]}}]}]})
    report = await run_scenarios(factory, sf)
    sug = {s.trigger: s for s in calibrate(sf, report)}["p_careers"]
    assert sug.positives == [0.9] and sug.negatives == [0.05] and sug.suggested == 0.48
    empty = calibrate(sf, RunReport([]))
    assert empty == []


def test_cli_eval_offline(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    code = main(["eval", str(NW / "northwind.steward.json"), str(NW / "scenarios.json"), "--knowledge", str(NW / "kb.json"),
                 "--offline", "--out", str(tmp_path / "run"), "--calibrate"])
    out = capsys.readouterr().out
    assert code == 0 and "scenarios passed" in out and "trigger calibration" in out
    assert (tmp_path / "run" / "report.md").exists()
