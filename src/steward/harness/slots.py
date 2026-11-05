"""Slot values: normalisation and validation (configurable, script-independent), and code-first contact capture.

A validator in the config:

    "validators": {"phone": {"normalize": ["unicode_digits", "strip_non_digits", {"country_code": {"code": "1", "trunk": ""}}],
                             "pattern": "^[2-9][0-9]{9}$"}}

Normalisation steps:
  unicode_digits    digits of any script -> ASCII (uses Unicode data, so every writing system works)
  strip_non_digits  keep only digits and '+'
  lower / strip     text clean-up
  {"country_code": {"code": "44", "trunk": "0"}}  "+44 7700..." / "0044 7700..." / "447700..." -> trunk + national number
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


def unicode_digits(text: str) -> str:
    """Replace every decimal digit of any script by its ASCII digit."""
    out = []
    for ch in text:
        if ch.isdigit() and not ("0" <= ch <= "9"):
            try:
                out.append(str(unicodedata.digit(ch)))
                continue
            except (TypeError, ValueError):
                pass
        out.append(ch)
    return "".join(out)


def normalize(value: Any, steps: Sequence[Any]) -> str:
    v = str(value if value is not None else "")
    for step in steps:
        if step == "unicode_digits":
            v = unicode_digits(v)
        elif step == "strip_non_digits":
            v = re.sub(r"[^\d+]", "", unicode_digits(v))
        elif step == "lower":
            v = v.lower()
        elif step == "strip":
            v = v.strip()
        elif isinstance(step, Mapping) and "country_code" in step:
            cc = step["country_code"]
            code, trunk = str(cc.get("code", "")), str(cc.get("trunk", ""))
            if code:
                digits = v.lstrip("+")
                if digits.startswith("00" + code):
                    digits = digits[2:]
                if digits.startswith(code) and (v.startswith("+") or v.startswith("00") or len(digits) > len(code) + 8):
                    digits = trunk + digits[len(code):]
                v = digits
        else:
            raise ValueError(f"unknown normalize step {step!r}")
    return v


def validate_value(validators: Mapping[str, Any], value: Any, name: str) -> str | None:
    """The normalised value if it is valid for validator `name`, else None."""
    spec = validators.get(name)
    if spec is None or value in (None, ""):
        return None
    spec = spec if isinstance(spec, Mapping) else spec.model_dump()
    v = normalize(value, spec.get("normalize") or [])
    return v if re.fullmatch(spec["pattern"], v) else None


_NUMBER_RUN = re.compile(r"\+?\d[\d\s\-().]{5,18}\d")


def number_like(text: str | None) -> str | None:
    """The first phone-like run of 7+ digits in the text (any script), valid or not."""
    t = unicode_digits(text or "")
    for m in _NUMBER_RUN.finditer(t):
        if len(re.sub(r"\D", "", m.group(0))) >= 7:
            return m.group(0).strip()
    return None


def find_contact(validators: Mapping[str, Any], validator: str | None, text: str | None) -> str | None:
    """Code-first capture: the first number-like run in the text that passes `validator` (normalised), else None."""
    if not validator:
        return None
    t = unicode_digits(text or "")
    for m in _NUMBER_RUN.finditer(t):
        v = validate_value(validators, m.group(0), validator)
        if v:
            return v
    return None
