"""Journey and policy triggers as decision questions that ride on calls already being made (the triage call in
parallel with message understanding, and each message part's route call)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from steward.config.models import Config, DecisionQuestion, Journey, Policy, TriggerNode
from steward.harness.expr import EvalContext, ExprEngine, ExprError
from steward.providers.base import Answer, Question


def as_question(q: DecisionQuestion) -> Question:
    out: Question = {"type": q.type, "instructions": q.instructions}
    if q.criteria:
        out["criteria"] = dict(q.criteria)
    return out


def _when_ok(expr: ExprEngine, when: str | None, ctx: EvalContext) -> bool:
    if not when:
        return True
    try:
        return bool(expr.eval(when, ctx))
    except ExprError:
        return False


def trigger_questions(cfg: Config, expr: ExprEngine, ctx: EvalContext, scope: Literal["all", "part"] = "all") -> dict[str, Question]:
    """j_<id> for journeys, p_<id> for policies. scope='all' also asks part-scope triggers on the whole message, so a
    single-part message needs no further call; scope='part' only the part-scope ones (for each part's route call)."""
    qs: dict[str, Question] = {}
    for j in cfg.journeys:
        t = j.trigger()
        if j.enabled and t and (scope == "all" or t.scope == "part") and _when_ok(expr, t.when, ctx):
            qs[f"j_{j.id}"] = as_question(t.decision)
    for p in cfg.policies:
        if p.enabled and (scope == "all" or p.scope == "part") and _when_ok(expr, p.when, ctx):
            qs[f"p_{p.id}"] = as_question(p.trigger)
    return qs


@dataclass
class Fired:
    kind: Literal["journey", "policy"]
    id: str
    score: float
    scope: str
    priority: int
    choice: str | None = None
    mode: str = "tap"
    takeover: str | None = None
    journey: Journey | None = None
    policy: Policy | None = None

    def info(self) -> dict[str, Any]:
        return {"choice": self.choice, "score": self.score}


def fired(cfg: Config, answers: dict[str, Answer], scope: str | None = None) -> list[Fired]:
    """Journeys/policies whose trigger passed its threshold, highest priority first."""
    out: list[Fired] = []
    for j in cfg.journeys:
        a = answers.get(f"j_{j.id}")
        t: TriggerNode | None = j.trigger()
        if not a or t is None or (scope and t.scope != scope):
            continue
        th = t.threshold if t.threshold is not None else (0.5 if t.mode == "tap" else 0.65)
        if a.get("type") == "choice":
            ch = a.get("choice")
            p = float((a.get("probabilities") or {}).get(ch or "", a.get("confidence", 0)) or 0)
            if ch and ch != "none" and p >= th:
                out.append(Fired("journey", j.id, p, t.scope, t.priority, ch, t.mode, t.takeover, journey=j))
        elif float(a.get("noul", 0) or 0) >= th:
            out.append(Fired("journey", j.id, float(a.get("noul", 0) or 0), t.scope, t.priority, None, t.mode, t.takeover, journey=j))
    for p in cfg.policies:
        a = answers.get(f"p_{p.id}")
        if not a or (scope and p.scope != scope):
            continue
        score = float(a.get("noul", a.get("confidence", 0)) or 0)
        if score >= p.threshold:
            out.append(Fired("policy", p.id, score, p.scope, p.priority, policy=p))
    return sorted(out, key=lambda f: -f.priority)
