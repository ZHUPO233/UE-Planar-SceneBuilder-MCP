# views/ · 各阶段的产物（**本包为空，等你走完阶段一、二**）

这里的东西全部由工具**自动**生成：

| 文件 | 谁写 | 里面是什么 |
|---|---|---|
| `plan_v1.json` | `generate_plan` | **当前这一版**平面放置表（**米**；每行一个物体：中心坐标 + 占地 + 朝向）。**闸门认的就是它** |
| `build_orders_v1.json` | `generate_build_orders` | 翻给 UE 的搭建指令表（**厘米**，补好 Z；白膜的 plane 会被压成薄 cube） |
| `build_state_v1.json` | `execute_build` | **搭建台账**：关卡里现在到底摆了什么（含 Actor 引用与白膜**实测**尺寸） |
| `acceptance.json` | `confirm_plan` / `request_plan_change` | 验收台账：谁 / 何时 / 哪一版几何确认过 / 你提过哪些要改的 |
| `environment_state_v1.json` | `setup_environment` | **环境台账**：配环境**动手前**那几个环境 Actor 的现值（`restore=true` 照它退回原样） |
| `exchange/` | `export_layout` | 阶段四导出的**交换文件**（`scene_v1.json` + `.csv`，给 Blender 等用的场景描述） |
| `preview/` | `capture_preview`（**归阶段七**） | **预览图**（PNG；机位基于世界坐标，出图**不动你的视口相机**） |
| `plan_v1_overview*.svg` | **AI 手绘**（代码不出图） | 给你看的确认图 —— 图里必须写着当前几何指纹、并覆盖"这一轮该改的那几行" |
| `archive/` | 工具 | 每一版数据与旧图的留档（只增不减） |

## 请守三条

1. **别手改这些 JSON**：改了就和几何指纹对不上，已确认的那一版**自动作废**。
2. **不要用位图当确认图**：`.png/.jpg` 读不出文字，工具**一律不认账**（核验不了"漏没漏元素"）；交付就出 `.svg`。
3. **别删 `archive/`**：那是"我们现在搭的是不是当初确认的那一版"的唯一凭据。
