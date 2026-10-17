# 交接单 · 2026-10-12（本会话做完的 + 现在卡在哪）

> ⚠ **这份是进度与交接，不是规则**。规则在 `AGENTS.md`（那份只放规则，末尾有**「文档地图」**：
> 一个事实只写一处 —— 写文档前先看那张表）。
> **接手第一件事**：按「开场先查状态」调 `official_status()` + `get_plan()` + `get_asset_list()`
> —— **别拿这份当状态源**，状态会腐烂，工具的返回值不会。
> 更早的交接（阶段二改 three.js 的来龙去脉）在 `exp\preview3d\HANDOVER.md`（§1~§15b）——
> ⚠ 那份**已标「任务已完工」**，里面"插件 0 改动 / 还是 SVG"之类的话**都是过期口径**，别照它办。

---

## 0 · 一句话现状

**代码三处（仓库源 / 插件树 / 演示工程）已逐字节一致**（`main.py` = `32F944B3C1E9`、
`planning/plan.py` = `1BE8525C7A7C`，2026-10-12 本轮又核过一遍，见 §2 与 §9）。
本会话四件整改 + **文档大统一**都做完了（§7）；本轮又**把部署侧两轮修复搬回仓库**并同步回三处（§9）。

**预检**：2026-10-12 用户跑过并转述「**全绿**」；⚠ **原始输出我这里仍然没有**（AGENTS 的口径是
"只认原始输出"，所以我只记"用户转述全绿"这一句，不写成实测证据）。要补就再跑一次、把输出贴进 §4 第 1 条。

**演示工程现在是纪元 2**（`views/plan_v2.json` 在 ⇒ `active_epoch() = 2`）——**阶段三落地/换纪元早已完成**
（v1 那套 02:14 冻结留档，v2 接管）。⇒ **§3 那条"卡在微调版挡住落地"的岔路已经作废**（人已经过站了）。

**当前进度（只读 E: 的 JSON 文件得出，不经 UE）**：`plan_v2.json` 79 行（44 资产 + 35 白膜）·
`build_state_v2.json` 79 行全落成 · 最后一次**微调是「路灯太矮了」**（8 盏 scale 1.0 → 1.55）·
`views/evaluate_v1.json`（13:02）报 **`ok 71` / `plan_changed 8`** —— 差的很可能就是那 8 盏灯还没重摆，
**待用 `evaluate_layout()` 复核一次**（那要 UE 在线，本轮没跑）。

---

## 1 · 本会话干了什么

| # | 整改 | 出处（用户原话） |
|---|---|---|
| ① | **阶段二不再出 SVG，改出一张 three.js 页** | 「整改2阶段，不出图，出three.js」 |
| ② | **删掉「演练」（`dry_run`）** | 「涉及演练的都删了，太浪费时间了」 |
| ③ | **阶段三落地 = 换纪元（一次调用、不可逆）** | 「三阶段只触发一次…只进行一次批量操作然后直接冻结纪元 1 开启纪元 2」 |
| ④ | **资产表与位置表同待遇进纪元 2** | 「把资产表和位置表一样的方式进纪元 2」 |
| ⑤ | **文档大统一**（本轮） | 「话说很多文档都自相矛盾了，你进行一次大统一」 |

外加两件**用户点名**的：
- **左右镜像修复**（UE 左手系 ↔ three 右手系）+ 把 `handedness_ok` 断言并进页面 `#selftest`；
- **插件里不许再出现「SVG」三个字母**（已清 17 处，三棵树残留 0）。

### 具体落点（代码 · ①~④）

| 文件 | 改了什么 |
|---|---|
| `planning/figure3d.py` | **新增**：出图数据层（判据复用 `planning`，不另写）。⚠ 模板路径**必须是函数**（`web_dir()`/`default_template()`）—— 写成模块常量会被 `init_unreal` 的 `_relocate()` 搬飞（实测） |
| `planning/web/{preview3d.html, three.min.js}` | **新增**：模板 + three r155 UMD。模板含 `toThree()`（唯一的坐标系交接规则）、`handedness` 回归断言 |
| `planning/plan.py` | `FIGURE_GLOBS`→HTML、`REQUIRED_VIEWS=("page",)`；`clear_stale_figures()`/`ELEVATION_GLOBS`/`VIEW_FOR_DIM` **整段删**；**不可回退闸**（`plan_v2` 在 ⇒ 写 `plan_v1` 拒收）；`promote_to_epoch2()` 改成**二元组返回**（含资产表搬家结果）+ **照抄资产表**；资产表**咽喉** `active_asset_list_path()` |
| `main.py` | 删 `dry_run`/`accept_user_edits` 形参；`drawing` 段与 `_drawing_geometry()` 整段删；**阶段三落地=换纪元**（落位前切换、不再单独一次调用）；`batch` 默认值 `true→false`（实测编辑器事件循环重入）；资产表写入走活动那份；阶段一加「地基漏记」确认报红 |
| `surfaces.py` | 资产表读取走它自己的 `active_asset_list_path()`（它不能 import planning，被迫两处同形） |
| `tests/preflight.py` | ⑥ 的 `batch` 默认值断言改成 `False`；⑫a 的测法**连 v2 一起指走** |
| `ue_plugin/build_plugin.ps1` | 同步清单加 `planning\web\`；`$required` 加模板与库 |

### 三个「别再踩」的实测教训（都写在代码注释里了）

1. **路径写成模块常量会被 `_relocate()` 搬飞** —— 模板/资产表那种要在插件里用的路径，**用函数现算**；常量只用于 `init_unreal` 要搬的（`REPO_ROOT` 底下那些）。
2. **拖动方向的判据 = 用户的手感**，脚本只能"防改回去"、**不能选号**（我拿"某个点的屏幕位移"选号，错了两次）。
3. **`batch=true` 走官方 `execute_tool_script` 从编辑器事件循环里发起会整体失败**（`Cannot run the event loop while another loop is running`）；`batch=false` 逐行实测可用（78/78）。

---

## 2 · 现场状态（2026-10-12 实查）

### 演示工程 `E:\UE TyroProject\demo 5.8 - C++\Saved\UEPLANE\`

| 文件 | 状态 |
|---|---|
| `views/plan_v1.json` | **在**（44 资产 + 35 白膜 = **79 行**）· `confirmed=true` · ⚠ **`mode="tuned"`（微调版）** · `confirmed_by="用户（微调）"` |
| `views/plan_v2.json` | **不在** ⇒ **还是纪元 1** |
| `views/build_state_v1.json` | **在**（79 行、每行都有 actor 引用、`ok`/`verified`/`mismatch` 无问题行）⇒ **阶段三其实已经落完了** |
| `views/build_state_v2.json` | **不在** |
| `catalog/asset_list.json` | 在（18 行） |
| `catalog/asset_list_v2.json` | **不在**（换纪元时才会照抄出来） |
| `catalog/elements.json` | 在（19 行） |

> ⚠ 上表是**文档口径的旧快照**（本轮只做文档统一，**没有再连 UE 复核**）——
> 要以 `get_plan()` 的返回为准。

### 三处代码一致性（逐文件 sha256 前 12 位 · 2026-10-12 本轮复核）

```
main.py                     = C328C81C9CD7   （仓库 = 插件树 = 部署侧）
schemas.py                  = 5EC5092B4D09
surfaces.py                 = C0DAEF4080D5
planning/plan.py            = 423DDCC42323
planning/figure3d.py        = 18F3B23A2B25
planning/__init__.py        = BCBE42E5B414
planning/web/preview3d.html = E6B8F486DD0A
planning/web/three.min.js   = EC0A84377F1D
tools.py                    = AFA305FB98B6   （仓库里没有这份 —— 插件自己的工具面）
```

⚠ **本轮没有跑 `build_plugin.ps1 -Check`**（跑它 = 跑 `py_compile`，属"不许自己跑东西"那一条）——
上面这份是**逐文件 `Get-FileHash` 比出来的**，结论一样：**三棵树全绿**。

---

## 3 · ⚠ 现在卡在哪（接手第一件要商量的事）

**你要做"阶段三落地 = 换纪元"，但这份 plan 是微调版，会被两道闸挡住：**

| 闸 | 说什么 |
|---|---|
| ① 微调版闸（代码里） | `_tuned_plan and mode == "full"` ⇒ **拒收**（"微调只动那几行，不许全量重摆"） |
| ② 凭据错配（语义） | 这次"落地"的凭据应该是**阶段二看图确认**；而现有一句是**微调原话**（`mode=tuned`，当时明确"没有图"） |

**三条出路（等用户拍板）**：

1. **（推荐）** 把 plan 恢复成"正常确认"：重落一次盘（three.js 页自动重生成）→ **用户看一眼** → `confirm_plan(confirmed_by, user_quote)` 把 `mode` 从 `tuned` 摘掉 ⇒ 再走「阶段三落地 = 换纪元」；
2. 改代码允许微调版走阶段三落地（判据要改，且凭据语义仍错配 —— 不建议）；
3. 先不换纪元，继续在纪元 1 里微调。

---

## 4 · 还没做的（清单）

| # | 事项 | 备注 |
|---|---|---|
| 1 | **用户跑预检** —— ✅ **2026-10-12 已跑，全绿** | ⚠ 但我**没有原始输出**；要留证据就再跑一次贴进 §1。命令：`& "F:\MCP_Server\.venv\Scripts\python.exe" "F:\MCP_Server\tests\preflight.py"` |
| 2 | 重启 UE 后让 agent 调一次 `get_plan()`，把报文发回来核 | 确认 `stages` 里阶段三那条口径、以及纪元仍 1 |
| 3 | 上面 §3 那条岔路 | 等用户 |
| 4 | ~~`_promote_epoch2()` 里三条判据的**报错原文**仍是旧口径~~ | ✅ **2026-10-12 本轮读码复核：已改好**（现在写的是「此后改行走：`request_plan_change` → `generate_plan(patch=…)` → `execute_build(only_labels=[…])`（**不画图、不重确认**）」）—— 本条关闭 |
| 5 | 演示工程 `views\` 里那四份杂物（`_preview3d_template.html` / `_mkpreview3d.ps1` / `preview3d.html` / `_mkfigs.ps1`） | `_mkfigs.ps1` 是**用户自己的**脚本，要清得他点头 |
| 6 | **文档漂移**（部分已解，剩下的要用户拍板） | ✅ 已解：本机 `docs/` **八份齐全**（`阶段一~阶段八` 都在，含 `docs/阶段四-微调.md`）；插件侧三份文档已改成**自足**（不再依赖读不到的 `docs/`）。⚠ **没解**：`main.py` 里 **27 行**写「见 `docs/阶段X-….md`」，而 **`docs/` 不在 `build_plugin.ps1` 同步清单里** ⇒ 插件里全是死链；其中 `docs/工具面审计.md`（`main.py:3060`）**在仓库里根本不存在**。要不要动（把 `docs/` 纳入同步清单 / 改那 27 行注释）= **等用户** |
| 7 | 资产表 vs 位置表的引用过期：`plan_v2.json` 的 `assets[].asset_path` 照抄的是阶段二那一刻；阶段一改过资产表的话**没有闸**查 | 用户要不要加"提醒哪几行可能过期"没说 |
| 8 | `dist/UEPLANE-v1.0.1/**`（含一份旧 `AGENTS.md`） | **本轮刻意不改**：它是 vX.Y.Z 的**发布快照**，改了就与那个版本的代码对不上 —— 下次打包时重新生成 |

---

## 5 · 怎么算做完 / 怎么跑

- **预检**（用户跑，脚本方式 —— 别只用 `import mcp_server.main`，那会给假绿）：
  ```powershell
  & "F:\MCP_Server\.venv\Scripts\python.exe" "F:\MCP_Server\tests\preflight.py"
  ```
- **改完产品代码之后**：`cd F:\MCP_Server\ue_plugin; .\build_plugin.ps1`（同步到插件树，带 `py_compile` 语法闸）
  → 再把 `ue_plugin\UEPLANE` 镜像到演示工程 `Plugins\UEPLANE`（`.\build_plugin.ps1 -Deploy "E:\UE TyroProject\demo 5.8 - C++\Plugins"`）
  → **清部署侧 `__pycache__`**（脚本已代劳；UE 会拿旧 `.pyc` 顶掉新源码）→ **重启 UE**（插件没有热重载）。
- ⚠ **`build_plugin.ps1` 只同步 Python 与 config，不同步任何 `.md`** ——
  所以 `ue_plugin\UEPLANE\README.md` / `PROMPT.md` 这类**插件自己的文档**，改了要靠 `-Deploy` 的 robocopy 镜像过去
  （它们**不是**"产品是源、插件是拷贝"那一类）。
- ⚠ **`E:\` 在工作区之外**：本会话里写它每次都撞沙箱、要提权；`robocopy` 报 `Copied 0` + `exit=0/1` = 已经一致。
- ⚠ **改完 `main.py` / 规划层 → 必须提醒用户跑预检**（AGENTS 纪律）。

---

## 6 · 本会话新增的工作区文件（可留可删）

| 路径 | 是什么 |
|---|---|
| `exp\preview3d\` | 阶段二改 three.js 的**工作区原型 + 实录**（`HANDOVER.md` §1~§15b、探针脚本 `_dragdir.js` 等 —— ⚠ 那几个探针**已标"作废"**：判据错了，留档当反例；目录顶部已加"原型已转正"横幅） |
| `.edgetmp\` | 本会话的**核对沙盒**：出图验证、纪元切换模拟、各处 diff 脚本、页面截图、`DOC-UNIFY-BRIEF.md`（文档统一的作业说明）；还有若干 `*.bak` 备份（每次批量改写前留的） |

两者都在 `.gitignore` 之外（`?? .edgetmp/`、`?? exp/`）—— **要不要入库由用户定**。

---

## 7 · 文档大统一（第五件 · 本轮在做）

**做法**：**唯一真值 = 运行时代码**（`get_plan().stages` / `main.py` 工具签名 / `planning` 常量与闸门）。
先把**规则**在 `AGENTS.md` 里一次成文，再让每一份文档**只讲它自己那一层**。

### 7.1 `AGENTS.md`（规则源 · 已完工）

- **一次重写**（56.7 KB，在 65536 指令预算内，余量 8.7 KB）。删掉的**自相矛盾**：

| 过期说法（原来在文件里） | 改成 |
|---|---|
| 「阶段二不再由代码出图，**图由 AI 拿 plan 自己画**」（两处：阶段一二口径、规划图闸门） | **一页 three.js，由 `write_plan()` 落盘时自动生成**，Agent 不许手画 |
| 「`awaiting_user` = 该出的**每一路图**都认账；只画一张会退回 `awaiting_figure`」（两种图时代） | 一页口径：**页出来了、且页正文认这一版数据**才算 `awaiting_user` |
| 「数据与图都要」里的 `plan_v1_overview*.svg` / 立面图 | 「数据与**页**都要」，页 = `views/<plan 名>_view.html` |
| 「`execute_build(batch=…)` 默认 **`true`**；真跑尚未实测」 | 默认 **`false`**（编辑器事件循环重入实测翻车；逐行 78/78 可用） |
| 「`confirm_stage3` **只做切换、一个 Actor 都不动**；`dry_run=true` + 它 = 只预告」 | **切换并进落地那一次调用**（先切后落）、**不可逆**；演练已删 |
| 「它与 … / `accept_user_edits` / `user_quote` 互斥」 | 互斥名单去掉已删的两个形参 |
| 「**资产表、位置表留着**（资产表不进纪元）」（纪元一节） | **资产表同待遇进纪元 2**（`asset_list_v2.json` + 咽喉） |
| 人工痕迹两步闸「先 `dry_run=true` 列清单留痕」 | **默认参数调一次 = 提问**（留痕 + 拒收 + 列清单），凭据只有 `user_quote` |
| 「点名重摆」**整段出现了两次**（第二段还写着 `accept_user_edits=true` + `dry_run=true`） | **删掉重复段**，只留同一份正确口径 |
| 阶段四「要让验收回到有图那种：**重画两张图** → `confirm_plan()`」 | **重落盘（页由代码自动重生成）→ `confirm_plan()`** |
| 阶段七闭环链「… → **重画图** → `confirm_plan` → …」 | 「… → 重落盘（页自动重生成）→ `confirm_plan` → …」 |
| 架构原则「带闸门的通道（**dry_run** + 先读现值 + …）」 | 去掉 `dry_run`（换成"整批校验"） |
| `origin_view_spec()`、`ELEVATION_GLOBS` 这类**已删的机制** | 一律删掉；出图相关只留现行三样（figure3d / 模板 / 一句重出命令） |
| ~~`--figure-checklist`~~ | ⚠ **2026-10-16 更正：它没被删** —— 代码判据 `src/mcp_server/planning/plan.py:2719 figure_checklist_cli()` + `:2841` 判 `--figure-checklist`，`main.py:12332/12916` 也在引用它。原文把它列进"已删机制"是**记错**，现按"仍在用"处理 |

- **新增的两节**：
  - **「文档地图」**（一个事实只写一处）：规则→`AGENTS.md`；状态/编号→**代码**；阶段细则→`docs/阶段N`；
    面向用户→`README.md` / `使用说明.md`；插件侧→`ue_plugin/**`（⚠ `docs/` 不在同步清单里，插件文档必须自足）；
    运行数据→`views/README.md` / `catalog/README.md`；进度→`HANDOVER.md`（**唯一允许腐烂的地方**）；发布快照→`dist/`。
  - 「**改完口径要顺手核这三处**」：`AGENTS.md` ↔ `README.md` 八阶段一览 ↔ `使用说明.md`/`使用版-AGENTS.md` 的操作步骤。
- 另有若干**当时只在 docs 里、规则文件里缺**的红线被补进来：阶段一「世界地基漏记会报红」、
  阶段三「搭哪张图」闸、阶段六 `ENV_FACTOR_ORDER` 七个因素、阶段七「`CaptureViewport` 画不出中文」。

### 7.2 其余文档（分工 · 见 §7.3 的验收）

| 组 | 文件 |
|---|---|
| A | `docs/阶段二-平面放置规划.md` · `docs/阶段三-资产布局生成.md` · `docs/阶段四-微调.md` |
| B | `docs/阶段一/五/六/七/八-*.md`（五份；阶段五那份 94.9 KB，最大） |
| C | `README.md` · `使用说明.md` · `使用版-AGENTS.md` · `SETUP.md` · `views/README.md` · `catalog/README.md` |
| D | `ue_plugin/UEPLANE/README.md` · `ue_plugin/UEPLANE/PROMPT.md` · `ue_plugin/PROMPT.zh-CN.md`（+ `ue_plugin/移植实测记录.md` 只加顶部历史声明） |

统一口径（四件整改各一条）：
**出图 = 一页 three.js**（`.svg` / `plan_wire` / `drawing` / 两张图 / 手画 —— 全部清掉，插件里更是不许出现 `SVG` 字样）；
**没有演练档**（`dry_run` / `accept_user_edits` 一切说法改成"默认参数那次 = 提问 + `user_quote`"）；
**换纪元并入阶段三落地**（先切后落、不可逆、资产表同待遇）；
**出图归阶段七**（凡写"出图归阶段八"的都要改）。

### 7.3 验收方法（**已按词复扫，结果见下**）

按**词**复扫全部文档，而不是靠印象（扫描 22 份 markdown，**排除** `.venv` / `dist` / `.edgetmp` / `exp`）：

```
dry_run · accept_user_edits · \.svg · 两张图 · 两张手绘 · 手画 · drawing ·
plan_wire · overview_r · elevation · 出图归阶段八 · batch 默认 true / batch=true ·
防伪 · 应答码
```

**验收标准**：剩下的每一处命中都必须是在讲「**它已删除 / 它是历史 / 不许再这么做**」，不能是在讲现行做法。

**结果：✅ 通过。** 逐处看过，剩下的命中分三类，没有一处是现行口径：

| 类别 | 例子 |
|---|---|
| **否定句**（说"已删/已作废/不再是"） | `docs/阶段二` L12「`drawing` 段**已删除**」、L40「`.svg` 这一类产物名**已作废**」；`docs/阶段四` L21「⚠ **不再是"重画两张图"**」、L34「`accept_user_edits` … **已删除**」；`README.md` L115「⚠ `accept_user_edits` 开关 2026-10-12 已删」 |
| **带日期的历史档**（明确标了"当时 / 历史"） | `docs/阶段三` L38 `> **历史（当时还有演练档，2026-10-04 之前…）**`、L48 `> **历史（当时默认还是 `true`…）**`；`docs/阶段六` 那批 `planned N / applied 0` 报文（段首已标历史）；`ue_plugin/移植实测记录.md`（顶部已加历史声明） |
| **只在讲"已撤"的制度** | `应答码` 三处都写成「2026-10-04 **撤掉了**当时的应答码…**那个洞重新敞着**」；`防伪` 两处都写成「**不承担防伪**」 |

⚠ **唯一一处"数字式过期"已收口**：`docs/阶段七` 的 `ok 66` 从两处减到一处（另一处改成指针）。

⚠ **`dist/UEPLANE-v1.0.1/**` 不在扫描范围**（发布快照，刻意不改，见 §4 第 8 条）。

---

## 8 · 大统一连带修到的**非 markdown 文件** + 待授权的代码侧残留

### 8.1 已改（**纯文案，一行逻辑都没动**）

| 文件 | 改了什么 | 影响面 |
|---|---|---|
| `ue_plugin/UEPLANE/Content/Python/ueplane/tools.py` | 4 处 **docstring**：`generate_plan`「本工具不出图（图由你拿数据自己画）」→「落盘时自动生成一页 three.js，你不用画」；`execute_build` 的 `confirm_stage3`「只做切换、一个 Actor 都不动」→「先切纪元、再当场落位（不可逆）」；`adopt_user_edits`「重画两张图」→「重出一页 three.js」；类 docstring 的阶段串补上「⑦ 评估与闭环迭代 · ⑧ 导出/交换」 | ⚠ 见下 8.2 —— 这些是**死文本**，改它们**不影响运行时** |
| `ue_plugin/UEPLANE/UEPLANE.uplugin` | `Description`：「user-approved **figures**」→「a three.js page the user inspects and approves」，并把阶段链补全（含微调 / 评估 / 导出） | ★ Fab 上架读这段；改了要**重新打插件包**才对上 |
| `config/environments.json`（repo + 插件树 + 部署侧，三份 hash 一致） | `_note` 里「先用 `dry_run` 验过」标成**历史**，并加一句「**演练档已于 2026-10-12 全工程整段删除** ⇒ 现在一调就是真做」 | 配置是**调用时读**的（不用重启） |
| `config/surface_materials.json`（同上三份） | 补一句「**演练档已于 2026-10-12 全工程删**；阶段三那道人工痕迹闸的第 1 步现在是**默认参数那次 `execute_build()`**（留痕 + 拒收 + 列清单），第 2 步才带 `user_quote`」 | 同上 |

⚠ **JSON 里的自由文本不许出现 ASCII 双引号**（会把 JSON 弄坏）——本轮我自己踩过一次，已改成 `「」`。
⚠ 我**没有**自己跑验证（按规矩不跑任何东西）⇒ **请跑预检时确认这两个 config 读得动**。

### 8.2 ⚠ 一个值得记下的机制（本轮实测）

`tools.py` 里那些手写的 docstring **在注册时会被 `_doc()` 用 `main.py` 里同名函数的 docstring 覆盖掉**
（`tools.py:111-135`，注释里记着"三层实测、两轮失败换来的"）。
⇒ 它们实际是**兜底文本**：只有 `M.<工具名>` 取不到 docstring 时才会露出；日志里 `_DOCED[0] / _DOCED[1]` 就是"装上了几个 / 共几个"。
⇒ **推论**：
- 我在 8.1 改的那 4 处**对 agent 看到的描述没有任何影响**（agent 看到的一直是 `main.py` 的那份，那份是对的）；
- 但它们会**误导读代码的人**（"只做切换、一个 Actor 都不动"正好与现行口径相反）——所以照改；
- ⚠ 反过来：**真正对外的是 `main.py` 的 docstring** ⇒ 8.3 那几条才是"agent 会读到"的过期文案。

### 8.3 代码侧残留（2026-10-12 **已按用户授权处理完**）

> 用户原话：「**2.过期的就删加修改**」⇒ 下表逐条已办。**它同时解决了另一件事**：同步过来的那份
> 部署侧 `main.py` 与本仓库的差异（见 §8.5）。**用户已跑预检：全绿（2026-10-12）**——
> ⚠ 但那次是全绿是在**本节的代码改动之前**，改完要**再跑一次**。

**已办**：
1. `main.py:8286`（**agent 会读到**的 docstring）「每一版都出两张图」→「每一版**出一页 three.js**（三种视角是那一页上的按钮）」。
2. `main.py:12256`（架构原则那条通道口径）去掉 `dry_run` → 「整批校验 + 先读现值 + 整份写 + 读回核对 + 台账」。
3. `main.py:3060` 引用的 `docs/工具面审计.md` **确实不存在** → 改写成「当时的判断；**那份文档已不存在，别再去找**」
   （**没有**补造一个文件来凑）。
4. `main.py` **14 处** `dry_run=False` / `dry_run=not confirm` 形参**全部删掉**（调用点噪音）；
   ⚠ **`schemas.py` 里那 6 个 `dry_run` 字段留着** —— 它自己写着「保留字段只为不改报文形状」，且预检可能在读它。
5. `schemas.py`：`BuildReport` docstring 去掉「/ 演练」；状态枚举描述里那个**不存在的** `dry` 档删掉；
   两处「重画图」→「重落盘（那一页由代码自动重生成）」。
6. `planning/plan.py` **12 处**「两张图 / 重画图 / 判不出维度 → 两张图都要」→ 一页口径
   （含**用户会看到的越界文本** `plan.py:524`，以及 `ACCEPTANCE_STATES["changes_requested"]` 那句）。
7. `main.py:11188` 拒收原文把 `catalog/asset_list.json` **写死**了 → 改成
   ``f"…活动那份 = \`{active_asset_list_path().name}\`"``（判据本来就走的咽喉）⇒ 纪元 2 里那句提示
   不再指向一个**不存在的文件**。
8. `ue_plugin/tools/mcp_probe.ps1:8` 默认 `-Toolset`：`ueplane.toolsets.UeplaneTools` → **`ueplane.tools.UeplaneTools`**
   （**没有** `ueplane/toolsets.py`，类就在 `ueplane/tools.py`）。
9. `.gitignore`：补 `views/*_view.html` + `views/*_view.htm`（确认页是**生成物**，每落一版一份 HTML、正文嵌整份几何 JSON）；
   并把第 40-43 行那句自相矛盾的注释改掉（原文写「`plan_v1.json` / `plan_wire_v1.png`…**要入库**」——
   `plan_wire_v1.png` 早删了，plan JSON 现在按「2026-10-04 本机运行状态」那条**一律不入库**）。

**仍留在 `src/` 里的 `dry_run` 字样**：全是 `[完工-NN]` 区块与 2026-09-26 / 10-04 / 10-08 的**带日期实测记录**
（讲的是"当时怎么跑的"）—— **按本项目口径保留**；`重画图` / `dry 演练` 已 **0 命中**。

**仍未动（等你拍板 —— 改的是包装策略 / 报文形状，不是过期文案）**：
- `main.py` **27 行** + `surfaces.py` 4 处 + `schemas.py` 1 处 + `config/*.json` 2 处写着「见 `docs/阶段X-….md`」，
  而 `docs/` **不在 `build_plugin.ps1` 同步清单里** ⇒ **插件里读不到**。三选一：**把 `docs/` 纳入同步清单**
  （我倾向这个：一处改、那 27 行一个字都不用动，代价是插件包多约 250 KB 文档）/ 把 27 行内联成一句结论 /
  接受"插件里读不到"。
- 那 6 个 `dry_run` 字段（见第 4 条）。

**原始清单（逐条已办，留档作证据）：**

| # | 位置 | 现状 | 建议 |
|---|---|---|---|
| 1 | `main.py:8286`（**`confirm_elements` 的 docstring ⇒ agent 会读到**） | 「正好与阶段二「**每一版都出两张图**」**对偶**」 | 改成「每一版**出一页 three.js**」 |
| 2 | `main.py:12229`（注释） | 「（**dry_run** + 先读现值 + 读回核对 + 台账）」 | 去掉 `dry_run`，换「整批校验」 |
| 3 | `main.py:3060`（注释） | 引用 `docs/工具面审计.md` | ⚠ **该文件在仓库里根本不存在**（dangling）——要么补文件、要么改写成"当时的判断" |
| 4 | `planning/plan.py:112-114 / 1079 / 1092`；`schemas.py` 两处 | 「**两张图**」/「**重画图**」 | 按"一页 three.js"改；这几处是**模块 docstring 与注释**，不对外 |
| 5 | `main.py` **27 行** + `surfaces.py` 4 处 + `schemas.py` 1 处 + `config/*.json` 2 处 | 「见 `docs/阶段X-….md`」，而 **`docs/` 不在 `build_plugin.ps1` 的同步清单里** | 三选一：① 把 `docs/` 纳入同步清单；② 把那 27 行改写成**内联一句结论**；③ 接受"插件里读不到"并在文案里写明。**我倾向 ②**（插件包不必背上 200 KB 文档），但这是**代码改动、要你授权** |
| 6 | `schemas.py` 14 个 `dry_run` 字段 + 1 处状态枚举描述仍列着 `dry` | 字段**恒 false**（描述已写「演练已删」），枚举描述与别处"没有 `dry` 那一档"**打架** | 字段可以留（改报文形状的风险）；**枚举描述那一处建议去掉 `dry`** |
| 7 | `ue_plugin/tools/mcp_probe.ps1:8` | 默认 `-Toolset "ueplane.toolsets.UeplaneTools"` | ⚠ **没有 `ueplane/toolsets.py`**（类在 `ueplane/tools.py`）⇒ 疑似过期。同目录 `verify_install.ps1` 与两份 PROMPT 用的都是 `ueplane.tools.UeplaneTools`。**我没跑脚本，按代码判断**；那行开头自称「Temp probe」，也可能是废件 |
| 8 | `main.py` 里 `confirm_stage3` 的 **docstring** | 「`mode` 只许 `auto` 或 `full`（阶段三那一次通常就是全量落地，配 `force_full=true`）」 | ⚠ **与代码直接相反**：`if mode != "auto": _conflicts.append(...)`（`main.py:4378`）⇒ **只有 `auto` 能带**，`full` / `force_full` 都当场拒收。⚠ **`AGENTS.md` 原来也抄了这句错的，本轮已按代码改掉** —— 代码和形参说明两处待改 |
| 9 | `main.py:4379` 的互斥文案 | 「（换纪元**不落位**、不看落位模式）」 | 换纪元现在**就是落位**（先切后落）⇒ 括号里那半句过期 |
| 10 | `schemas.py:803-805` `PlanStatus.image_path` 的描述 | 「（恒空 —— **图由 AI 拿 data_path 自己画，代码不出图**）」 | 值确实**恒空**，但**理由写反**：页由代码在落盘时自动生成。同一文件 `:820-823` 的注释正是反着写的（自相矛盾） |
| 11 | `main.py:11188-11192` 的拒收原文 | 判据走 `active_asset_list_path()`（**对的**），但文案**写死**「还没有阶段一签过字的资产清单（`catalog/asset_list.json`）」 | 纪元 2 且 v1 已删时，这句话指向**一个不存在的文件** ⇒ 文案要改成咽喉名 |
| 12 | `schemas.py:168` `BuildReport` 类 docstring | 「落完（或拒收 / **演练**）之后的如实交代」 | 演练已删 |
| 13 | `schemas.py:1302` | 状态枚举描述里仍列着 `dry`（「演练」档） | 与同文件 `:853` 等处的「2026-10-04 起**没有** `dry` 那一档」**打架** ⇒ 建议去掉那个值 |

### 8.4 本轮已同步到部署侧（`E:\UE TyroProject\demo 5.8 - C++\Plugins\UEPLANE`）

逐个 `Copy-Item` + 回读 hash，**仓库 = 插件树 = 部署侧**（**没有**跑 `build_plugin.ps1`，所以没触发 `py_compile`）：

```
Content\Python\ueplane\main.py            59022FA6488B
Content\Python\ueplane\schemas.py         4EF6158FC16A
Content\Python\ueplane\surfaces.py        C0DAEF4080D5
Content\Python\ueplane\planning\plan.py   65821186D83E
Content\Python\ueplane\planning\figure3d.py          18F3B23A2B25
Content\Python\ueplane\planning\web\preview3d.html   E6B8F486DD0A
Content\Python\ueplane\planning\web\three.min.js     EC0A84377F1D
Content\Python\ueplane\tools.py           C8163E1A80CB   （插件独有，仓库里没有这份）
Content\config\environments.json          938ABDAA102C
Content\config\surface_materials.json     A39EECB2EB42
UEPLANE.uplugin                           008B5CDD8C57
README.md                                 A0429DE5B010
PROMPT.md                                 F98FC1D03F75
```

⚠ **改的是插件里的文件 ⇒ 要重启 UE 才生效**（插件没有热重载）；部署侧 `__pycache__` 已清。
⚠ ⚠ **`main.py` / `schemas.py` / `planning/plan.py` 是在用户那次"预检全绿"之后才改的** ⇒
**要再跑一次预检**（命令见 §5）。

### 8.5 ⚠ 部署侧的 `main.py` 曾经比仓库新 —— 已按用户指令同步回源（2026-10-12）

**发现**：本轮同步到部署侧之后复核 hash，发现 `E:\…\Plugins\UEPLANE\…\main.py` 与仓库**不一致**
（部署侧 02:17 被改过，比仓库 **+2722 字节 / +17 行**）——**不是本轮改的**（本轮只拷了 tools.py / 两个
config / uplugin / README / PROMPT）。我把差异抓出来看清楚了（`Compare-Object` + 去重行集比对）：

- 那是一次**真 BUG 修复**（`_stage_overview()`）：`_VERDICT_ORDER` 里**含 `ok`**，而它自己的注释写着
  「`ok` 永远在最后：它是『没命中任何一条』的兜底，**不是『检查通过』的合格证**」。把 `ok` 当"要干活"的后果：
  ① 报告 `ok 79`、其余计数全 0 ⇒ ③ 仍报「进行中」；② 它把 `_built_ok3` 一起带成 `False` ⇒
  「③ **需你确认**（收尾 = 换纪元）」那一支**永远不出现**。
  修法是 `if k != "ok" and ...`（**与同文件另两处早已这么写的判据同一套**，不另造口径）。
- 另加一条**用户指令**（注释里引了原话）：「**纪元切换就标为已完成**」⇒
  `_epoch2_done = bool(isinstance(build_doc, dict) and rows3 and not bad)`；成立就把 ③ 标 `STAGE_STATE_DONE`。
  ⚠ 两条边界按注释照实记着：报告里的非零计数**照旧写进依据**、台账里**真有没落成的行时不标已完成**。

**处置**：用户回「1，同步」⇒ 已把部署侧那份 `main.py` **拷回仓库 + 插件树**（三棵树 hash 一致），
此后所有改动都在仓库侧进行、再往下同步。**判据仍是代码**：以后遇到"部署侧与仓库不一致"，
先抓差异看清楚**它是什么**再决定方向 —— **不要默认"仓库就是新的"**（这次仓库反而是旧的）。

### 8.6 仍未处理（等你）

| # | 事项 | 说明 |
|---|---|---|
| 1 | **`docs/` 不在插件同步清单里** | `main.py` 27 行 + `surfaces.py` 4 处 + `schemas.py` 1 处 + `config/*.json` 2 处写着「见 `docs/阶段X-….md`」⇒ 插件里读不到。三选一（把 `docs/` 纳入同步 / 内联结论 / 接受读不到）—— **我倾向纳入同步**：一处改、那 27 行一个字不用动，代价是插件包多约 250 KB 文档 |
| 2 | `schemas.py` 那 **6 个 `dry_run` 字段** | 调用点的形参已删干净；字段本身**留着**（它写着"只为不改报文形状"，且预检可能在读它）。要连字段一起删再动 |
| 3 | `.gitignore` 已补 `views/*_view.html` | ⚠ 但**已经在库里被跟踪过的旧 HTML 不会被新规则移除** —— 要不要 `git rm --cached` 由你定 |
| 4 | `AGENTS.md` 里「本工程**现在就是纪元 2**」 | 与它自己「只放规则、不放状态」略有张力（紧接下一句已写明"它是现算的，不是写死的 2"）。**本轮刻意没动** —— 纯措辞，不值得为它再触发一次指令重载 |

---

## 9 · 部署侧修复搬回仓库 + 三处同步（2026-10-12 又一轮）

> 这一轮的触发：用户说「**部署端又修了个 BUG，同步过来**」，并给了一张说明图（A 案：补一道确认闸）。

### 9.1 搬回来的：`confirm_plan()` 纪元 2 硬闸（**本轮唯一的行为变化**）

- **由来**（图上的原话口径）：换纪元**此前只换了「数据权限」**——有墙的只有"往冻结的 `plan_v1.json` 的写"；
  而「**② 出图 → 用户点头 → 确认**」这套**流程一面墙都没有**：`confirm_plan()` 照旧能调（连 `active_epoch()` 都不读）
  ⇒ agent 可以**把前三阶段的整套仪式重演一遍**，而"不可回到前三阶段"当时只是 `execute_build` warning 里的一句**文案**。
- **现在**：`confirm_plan()` 进门先算纪元，`>= 2` ⇒ **拒收，且连确认都不记**（一个字节不写）；
  拒收原文：`拒绝确认：**纪元 2 已开 —— 前三阶段不会再回来**（用户 2026-10-12 指令）` + 给纪元 2 的正路。
- **坦白写进注释的两条边界**：它管住的是"**留痕的**看图验收"；agent 自愿把 `views/<plan 名>_view.html` 交给用户看
  **谁也拦不住、也不需要拦**。它管住的是：**没有任何工具会替这件事落款了。**
- **与仓库既有那处是同一套口径**：`generate_plan()` 里早就有 `active_epoch() >= 2 ⇒ 整表重发拒收`。

### 9.2 同批搬回来的：12 处「指针文案」

在纪元 2 里**指错路**的 next_step / docstring / 报错文案，逐条改成"（**仅纪元 1**）+ 纪元 2 的正路"：

| 组 | 处数 | 落点（改后行号） |
|---|---|---|
| 「回正常验收 → `confirm_plan()`」 | 5 | `3374` · `4427` · `10075` · `10294` · `11501` |
| `get_plan` 阶段总览 ②③⑦ 的 `next` | 4 | `10299` · `10300` · `10374` · `10573` |
| `adopt_user_edits` 认领两处 | 2 | `5892` · `6064` |
| **改动窗口的生命周期**（顺手改正旧话） | 3 | `9877` · `9933` · `11910` —— 窗口其实是「**下一次落盘**」就关（实测 `generate_plan()` 落完就关），**不是**"开到下一次 `confirm_plan()`" |

### 9.3 同批我自己改的（用户授权「你觉得怎么好就怎么改」）

| # | 文件 | 改了什么 | 性质 |
|---|---|---|---|
| 1 | `main.py:10014` | `[\"page\"]` 原来是**弯引号** `[“page”]`（agent 照抄会拼出非法字符） | **真 BUG** |
| 2 | `main.py:10016-10020` | 补「那是**历史理由**，别当成「现在还要两张」（现行恒一张）」 | 文案 |
| 3 | `main.py:11827` | 注释里"手画那**两张图**" → "那两张**矢量图**（⚠ 手画那套已整套删除）" | 文案 |
| 4 | `main.py:3916-3922` | `_place_rows` 的 `batch` 默认 `True` → **`False`**（与工具面统一）+ docstring 同步 | 一致性（⚠ 唯一调用点**总是显式传参** ⇒ 零行为影响） |
| 5 | `planning/plan.py:2470` | `promote_to_epoch2` 返回标注 `-> dict` → **`-> tuple[dict, dict]`**（函数体实际返回二元组） | 假标注 |
| 6 | `planning/plan.py:1319/1572/1581/1595` | `origin_view_spec()` 与相邻常量那 4 处**指向已删 `drawing` 段**的引用 → 改成 `_compose_build_rows()`（阶段三算 `z_base_cm`/`z_top_cm` 的唯一那处） | 文案 / 死引用 |
| 7 | `README.md:49`、`使用说明.md:104` | 「官方 52 个工具集」→ 注明"**运行时现算**（早期实测 52，2026-10-12 实测 **53**），别写死" | 防腐 |
| 8 | `tests/preflight.py:305` | 异常分支标签「（默认 true）」→「（**断言：默认 false**）」 | 会误导 |

> ⚠ **我自己改错又修回的一处**（如实记）：第 2 条第一次替换时用 **ASCII 直引号**包中文，
> **打断 Python 字符串**（语法错）；回读时发现、改成了 `「」`。**教训**：Python 源码里包中文一律用 `「」`，
> 别用 `"` / `'`。任务书 §0 第 1 条也写着改 JSON 时同理（那条说的是 JSON）。

### 9.4 三处同步（用户跑的两轮 `build_plugin.ps1`）

```
第一轮（--Deploy）：11 文件同步 + py_compile 全过；"不一样的有 2 个" = main.py / plan.py（我改的那两个）
第二轮（--Deploy）：11 文件同步 + py_compile 全过；"不一样的有 1 个" = plan.py
结果：main.py = 32F944B3C1E9、planning/plan.py = 1BE8525C7A7C  —— 仓库 = 插件树 = 部署侧，逐字节一致
```
⚠ `bridge.py` / `__init__.py` 与仓库不同是**预期**（在 `build_plugin.ps1` 的**不同步名单**里，插件自维护）。
⚠ 部署侧 `__pycache__` 已清（2 个目录 / 9 个 `.pyc`），重启后按新代码重建（实测 13:49:03）。

### 9.5 这轮**顺手查实**的两件事

1. **`dry_run` / `accept_user_edits` / `两张图` / `drawing` 的残留全部合规**：
   `dry_run` 只剩带日期或带"已删"标注的历史注释；`drawing` 剩 5 处全是"**已删除**"性质的说明
   （`schemas.py:820`、`main.py:11827/11830`、`plan.py:1320/1574`）—— 没有一处再把它当现役东西。
2. **插件侧三份文档确实自足**：`ue_plugin/UEPLANE/README.md` / `PROMPT.md` / `PROMPT.zh-CN.md`
   里 `docs/阶段` **0 命中**；"一页 three.js"、"完成搭建那一次调用冻结 plan 并开新 plan"都写成**行为**，
   不依赖 `confirm_stage3` 这个名字。⇒ §8.6 第 1 条（"插件里读不到 docs"）**对 agent 无害**：
   那 27 行只出现在 `main.py` **给开发看的注释**里；agent 读的是工具 docstring。

### 9.6 本轮仍未做 / 等你

| # | 事项 | 说明 |
|---|---|---|
| 1 | **预检的原始输出** | 你转述"全绿"，但我手里没有原文 —— 贴一次我就把原文记进 §4 第 1 条（那是这份文件唯一能补上的证据缺口） |
| 2 | **`evaluate_layout()` 复核那 8 行** | 报告（13:02）说 `plan_changed 8`（大概率就是"路灯太矮了"那 8 盏）；要复核 / 重摆**得 UE 在线**，本轮没跑 |
| 3 | 部署侧那份 `main.py` 的**旧 `__pycache__`** | 已清；重启后已按新码重建 |
| 4 | §8.6 那四条 | 原样挂着（`docs/` 是否纳入同步 / 6 个 `dry_run` 字段 / `.gitignore` 已被跟踪的旧 HTML / `AGENTS.md` 那句状态） |


