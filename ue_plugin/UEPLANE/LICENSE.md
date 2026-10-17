# UEPLANE · License

Copyright (c) 2026 ZHUPO233. All rights reserved.

## 1. 插件本体

UEPLANE 插件（含 `Content/Python/` 下的全部 Python 源码、`Content/config/` 下的配置、
`Content/Python/ueplane/planning/web/` 下由本插件生成的页面，以及 `Tools/` 下的模板与脚本）
为 **ZHUPO233 的专有作品**。

- 购买 / 合法取得本插件后，你可以在**你的项目与你的团队内部**使用、修改、随项目分发本插件；
- 不得**单独再分发、转售、出租本插件本体**（含其源码），也不得把本插件作为另一款商品的主要价值来源打包出售；
- 本插件依赖的第三方组件按各自的许可证分发，见 `THIRD_PARTY_NOTICES.md`。

> 若你在 Fab 上取得本插件，则**以 Fab 的最终用户许可协议（EULA）为准**；
> 本文件仅用于说明包内各部分的归属与第三方义务，与 Fab EULA 冲突时以 Fab EULA 为准。

## 2. 你的工程内容不属于本许可

插件运行时会读写你工程里的 `Saved/UEPLANE/`（元素表 / 规划 / 台账 / 材质单 / 环境记录 / 验收台账）。
**这些数据是你的**，本许可不主张任何权利；插件不含任何遥测，不会把工程数据发到任何网络位置。

## 3. 无担保

本插件按“现状”提供，不附带任何明示或默示担保。因使用本插件导致的项目损失、资产损坏或
构建失败，作者不承担责任 —— **所有落关卡的动作都由你（或你的 AI 客户端）显式调用触发**，
插件本身从不自动存盘（阶段六 `setup_environment(save=true)` 是唯一例外，且由调用方传参决定）。

## 4. 第三方

- three.js —— MIT，版权与许可证全文见 `THIRD_PARTY_NOTICES.md`
- Unreal Engine 及其自带插件 —— 受 Epic Games 的 EULA 约束，不在本许可范围内
