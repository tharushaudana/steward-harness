# Configuration reference

Generated from `steward.config.models` by `steward schema --markdown`; do not edit by hand. The JSON Schema
(`schema/steward.schema.json`) has the same information for editors and agents.

## Config (root)

The whole file. Unknown keys are errors everywhere (typos never pass silently).

| field | type | default | description |
|---|---|---|---|
| `schema_version` | 2 | `2` |  |
| `version` | int | `0` |  |
| `changelog` | list[ChangelogEntry] | `[]` |  |
| `profile` | Profile | object (see its section) |  |
| `prompts` | Prompts | object (see its section) |  |
| `settings` | Settings | object (see its section) |  |
| `contacts` | dict[str, Any] | `{}` | Free data, usable as {{contacts.x}}. |
| `validators` | dict[str, Validator] | `{}` |  |
| `slots` | dict[str, Slot] | `{}` |  |
| `templates` | dict[str, Template] | `{}` |  |
| `rules` | Rules | object (see its section) |  |
| `channels` | dict[str, Channel] | `{}` |  |
| `flow` | Flow | object (see its section) |  |
| `policies` | list[Policy] | `[]` |  |
| `tools` | dict[str, ToolSpec] | `{}` |  |
| `journeys` | list[Journey] | `[]` |  |

## profile

Who the assistant is: a small system prompt shown to the answer model.

| field | type | default | description |
|---|---|---|---|
| `name` | str | `"Assistant"` |  |
| `identity` | str | `""` |  |
| `purpose` | str | `""` |  |
| `tone` | str | `""` |  |
| `goals` | str | `""` |  |
| `escalation` | str | `""` | What to tell customers when the assistant cannot help (contacts). |
| `greeting` | str \| dict[str, str] | `"Hello! How can I help you today?"` |  |

## prompts

Extra prompt text.

| field | type | default | description |
|---|---|---|---|
| `answer_extra` | str | `""` | Extra instructions added to the client-rules block of the answer prompt. |

## settings

Runtime behaviour.

| field | type | default | description |
|---|---|---|---|
| `languages` | list[str] | `["en"]` | Reply languages; the first is the default. |
| `script_lang` | dict[str, str] | `{}` | Map a detected script to a language, e.g. {'latin': 'en', 'other': 'xx'}. |
| `contact_slot` | str | `"phone"` | The slot holding the customer's contact (captured by code first). |
| `ask_timeout_turns` | int | `3` |  |
| `max_asks` | int | `2` | How often per session the flow may ask for the contact. |
| `max_reasks` | int | `2` | A journey asking again after a reply more than this takes its gave_up port. |
| `paused_template` | str | `""` | Template sent while a session is handed over to a human. |
| `after_handover` | "pause" \| "continue" | `"pause"` | After a handover: 'pause' answers only with paused_template until a human releases the session; 'continue' keeps answering normally (the team has been told and joins when ready). |
| `paused_contact_tool` | str \| null | `null` | While paused: a tool instance (e.g. save_lead) called when the customer shares a valid contact, so it is never lost. |
| `language_names` | dict[str, str] | `{}` | Display names of reply languages for the answer model, e.g. {'hi': 'Hindi (Devanagari script)'}; common codes are built in. |
| `agents_default_online` | bool | `false` |  |
| `tools_mode` | "live" \| "mock" | `"live"` |  |
| `allow_hosts` | list[str] | `[]` | Hosts the built-in http.request tool may call. |

## validators.<id>

Normalise then match a value (phone numbers, emails, ids).

| field | type | default | description |
|---|---|---|---|
| `normalize` | list[str \| dict[str, Any]] | `[]` | Steps: unicode_digits, strip_non_digits, lower, strip, {'country_code': {'code': '1', 'trunk': ''}}. |
| `pattern` | str | **required** | Regular expression the normalised value must fully match. |

## slots.<name>

What the assistant may learn about the customer (extract cards fill them).

| field | type | default | description |
|---|---|---|---|
| `type` | "string" \| "phone" \| "email" \| "enum" \| "number" | `"string"` |  |
| `description` | str | `""` |  |
| `validator` | str \| null | `null` |  |
| `values` | list[str] | `[]` | Options of an enum slot. |
| `value_descriptions` | dict[str, str] | `{}` | What each enum option means; shown to the extractor so it picks the most specific option. |
| `value_descriptions_source` | str | `""` |  |
| `persist` | "session" \| "turn" | `"session"` |  |
| `pii` | bool | `false` |  |
| `overwrite` | "latest" \| "keep_first" | `"latest"` |  |

## templates.<id>

Approved texts, one per language. `{{slots.name|prefix:', '}}` placeholders.

| field | type | default | description |
|---|---|---|---|
| `text` | dict[str, str] | **required** | One text per language. |
| `approved` | bool \| dict[str, bool] | `true` | Client sign-off, overall or per language. |
| `source` | str | `""` |  |
| `requires_receipt` | str \| null | `null` | Only sent if this tool succeeded in the turn. |
| `covers` | list[str] | `[]` | Templates made redundant when this one is sent. |
| `counts_as_ask` | str \| null | `null` | Sending this template counts as asking for this slot. |
| `once_per_session` | bool | `false` | Send at most once per conversation (e.g. a policy's standard reply that should not repeat every turn). |

## rules

Checks applied to every generated reply.

| field | type | default | description |
|---|---|---|---|
| `voice` | list[str] | `[]` |  |
| `no_emoji` | bool | `false` |  |
| `plain_text` | bool | `false` | Plain text replies: no markdown (bold, italics, headings, tables) and no bullet symbols; enforced in code. |
| `required_wording` | list[RequiredWording] | `[]` |  |
| `forbidden_phrases` | list[ForbiddenPhrase] | `[]` |  |
| `claims_without_receipt` | ClaimsRule | object (see its section) |  |

## rules.required_wording[]

| field | type | default | description |
|---|---|---|---|
| `id` | str | **required** |  |
| `instruction` | str | `""` |  |
| `when_regex` | str \| null | `null` |  |
| `when_mentions_entity_types` | list[str] | `[]` |  |
| `check` | dict[str, str] | `{}` | Regex per language that must match the reply. |
| `insert` | dict[str, str] | `{}` | Text inserted when the check still fails. |

## rules.forbidden_phrases[]

| field | type | default | description |
|---|---|---|---|
| `phrase` | str | **required** |  |
| `replace` | str | `""` |  |

## rules.claims_without_receipt

Phrases that claim an action; only allowed when that tool succeeded.

| field | type | default | description |
|---|---|---|---|
| `phrases` | list[str] | `[]` |  |
| `receipt_tools` | list[str] | `[]` |  |

## channels.<name>

Per channel (Message.channel) behaviour.

| field | type | default | description |
|---|---|---|---|
| `mode` | "full" \| "template_only" | `"full"` |  |
| `template` | str \| null | `null` | For template_only channels: the only reply sent. |

## flow

Conversation-level steps: welcome, asking for the contact, thanking.

| field | type | default | description |
|---|---|---|---|
| `welcome` | FlowStep \| null | `null` |  |
| `ask_contact` | AskContactStep \| null | `null` |  |
| `thank_you` | FlowStep \| null | `null` |  |

## flow.ask_contact

| field | type | default | description |
|---|---|---|---|
| `template` | str | **required** |  |
| `template_when_unanswered` | str \| null | `null` | Used when the question could not be answered. |

## policies[]

A decision question plus an action (template or prompt injection).

| field | type | default | description |
|---|---|---|---|
| `id` | str | **required** |  |
| `enabled` | bool | `true` |  |
| `scope` | "turn" \| "part" | `"part"` |  |
| `priority` | int | `0` |  |
| `threshold` | float | `0.65` |  |
| `when` | str \| null | `null` | Optional code expression; the policy is only asked when it is true. |
| `trigger` | DecisionQuestion | **required** |  |
| `action` | PolicyAction | object (see its section) |  |

## policies[].action

| field | type | default | description |
|---|---|---|---|
| `type` | "template" \| "inject" | `"template"` |  |
| `template` | str \| null | `null` |  |
| `text` | str \| null | `null` | For inject: the instruction added to the answer prompt. |
| `position` | "before" \| "body" \| "suffix" | `"body"` |  |
| `exclusive` | bool | `false` | Only this template; no knowledge answer. |
| `suppress_kb` | bool | `false` | No knowledge answer for the part this policy matched. |
| `no_ask` | bool | `false` | Do not ask for the contact after this template. |

## DecisionQuestion (trigger / decision)

A question for the decision model.

| field | type | default | description |
|---|---|---|---|
| `type` | "noul" \| "choice" \| "score" | `"noul"` |  |
| `instructions` | str | **required** | The question, phrased about the customer's message. |
| `criteria` | dict[str, str] | `{}` | noul: {'true': ..., 'false': ...} descriptions. choice: {option: description}; 'none' = no port. |

## tools.<id>

A tool instance. `impl` defaults to the instance id; register it with @bot.tool(impl).

| field | type | default | description |
|---|---|---|---|
| `impl` | str \| null | `null` |  |
| `description` | str | `""` |  |
| `args` | dict[str, Any] | `{}` | Arguments; strings may use {{slots.x}} placeholders. |
| `idempotency_key` | str \| null | `null` | Template of a key; repeated calls with the same key merge. |
| `side_effect` | bool | `true` |  |
| `timeout_ms` | int | `5000` |  |
| `mock` | dict[str, Any] \| null | `null` | Result used in mock mode: {'ok': true, 'data': {...}}. |

## journeys[]

A graph of cards (nodes) joined by edges from '<node>.<port>' to '<node>'.

| field | type | default | description |
|---|---|---|---|
| `id` | str | **required** |  |
| `title` | str | `""` |  |
| `enabled` | bool | `true` |  |
| `nodes` | list[TriggerNode \| ConditionNode \| DecisionNode \| ExtractNode \| ToolNode \| AskNode \| SetNode \| EmitNode \| EndNode \| CustomNode] | **required** |  |
| `edges` | list[Edge] | `[]` |  |

## journeys[].edges[]

| field | type | default | description |
|---|---|---|---|
| `from` | str | **required** | '<node id>.<port>' |
| `to` | str | **required** |  |

## card: trigger

Starts the journey when the decision model says so (and `when` is true).

| field | type | default | description |
|---|---|---|---|
| `id` | str | **required** |  |
| `ui` | UI \| null | `null` |  |
| `note` | str | `""` |  |
| `type` | "trigger" | `"trigger"` |  |
| `scope` | "turn" \| "part" | `"part"` |  |
| `mode` | "tap" \| "exclusive" | `"tap"` |  |
| `takeover` | "turn" \| null | `null` |  |
| `priority` | int | `0` |  |
| `threshold` | float \| null | `null` | Default 0.5 for taps, 0.65 for exclusive journeys. |
| `when` | str \| null | `null` |  |
| `decision` | DecisionQuestion | **required** |  |

## card: condition

Branches on a safe expression.

| field | type | default | description |
|---|---|---|---|
| `id` | str | **required** |  |
| `ui` | UI \| null | `null` |  |
| `note` | str | `""` |  |
| `type` | "condition" | `"condition"` |  |
| `expr` | str | **required** |  |

## card: decision

Branches on a decision-model question.

| field | type | default | description |
|---|---|---|---|
| `id` | str | **required** |  |
| `ui` | UI \| null | `null` |  |
| `note` | str | `""` |  |
| `type` | "decision" | `"decision"` |  |
| `decision` | DecisionQuestion | **required** |  |
| `threshold` | float | `0.5` |  |

## card: extract

Fills slots with the extract model; validators decide ok / partial / invalid.

| field | type | default | description |
|---|---|---|---|
| `id` | str | **required** |  |
| `ui` | UI \| null | `null` |  |
| `note` | str | `""` |  |
| `type` | "extract" | `"extract"` |  |
| `slots` | list[str] | **required** |  |
| `require` | list[str] | `[]` |  |
| `source` | "conversation" \| "message" | `"conversation"` |  |
| `instructions` | str | `""` |  |

## card: tool

Calls a tool instance by id.

| field | type | default | description |
|---|---|---|---|
| `id` | str | **required** |  |
| `ui` | UI \| null | `null` |  |
| `note` | str | `""` |  |
| `type` | "tool" | `"tool"` |  |
| `tool` | str | **required** |  |
| `save_as` | str \| null | `null` | Where the tool's data goes, e.g. vars.result. |

## card: ask

Asks the customer and waits; the next message resumes the journey.

| field | type | default | description |
|---|---|---|---|
| `id` | str | **required** |  |
| `ui` | UI \| null | `null` |  |
| `note` | str | `""` |  |
| `type` | "ask" | `"ask"` |  |
| `require` | list[str] | `[]` |  |
| `template` | str \| null | `null` | Optional: empty when an Output card just asked. |
| `templates_by_missing` | dict[str, str] | `{}` | Ask only for what is missing: {'name': 'ask_name', 'phone': 'ask_phone', 'name,phone': 'ask_name_phone'} (keys are the missing required slots, sorted, comma-separated); falls back to `template`. |

## card: set

Assigns expressions to state.x / vars.x / slots.x.

| field | type | default | description |
|---|---|---|---|
| `id` | str | **required** |  |
| `ui` | UI \| null | `null` |  |
| `note` | str | `""` |  |
| `type` | "set" | `"set"` |  |
| `assign` | dict[str, Any] | `{}` | {'state.x': 'expression', ...} |

## card: emit

Adds a template to the reply.

| field | type | default | description |
|---|---|---|---|
| `id` | str | **required** |  |
| `ui` | UI \| null | `null` |  |
| `note` | str | `""` |  |
| `type` | "emit" | `"emit"` |  |
| `template` | str | **required** |  |
| `position` | "before" \| "body" \| "replace" \| "suffix" | `"body"` |  |

## card: end

Ends the journey with a status.

| field | type | default | description |
|---|---|---|---|
| `id` | str | **required** |  |
| `ui` | UI \| null | `null` |  |
| `note` | str | `""` |  |
| `type` | "end" | `"end"` |  |
| `status` | "done" \| "handover" \| "abandoned" \| "error" | `"done"` |  |

## Card ports

Edges leave a card through one of its ports. A choice question gives the trigger and
decision cards one port per option instead.

| card | ports | what it does |
|---|---|---|
| `trigger` | `next` | When the journey applies: a decision question (yes/no, or a choice with one port per option) plus an optional code `when`. |
| `condition` | `true`, `false` | Branch on slots, vars, state, trigger or turn with a safe expression: has(), valid(x, 'validator'), len(), lower(), comparisons. |
| `decision` | `yes`, `no` | A judgement by the decision model: yes/no, or a choice with one port per option plus 'unsure'. |
| `extract` | `ok`, `partial`, `invalid`, `error` | The LLM fills slots from the latest message or the whole conversation; code validates them. 'invalid': a required value was given but failed validation (see vars.invalid). |
| `tool` | `ok`, `error`, `timeout` | Calls a tool instance; every call leaves a receipt. |
| `ask` | `resume`, `gave_up` | Optionally sends a template, then waits: the journey resumes here on the customer's reply, or takes gave_up. |
| `set` | `next` | Assigns values: keys like state.x, vars.x or slots.x; values are expressions. |
| `emit` | `next` | Sends an approved template to the output collector (before, body, replace, suffix). |
| `end` | (none) | Ends the journey with a status. |
