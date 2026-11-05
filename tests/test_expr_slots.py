from __future__ import annotations

import pytest

from steward.harness.expr import EvalContext, ExprEngine, ExprError
from steward.harness.slots import find_contact, normalize, number_like, unicode_digits, validate_value

PHONE = {"phone": {"normalize": ["unicode_digits", "strip_non_digits", {"country_code": {"code": "1", "trunk": ""}}],
                   "pattern": "^[2-9][0-9]{9}$"}}


def ctx(env):
    return EvalContext(env, validate=lambda v, name: validate_value(PHONE, v, name))


def test_expressions_evaluate():
    e = ExprEngine()
    env = {"slots": {"name": "Ada", "phone": "2025550147"}, "vars": {"agents": {"available": False}},
           "trigger": {"choice": "complaint"}, "state": {}}
    assert e.eval("has(slots.name) and valid(slots.phone, 'phone')", ctx(env)) is True
    assert e.eval("vars.agents.available == True", ctx(env)) is False
    assert e.eval("trigger.choice == 'complaint'", ctx(env)) is True
    assert e.eval("has(slots.missing)", ctx(env)) is False
    assert e.eval("not has(state.done)", ctx(env)) is True
    assert e.eval("trigger.choice in ['complaint', 'legal']", ctx(env)) is True
    assert e.eval("state.count > 3", ctx(env)) is False  # None > 3 is false, not a crash


@pytest.mark.parametrize("bad", ['__import__("os")', "slots.__class__", 'open("x")', "1+1", "x.y", "(lambda: 1)()",
                                 "slots['a']", "-1", "has(x=1)", ""])
def test_unsafe_expressions_are_rejected(bad):
    with pytest.raises(ExprError):
        ExprEngine().check(bad)


def test_register_function_and_root():
    e = ExprEngine()
    e.register("open_now", lambda c: c.env["clock"]["hour"] < 18)
    e.add_root("clock")
    assert e.eval("open_now()", EvalContext({"clock": {"hour": 10}})) is True
    with pytest.raises(ValueError):
        e.register("_private", lambda c: 1)


def test_unicode_digits_any_script():
    assert unicode_digits("١٢٣") == "123"  # Arabic-Indic
    assert unicode_digits("१२") == "12"  # Devanagari
    assert unicode_digits("abc 42") == "abc 42"


def test_phone_validation_and_country_code():
    assert validate_value(PHONE, "+1 (202) 555-0147", "phone") == "2025550147"
    assert validate_value(PHONE, "001 202 555 0147", "phone") == "2025550147"
    assert validate_value(PHONE, "202-555-0147", "phone") == "2025550147"
    assert validate_value(PHONE, "12345", "phone") is None
    assert validate_value(PHONE, "", "phone") is None
    assert validate_value(PHONE, "2025550147", "unknown") is None
    assert normalize(" Ada ", ["strip", "lower"]) == "ada"
    with pytest.raises(ValueError):
        normalize("x", ["no_such_step"])


def test_contact_capture_and_number_like():
    assert find_contact(PHONE, "phone", "I'm Ada, call me on 202 555 0147 please") == "2025550147"
    assert find_contact(PHONE, "phone", "my order is 1234") is None
    assert number_like("+44 7700 900123") == "+44 7700 900123"  # phone-like, even if the validator rejects it
    assert number_like("price 12345") is None
