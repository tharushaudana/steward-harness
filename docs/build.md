# Building knowledge (`steward.build`)

Optional. A knowledge base can come from anywhere that produces a `KnowledgeSnapshot`. This module builds one from markdown documents with a few cheap, cached model passes. The engine never needs hand-written synonyms or calculators: everything domain-specific is discovered here.

```bash
steward build pages/*.md --approved approved.md --description "Northwind Bikes, a bicycle shop" -o kb.json --budget 0.25
```

```python
from steward.build import SourceDocument, build, overlay_approved

docs = [SourceDocument.from_markdown(text, doc_id="prices", url="https://shop.example/prices", id_prefix="P01")]
report = await build(docs, provider, writer=store, description="a bicycle shop", cache_dir=".steward-build-cache")
print(report.notes, report.cost_usd, report.cache_hits)
```

## The passes

| pass | mechanism | output |
|---|---|---|
| **S**: schema | llm | Entity types, entities with aliases, numeric attributes; a one-line summary |
| **U**: units | llm, per document batch | Section summaries, the questions each section answers, typed numeric values. Every value's `source_quote` must appear verbatim in its section, or it is dropped |
| **B**: tree | llm | Domains and topics, then each section assigned to a topic |
| **D**: calculators | llm, then code | Formulas over the numeric catalog. Each is validated in code (safe AST, declared variables, a worked example for every entity) and rejected if anything fails |

Sections stay section-sized. Values are only for numeric parameters. Every response is cached under a hash of the prompt version and its inputs, so a re-run costs nothing and a changed document costs only its own passes. `budget_usd` stops a runaway build.

## Approved knowledge

Management-approved information (price lists, contacts) **outranks** the source documents:

```python
approved = SourceDocument.from_markdown(text, doc_id="approved", kind="approved", id_prefix="A01")
await overlay_approved(store, [approved], provider)
```

This does three things:
1. The approved sections are added, with values ranked first.
2. **Supersede**: the source lines they contradict are marked outdated and hidden from answers. This happens in three steps:
   - a decision batch per approved section picks the candidate source sections;
   - an LLM names the outdated source lines together with the approved line that contradicts each one, and both are verified verbatim in code;
   - a per-line decision confirms each pair, with both section names shown, so a spec line of one product is never "contradicted" by another product's line.
3. Admins review the marks. An override (`reject` a mark, `add` one by hand) survives re-runs:

```python
from steward.build import set_override
from steward.knowledge.model import SupersedeOverride

await set_override(store, SupersedeOverride("P01_00", "Trail 500: $1,290", "reject"))
await set_override(store, clear=("P01_00", "Trail 500: $1,290"))
```

## In the admin page

```python
from steward.build import KnowledgeAdmin
app.include_router(create_router(bot, auth=auth, knowledge_admin=KnowledgeAdmin(bot, provider=provider)), prefix="/steward")
```

This adds the approved-knowledge tab:
- edit the approved markdown (kept in the RuntimeStore);
- rebuild the overlay (in the background);
- review every outdated mark with its evidence;
- reject or add marks.

| endpoint | role |
|---|---|
| `GET /knowledge/approved` | viewer: the text, the approved and source sections, every mark, the build status |
| `PUT /knowledge/approved` | editor: save the approved markdown |
| `POST /knowledge/approved/build` | editor: rebuild the overlay (409 while one is running) |
| `POST /knowledge/mark` | editor: `{"unit", "line", "action": "reject" or "add" or "clear", "by"}` |
| `GET /knowledge/section/{id}` | viewer: the lines of a section and which are outdated |

The knowledge store must also be a `KnowledgeWriter` (memory, SQLite, MongoDB and Firestore all are).
