"""Scenario evals: multi-turn conversations with expectations, global invariants, reports and trigger calibration.

    from steward.evals import load_scenarios, run_scenarios
    report = await run_scenarios(lambda: make_bot(), load_scenarios("scenarios.json"), mock_tools=True)
    print(report.to_markdown())

CLI: steward eval CONFIG SCENARIOS [--knowledge kb.json] [--offline] [--record | --replay] [--calibrate]"""

from steward.evals.calibrate import Suggestion, calibrate
from steward.evals.runner import (
           RunReport,
           ScenarioResult,
           TurnResult,
           check,
           invariants,
           run_scenario,
           run_scenarios,
           write_report,
)
from steward.evals.scenarios import Expect, Scenario, ScenarioFile, Turn, load_scenarios

__all__ = ["Expect", "RunReport", "Scenario", "ScenarioFile", "ScenarioResult", "Suggestion", "Turn", "TurnResult",
           "calibrate", "check", "invariants", "load_scenarios", "run_scenario", "run_scenarios", "write_report"]
