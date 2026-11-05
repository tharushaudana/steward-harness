"""The repository must never contain private client words. The list is stored only as sha256 hashes
(tests/banned_hashes.txt), so the guard itself does not leak them."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("src", "tests", "examples", "docs", "schema")
TOP_FILES = ("README.md", "AGENTS.md", "llms.txt", "CHANGELOG.md", "pyproject.toml", ".env.example")
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".html", ".js", ".css", ".cfg", ".ini"}


def banned_hashes() -> set[str]:
    lines = (ROOT / "tests" / "banned_hashes.txt").read_text(encoding="utf-8").splitlines()
    return {x.strip() for x in lines if x.strip() and not x.startswith("#")}


def files() -> list[Path]:
    out = [ROOT / f for f in TOP_FILES if (ROOT / f).exists()]
    for d in SCANNED:
        if (ROOT / d).exists():
            out += [p for p in (ROOT / d).rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES
                    and p.name != "banned_hashes.txt" and "vendor" not in p.parts]
    return out


def test_no_private_words_in_repository() -> None:
    hashes = banned_hashes()
    assert hashes, "tests/banned_hashes.txt is empty"
    hits = []
    for path in files():
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for token in set(re.findall(r"[a-z0-9_]+", text)):
            if hashlib.sha256(token.encode()).hexdigest() in hashes:
                hits.append(f"{path.relative_to(ROOT)}: a private word (hash {hashlib.sha256(token.encode()).hexdigest()[:10]})")
    assert not hits, "private words found:\n" + "\n".join(sorted(hits))
