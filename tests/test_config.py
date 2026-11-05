from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from steward.cli import main
from steward.config import Config, load_dict, load_file, schema, validate
from steward.config.catalog import node_ports
from steward.config.migrate import migrate
from steward.config.models import CustomNode, ToolNode, TriggerNode
from steward.errors import ConfigError

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "northwind" / "northwind.steward.json"


@pytest.fixture
def raw():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def codes(issues):
    return {i.code for i in issues if i.severity == "error"}


def test_example_config_is_valid(raw):
    cfg, notes = load_dict(raw)
    assert not notes and validate(cfg) == []
    assert isinstance(cfg.journey("lead_capture").trigger(), TriggerNode)
    assert cfg.tools["is_agent_available"].impl == "agents.available"
    assert cfg.templates["ask_phone"].is_approved("es")


def test_round_trip_is_stable(raw):
    cfg, _ = load_dict(raw)
    again, _ = load_dict(cfg.to_dict())
    assert again == cfg
    assert any(e.get("from") == "t.next" for e in cfg.to_dict()["journeys"][2]["edges"])


def test_committed_schema_is_current():
    committed = json.loads((ROOT / "schema" / "steward.schema.json").read_text(encoding="utf-8"))
    assert committed == schema(), "run: uv run steward schema -o schema/steward.schema.json"


def test_committed_config_reference_is_current():
    from steward.config.reference import config_reference

    committed = (ROOT / "docs" / "config-reference.md").read_text(encoding="utf-8")
    assert committed == config_reference(), "run: uv run steward schema --markdown -o docs/config-reference.md"


def test_unknown_keys_and_bad_types_have_paths(raw):
    bad = copy.deepcopy(raw)
    bad["settings"]["max_askz"] = 3
    bad["journeys"][0]["nodes"][2]["expr"] = 42
    with pytest.raises(ConfigError) as e:
        load_dict(bad)
    paths = {i.path for i in e.value.issues}
    assert "settings.max_askz" in paths and "journeys[0].nodes[2].expr" in paths


def test_graph_and_reference_checks(raw):
    bad = copy.deepcopy(raw)
    hr = bad["journeys"][1]
    hr["edges"] = [e for e in hr["edges"] if e["from"] != "ask.resume"]         # ask without resume
    hr["edges"].append({"from": "have.maybe", "to": "ex"})                       # unknown port
    hr["edges"].append({"from": "t.next", "to": "ex"})                           # second arrow on a port
    del bad["tools"]["save_lead"]["idempotency_key"]                             # side effect without a key
    bad["journeys"][0]["nodes"][2]["expr"] = "__import__('os')"                  # unsafe expression
    bad["templates"].pop("apology")                                              # dangling template reference
    bad["policies"][0]["action"]["template"] = "carrers"                         # typo
    cfg, _ = load_dict(bad)
    issues = validate(cfg)
    assert {"ask_without_resume", "unknown_port", "multiple_arrows", "no_idempotency_key", "bad_expression",
            "unknown_template"} <= codes(issues)
    typo = next(i for i in issues if "carrers" in i.msg)
    assert "careers" in typo.hint and typo.path == "policies[0].action.template"


def test_registered_tool_implementations_are_checked(raw):
    cfg, _ = load_dict(raw)
    issues = validate(cfg, tool_impls={"records.append", "agent.available"})
    bad = [i for i in issues if i.code == "unregistered_impl"]
    assert len(bad) == 1 and "agents.available" in bad[0].msg and "agent.available" in bad[0].hint


def test_choice_trigger_ports_and_custom_cards(raw):
    cfg, _ = load_dict(raw)
    t = cfg.journey("immediate_handover").trigger()
    assert node_ports(t) == ["complaint", "legal", "safety"]
    assert node_ports(cfg.journey("human_request").node("ex")) == ["ok", "partial", "invalid", "error"]
    raw["journeys"][0]["nodes"].append({"id": "q", "type": "lookup_order", "order_field": "x"})
    cfg2, _ = load_dict(raw)
    node = cfg2.journey("immediate_handover").node("q")
    assert isinstance(node, CustomNode)
    assert "unknown_card" in codes(validate(cfg2))


def test_tool_impl_defaults_to_key():
    cfg = Config.model_validate({"tools": {"crm.save": {"args": {}}}})
    assert cfg.tools["crm.save"].impl == "crm.save"


def test_legacy_alias_fields_are_accepted():
    node = ToolNode.model_validate({"id": "a", "type": "tool", "tool": "x"})
    assert node.tool == "x"
    t = TriggerNode.model_validate({"id": "t", "type": "trigger", "jev": {"instructions": "q?"}})
    assert t.decision.instructions == "q?"


def test_migrate_v1():
    v1 = {
        "schema_version": 1, "kb": "shop", "changelog": [{"v": 2, "at": "2026-01-01", "by": "admin", "note": "x"}],
        "settings": {"languages": ["en", "es"]},
        "validators": {"phone": {"normalize": ["local_digits", "strip_non_digits", "xx_country_code"], "pattern": "^[0-9]{10}$"}},
        "slots": {"phone": {"type": "phone", "validator": "phone"}},
        "templates": {"hi": {"en": "Hi", "es": "Hola", "approved": {"en": True, "es": False}}},
        "tools": {"save": {"impl": "builtin:store.append", "args": {}, "idempotency_key": "k"}},
        "flow": {"ask_number": {"template": "hi", "template_name_phone": "hi"}},
        "channels": {"comment": {"template": "hi"}},
        "journeys": [{"id": "j", "nodes": [
            {"id": "t", "type": "trigger", "jev": {"type": "noul", "instructions": "q?"}},
            {"id": "e", "type": "extract", "slots": ["phone"], "from": "message"},
            {"id": "o", "type": "emit", "kind": "template", "template": "hi"},
            {"id": "x", "type": "end"}],
            "edges": [{"from": "t.next", "to": "e"}, {"from": "e.ok", "to": "o"}, {"from": "o.next", "to": "x"}]}],
    }
    out, notes = migrate(v1)
    assert out["schema_version"] == 2 and "kb" not in out and notes
    assert out["templates"]["hi"] == {"text": {"en": "Hi", "es": "Hola"}, "approved": {"en": True, "es": False}}
    assert out["tools"]["save"]["impl"] == "records.append"
    assert out["validators"]["phone"]["normalize"][0] == "unicode_digits"
    assert out["flow"]["ask_contact"] == {"template": "hi", "template_when_unanswered": "hi"}
    assert out["channels"]["comment"]["mode"] == "template_only"
    nodes = out["journeys"][0]["nodes"]
    assert "decision" in nodes[0] and nodes[1]["source"] == "message" and "kind" not in nodes[2]
    cfg, _ = load_dict(v1)  # the loader migrates automatically
    assert cfg.changelog[0].version == 2 and not [i for i in validate(cfg) if i.severity == "error"]
    with pytest.raises(ConfigError):
        load_dict({"schema_version": 99})


def test_cli_check_schema_migrate(tmp_path, capsys):
    assert main(["check", str(EXAMPLE)]) == 0
    assert "OK" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text('{"templates": {"a": {"text": {"en": "x"}}}, "policies": [{"id": "p", "trigger": {"instructions": "q"}, '
                   '"action": {"template": "nope"}}]}', encoding="utf-8")
    assert main(["check", str(bad)]) == 1
    out = tmp_path / "schema.json"
    assert main(["schema", "-o", str(out)]) == 0 and json.loads(out.read_text())["title"] == "Steward configuration"
    assert main(["check", str(tmp_path / "missing.json")]) == 1
    v1 = tmp_path / "v1.json"
    v1.write_text('{"templates": {"a": {"en": "x"}}}', encoding="utf-8")
    assert main(["migrate", str(v1), "-o", str(tmp_path / "v2.json")]) == 0
    assert json.loads((tmp_path / "v2.json").read_text())["templates"]["a"] == {"text": {"en": "x"}}


def test_load_file_errors(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid JSON"):
        load_file(p)
