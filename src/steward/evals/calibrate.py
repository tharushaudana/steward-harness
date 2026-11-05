"""Suggest trigger thresholds from an eval run.

A scenario turn that expects a journey (expect.journeys) or policy (expect.policies) is a POSITIVE for its trigger; a
turn that lists it in no_journeys / no_policies is a NEGATIVE. With the triage scores the run recorded, the suggested
threshold sits halfway between the highest negative and the lowest positive. When they overlap no threshold separates
them: the trigger's wording (instructions/criteria) needs work, not the number."""

from __future__ import annotations

from dataclasses import dataclass

from steward.evals.runner import RunReport
from steward.evals.scenarios import ScenarioFile


@dataclass
class Suggestion:
    trigger: str  # triage question id: j_<journey> or p_<policy>
    positives: list[float]
    negatives: list[float]
    suggested: float | None
    note: str


def calibrate(scenarios: ScenarioFile, report: RunReport) -> list[Suggestion]:
    by_id = {s.id: s for s in scenarios.scenarios}
    pos: dict[str, list[float]] = {}
    neg: dict[str, list[float]] = {}
    for r in report.results:
        sc = by_id.get(r.id)
        if sc is None:
            continue
        for t in r.turns:
            exp = sc.turns[t.index].expect
            labels = [(f"j_{j}", True) for j in exp.journeys] + [(f"j_{j}", False) for j in exp.no_journeys]
            labels += [(f"p_{p}", True) for p in exp.policies] + [(f"p_{p}", False) for p in exp.no_policies]
            for key, positive in labels:
                score = t.triage.get(key)
                if isinstance(score, int | float):
                    (pos if positive else neg).setdefault(key, []).append(float(score))
    out: list[Suggestion] = []
    for key in sorted(set(pos) | set(neg)):
        p, n = sorted(pos.get(key, [])), sorted(neg.get(key, []))
        if p and n:
            lo, hi = p[0], n[-1]
            if lo > hi:
                out.append(Suggestion(key, p, n, round((lo + hi) / 2, 2), f"separates {len(p)} positives from {len(n)} negatives"))
            else:
                out.append(Suggestion(key, p, n, None, f"overlap: a positive scored {lo} and a negative {hi}; reword the trigger"))
        elif p:
            out.append(Suggestion(key, p, n, None, f"only positives (lowest {p[0]}); add negative turns to calibrate"))
        else:
            out.append(Suggestion(key, p, n, None, f"only negatives (highest {n[-1]}); add positive turns to calibrate"))
    return out
