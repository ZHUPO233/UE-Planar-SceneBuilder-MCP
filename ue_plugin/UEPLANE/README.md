# UEPLANE

**Reference-image driven scene reproduction for Unreal Engine 5.8 — a gated workflow that runs inside the editor.**

UEPLANE turns "here is a photo of a street" into "the street is in my level", as a **process**:
it extracts an element list, finds real assets for it, lays out one row per object, writes a page you look
at and approve, builds the level, applies surfaces, configures the environment and reconciles the result —
keeping a durable ledger of what it placed so later changes are **incremental** rather than a full rebuild.

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

Everything UEPLANE writes **about its own workflow** lives under `Saved/UEPLANE/`; it does not scatter
files through your Content folder. Two tools can still touch project assets there, and both say so in
their own description: one **creates** missing material assets under `/Game/UEMCP/Materials/`, and one
**renames** assets you point it at.

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
describe_toolset("ueplane.tools.UeplaneTools")   -> the 21 tools, with schemas
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
| 1 · Assets | You supply reference images; the element list is extracted from them and each element is matched to a real asset by name — **you confirm both**. No guessing: a name that does not match is reported as missing and registered as a whitebox placeholder instead. |
| 2 · Placement plan | A placement table, **one row per object**, with positions, footprints and absolute base elevation. Writing it also generates a **three.js page** of the table, and **you approve the plan against that page**. Geometry changes invalidate the approval — the fingerprint is checked in code. |
| 3 · Build | Actors are placed into the level, incrementally, with a ledger that makes later edits touch only the affected rows. The call that finishes this stage also **freezes the approved plan and opens its successor**, so everything after it is a row-level edit rather than a fresh approval cycle. |
| 4 · Tuning | Small edits after the build: your own words are recorded, the plan is patched **row by row**, and only those rows are re-placed. **No page, no second approval** — you judge the result in the editor. |
| 5 · Surfaces | Materials and material instances are applied to the whiteboxes as component-level overrides. |
| 6 · Environment | Time-of-day, sky, atmosphere, fog, clouds and post-process as one consistent set, with a rollback ledger. |
| 7 · Review & iteration | Three-way reconciliation (plan ↔ build ledger ↔ actual level) plus camera preview renders. Read-only: it reports numbers, it does not judge and it does not edit. |
| 8 · Export / exchange (on demand, last) | A DCC-agnostic scene description of the layout, for moving the same scene to Blender and back. |

> `get_plan().stages` lists these same eight stages in order — its stage **names are in Chinese**, so match
> them by the `no` field rather than by wording. It is computed live from the files on disk; this table is
> only the shape of the workflow, not a status report.

### The gates are the product

Every stage that changes something is gated, and the gates are enforced in code rather than described
in a prompt:

- **Never draw the page yourself.** The approval page is written by code the moment the plan is saved. If
  it is missing, or the verdict says it does not match the data, read `plan_view.errors` — drawing one
  yourself does not count, and bitmaps are not accepted.
- **Look at the scene before changing it.** With a build ledger present, editing the plan and building the
  level are both refused until a *current* `evaluate_layout()` report exists for the plan on disk.
- **Nothing is placed** before the placement table has been approved against a page you have seen.
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
  it downloads nothing, and the only assets it can create are **materials / material instances** — through
  the one tool whose description says it creates them.
- The workflow's own state lives under `Saved/UEPLANE/`.
- Saving the level is an explicit step in two places: the environment stage saves automatically once it
  succeeds (`save=true` by default), and the surfaces stage saves when you ask it to (`save=true`).
  Building the level, drawing pages and reconciling the layout never save.
- Rain, snow and Niagara-based weather are not part of this release.
- **This plugin is one of two ways to run the same workflow.** The same engine is published as a free,
  MIT-licensed standalone MCP server in the project repository. The feature set is identical — the
  plugin simply needs no Python environment, and connects with one URL instead of a command. If you are
  comfortable setting up a Python MCP server yourself, the free one will do the same job.

---

## Links

- Documentation and issue tracker: <https://github.com/ZHUPO233/UE-Planar-SceneBuilder-MCP>
- Support: <https://github.com/ZHUPO233/UE-Planar-SceneBuilder-MCP/issues>

---

# 安装 · 前置 · 排错（1.0.0 补）

## 前置依赖

| 依赖 | 要求 |
|---|---|
| Unreal Engine | **5.8**（`UEPLANE.uplugin` 的 `EngineVersion` 写死 5.8.0） |
| Python | UE 自带的 3.11，**无需另装**；但必须在编辑器里启用 Python |
| 官方插件 | `PythonScriptPlugin` / `ToolsetRegistry` / `ModelContextProtocol` / `EditorToolset`（已在 `.uplugin` 里声明，会自动启用） |
| MCP 客户端 | 任意支持 MCP 的 AI 客户端（把 `PROMPT.md` 里那两行规则之间的代码块贴进系统提示） |

## 安装

1. 把 `UEPLANE/` 整个文件夹放进 **项目** 的 `Plugins/`（或 **引擎** 的 `Engine/Plugins/`）
2. 重启编辑器 → `Edit > Plugins` 搜 `UEPLANE`，确认 Enabled
3. 验证链路：让 MCP 客户端调一次 **`official_status()`**
   - 正常返回 `{"reachable": true, "toolset_count": 12 …}`
   - 之后 `get_plan().stages` 可看八阶段总览

## 数据放在哪

所有状态都在**你的工程**里，不随插件分发：

```
Saved/UEPLANE/scenes/<场景 key>/
  catalog/   elements.json / asset_list.json / material_list.json …
  views/     plan_v2.json / build_state_v2.json / evaluate_v1.json / acceptance.json / *.html
```

**换参考图 = 换场景**（每个关卡各自一套状态）；同一张图上推倒重来用 `reset_scene()`（要你本人的原话）。

## 排错

| 现象 | 原因 / 处理 |
|---|---|
| MCP 工具里看不到 `ueplane` 工具集 | 插件没启用，或 `PythonScriptPlugin` 没开 → 看 `Output Log` 搜 `UEPLANE` |
| `拒收：动 UE 之前必须先确认链路` | 本进程还没做过链路检查 → 先调一次 `official_status()` |
| `拒收：还没有阶段一签过字的资产清单` | 顺序反了：先 `confirm_elements` → `find_assets` → `confirm_assets`，再规划 |
| 阶段⑤ 材质**整批拒收** | 插件**不自带任何材质**：先 `apply_surfaces(mode="probe")` 按你的工程现探；自己造材质要改 `Content/config/surface_materials.json` 的 `create` 段（默认空） |
| 阶段②那一页打不开 / 空白 | 检查 `Content/Python/ueplane/planning/web/{preview3d.html,three.min.js}` 是否随包；页面由代码生成，失败时错误写在页面上 |
| 材质/云实例建到了奇怪目录 | 自建资产固定落在插件命名空间 `/UEPLANE/Materials` 与 `/UEPLANE/env` |
| 改完 plan 后落关卡被拒 | 先 `get_plan()` 回读，再 `evaluate_layout()` 重跑对账，然后才 `execute_build()` |
| 关卡没存 | 本插件**除阶段⑥ `setup_environment(save=true)` 外从不存盘** → 满意后自己 Ctrl+S |

## 已知边界（如实）

- **工具拦不住模型“不停手”**：`stages[].must_stop` / `stop_why` 是强提示 + 工具入口闸，不是硬停；
  真正的硬停需要在 **MCP 客户端一侧**拦住工具调用。
- 纪元 2 开始后前三阶段的写工具会**冻结**（`reset_scene()` 才能重来）。
- 不支持 UE 5.8 以外的版本。