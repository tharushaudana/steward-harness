"""Run scenarios against a Steward instance and check expectations plus global invariants.

Every scenario gets a FRESH bot from the factory (its own stores) and a session named `eval_<id>`; messages are sent with
is_test=True, so tool idempotency keys are prefixed `test:` and real customer records are never touched. Use
`settings.tools_mode = "mock"` (or `mock_tools=True` here) to keep side effects out entirely."""

from __future__ import annotations

import inspect
import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from steward.evals.scenarios import _UNSET, Expect, Scenario, ScenarioFile
from steward.types import Message, Reply

BotFactory = Callable[[], Any]  # () -> Steward | Awaitable[Steward]

_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")


@dataclass
class TurnResult:
    index: int
    user: str
    reply: str
    status: str
    failures: list[str] = field(default_factory=list)
    journeys: dict[str, str] = field(default_factory=dict)
    policies: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    llm_calls: int = 0
    decision_calls: int = 0
    triage: dict[str, Any] = field(default_factory=dict)  # trigger scores seen this turn (for calibrate)


@dataclass
class ScenarioResult:
    id: str
    tags: list[str]
    turns: list[TurnResult]
    failures: list[str] = field(default_factory=list)  # scenario-level (records, errors)
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.failures and all(not t.failures for t in self.turns)

    @property
    def cost_usd(self) -> float:
        return round(sum(t.cost_usd for t in self.turns), 6)


@dataclass
class RunReport:
    results: list[ScenarioResult]

    @property
    def passed(self) -> int:
        return sum(r.passed for r in self.results)

    @property
    def ok(self) -> bool:
        return self.passed == len(self.results)

    @property
    def cost_usd(self) -> float:
        return round(sum(r.cost_usd for r in self.results), 6)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "total": len(self.results), "cost_usd": self.cost_usd,
                "results": [dict(asdict(r), passed=r.passed) for r in self.results]}

    def to_markdown(self) -> str:
        rows = [f"# Eval: {self.passed}/{len(self.results)} scenarios passed (cost ${self.cost_usd:.4f})", "",
                "| scenario | result | turns | cost $ | failures |", "|---|---|---|---|---|"]
        for r in self.results:
            fails = [*r.failures, *(f"turn {t.index + 1}: {f}" for t in r.turns for f in t.failures)]
            rows.append(f"| {r.id} | {'PASS' if r.passed else 'FAIL'} | {len(r.turns)} | {r.cost_usd:.4f} | "
                        f"{'<br>'.join(x.replace('|', '/') for x in fails)[:600]} |")
        for r in self.results:
            if r.passed:
                continue
            rows += ["", f"## {r.id}", ""]
            for t in r.turns:
                rows += [f"**user:** {t.user}", "", f"**reply ({t.status}):** {t.reply}", ""]
        return "\n".join(rows) + "\n"


# ------------------------------------------------------------------------------------------------ checks
def invariants(reply: Reply, *, no_emoji: bool) -> list[str]:
    """Rules every reply must keep, whatever the scenario says."""
    out: list[str] = []
    if not reply.text.strip():
        out.append("invariant: empty reply")
    if "{{" in reply.text or "}}" in reply.text:
        out.append("invariant: unrendered template placeholder in the reply")
    if no_emoji and _EMOJI.search(reply.text):
        out.append("invariant: emoji in the reply while rules.no_emoji is on")
    if reply.status == "error":
        out.append("invariant: status error")
    sentences = [s.strip().lower() for s in re.split(r"(?<=[.!?])\s+", reply.text) if len(s.strip()) > 25]
    if len(sentences) != len(set(sentences)):
        out.append("invariant: a sentence is repeated in the reply")
    return out


def check(expect: Expect, reply: Reply, turn: TurnResult) -> list[str]:
    out: list[str] = []
    text = reply.text
    if expect.status is not None:
        allowed = [expect.status] if isinstance(expect.status, str) else expect.status
        if reply.status not in allowed:
            out.append(f"status {reply.status!r}, expected {' or '.join(map(repr, allowed))}")
    out += [f"missing text {s!r}" for s in expect.contains if s.lower() not in text.lower()]
    out += [f"unexpected text {s!r}" for s in expect.not_contains if s.lower() in text.lower()]
    out += [f"no match for /{p}/" for p in expect.regex if not re.search(p, text)]
    out += [f"unexpected match for /{p}/" for p in expect.not_regex if re.search(p, text)]
    for jid, status in expect.journeys.items():
        got = turn.journeys.get(jid)
        if got is None:
            out.append(f"journey {jid} did not run")
        elif status != "*" and got != status:
            out.append(f"journey {jid} ended {got!r}, expected {status!r}")
    out += [f"journey {j} ran ({turn.journeys[j]})" for j in expect.no_journeys if j in turn.journeys]
    out += [f"policy {p} did not apply" for p in expect.policies if p not in turn.policies]
    out += [f"policy {p} applied" for p in expect.no_policies if p in turn.policies]
    out += [f"tool {t} did not run successfully" for t in expect.tools if t not in turn.tools]
    out += [f"tool {t} ran" for t in expect.no_tools if t in turn.tools]
    slots = reply.state.get("slots") or {}
    for k, v in expect.slots.items():
        if v == "*":
            if slots.get(k) in (None, ""):
                out.append(f"slot {k} is empty")
        elif slots.get(k) != v:
            out.append(f"slot {k} = {slots.get(k)!r}, expected {v!r}")
    if expect.pending is not _UNSET:
        pend = (reply.state.get("pending") or {}).get("journey")
        if pend != expect.pending:
            out.append(f"pending ask {pend!r}, expected {expect.pending!r}")
    if expect.max_cost_usd is not None and turn.cost_usd > expect.max_cost_usd:
        out.append(f"cost ${turn.cost_usd:.5f} > ${expect.max_cost_usd}")
    if expect.max_llm_calls is not None and turn.llm_calls > expect.max_llm_calls:
        out.append(f"{turn.llm_calls} LLM calls > {expect.max_llm_calls}")
    return out


def _triage_scores(reply: Reply) -> dict[str, Any]:
    scores: dict[str, Any] = {}
    for s in reply.trace.steps:
        if s.name in ("triage", "late_trigger") and isinstance(s.output, dict):
            for k, v in s.output.items():
                scores.setdefault(k, v)
    return scores


# ------------------------------------------------------------------------------------------------ running
async def _make(factory: BotFactory) -> Any:
    bot = factory()
    return await bot if inspect.isawaitable(bot) else bot


async def run_scenario(factory: BotFactory, sc: Scenario, *, shared: ScenarioFile | None = None,
                       mock_tools: bool = False) -> ScenarioResult:
    bot = await _make(factory)
    t0 = time.perf_counter()
    res = ScenarioResult(sc.id, sc.tags, [])
    try:
        for k, v in {**(shared.setup.runtime if shared else {}), **sc.setup.runtime}.items():
            await bot.stores.runtime.set(k, v)
        if mock_tools:
            await bot.ensure_config()
            doc = await bot.admin.adraft()
            draft = dict(doc.config)
            draft["settings"] = dict(draft.get("settings") or {}, tools_mode="mock")
            await bot.admin.asave_draft(draft, doc.rev)
            await bot.admin.apublish("eval: mock tools")
        cfg = await bot.config()
        session = f"eval_{sc.id}"
        for i, t in enumerate(sc.turns):
            for k, v in t.runtime.items():
                await bot.stores.runtime.set(k, v)
            reply: Reply = await bot.ahandle(Message(session, t.user, channel=sc.channel, is_test=True, lang=t.lang))
            tot = reply.trace.totals
            tr = TurnResult(i, t.user, reply.text, reply.status,
                            journeys={j.get("journey", "?"): j.get("status", "?") for j in reply.journeys},
                            policies=list(reply.policies),
                            tools=[r["tool"] for j in reply.journeys for r in j.get("receipts", []) if r.get("ok")],
                            cost_usd=tot.cost_usd, llm_calls=tot.llm_calls, decision_calls=tot.decision_calls,
                            triage=_triage_scores(reply))
            tr.failures = [*invariants(reply, no_emoji=cfg.rules.no_emoji), *check(t.expect, reply, tr)]
            res.turns.append(tr)
        for rtype, n in sc.records.items():
            got = len(await bot.stores.records.list(rtype, include_test=True))
            if got != n:
                res.failures.append(f"{got} '{rtype}' records, expected {n}")
    except Exception as e:
        res.failures.append(f"error: {type(e).__name__}: {e}"[:400])
    finally:
        res.seconds = round(time.perf_counter() - t0, 2)
        close = getattr(bot, "close", None)
        if callable(close):
            close()
    return res


async def run_scenarios(factory: BotFactory, scenarios: ScenarioFile, *, only: list[str] | None = None,
                        tags: list[str] | None = None, mock_tools: bool = False, out_dir: str | Path | None = None,
                        resume: bool = False, on_result: Callable[[ScenarioResult], Any] | None = None) -> RunReport:
    """Run scenarios one after another (deterministic order, readable costs). With out_dir, each result is saved as
    it finishes; resume=True skips scenarios that already passed in that directory."""
    out = Path(out_dir) if out_dir else None
    done: dict[str, ScenarioResult] = {}
    if out and resume and (out / "results.json").exists():
        for r in json.loads((out / "results.json").read_text(encoding="utf-8"))["results"]:
            if r.get("passed"):
                done[r["id"]] = ScenarioResult(r["id"], r["tags"], [TurnResult(**t) for t in r["turns"]], r["failures"],
                                               r["seconds"])
    results: list[ScenarioResult] = []
    for sc in scenarios.scenarios:
        if only and sc.id not in only:
            continue
        if tags and not set(tags) & set(sc.tags):
            continue
        r = done.get(sc.id) or await run_scenario(factory, sc, shared=scenarios, mock_tools=mock_tools)
        results.append(r)
        if on_result:
            maybe = on_result(r)
            if inspect.isawaitable(maybe):
                await maybe
        if out:
            write_report(RunReport(results), out)
    return RunReport(results)


def write_report(report: RunReport, out_dir: str | Path) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(report.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "report.md").write_text(report.to_markdown(), encoding="utf-8")


def print_result(r: ScenarioResult) -> None:
    print(f"{'PASS' if r.passed else 'FAIL'} {r.id} ({len(r.turns)} turns, ${r.cost_usd:.4f}, {r.seconds}s)")
    for f in r.failures:
        print(f"     {f}")
    for t in r.turns:
        for f in t.failures:
            print(f"     turn {t.index + 1}: {f}")
