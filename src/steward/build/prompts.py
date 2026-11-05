"""Prompts and JSON schemas of the build passes. Changing a prompt? Bump PROMPT_VERSION: it is part of every build
cache key, so old cached outputs are not reused."""

from __future__ import annotations

from typing import Any

PROMPT_VERSION = "s1"


def obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": required or list(props), "properties": props}


S_STR: dict[str, Any] = {"type": "string"}
S_STRN: dict[str, Any] = {"type": ["string", "null"]}
S_NUMN: dict[str, Any] = {"type": ["number", "null"]}
S_STRS: dict[str, Any] = {"type": "array", "items": S_STR}

# ------------------------------------------------------------------------------------------------ Pass S: vocabulary
SYS_S = """You design the vocabulary for a knowledge base built from a business's source documents. You see the sections that contain the most numbers.
Identify:
1. entity_types: kinds of things that have numeric parameters customers ask about (e.g. product, plan, tier, add_on, service, location, policy). Short snake_case.
2. entities: every concrete entity that has numeric parameters. id = "<type>:<snake_name>" (e.g. "plan:pro"). title = display name. aliases = other names customers or the documents use (lowercase, include short forms).
3. attributes: the numeric parameter names (snake_case) per entity type, with unit (e.g. USD/month, USD/unit, count, days, percent, minutes/month) and a one-line description. Make names specific and consistent across entities (e.g. monthly_price vs annual_monthly_price; included_units vs extra_unit_price).
Use the EXACT names the documents use (never invent or rename entities). Attribute names must not repeat the type (use monthly_price, not plan_monthly_price).
Prefer a compact vocabulary that covers the recurring parameters; skip one-off trivia and marketing statistics."""

SCHEMA_S = obj({"summary": S_STR,
                "entity_types": {"type": "array", "items": obj({"type": S_STR, "description": S_STR})},
                "entities": {"type": "array", "items": obj({"id": S_STR, "type": S_STR, "title": S_STR, "aliases": S_STRS})},
                "attributes": {"type": "array", "items": obj({"name": S_STR, "entity_type": S_STR, "unit": S_STRN, "description": S_STR})}})

# ------------------------------------------------------------------------------------------------ Pass U: sections + values
SYS_U = """You index the sections of one document for a retrieval system. For EVERY section id given, output:
- title: <=8 words naming what the section is about
- summary: <=30 words, a routing hint stating concretely which customer questions the section answers (mention key terms, entities, numbers' meaning)
- keywords: up to 8 terms, including synonyms a customer might use
- questions: 3-6 short questions a customer might ask that this section answers. Cover EVERY distinct fact or rule in the section,
  not only its main topic (a sentence mentioned in passing still gets a question). Phrase them casually, the way customers ask.
Then extract VALUES: only COMMERCIAL numeric terms a customer might compute with: prices, fees, rates, discounts, included
quotas/allowances, usage limits, contract or trial durations, percentages of those.
Do NOT extract technical specifications (dimensions, weights, capacities, engine/performance figures, model codes): they stay
in the section text. Do NOT extract prose facts, yes/no features, or numbers that are just examples/marketing stats.
For each value: unit_id (the section it is in), entity (use the canonical entity ids below when they apply, else create "<type>:<snake_name>"),
attribute (use the attribute names below when they apply, else a specific snake_case name), value (text as written), num (the number), unit,
conditions (list of "key=value" qualifiers, e.g. "billing=annual"; [] if none), source_quote (exact, verbatim span <=200 chars from the section containing the number).
No inference or arithmetic: only values the text states.
VOCABULARY:
{vocab}"""

SCHEMA_U = obj({"units": {"type": "array", "items": obj({"id": S_STR, "title": S_STR, "summary": S_STR, "keywords": S_STRS,
                                                         "questions": S_STRS})},
                "values": {"type": "array", "items": obj({"unit_id": S_STR, "entity": S_STR, "attribute": S_STR, "value": S_STR,
                                                           "num": {"type": "number"}, "unit": S_STRN, "conditions": S_STRS,
                                                           "source_quote": S_STR})}})

RECALL_NOTE = ("\nThis is a focused pass: extract EVERY numeric parameter in the section. When one figure applies to several "
               "entities (e.g. '$100/line' listed for three plans), output one value per entity.")

# ------------------------------------------------------------------------------------------------ Pass B: tree
SYS_B = """You design the navigation tree of a knowledge base. A decision model will walk it (domain -> topic -> section) to find the sections needed for a customer question.
From the section list, design 6-10 DOMAINS, each with 2-7 TOPICS, covering ALL the sections (including pricing, billing, legal/privacy and support content if present).
Titles: short. Descriptions: <=18 words, concrete routing hints: which customer questions the node answers, key terms and synonyms.
Output PLAIN TEXT, exactly this line format, nothing else (no section ids):
DOMAIN: <title> | <description>
TOPIC: <title> | <description>
TOPIC: ...
DOMAIN: ..."""

SYS_B2 = """Assign each section to the single best topic of the navigation tree. Output one line per section, exactly: <section_id> -> <topic_id>
Every section id listed must get a line. Nothing else."""

# ------------------------------------------------------------------------------------------------ Pass D: calculators
SYS_D = """You design deterministic CALCULATORS for a customer-support engine. You see the numeric parameter catalog (per entity type) and the source text stating the rules.
Propose 2-8 calculators for computations customers plausibly ask for (e.g. total cost for a usage quantity, overage, annual totals or savings, add-on costs). Only formulas the text supports.
For each calculator:
- id (snake_case), title, description (<=25 words: what question it answers, mention the user quantities it needs)
- entity_type: the entity type it is evaluated for (e.g. "plan"); the engine binds "$entity" to the entity the customer means
- expression: arithmetic using only + - * / max min round, numbers and variable names
- inputs: catalog values used: var, attribute, entity ("$entity" for the bound entity, or a fixed entity id), fallback_entity (fixed entity id or null)
- user_inputs: quantities the customer must supply: var, description, unit, ask (question to ask if missing), default (number or null: only when the text implies a default), assumption (text stating the default, or null)
- example: an entity id with its catalog values plus user values, and the expected result (compute carefully).
Use exact attribute names and entity ids from the catalog."""

SCHEMA_D = obj({"calculators": {"type": "array", "items": obj({
    "id": S_STR, "title": S_STR, "description": S_STR, "entity_type": S_STR, "expression": S_STR,
    "inputs": {"type": "array", "items": obj({"var": S_STR, "attribute": S_STR, "entity": S_STR, "fallback_entity": S_STRN})},
    "user_inputs": {"type": "array", "items": obj({"var": S_STR, "description": S_STR, "unit": S_STRN, "ask": S_STR,
                                                   "default": S_NUMN, "assumption": S_STRN})},
    "example": obj({"entity": S_STR, "user_values": {"type": "array", "items": obj({"var": S_STR, "value": {"type": "number"}})},
                    "expected": {"type": "number"}})})}})

# ------------------------------------------------------------------------------------------------ supersede
SUPERSEDE_Q = ("Does the APPROVED section state something that contradicts, corrects or replaces a statement in the SOURCE section '{}' "
               "(a different number, price, contact, address, list, policy or availability), so that the source statement is now wrong or outdated?")
SYS_SUP = """You compare a business's management-approved section (authoritative, current) with a section of its other sources (may be outdated).
List the lines of the SOURCE section that the APPROVED section contradicts or replaces. For each, give:
- source_line: the source line copied EXACTLY (verbatim, whole line);
- approved_line: the APPROVED line that makes it wrong, copied EXACTLY.
Only lines that a specific approved line contradicts (different number, contact, place, rule or availability). Lines that merely add
detail, agree, or are about something no approved line addresses are NOT outdated, even if they look old. No approved line = not outdated.
full = true only if every line of the source section is outdated.
Return {"outdated": [{"source_line": "...", "approved_line": "..."}], "full": false, "why": "<one short sentence>"}."""
SCHEMA_SUP = obj({"outdated": {"type": "array", "items": obj({"source_line": S_STR, "approved_line": S_STR})},
                  "full": {"type": "boolean"}, "why": S_STR})
SUPERSEDE_LINE_Q = ("SOURCE line (may be outdated), from the source section «{}»: «{}»\n"
                    "APPROVED line (current, authoritative), from the approved section «{}»: «{}»\n"
                    "Does the APPROVED line contradict the SOURCE line, so that the source line is now wrong or misleading?")
SUPERSEDE_LINE_CRIT = {"true": "They state different facts about the same thing, so a customer reading the source line would be misinformed.",
                       "false": "They agree, are about different things (e.g. two different models, as the section names show), "
                                "or the approved line only adds information."}
