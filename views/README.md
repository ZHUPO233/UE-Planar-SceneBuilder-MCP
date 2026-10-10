# views/ · 阶段二~八的运行产物（**本包为空，等你走完流程**）

这个目录里的东西**全部由工具自动写**，不需要你手写。**干净包里它是空的** ——
`views/` 下现在一个文件都没有，那是**预期的**：本包只带功能（源码 / 配置 / 文档），
**不带任何跑过的场景数据**。

走完流程之后，这里会出现下面这些（⚠ 文件名是**纪元 2** 那一套，`_v2` 结尾的那两份才是现行的）：

| 文件 / 目录 | 谁写 | 里面是什么 |
|---|---|---|
| `plan_v2.json` | `generate_plan` | **当前这一版**平面放置表（闸门认的就是它） |
| `build_orders_v1.json` | `execute_build(dry_run=true)` | 翻给 UE 的搭建指令表（厘米） |
| `build_state_v2.json` | `execute_build` | **搭建台账**：关卡里现在到底摆了什么 |
| `acceptance.json` | `confirm_plan` / `request_plan_change` | 验收台账：谁 / 何时 / 哪一版几何确认过 |
| `user_edits_v1.json` | `execute_build` | 「人工痕迹」问答留痕（问过哪几行 + 用户原话） |
| `environment_state_v1.json` | `setup_environment` | **环境台账**：动手前那几个环境 Actor 的现值（`restore=true` 照它退回原样） |
| `surface_materials_v1.json` | `create_surfaces` / `apply_surfaces` | **材质台账**：建过 / 派生过 / 调过哪些参数（回滚依据） |
| `evaluate_v1.json` | `evaluate_layout` | **落位对账报告**（plan / 台账 / 关卡现读值 三方逐行比） |
| `preview/` | `capture_preview` | **阶段七**的预览图（PNG） |
| `*.svg` | AI 手画 | 给你看的**确认图** —— ⚠ **只在前三阶段**（阶段二验收）要：**阶段四微调不出图** |
| `exchange/` | `export_layout` | **第八阶段**（按需）的交换文件（给 Blender 的场景描述）—— ⚠ 这一版**没把它编排进流程** |
| `archive/` | 工具 | 历史留档（旧版数据与旧图，只增不减） |

## 请守两条

1. **别手改**：这些都是"签过字 / 确认过"的凭据，改一个数就与几何指纹对不上，
   等于把已经确认过的那一版作废，必须重新出图给你看、重新确认。
2. **别当临时文件删**：`archive/` 回答的是「**我们现在搭的，是不是当初确认的那一版**」——
   每重签一次 / 每改一次坐标都会往里留一份旧的。删了就没法追溯。
