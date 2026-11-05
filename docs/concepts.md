# Concepts

## Three kinds of model call, and code everywhere else

| mechanism | used for | default model |
|---|---|---|
| **decision** | Yes/no, choice and score questions in batches: triage, routing, selection, sufficiency, supersede review. Cheap; billed on input only. | `typesafe/jev-1.14-20261014` via OpenRouter `/api/alpha/decisions` |
| **llm** | Writing the answer, rewriting a follow-up into a standalone question, extracting slots | `z-ai/glm-5.3-flash` (chat), any model per purpose |
| **code** | Calculators, validators, expressions, templates, rules checks, number checks, state | |

Every step lands in `reply.trace` with its mechanism, tokens, cost and latency.

## A turn

1. **Gates.** A session handed over to a human (`state.handover_active`) gets only the holding template (status `paused`). A `template_only` channel (for example public comments) gets its single template, with no model calls.
2. **Triage**, in one decision batch:
   - every enabled journey trigger (`j_<journey id>`);
   - every policy (`p_<policy id>`);
   - the message kind: request, chitchat, about the assistant, or about the conversation.

   This runs in parallel with **understand**, which rewrites a follow-up ("and the red one?") into a standalone question. The rewrite only resolves references; it never adds facts.
3. **Resume.** If a journey is waiting at an `ask` card, a decision checks whether the message answers it. If it does, the journey continues from there. A journey that asks again after a reply more than `settings.max_reasks` times takes the `gave_up` port.
4. **Journeys.**
   - Triggered journeys run, ordered by priority.
   - An `exclusive` trigger takes over its part of the message (or the whole turn with `takeover: "turn"`). A `tap` trigger runs alongside the knowledge answer, for example lead capture when a phone number appears.
   - **Late triggers** re-ask, on the rewritten message, triggers whose first score was unsure (between 0.3 and the threshold).
5. **Knowledge answer (DADR):** shortlist, route, select sections and calculators, gate, expand, generate, check numbers. The answer may only use the selected evidence. Calculators compute in code. A reply that cites a number missing from the evidence is rejected. When nothing relevant is found, the status is `insufficient` and the escalation contacts are offered, not invented facts.
6. **Compose.** Journey and policy templates are placed `before`, in the `body`, as a `suffix`, or `replace` everything. Each template is sent once per reply.
   - `covers` drops templates made redundant by another.
   - `requires_receipt` only sends a template when its tool succeeded in this turn.
   - The **flow** (`welcome`, `ask_contact`, `thank_you`) adds a greeting on the first turn and asks for the contact at most `settings.max_asks` times.
7. **Rules.** Every generated reply is checked for:
   - forbidden phrases (replaced);
   - required wording (inserted when the check regex fails);
   - emoji when they are off;
   - **claims without receipt**, such as "I have forwarded your request" when no handover tool ran.
8. **Commit.** The session state is saved with compare-and-set; a concurrent update retries the turn. Events (`turn_end`, `record`, `handover`, …) are delivered only after the save succeeds (an outbox).

## Journeys

A journey is a graph of cards. Edges go from `<card id>.<port>` to `<card id>`. Each card has a mechanism:

| card | mechanism | ports |
|---|---|---|
| trigger | decision (+ optional `when` expression) | `next` (or one per choice option) |
| condition | code expression | `true`, `false` |
| decision | decision | `yes`, `no` (or one per option, plus `unsure`) |
| extract | llm + validators | `ok`, `partial`, `invalid`, `error` |
| tool | your callback | `ok`, `error`, `timeout` |
| ask | state (waits for the customer) | `resume`, `gave_up` |
| set | code | `next` |
| emit | template | `next` |
| end | | ends with `done`, `handover`, `abandoned` or `error` |

`steward check` and the admin page validate the graph:
- unknown ports, dangling edges and unreachable cards;
- templates, slots and tools that don't exist;
- expressions that don't parse.

## Expressions

Conditions, `set` values and `when` guards use a small, safe expression language (parsed with Python's `ast`, with no attribute access to Python objects).

The names you can use:
- `slots.x`: what is known about the customer;
- `state.x`: per-session flags;
- `vars.x`: per-journey-run variables, including tool results via `save_as`;
- `trigger.choice` and `trigger.score`;
- `contacts.x`;
- `turn.message`, `turn.channel`, `turn.lang`, `turn.session`, `turn.date`, `turn.contact_in_message`, `turn.number_in_message`, `turn.is_test`, `turn.metadata`.

Functions: `has(x)`, `valid(x, 'validator')`, `len(x)` and `lower(x)`, plus any you add with `@bot.expr_function`.

## Slots and validators

Slots are filled by `extract` cards (an LLM call over the latest message or the whole conversation), and validators decide the result in code:
- A validator first applies its **normalize** steps: `digits`, `strip`, `lower`, `{"country_code": {"code": "1", "trunk": "0"}}`. It then requires its `pattern` to match fully.
- A required slot that was given but fails validation sends the extract card out through its `invalid` port, so the journey can say so.
- `enum` slots take `values`, and `value_descriptions` tell the extractor what each option means.
- The contact slot (`settings.contact_slot`) is also captured by code first, so a phone number in a message is never lost to a model mistake.

## Templates and languages

Templates hold one text per language (`settings.languages`; the first one is the default). The reply language is:
- `Message.lang` when the host sets it;
- otherwise the customer's script, mapped by `settings.script_lang` (`{"latin": "en", "other": "xx"}`);
- otherwise the default language.

Placeholders work as follows:
- `{{slots.name}}` inserts a value;
- `{{slots.name|prefix:', '}}` adds a prefix only when the value is set;
- `|suffix:`, `|default:`, `|upper` and `|lower` work the same way.

`approved: false` marks a text that still needs client sign-off; the admin page shows those texts.

## Policies

A policy is a decision question plus an action:
- **`template`** sends an approved template. With `exclusive`, the policy's template is the only reply. With `suppress_kb`, there is no knowledge answer for the part the policy matched. With `no_ask`, the reply doesn't also ask for the contact.
- **`inject`** adds an instruction to the answer prompt for this turn.

`scope: "part"` applies a policy to one part of a multi-question message. `scope: "turn"` applies it to the whole message.

## Records and handover

The built-in `records.append` tool writes to the RecordStore. Writes are idempotent by key: a repeated key merges new non-empty fields into the existing record. The admin page lists the records and exports them as CSV.

A handover works like this:
1. A journey sets `state.handover_active`.
2. The session is paused until an agent releases it (`bot.sessions.release(session)` or the admin API).
3. `bot.on("handover", ...)` is where you alert your agents.
