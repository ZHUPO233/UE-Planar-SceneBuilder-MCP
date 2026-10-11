# UEPLANE

**Reference-image driven scene reproduction for Unreal Engine 5.8 — a gated workflow that runs inside the editor.**

UEPLANE turns "here is a photo of a street" into "the street is in my level", as a **process**:
it extracts an element list, finds real assets for it, lays out one row per object, shows you figures to
approve, builds the level, applies surfaces and configures the environment — keeping a durable ledger of
what it placed so later changes are **incremental** rather than a full rebuild.

It does not ship scene assets. It ships the **workflow**.

---

## Requirements

| | |
|---|---|
| Engine | Unreal Engine **5.8** |
| Platforms | Windows (Editor) |
| Modules | **none** — this is a pure Python plugin |
| Built-in plugins | `PythonScriptPlugin`, `ToolsetRegistry`, `ModelContextProtocol`, `EditorToolset` (all declared as dependencies and enabled automatically) |

> Those are Epic's own editor plugins. UEPLANE registers its tools through `ToolsetRegistry`, and the
> editor tools it drives (`SceneTools`, `ActorTools`, `PrimitiveTools`, …) come from `EditorToolset` —
> which is **off by default in a fresh project**, so UEPLANE declares it and lets the editor turn it on.
> If your engine build does not ship these plugins, UEPLANE has nothing to attach to.

---

## Install

1. Copy the `UEPLANE` folder into your project's `Plugins/` directory (or into the engine's `Plugins/`).
2. Restart the editor. The four plugins listed above are enabled automatically as dependencies —
   if the editor asks whether to enable them, say yes.
3. **Start the MCP server once**: open the console (`~`) and run

   ```
   ModelContextProtocol.StartServer
   ```

   The endpoint is then served by the editor at `http://127.0.0.1:8000/mcp`. It is remembered from
   then on and starts with the editor.
4. The first time the plugin loads it creates its working directory
   `<YourProject>/Saved/UEPLANE/` and seeds a copy of its configuration into `Saved/UEPLANE/config/`.

Everything UEPLANE writes lives under `Saved/UEPLANE/` — nothing is written into your Content folder.

---

## Connecting your agent

The endpoint is served by the editor itself, on your machine only.

**1. Start the server once.** In the editor console (`~`):

```
ModelContextProtocol.StartServer
```

It is remembered after that and starts with the editor. If you skip this, nothing is listening on the
port and your client will simply fail to connect.

**2. Point your MCP client at it** (streamable HTTP):

```json
{
  "mcpServers": {
    "unreal-editor": { "url": "http://127.0.0.1:8000/mcp" }
  }
}
```

The port is `8000` by default; the path is `/mcp`. Some clients use a different root key — VS Code's
`.vscode/mcp.json` uses `servers`, for example.

**3. Expect three meta-tools, not twenty.** Unreal's MCP server runs with tool search on, so
`tools/list` returns only:

| meta-tool | what it does |
|---|---|
| `list_toolsets` | lists every registered toolset |
| `describe_toolset` | shows one toolset's tools and their parameter schemas |
| `call_tool` | actually calls one |

So a client discovers UEPLANE first and calls it second:

```
list_toolsets                                    -> finds "ueplane.tools.UeplaneTools"
describe_toolset("ueplane.tools.UeplaneTools")   -> the 20 tools, with schemas
call_tool(toolset_name="ueplane.tools.UeplaneTools",
          tool_name="official_status", arguments={})
```

> If your agent is not good at discovery, give it the toolset name up front — that is all it needs.
> **`PROMPT.md`** in this folder is a ready-to-paste instruction block that tells an agent exactly
> where to look; drop it into your client's system prompt or project instructions.

**4. If your agent stops working after you restart the editor.** The editor's MCP server forgets all
sessions when it restarts, so a client that is still holding the old session id gets refused. Unreal
does the right thing here — it answers `404` with `Unknown session id …; client should reinitialize` —
but not every MCP client acts on that yet, and some report only a bare `Error POSTing to endpoint:`.

Fix: reconnect that MCP server in your client, or restart the client. Nothing is lost —
UEPLANE keeps its state on disk under `Saved/UEPLANE/`, not in the session.

You will hit this whenever the editor restarts: enabling a plugin, switching levels, or recovering
from a crash. It is a client-side gap, not an Unreal or UEPLANE problem.

---

## Getting started

Once connected:

1. `official_status` — link self-check. **Call this first**: it is the key to the gate that every
   editor-touching tool checks, so nothing else will run until it reports a healthy link.
2. `get_plan` — read the current state. Its `stages` field is an eight-stage overview with the evidence
   for each stage and what to call next; start there rather than guessing where you left off.
3. Work the stages in order. The tools are grouped by stage in their descriptions; each one explains
   what it will not do as carefully as what it will.

---

## The workflow

| Stage | What happens |
|---|---|
| 1 · Elements | You supply reference images; the element list is extracted and **you confirm it**. |
| 2 · Assets | Each element is matched to a real asset by name. No guessing — a name that does not match is reported as missing and registered as a whitebox placeholder instead. |
| 3 · Layout | A placement table, **one row per object**, with positions, footprints and absolute base elevation. |
| 4 · Approval | Figures are drawn from the table and **you approve them**. Geometry changes invalidate the approval — the fingerprint is checked in code. |
| 5 · Build | Actors are placed into the level, incrementally, with a ledger that makes later edits touch only the affected rows. |
| 6 · Surfaces | Materials and material instances are applied to the whiteboxes as component-level overrides. |
| 7 · Environment | Time-of-day, sky, atmosphere, fog, clouds and post-process as one consistent set, with a rollback ledger. |
| 8 · Review | Three-way reconciliation (plan ↔ build ledger ↔ actual level) plus camera preview renders. |

### The gates are the product

Every stage that changes something is gated, and the gates are enforced in code rather than described
in a prompt:

- **Nothing is placed** before the placement table has been approved against figures you have seen.
- **Nothing is overwritten** without your own words: where the plugin finds that a person has moved
  something in the editor, it refuses by default and asks.
- **Nothing is reported as fine when it could not be checked.** A tool that could not read something says
  so — "not checked" and "checked and fine" are never allowed to look the same.
- **A full ledger** records what was placed, what was changed and what the previous values were, so
  changes are reversible and re-runs are incremental.

---

## Configuration

`Saved/UEPLANE/config/` holds plain JSON, seeded on first run and read on every call — changes take
effect without editing code or restarting the editor.

| File | What it controls |
|---|---|
| `environments.json` | Environment factors and time-of-day presets (sun, sky, fog, clouds, post-process…). |
| `surface_materials.json` | Recipes for creating missing materials and material instances. |
| `asset_categories.json` | The asset category table used when building the element list. |

---

## Notes and limits

- The editor must be running; UEPLANE drives the open level.
- Terrain, meshes and textures come from **your** project. UEPLANE finds and places what you already own;
  it does not download or generate assets.
- The workflow's own state lives under `Saved/UEPLANE/` and nothing is written into `Content/`.
  Saving the level is an explicit step, and only the environment stage does it automatically.
  Two tools do touch project assets, and they say so in their own description: one can **create**
  missing material assets, and one can **rename** assets you point it at.
- Rain, snow and Niagara-based weather are not part of this release.
- **This plugin is one of two ways to run the same workflow.** The same engine is published as a free,
  MIT-licensed standalone MCP server in the project repository. The feature set is identical — the
  plugin simply needs no Python environment, and connects with one URL instead of a command. If you are
  comfortable setting up a Python MCP server yourself, the free one will do the same job.

---

## Links

- Documentation and issue tracker: <https://github.com/ZHUPO233/UE-Planar-SceneBuilder-MCP>
- Support: <https://github.com/ZHUPO233/UE-Planar-SceneBuilder-MCP/issues>
