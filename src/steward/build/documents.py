"""Source documents: the input of a knowledge build. Crawling or exporting them is the host's job; this module turns
Markdown-like text into section-sized units (split by ## / ### headings; tiny sections merged, huge ones split)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class SourceSection:
    id: str
    heading_path: str
    text: str
    tokens: int = 0


@dataclass
class SourceDocument:
    id: str
    title: str
    sections: list[SourceSection] = field(default_factory=list)
    url: str = ""
    kind: Literal["source", "approved"] = "source"

    @classmethod
    def from_markdown(cls, text: str, *, doc_id: str, title: str | None = None, url: str = "",
                      kind: Literal["source", "approved"] = "source", id_prefix: str | None = None) -> SourceDocument:
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        h1 = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), None)
        title = title or h1 or doc_id
        lines = [ln for ln in lines if not ln.startswith("# ")]
        prefix = id_prefix or doc_id
        secs = [SourceSection(f"{prefix}_{i:02d}", hp, txt, len(txt) // 4) for i, (hp, txt) in enumerate(split_markdown(lines, title))]
        return cls(doc_id, title, secs, url, kind)

    @property
    def sha1(self) -> str:
        return hashlib.sha1("\n".join(s.text for s in self.sections).encode()).hexdigest()


def split_markdown(lines: list[str], title: str, max_chars: int = 2600) -> list[tuple[str, str]]:
    """(heading path, text) sections: split on ##/###; merge small siblings under the same ## (or tiny ones); fold a tiny
    leading fragment into the next section; hard-split anything longer than max_chars at a line break."""
    secs: list[tuple[str, str | None, list[str]]] = []
    cur: list[str] = []
    path, h2 = [title], None
    for ln in lines:
        m = re.match(r"^(#{1,3}) (.*)", ln)
        if m and len(m.group(1)) in (2, 3):
            if cur:
                secs.append((" > ".join(path), h2, cur))
            if len(m.group(1)) == 2:
                h2 = m.group(2)
                path = [title, h2]
            else:
                path = [title, *([h2] if h2 else []), m.group(2)]
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        secs.append((" > ".join(path), h2, cur))
    merged: list[tuple[str, str | None, str]] = []
    for hp, parent, ls in secs:
        txt = "\n".join(ls)
        if merged:
            php, pparent, ptxt = merged[-1]
            same_parent = parent is not None and parent == pparent
            small = len(ptxt) < 700 or len(txt) < 250
            if ((same_parent and small) or len(txt) < 150) and len(ptxt) + len(txt) < max_chars:
                merged[-1] = (php, pparent, ptxt + "\n" + txt)
                continue
        merged.append((hp, parent, txt))
    folded: list[tuple[str, str | None, str]] = []
    for hp, parent, txt in merged:
        if folded and len(folded[-1][2]) < 150:
            _, _, ftxt = folded.pop()
            txt = ftxt + "\n" + txt
        folded.append((hp, parent, txt))
    out: list[tuple[str, str]] = []
    for hp, _, txt in folded:
        while len(txt) > max_chars:
            cut = txt.rfind("\n", 0, max_chars)
            cut = cut if cut > 800 else max_chars
            out.append((hp, txt[:cut]))
            txt = txt[cut:].lstrip()
        if txt.strip():
            out.append((hp, txt))
    return out
