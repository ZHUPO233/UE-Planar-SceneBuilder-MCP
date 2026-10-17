# UEPLANE · Changelog

本文件记录**面向买家**的版本变化。版本号以 `UEPLANE.uplugin` 的 `VersionName` 为准。

## 2.0.0 — 2026-10-17（首发）

首个发布版。八阶段、带闸门的“参考图 → UE 场景”流程，纯 Python（`Modules: []`），
运行在官方 `PythonScriptPlugin` / `ToolsetRegistry` / `ModelContextProtocol` / `EditorToolset` 之上。

> ⚠ **版本号为什么直接从 2.0.0 起**：插件**此前从未对外发布过**（没有 1.x 的插件版），
> 而它所在的仓库里 MCP server 已发到 v1.0.1。为免"同一个仓库两个版本线混淆"，
> 本插件与仓库的 v2.0.0 走同一条版本线 ⇒ 首发即 **2.0.0**。

**流程（八阶段）**
- ① 资产确认：元素表（按 11 个大类填）+ 两个场景项（地图大小 / 时段）
- ② 平面放置规划：一物一行的放置表 + **three.js 验收页**（能转 / 能缩 / 能点 / 树不是方块）
- ③ 整体落关卡：逐行搭建 + 台账 + `evaluate_layout()` 对账
- ④ 微调：`request_plan_change` → `generate_plan(patch=…)` → `execute_build(only_labels=[…])`（不画图、不重确认）
- ⑤ 表面材质：`probe` → `confirm`（材质 + 材质实例两栏）→ `apply` → `focus` → `save`
- ⑥ 环境搭建：太阳 / 天空大气 / 天光 / 高度雾 / 体积云 / 后处理 / 时段预设
- ⑦ 评估与出图；⑧ 导出交换（按需）

**闸门与留痕**
- 纪元机制：首次全量搭建后进入**纪元 2**，前三阶段的写工具冻结（`reset_scene` 才能重来）
- 用户原话闸：`confirm_plan` / `confirm_assets` / `apply_surfaces(confirm)` / `adopt_user_edits` 等
  必须带用户本人的原话，拿不出原话 = 拒收
- 回读闸 / 对账闸：改过 plan 必须 `get_plan()` 回读、`evaluate_layout()` 重跑后才许落关卡
- 整批拒收纪律：一条不合法 ⇒ 一个字节都不写

**本版针对“AI 擅自跳步”的整改（2026-10-17）**
- `stages` 八行新增结构化字段 **`must_stop` / `stop_why`**：① ② ⑤ ⑥ 四个阶段的
  “必须停手”由代码统一写进 `next` 的**最前面**（以前只写在某些分支的散文里，
  产物一出现那句话就消失 —— 实测据此一路跳到阶段二）
- `confirm_elements()` / `find_assets()` 的 `next_step` 改为**停止操作在前、指路在后**，
  并把“下一步动作”压到“人类给出路径 / 人类回话之后才”
- `stages[].next` 只保留“下一步调谁”，做法一律留在各工具的 `next_step` / `note`（单一来源）

**已知边界**
- 工具拦不住模型“不停手”：`must_stop` 是**可见的强提示**，不是硬停
  （硬停需要在 MCP 客户端一侧拦截工具调用）
- 不附带任何材质 / 网格资产：阶段五会**读取你工程里已有的**材质与材质实例

**本版不包含**
- C++ 模块、任何美术资产、任何遥测
- 工程状态（`Saved/UEPLANE/` 不随包分发 —— 装上就是全新空状态）
