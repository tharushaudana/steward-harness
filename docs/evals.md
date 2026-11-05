# Evaluating an assistant

A configuration change can quietly break a conversation that used to work. Scenarios pin conversations down.

```bash
steward eval assistant.steward.json scenarios.json --knowledge kb.json --record      # real models, recorded
steward eval assistant.steward.json scenarios.json --knowledge kb.json --replay      # CI: free, deterministic
steward eval ... --only lead_saved_once,greeting --tags handover --mock-tools --calibrate --out runs/today --resume
```

Every scenario runs with a **fresh bot**: new in-memory stores, a session named `eval_<id>`, and `is_test=True` messages. The run writes `results.json` and `report.md` into `--out` (by default `runs/<timestamp>`). The exit code is 1 when any scenario fails.

## Scenario file

JSON, or YAML when PyYAML is installed. Unknown keys are errors.

```json
{
 "setup": {"runtime": {"agents_online": false}},
 "scenarios": [
  {"id": "human_request_offline", "tags": ["handover"], "channel": "dm",
   "turns": [
    {"user": "I want to talk to a person", "expect": {"status": "needs_input", "pending": "human_request"}},
    {"user": "Ada 202 555 0147", "expect": {"pending": null, "journeys": {"human_request": "done"},
                                            "contains": ["Nobody from the team is available"]}},
    {"user": "can I talk to someone now?", "runtime": {"agents_online": true}, "expect": {"status": "handover"}}],
   "records": {"lead": 1, "handover": 1}}
 ]
}
```

| expect key | passes when |
|---|---|
| `status` | `reply.status` equals it (or is one of a list) |
| `contains`, `not_contains` | Case-insensitive substrings of the reply |
| `regex`, `not_regex` | Regular expressions on the reply |
| `journeys` | `{journey id: outcome status}` (`"*"` = ran at all) |
| `no_journeys` | Those journeys did not run |
| `policies`, `no_policies` | Those policies applied or did not apply |
| `tools`, `no_tools` | Those tool instances ran successfully, or did not |
| `slots` | Slot values after the turn (`"*"` = any non-empty value) |
| `pending` | The journey waiting for an answer, or `null` |
| `max_cost_usd`, `max_llm_calls` | Budget for the turn |

A turn's `runtime` sets runtime switches before the message is sent, the way an admin would. A scenario's `records` checks the record counts at the end (test records included). Record counts need live tools: with `--mock-tools`, nothing is written.

**Invariants** are checked on every reply, whatever the scenario says:
- the reply is not empty;
- no unrendered `{{placeholder}}`;
- no emoji while `rules.no_emoji` is on;
- no status `error`;
- no repeated sentence.

## Calibrating trigger thresholds

With `--calibrate`, each labelled turn becomes a data point for its trigger:
- a turn expecting a journey or policy is a **positive**;
- a turn listing it in `no_journeys` or `no_policies` is a **negative**.

The suggested threshold sits halfway between the highest negative and the lowest positive. Overlapping scores mean the trigger's wording needs work, not its number.

## From code

```python
from steward.evals import load_scenarios, run_scenarios

report = await run_scenarios(make_bot, load_scenarios("scenarios.json"), mock_tools=True, out_dir="runs/x")
assert report.ok, report.to_markdown()
```

`make_bot` is any callable, sync or async, that returns a fresh `Steward`.
