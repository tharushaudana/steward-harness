# Tools

Tools are how journeys act on the world. There are two levels:

- A **tool instance** in the config (`tools.<id>`) says what to call and with which arguments. Admins edit these, and journeys refer to them by instance id.
- A **tool implementation** in your code is registered under a string **impl id**. One implementation can serve many instances. For example, the built-in `records.append` serves both `save_lead` and `create_handover`.

```json
"tools": {
  "save_lead": {
    "impl": "crm.save_lead",
    "args": {"record": {"name": "{{slots.name}}", "phone": "{{slots.phone}}"}},
    "idempotency_key": "lead:{{slots.phone}}:{{turn.date}}",
    "timeout_ms": 5000,
    "mock": {"ok": true, "data": {"id": "mock-lead"}}
  },
  "is_agent_available": {"impl": "agents.available", "side_effect": false}
}
```

When `impl` is omitted, it defaults to the instance id.

```python
from steward import ToolCall, ok, fail

@bot.tool("crm.save_lead", timeout_s=3)
async def save_lead(call: ToolCall):
    if not call.args["record"].get("phone"):
        return fail("no phone")
    ref = await crm.upsert(call.args["record"], key=call.idempotency_key)
    return ok({"id": ref}, receipt={"crm_id": ref})
```

## ToolCall

| field | meaning |
|---|---|
| `tool_id`, `impl` | The config instance id and the implementation id |
| `args` | The instance's `args`, with `{{...}}` placeholders rendered |
| `idempotency_key` | The rendered key, or `None`; prefixed `test:` for test conversations |
| `turn` | `TurnInfo`: session, channel, message, language, `is_test`, metadata |
| `slots`, `vars`, `state` | Read-only copies of the journey environment |
| `mode` | `"live"` or `"mock"` |
| `timeout_s` | The instance's timeout |
| `settings` | The config `settings` as a dict |

## Results

A tool may return any of these:
- `ok(data, receipt=...)` or `fail(error, retryable=False)`;
- a dict: `{"ok": true, "data": {...}}`;
- `None`, which means ok with no data.

An exception or a timeout never breaks the conversation. The card takes its `error` or `timeout` port.

Every call leaves a **receipt** in `reply.journeys[*].receipts`. Templates with `requires_receipt` and the claims rule depend on receipts. The assistant may only say "I've passed your details to the team" when a tool really did that in this turn.

## Idempotency

Retries and repeated messages must not create duplicates. Render a key from the slots (`lead:{{slots.phone}}:{{turn.date}}`) and use it as the upsert key in your system. The built-in `records.append` merges new non-empty fields into the existing record for the same key.

## Mock mode

Set `settings.tools_mode = "mock"` (or run `steward eval --mock-tools`) to run journeys without side effects. Each call resolves in this order:
1. the config instance's `mock`;
2. the implementation's `mock=` (`@bot.tool("x", mock={...})` or a callable);
3. a read-only implementation (`side_effect: false`) runs for real;
4. otherwise the call fails with "no mock result".

## Built-in implementations

| impl id | args | result |
|---|---|---|
| `records.append` | `{"table": "lead", "record": {...}}` | Appends to the RecordStore. Idempotent by key; a duplicate merges new fields. Emits a `record` event. |
| `agents.available` | | `{"available": bool}` from the runtime switch `agents_online` (toggled in the admin page), else `settings.agents_default_online` |
| `http.request` | `{"method", "url", "headers", "body"}` | Calls only hosts listed in `settings.allow_hosts`. `${env:NAME}` in headers is read from the environment, so secrets never live in the config. |

Register a built-in id yourself to replace it, for example `@bot.tool("agents.available")` to read your staffing system.

`bot.check()` fails when a config instance points at an unregistered implementation, and suggests the closest id.
