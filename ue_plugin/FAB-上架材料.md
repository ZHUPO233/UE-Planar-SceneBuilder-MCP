# UEPLANE · Fab 上架材料与自查清单（**插件版**）

> 这份文件**不进包**（已列在 `MakePlugin.ps1` 的 `$ExcludeDevFiles` 里）。
> 四节：① `.uplugin` 字段逐个说明 ② 给 Fab 表单的申报文案 ③ 上架自查清单 ④ 构建链条怎么用。
> ⚠ 所有数字以**可核对的产物**为准（Fab 硬要求 *"All text must be accurate"*）——
> **不要手写数字**：工具数从 `describe_toolset` 数，或从 `tests/check_tools.py` 的
> `EXPECTED_TOOLS` 数。
>
> ⚠ **2026-10-11 插件化之后的版本**：早先那份是给"独立 server 版"写的，结构已变
> （见第四节）。旧目录 `fab_upload/` 已删，材料只剩这一份。

---

## 一、`.uplugin` 字段逐个说明

| 字段 | 值 | 为什么 |
|---|---|---|
| `FileVersion` | `3` | 官方 VibeUE 用的也是 3 |
| `Version` | `1` | 插件清单格式版本，**不是产品版本** |
| `VersionName` | `1.0.0` | **产品版本**；打包时可用 `MakePlugin.ps1 -Version 1.0.1` 覆盖 |
| `EngineVersion` | `5.8.0` | ⚠ **必须精确到补丁号**（Fab 表单的硬字段）；文案里才写 `5.8+` |
| `FriendlyName` | `UEPLANE` | 商店里显示的名字 |
| `Category` | `Editor` | 编辑器插件 |
| `CanContainContent` | `true` | ⚠ **必须为 true**，否则 `Content/Python/` 不会被扫、`init_unreal.py` 不会执行（**实测过**） |
| `IsBetaVersion` / `IsExperimentalVersion` | `false` | ⚠ **别自己标实验** —— 那是你的产品，不是引擎的实验件 |
| `Modules` | `[]` | ⚠ **纯 Python 插件没有 C++ 模块**。已实测：`Modules: []` + `CanContainContent:true` 能让 UE 加载插件并执行 `init_unreal.py` |
| `Plugins[]` | **四个全列** | `PythonScriptPlugin`（Python 运行时）、`ToolsetRegistry`（工具集注册表）、`ModelContextProtocol`（MCP 端点）、**`EditorToolset`**（→ 见下面那条 ⚠）。⚠ 列上 = 让 UE 自动帮你启用 |
| `FabURL` | **先留空** | 上架后才拿得到 listing id，形如 `com.epicgames.launcher://ue/Fab/product/<uuid>` |
| `DocsURL` / `SupportURL` / `CreatedByURL` | 指向你的 GitHub | ⚠ Fab 会**点开验证**，别填不存在的地址 |

### ⚠⚠ `EditorToolset` 那一行是**干净工程实测**换来的（2026-10-11）

**症状**：在开发机上一切正常，**装进一个新建的 Blank 工程后 UEPLANE 全废** ——
`official_status` 报"toolset registry 里一个工具集都没列出来"。

**根因**（两个工程的日志一比就明白）：

| | 注册的 toolset | 挂载的插件 |
|---|---|---|
| 开发机那个老工程 | 40+ 个（`editor_toolset.toolsets.scene.SceneTools` 等全在） | …+ `AllToolsets` + **`EditorToolset`** |
| 新建的 Blank 工程 | **只有 2 个** | **缺这两个** |

UEPLANE 真正调用的那一整套（`SceneTools` / `ActorTools` / `PrimitiveTools` / `ObjectTools` /
`AssetTools` / `StaticMeshTools` / `MaterialTools` / `EditorAppToolset` / `ProgrammaticToolset`…）
**全部来自 `EditorToolset` 这一个插件**，而它：

- 在 `Engine/Plugins/Experimental/Toolsets/EditorToolset/`
- **`EnabledByDefault: false`** —— 所以新建工程里默认是关的
- ⚠ 老工程之所以有，是**当年手动开过**（或者被 `AllToolsets` 带进来的）

**修法**：在 UEPLANE 的 `.uplugin` 里**声明 `EditorToolset`**（`Enabled: true`）⇒ UE 会自动帮你启用它。

⚠ **只声明 `EditorToolset`，不声明 `AllToolsets`**：后者是聚合插件（把 20 多个都拉进来），
UEPLANE 用不到那么多 —— **依赖越少，买家越好装**。
⚠ **这条不实测就发现不了**：你自己的开发机上它一直是开着的。


### ⚠ 关于 `WhitelistPlatforms`（纯 Python 插件的一个真实差异）

VibeUE 把它写在 `Modules[0]` 里 —— 因为**它是模块级字段，给 C++ 模块限定编译平台**。
**本插件没有 Module**，所以：

- **不写**它就对了（没有要编译的东西，谈不上平台白名单）；
- **但 Fab 的商品表单里仍要填"支持平台"** ⇒ **只填 Win64**（只在 Windows 上验过，别替用户承诺 Linux/Mac）。

> ⚠ **仍未实测**：`Modules: []` 的插件在 Fab 提交表单里会不会被要求填平台字段、
> `WhitelistPlatforms` 在插件级是否被识别 —— 这两条**没有实测证据**，第一次提交时留意报错。

### 待补清单

- [x] **`Resources/Icon128.png`** —— 已放一张 **128×128 占位图**（深色底 + 蓝色十字路 + 四个体块）。
      ⚠ **上架前请换成正式设计** —— 目前这张是我用脚本画的几何图，不是设计稿。
- [x] **`README.md`**（面向买家：安装 + 用法）—— 已有，包内已含
- [x] **`Config/FilterPlugin.ini`** —— 已有
- [x] **`Content/Python/`**（入口 + toolset 包）—— 已有
- [ ] **版权头**：每个 `.py` 首行加 `# Copyright <你的公司/名字> <年> All Rights Reserved.`
      ⚠ **尚未加**（19 个 `.py`）。⚠ 注意 `ueplane/main.py` 等四份是**从产品拷来的**，
      版权头要加就**得加到产品仓库那一份**，否则下次同步被覆盖。
- [ ] **3-5 张截图 + 1-2 分钟演示视频**（Fab 表单的 Media 段）
- [ ] **Fab listing id** ⇒ 回填 `FabURL`

---

## 二、给 Fab 表单的申报文案（`FAB_Tech_Details.md` 草稿）

⚠ **四段式**（照 VibeUE 的结构，但**改了一处**：它没写引擎插件是 Experimental，**建议你写**）。

```markdown
Reference-image driven scene reproduction pipeline for Unreal Engine 5.8+. Adds a gated workflow
to the editor's native MCP tool surface (ToolsetRegistry): element list -> asset list ->
one-row-per-object placement table -> user-approved figures -> level build -> surfaces -> environment,
with a durable ledger and per-row incremental rebuilds.

Module — none. Pure Python toolset plugin (no C++ module, nothing to compile). Platforms: Win64.
Engine: UE 5.8.

Prerequisites — UEPLANE attaches to Unreal's native MCP stack; it declares and auto-enables
ModelContextProtocol, ToolsetRegistry, PythonScriptPlugin and EditorToolset. Those engine plugins are
marked Experimental by Epic and may change between engine versions. Enable the plugin, restart the
editor, and the MCP endpoint is served by the editor itself.

Dependencies — no third-party Python packages are bundled. The workflow imports engine modules only
(unreal, toolset_registry) and ships its own minimal local implementations for the few helpers it
needs, so nothing has to be installed alongside the plugin.

Setup — point your MCP-capable agent at the editor's MCP endpoint (default
http://127.0.0.1:8000/mcp). The agent discovers UEPLANE's 20 tools via list_toolsets, then calls
official_status first (link self-check) and get_plan (state overview).

Docs: <你的文档地址>
```

**每个字段的用意**：
- 第 1 段：**"pipeline"** 放在第一个词 —— 买家一眼知道**这是流程工具，不是编辑器遥控器**。
- 第 2 段：Fab 的三个硬字段（Module / 平台 / 引擎）一次给全；`Module — none` 是**如实**，
  也顺带说明"没有要编译的东西"。
- 第 3 段：**Prerequisites 单列**，把"依赖 Epic 自己那两个 Experimental 插件"摆在明面上、**不藏**。
- 第 4 段：**"no third-party Python packages"** 这条是**可核对的真话**（见自查清单），
  而且是对买家最实在的信息：**装上就能用，不用 pip 任何东西**。
- ⚠ 工具数写 **20** —— 这个数字**能从 `describe_toolset` 数出来**，改工具面时必须同步改这里。

### 商品描述（`FAB-DESCRIPTION.md` 骨架）

⚠ 只给骨架，**卖点你自己写**。三条原则：

1. **第一句说清"你解决什么"**，不要从技术名词开头；
2. **必须写"我怎么跟 Epic 官方工具集分工"**（Fab 上同类产品都在抢这个位置）；
3. ⚠ **每条数字都要能在代码里数出来**（VibeUE 就是死在这里：技能数三份文件写 34/36/88）。

```
🚀 UEPLANE — reference-image driven scene reproduction for Unreal Engine 5.8

Epic's 5.8 toolsets give an agent the ability to *drive* the editor. UEPLANE gives it a *process*
with people in the loop: reference images in, an inspectable and reversible scene out.

🎯 WHAT MAKES UEPLANE DIFFERENT:
• Every stage is gated — the element list, the asset list and the placement table are each confirmed
  by you; the plan is approved against rendered figures before anything is placed.
• One row per object — the placement table is an inspectable plan, not a one-shot script.
• Durable ledger + incremental — the plugin knows what it placed; a later edit touches only that row.
• Nothing is overwritten silently — where it finds a person has moved something, it refuses and asks.
• "Not checked" never looks like "fine" — a tool that could not read something says so.

🛠️ HOW TO USE:
1. Install the plugin and restart the editor (it auto-enables the MCP stack it needs).
2. Point your MCP agent at the editor's endpoint (default http://127.0.0.1:8000/mcp).
3. Call official_status, then get_plan, and follow the eight-stage overview.

Requirements: Win64 · Unreal Engine 5.8 · Unreal's native MCP stack (Experimental, by Epic).
```

---

## 三、上架自查清单

### 产品与结构
- [x] 插件目录只有一个 `UEPLANE/`，结构是 `UEPLANE.uplugin` + `Content/` + `Config/` + `Resources/`
- [x] **无未使用的文件/目录**（`tools/`（验证脚本）、`dist/`、`build_plugin.ps1`、`MakePlugin.ps1`、
      `移植实测记录.md` **都在排除清单里**）
- [x] `Config/FilterPlugin.ini` 只列"标准结构之外要发的"（目前只有 `/README.md`）
      —— ⚠ `Content/` 是 UAT **自动包含**的，业务代码在它下面，**不用列**
- [x] `MakePlugin.ps1` 跑过一遍，**包内无残留**（脚本第 ④ 步会警告；实测包内 17 个文件，无脚本/无缓存）

### 构建与依赖
- [ ] 在**干净的 UE 5.8 工程**里解压安装、启用、跑通一遍（**这一条不能省**）
      ⚠ **目前没做** —— 打出的 zip 只在开发机上以"目录部署"形态跑过，**没在干净工程里装过**
- [x] 随包 Python **只 import 引擎模块**（`unreal` / `toolset_registry`）—— **不带第三方包**
      ⚠ 这是**实测过的**：UE 自带 Python 3.11 的 `site-packages` 只有 `pip`/`setuptools`/`pkg_resources`，
      没有 `pydantic` / `mcp` / `anyio` —— 所以插件里放了 `ueplane/_shims.py`（**我们自己的**最小替身）
- [x] 无 C++ 第三方库（**根本没有 C++** ⇒ 这项 N/A）

### 合规
- [ ] 每个 `.py` 首行有版权头（`.py` 用 `#`，不是 `//`）—— **尚未加**
- [x] `Resources/Icon128.png` 是 **128×128**（⚠ 是占位图，见待补清单）
- [x] **`LICENSE` 不随包**（Fab 只认 Fab License）—— ⚠ 这个决定**同时**写在
      `MakePlugin.ps1` 与这份清单里（VibeUE 只写了脚本注释，两边对不上）
- [x] 未重新分发任何 Epic 示例内容 / Megascans

### 文案（这一节 VibeUE 栽过）
- [ ] **所有数字口径统一**，且**能从代码数出来**（工具数 = **20**，从 `describe_toolset` 数）
- [x] 技术信息里**写明"依赖官方 Experimental MCP 栈"**
- [ ] 中英文都有（Fab 要求 "All text fields must contain an English version"）
- [ ] `FabURL` / `DocsURL` / `SupportURL` 都能点开

### 媒体
- [ ] 3-5 张截图（**能看出功能**，不是纯 UI 截图）
- [ ] 1-2 分钟演示视频

---

## 四、构建链条怎么用（**插件化之后的新结构**）

```
ue_plugin/
  UEPLANE/                 ← 要发出去的那棵树（= 解压后放进 Plugins\ 的东西）
  build_plugin.ps1         ← **防分叉的唯一入口**
  MakePlugin.ps1           ← 打包 zip
  tools/mcp_probe.ps1      ← 验证用（不进包）
  移植实测记录.md            ← 实测记录（不进包）
  dist/UEPLANE-1.0.0.zip   ← 产物
```

### ⚠ 为什么必须有 `build_plugin.ps1`

插件要自包含（能上传 Fab），所以它里面**必然有一份**产品业务代码的拷贝：

```
UEPLANE/Content/Python/ueplane/{main,schemas,surfaces}.py + planning/
```

但那四份**不是插件的资产** —— 它们是产品仓库 `src/mcp_server/` 里的同一份东西（一字节没改）。
**一旦有人只改一边，就成了两套代码**。所以：

| 命令 | 作用 |
|---|---|
| `.\build_plugin.ps1` | 产品 → 插件树同步（8 个文件，逐个打哈希） |
| `.\build_plugin.ps1 -Check` | **只比对不写**；有差异退出码 1（CI / 上架前用） |
| `.\build_plugin.ps1 -Deploy "<工程>\Plugins"` | 同步 + robocopy 部署 + 清 `__pycache__` |

### ⚠ `MakePlugin.ps1` 的第一步是硬闸

它先跑 `build_plugin.ps1 -Check`：**产品与插件的拷贝不一致就拒绝打包**。
——免得发出去一份"看着像、其实不是那一版"的东西。

### 完整上架流程

1. 改产品代码 → `.\build_plugin.ps1`（同步）→ 在 UE 里验一遍
2. `.\MakePlugin.ps1 -Version 1.0.0` → 得到 `dist/UEPLANE-1.0.0.zip`
3. **在干净工程里解压验证**（第三节那条不能省）
4. 上传 Fab → 拿到 listing id → 回填 `.uplugin` 的 `FabURL` → 重新打包

> ⚠ **`.ps1` 必须存成 UTF-8 with BOM**：Windows PowerShell 5.1 读无 BOM 的 `.ps1` 会按
> ANSI(GBK) 解，中文注释会把语法读崩（2026-10-11 实测：报一堆 `Unexpected token`）。
> 仓库里能跑的 `setup.ps1` / `sync-to-D.ps1` 都是 UTF8-BOM —— 照它们。
>
> ⚠ 这套链条**只在开发机上跑通过**（2026-10-11：包内 17 个文件 / 1.443 MB / 退出码 0）。
> **没在干净工程里装过**；第一次提交时**把报错原文留档**，下一次就能避开。
