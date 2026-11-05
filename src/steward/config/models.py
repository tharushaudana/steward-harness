"""The configuration an admin edits (JSON file or ConfigStore), as typed pydantic models.

Everything here is data: profile, rules, templates, policies, journeys, tool instances. Code-side things (tool
implementations, stores, providers, engine thresholds, locked core prompts) are passed to Steward in Python.

Unknown keys are errors (extra="forbid"), so a typo never silently does nothing. Generate the JSON Schema with
`steward schema` (or `Config.model_json_schema()`); config files point at it with "$schema".
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Discriminator, Field, Tag, model_validator

SCHEMA_VERSION = 2

LocalizedText = dict[str, str]  # {"en": "...", "es": "..."}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ================================================================================ decisions (questions for the decision model)
class DecisionQuestion(_Model):
    """A bounded question answered by the decision model. noul: yes/no probability; choice: one of `criteria`'s keys."""

    type: Literal["noul", "choice", "score"] = "noul"
    instructions: str = Field(description="The question, phrased about the customer's message.")
    criteria: dict[str, str] = Field(default_factory=dict, description=(
        "noul: {'true': ..., 'false': ...} descriptions. choice: {option: description}; 'none' = no port."))


# ================================================================================ profile, prompts, settings
class Profile(_Model):
    name: str = "Assistant"
    identity: str = ""
    purpose: str = ""
    tone: str = ""
    goals: str = ""
    escalation: str = Field(default="", description="What to tell customers when the assistant cannot help (contacts).")
    greeting: str | LocalizedText = "Hello! How can I help you today?"


class Prompts(_Model):
    answer_extra: str = Field(default="", description="Extra instructions added to the client-rules block of the answer prompt.")


class Settings(_Model):
    languages: list[str] = Field(default_factory=lambda: ["en"], description="Reply languages; the first is the default.")
    script_lang: dict[str, str] = Field(default_factory=dict, description=(
        "Map a detected script to a language, e.g. {'latin': 'en', 'other': 'xx'}."))
    contact_slot: str = Field(default="phone", description="The slot holding the customer's contact (captured by code first).")
    ask_timeout_turns: int = 3
    max_asks: int = Field(default=2, description="How often per session the flow may ask for the contact.")
    max_reasks: int = Field(default=2, description="A journey asking again after a reply more than this takes its gave_up port.")
    paused_template: str = Field(default="", description="Template sent while a session is handed over to a human.")
    after_handover: Literal["pause", "continue"] = Field(default="pause", description=(
        "After a handover: 'pause' answers only with paused_template until a human releases the session; 'continue' "
        "keeps answering normally (the team has been told and joins when ready)."))
    paused_contact_tool: str | None = Field(default=None, description=(
        "While paused: a tool instance (e.g. save_lead) called when the customer shares a valid contact, so it is never lost."))
    language_names: dict[str, str] = Field(default_factory=dict, description=(
        "Display names of reply languages for the answer model, e.g. {'hi': 'Hindi (Devanagari script)'}; common codes are built in."))
    agents_default_online: bool = False
    tools_mode: Literal["live", "mock"] = "live"
    allow_hosts: list[str] = Field(default_factory=list, description="Hosts the built-in http.request tool may call.")


# ================================================================================ slots, validators
class Validator(_Model):
    normalize: list[str | dict[str, Any]] = Field(default_factory=list, description=(
        "Steps: unicode_digits, strip_non_digits, lower, strip, {'country_code': {'code': '1', 'trunk': ''}}."))
    pattern: str = Field(description="Regular expression the normalised value must fully match.")


class Slot(_Model):
    type: Literal["string", "phone", "email", "enum", "number"] = "string"
    description: str = ""
    validator: str | None = None
    values: list[str] = Field(default_factory=list, description="Options of an enum slot.")
    value_descriptions: dict[str, str] = Field(default_factory=dict, description=(
        "What each enum option means; shown to the extractor so it picks the most specific option."))
    value_descriptions_source: str = ""
    persist: Literal["session", "turn"] = "session"
    pii: bool = False
    overwrite: Literal["latest", "keep_first"] = "latest"


# ================================================================================ templates
class Template(_Model):
    """Approved wording, sent verbatim (placeholders like {{slots.name}} are filled in)."""

    text: LocalizedText = Field(description="One text per language.")
    approved: bool | dict[str, bool] = Field(default=True, description="Client sign-off, overall or per language.")
    source: str = ""
    requires_receipt: str | None = Field(default=None, description="Only sent if this tool succeeded in the turn.")
    covers: list[str] = Field(default_factory=list, description="Templates made redundant when this one is sent.")
    counts_as_ask: str | None = Field(default=None, description="Sending this template counts as asking for this slot.")
    once_per_session: bool = Field(default=False, description=(
        "Send at most once per conversation (e.g. a policy's standard reply that should not repeat every turn)."))

    def is_approved(self, lang: str) -> bool:
        return self.approved if isinstance(self.approved, bool) else self.approved.get(lang, True)


# ================================================================================ rules
class RequiredWording(_Model):
    id: str
    instruction: str = ""
    when_regex: str | None = None
    when_mentions_entity_types: list[str] = Field(default_factory=list)
    check: LocalizedText = Field(default_factory=dict, description="Regex per language that must match the reply.")
    insert: LocalizedText = Field(default_factory=dict, description="Text inserted when the check still fails.")


class ForbiddenPhrase(_Model):
    phrase: str
    replace: str = ""


class ClaimsRule(_Model):
    phrases: list[str] = Field(default_factory=list)
    receipt_tools: list[str] = Field(default_factory=list)


class Rules(_Model):
    voice: list[str] = Field(default_factory=list)
    no_emoji: bool = False
    plain_text: bool = Field(default=False, description=(
        "Plain text replies: no markdown (bold, italics, headings, tables) and no bullet symbols; enforced in code."))
    required_wording: list[RequiredWording] = Field(default_factory=list)
    forbidden_phrases: list[ForbiddenPhrase] = Field(default_factory=list)
    claims_without_receipt: ClaimsRule = Field(default_factory=ClaimsRule)


# ================================================================================ channels, flow
class Channel(_Model):
    mode: Literal["full", "template_only"] = "full"
    template: str | None = Field(default=None, description="For template_only channels: the only reply sent.")


class FlowStep(_Model):
    template: str
    channel: str | None = None


class AskContactStep(_Model):
    template: str
    template_when_unanswered: str | None = Field(default=None, description="Used when the question could not be answered.")


class Flow(_Model):
    welcome: FlowStep | None = None
    ask_contact: AskContactStep | None = None
    thank_you: FlowStep | None = None


# ================================================================================ policies
class PolicyAction(_Model):
    type: Literal["template", "inject"] = "template"
    template: str | None = None
    text: str | None = Field(default=None, description="For inject: the instruction added to the answer prompt.")
    position: Literal["before", "body", "suffix"] = "body"
    exclusive: bool = Field(default=False, description="Only this template; no knowledge answer.")
    suppress_kb: bool = Field(default=False, description="No knowledge answer for the part this policy matched.")
    no_ask: bool = Field(default=False, description="Do not ask for the contact after this template.")


class Policy(_Model):
    id: str
    enabled: bool = True
    scope: Literal["turn", "part"] = "part"
    priority: int = 0
    threshold: float = 0.65
    when: str | None = Field(default=None, description="Optional code expression; the policy is only asked when it is true.")
    trigger: DecisionQuestion
    action: PolicyAction = Field(default_factory=lambda: PolicyAction())


# ================================================================================ tools (instances; implementations are code)
class ToolSpec(_Model):
    """A tool instance journeys reference by its key. `impl` names the registered implementation (defaults to the key)."""

    impl: str | None = None
    description: str = ""
    args: dict[str, Any] = Field(default_factory=dict, description="Arguments; strings may use {{slots.x}} placeholders.")
    idempotency_key: str | None = Field(default=None, description="Template of a key; repeated calls with the same key merge.")
    side_effect: bool = True
    timeout_ms: int = 5000
    mock: dict[str, Any] | None = Field(default=None, description="Result used in mock mode: {'ok': true, 'data': {...}}.")


# ================================================================================ journeys (graphs of cards)
class UI(_Model):
    x: float = 0
    y: float = 0


class _Node(_Model):
    id: str
    ui: UI | None = None
    note: str = ""


class TriggerNode(_Node):
    type: Literal["trigger"] = "trigger"
    scope: Literal["turn", "part"] = "part"
    mode: Literal["tap", "exclusive"] = "tap"
    takeover: Literal["turn"] | None = None
    priority: int = 0
    threshold: float | None = Field(default=None, description="Default 0.5 for taps, 0.65 for exclusive journeys.")
    when: str | None = None
    decision: DecisionQuestion = Field(validation_alias=AliasChoices("decision", "jev"))


class ConditionNode(_Node):
    type: Literal["condition"] = "condition"
    expr: str


class DecisionNode(_Node):
    type: Literal["decision"] = "decision"
    decision: DecisionQuestion = Field(validation_alias=AliasChoices("decision", "jev"))
    threshold: float = 0.5


class ExtractNode(_Node):
    type: Literal["extract"] = "extract"
    slots: list[str]
    require: list[str] = Field(default_factory=list)
    source: Literal["conversation", "message"] = Field(default="conversation", validation_alias=AliasChoices("source", "from"))
    instructions: str = ""


class ToolNode(_Node):
    type: Literal["tool"] = "tool"
    tool: str
    save_as: str | None = Field(default=None, description="Where the tool's data goes, e.g. vars.result.")


class AskNode(_Node):
    type: Literal["ask"] = "ask"
    require: list[str] = Field(default_factory=list)
    template: str | None = Field(default=None, description="Optional: empty when an Output card just asked.")
    templates_by_missing: dict[str, str] = Field(default_factory=dict, description=(
        "Ask only for what is missing: {'name': 'ask_name', 'phone': 'ask_phone', 'name,phone': 'ask_name_phone'} "
        "(keys are the missing required slots, sorted, comma-separated); falls back to `template`."))


class SetNode(_Node):
    type: Literal["set"] = "set"
    assign: dict[str, Any] = Field(default_factory=dict, description="{'state.x': 'expression', ...}")


class EmitNode(_Node):
    type: Literal["emit"] = "emit"
    template: str
    position: Literal["before", "body", "replace", "suffix"] = "body"


class EndNode(_Node):
    type: Literal["end"] = "end"
    status: Literal["done", "handover", "abandoned", "error"] = "done"


class CustomNode(_Node):
    """A card type registered by the host (steward.harness.cards). Its fields are validated by that card."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    type: str


BUILTIN_NODE_TYPES = ("trigger", "condition", "decision", "extract", "tool", "ask", "set", "emit", "end")


def _node_tag(v: Any) -> str:
    t = v.get("type") if isinstance(v, dict) else getattr(v, "type", None)
    return t if t in BUILTIN_NODE_TYPES else "custom"


Node = Annotated[
    Annotated[TriggerNode, Tag("trigger")] | Annotated[ConditionNode, Tag("condition")]
    | Annotated[DecisionNode, Tag("decision")] | Annotated[ExtractNode, Tag("extract")]
    | Annotated[ToolNode, Tag("tool")] | Annotated[AskNode, Tag("ask")] | Annotated[SetNode, Tag("set")]
    | Annotated[EmitNode, Tag("emit")] | Annotated[EndNode, Tag("end")] | Annotated[CustomNode, Tag("custom")],
    Discriminator(_node_tag),
]


class Edge(_Model):
    from_: str = Field(alias="from", description="'<node id>.<port>'")
    to: str


class Journey(_Model):
    id: str
    title: str = ""
    enabled: bool = True
    nodes: list[Node]
    edges: list[Edge] = Field(default_factory=list)

    def trigger(self) -> TriggerNode | None:
        return next((n for n in self.nodes if isinstance(n, TriggerNode)), None)

    def node(self, node_id: str) -> Any:
        return next((n for n in self.nodes if n.id == node_id), None)


# ================================================================================ root
class ChangelogEntry(_Model):
    ts: str = ""
    version: int = 0
    note: str = ""


class Config(_Model):
    """A complete assistant configuration (schema_version 2)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, title="Steward configuration")

    schema_: str | None = Field(default=None, alias="$schema")
    schema_version: Literal[2] = SCHEMA_VERSION
    version: int = 0
    changelog: list[ChangelogEntry] = Field(default_factory=list)
    profile: Profile = Field(default_factory=lambda: Profile())
    prompts: Prompts = Field(default_factory=lambda: Prompts())
    settings: Settings = Field(default_factory=lambda: Settings())
    contacts: dict[str, Any] = Field(default_factory=dict, description="Free data, usable as {{contacts.x}}.")
    validators: dict[str, Validator] = Field(default_factory=dict)
    slots: dict[str, Slot] = Field(default_factory=dict)
    templates: dict[str, Template] = Field(default_factory=dict)
    rules: Rules = Field(default_factory=Rules)
    channels: dict[str, Channel] = Field(default_factory=dict)
    flow: Flow = Field(default_factory=Flow)
    policies: list[Policy] = Field(default_factory=list)
    tools: dict[str, ToolSpec] = Field(default_factory=dict)
    journeys: list[Journey] = Field(default_factory=list)

    @model_validator(mode="after")
    def _default_impl(self) -> Config:
        for key, spec in self.tools.items():
            if not spec.impl:
                spec.impl = key
        return self

    def journey(self, journey_id: str) -> Journey | None:
        return next((j for j in self.journeys if j.id == journey_id), None)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)
