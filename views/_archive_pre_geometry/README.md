# 归档 · 几何法之前的「参考图重建 + 目视比对」（2026-09-23）

> 这批东西**不是当前链路的一部分**。归档是为了不让下一个打开 `views/` 的人（或 Agent）把历史当现行。

## 当时在做什么

一次后来被放弃的做法：把相机摆到**从参考图推导出的视点** → 在 UE 里截图 → 和参考图并排比对
（`_compare_*.png`）→ 人眼看着调，直到像为止。

| 文件 | 是什么 |
|---|---|
| `reference_views.json` | 3 个命名视点定义。坐标是**厘米**：`[-2000, 0, 165]` = 站在 -20 m、眼高 165 cm、俯角 2° |
| `ref_*.json` / `ref_*.png` | 每次截图记录：相机参数（`camera_location_cm` / `rotation` / `fov`）+ 标注到图上的 actor 名与屏幕坐标 |
| `_compare_*.png`、`_ab_grassmesh.png` | 参考图 × 截图的并排比对；`_ab_*` 是草材质 A/B |
| `blockout_*.png`（19:04） | 白模阶段的街视 / 顶视 |
| `plant_*.png`、`top_down_*.png`（20:11–20:12） | 种植阶段的街视 / 顶视 |
| `thumbs/` | 材质缩略图与拼版 |
| `overview_01.png`、`ref_view_01.png` | 最早的渲染 |

## 为什么归档（而不是直接删）

1. **不是当前链路。** `grep src/` 对 `reference_views.json` / `ref_*` **零命中**；当前链路是
   `config/plan_params.json` → `plan.py` → `views/plan_v1.json` + `views/plan_wire_v1.png`。
2. **它不违反「不做图像识别推理」这条硬性质。** 那条约束的是**阶段二几何的来源**
   （不许从图里估尺寸 / 读走向）。这批是**渲染结果的目视验收**，判断者是人，不是模型读图算几何。
3. **但它有一处真踩线，所以不保留为现行做法。** `reference_views.json` 的 `source` 原文：

   > "参考图推导：地平线在画面 **46.6%** 高度 → 俯角约 2 度"

   —— **这是从参考图里量了数值**。量的是相机俯角（不是尺寸、不是走向），字面上没违反规则，
   但属于"从图里读数"的同类动作。当前 `plan_params.json` 那两条参数都用目测措辞，没继承它。

## 一个必须一起留档的事实（与规划图闸门有关）

这批截图对应的内容**当时真摆进了关卡**：

- `Scenario/`（含 Houses / Planting / Road / Trees）**12 个** StaticMeshActor
- `Blockout/`（含 Lawn / Placeholder）**18 个**
- 合计 **30 个**，全在 `/Game/demo/Landscape/MCP`

时间线：

```
19:04  blockout_street_v1.png / blockout_topdown_v1.png
20:11  plant_street_v1.png / plant_top_v1.png
20:12  plant_street_v2.png / plant_top_v2.png       ← 摆完
20:24  catalog/asset_list.json 签字交付
21:56  views/plan_v1.json 确认（confirmed_by: 用户本人）  ← 闸门才关上
```

**摆在了规划图确认之前。** 闸门自己的原话是「目的是免得 AI 拿到资产表就开始搭建」。

⚠ 公平起见：这条闸门规矩也是 2026-09-23 立的，**谁先谁后待核实** —— 如果顺序是
「先手工试搭看效果 → 因为这次经历才立了规矩」，那这不是违规，而是规矩的**起因**。

不管哪种，**状态上的问题是同一个**：「我们现在搭的是不是当初确认的那张图」在 30 个
不来自 `plan_v1.json` 的 Actor 还在关卡里时，答不出来。

## 关卡侧的处置（2026-09-23 晚）

- **完整备份**：`/Game/demo/Landscape/MCP_pregate_20260923`
  （整关卡复制，`AssetTools.duplicate` 返回 `true`；随后 `exists` 已复核）。
  这 30 个 Actor 的全部信息都在里面 —— 要复盘或取回，从这份备份里看，不用从截图猜。
- **工作关卡 `/Game/demo/Landscape/MCP` 里的 30 个 Actor 已从场景移除**
  （30 次 `remove_from_scene` 全部返回 `true`），目的是让上面那个问题重新答得出来。

  复核证据（同一次会话内实测）：

  | 检查 | 移除前 | 移除后 |
  |---|---|---|
  | `get_folders` | `Blockout`、`Blockout/Lawn`、`Blockout/Placeholder`、`Lighting`、`Scenario`、`Scenario/Houses`、`Scenario/Planting`、`Scenario/Road`、`Scenario/Trees` | **`["Lighting"]`** |
  | `get_actors_in_folder("Scenario", recursive)` | 12 个 | `Folder does not exist` |
  | `get_actors_in_folder("Blockout", recursive)` | 18 个 | `Folder does not exist` |

  正面清点（`find_actors(name="")` 全量，共 16 个 Actor）里**还剩 3 个网格** —— 但都不是场景内容，是底座：

  | Actor | 标签 | 性质 |
  |---|---|---|
  | `StaticMeshActor_0` | `SM_SkySphere` | 天空球网格（旧方案） |
  | `StaticMeshActor_36` | `SM_SkySphere2` | **第二个**天空球，scale 400，位于原点 |
  | `Floor_0` | `Floor` | 地面 |

  → **「搭的是不是当初确认的那张图」现在答得出来了**：剩下的全是底座 / 灯光 / 引擎默认 ——
  `WorldSettings` / `Brush` / `DirectionalLight` / `SkyAtmosphere` / `SkyLight` /
  `ExponentialHeightFog` / `VolumetricCloud` / `PlayerStart` / `BuoyancyManager` /
  `DefaultPhysicsVolume` / `GameplayDebuggerPlayerManager` / `ChaosDebugDrawActor` /
  `AbstractNavData-Default` —— **没有一个是场景内容**（房子 / 路 / 树 / 灌木那 30 个已清掉）。

  ⚠ 顺带记账（与闸门无关，是技术债）：**两个 SkySphere 互相重复，且都与 `SkyAtmosphere` 重复**。
  现代 UE 的天空用 `SkyAtmosphere` + `VolumetricCloud` 就够，天空球网格是旧做法。留一个还是都删，待定。
- ⚠ **工作关卡未存盘**（本项目规矩：不许存盘）。所以这次移除是**内存态**：
  Ctrl+Z 或重载关卡即可回退。**要落盘由用户决定。**

## `catalog/asset_list.json` 的同步修正

`bush` 那条备注原来写着 `……现按 0.5 缩放摆放在绿化带 (1500,310)；……` ——
**阶段三的摆放信息混进了阶段一的签字交付物**。那半句已删（`items[].note` 与 `deliverable` 两处同步）。

⚠ 提醒：`asset_list.json` 的「签字」用的是**资产库指纹**（`signed_hash` / `stale`），
它保证的是「资产库没变」，**不是「表文件没被手改」**。所以这次手改不会让 `stale` 变红 ——
换句话说，**这条防线覆盖不到"表被别人改了"这种情形**。这是本次暴露出来的一个口径缺口，待处理。
