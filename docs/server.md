# Admin page and API

`pip install "steward-harness[server]"` (FastAPI).

```python
from steward.server import Principal, create_app, create_router

app.include_router(create_router(bot, auth=my_auth), prefix="/steward")   # inside your FastAPI app
app = create_app(bot, auth=my_auth, prefix="/steward")                    # or a standalone app
```

The page is at `<prefix>/admin`. It is static: every piece of data comes from the authenticated API below.

## Auth and roles

`auth(request)` may be sync or async. It returns a `Principal(id, roles)` or raises `HTTPException(401/403)`.

| role | can |
|---|---|
| `viewer` | read the config, history, catalog, prompt preview, sessions, records, agents switch |
| `editor` | also edit the draft, validate, use the test chat, reset or release sessions, toggle agents online |
| `publisher` | also publish, discard the draft, restore an old version |

`create_router` refuses to start without `auth` unless `insecure_local=True`. Use that only for local development; `steward serve --insecure-local` only binds to localhost.

## Endpoints

| method and path | role | |
|---|---|---|
| `GET /admin` | | The page |
| `GET /config?draft=1` | viewer | The config document (published, or the draft) with its `rev` |
| `PUT /config` | editor | Save the draft (`{"config": {...}, "base_rev": "..."}`). Returns the validation issues; a stale `base_rev` returns 409 |
| `POST /config/validate` | editor | Validate without saving |
| `POST /config/publish` | publisher | Publish the draft (`{"note": "..."}`) |
| `POST /config/discard` | publisher | Drop the draft |
| `GET /config/history` | viewer | Published versions |
| `POST /config/restore` | publisher | Make an old version the draft |
| `GET /catalog` | viewer | Card types and ports, registered tool implementations, languages, knowledge section titles (for the editors) |
| `GET /prompt?draft=1` | viewer | The system prompts the models will see |
| `POST /chat` | editor | The test chat (`{"text", "session", "channel", "config": "draft", "is_test": true}`) with the full trace |
| `GET /sessions/{id}` | viewer | Session state |
| `POST /sessions/{id}/reset`, `/release` | editor | Forget a session; hand it back from a human to the assistant |
| `GET`/`POST /runtime/agents` | viewer / editor | The "agents online" switch |
| `GET /records?type=lead`, `/records.csv` | viewer | Records, and the CSV export |

With `knowledge_admin=KnowledgeAdmin(bot)` (from `steward.build`), the approved-knowledge tab and its endpoints are added (`/knowledge/...`). See [build.md](build.md).

## The page

The page has these tabs:
- **Journeys:** a canvas of cards and edges, edited with the bundled Drawflow editor.
- **Policies, Rules, Templates:** each language side by side, with `approved` flags.
- **Prompt and profile:** with a prompt preview.
- **Slots and tools.**
- **Records:** with the agents switch.
- **History.**
- **Test:** chat against the draft, with the trace.

Edits only change the draft. The live assistant keeps using the published version until a publisher publishes.
