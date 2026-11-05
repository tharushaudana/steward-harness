"""The runnable examples keep working (offline)."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

EX = Path(__file__).resolve().parents[1] / "examples"


def test_minimal_example(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["minimal.py"])
    runpy.run_path(str(EX / "minimal.py"), run_name="__main__")
    out = capsys.readouterr().out
    assert "Welcome to Northwind Bikes" in out and "Thank you, Ada" in out and "[event tool_call] save_lead" in out


def test_custom_knowledge_store_example(capsys):
    runpy.run_path(str(EX / "custom_knowledge_store.py"), run_name="__main__")
    out = capsys.readouterr().out
    assert "9 sections" in out and "S1" in out


def test_fastapi_example_imports(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-used")
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient

    mod = runpy.run_path(str(EX / "fastapi_app.py"))
    c = TestClient(mod["app"])
    assert c.get("/steward/config").status_code == 401
    assert c.get("/steward/config", headers={"authorization": "Bearer change-me"}).status_code == 200
