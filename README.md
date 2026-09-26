# UEMCP · UE 平面场景搭建（参考图驱动）

把**一张参考图 + 一句需求**变成 UE 关卡里的场景：先规划成一份**可核对的平面布局**（每个物体一行：中心坐标 + 占地 + 朝向），你点头之后再在**你自己指定的关卡**里按这份布局搭建；之后要改，**只改你指的那几处**，不推倒重来。

**它是什么**：MCP **编排层**（编排 = 它自己一步都不碰 UE，只调度官方工具）。链路：

```
宿主（DSH / Trae / Cursor / 豆包） → 本 server（stdio）→ 官方 Unreal MCP（HTTP 127.0.0.1:8000）→ UE
```

## 使用说明

1. 解压到一个**路径不含空格**的目录（Trae 的配置不允许空格）；
2. 在该目录跑 `.\setup.ps1` —— 建环境、装依赖、跑自检、并把各家客户端配置写好；
3. 按你的客户端做**最后一次动作**（重启客户端 / 打开「启用项目级 MCP」/ 重载窗口）—— 见 `SETUP.md`；
4. 让 AI 调一次 `get_plan()`：能返回状态就说明通了（连上应有 **13 个工具**）。

用户侧怎么用（见 **`交付说明.md`**；给 AI 的规则见 **`AGENTS.md`**（交付给客户的版本是 `交付版-AGENTS.md`）。

## 阶段

| 阶段 | 状态 |
|---|---|
| 一 · 资产确认（元素表 → 用户确认 → 找资产 → 交付清单） | **已实现** |
| 二 · 平面放置规划（`plan_v1.json` + 确认图 + 验收闸门） | **已实现** |
| 三 · 资产布局生成（指令表 → 批量落关卡 / 增量重摆） | **已实现** |
| 四 · 碰撞检测与布局修正 | **未实现** |
| 五 · 布局序列化 | 部分（指令表已有） |
| 六 · 材质与实例化 | **未实现**（现在落下去的是**白模灰块**） |
| 七 · 环境灯光与预览相机 | **未实现** |
| 八 · 评估与闭环迭代 | **未实现** |

## 仓库导航

| 路径 | 里面是什么 |
|---|---|
| `src/mcp_server/main.py` | 工具面（13 个工具）、各道闸门、官方调用封装 |
| `src/mcp_server/planning/plan.py` | 阶段二规划层（几何指纹 / 图判据 / 验收台账） |
| `docs/` | 各阶段口径：`阶段一-资产确认` / `阶段二-平面放置规划` / `阶段三-资产布局生成` / `阶段二至八-全流程规划` |
| `catalog/` | 阶段一**签过字**的交付物（元素表 / 资产清单 / 资产库指纹）—— **别手改** |
| `views/` | 阶段二产物：当前放置表、确认图、验收与搭建台账、`archive/` 留档 —— **别手改** |
| `tests/` | `preflight.py`（重启前自检）+ `check_tools.py`（工具面 / 链路回归，要 UE） |
| `serve_http.py` | 只认 URL 的客户端用（端口 `8770`） |

## 该MCP硬规矩

① **禁止猜"疑似资产"** —— 名字对得上才算找到，找不到就报缺并请用户给【路径 + 名字】；② **用户确认前不落盘、不搭建东西**；③ **规划图没经用户确认不许进第三阶段**，数据与图缺一不可且必须一致；④ **绝不存盘**（存不存由用户在 UE 里决定）；⑤ 返回数字必须**无损穿过 JSON**（`-0.0` / `NaN` / `Infinity` 会被宿主整条拒收）。

**13 个工具**：`official_status`、`check_build_target`、`confirm_elements`、`plan_assets`、`rename_assets`、`confirm_assets`、`get_asset_list`、`generate_plan`、`get_plan`、`request_plan_change`、`confirm_plan`、`generate_build_orders`、`execute_build`。

⚠ 宿主**不读** MCP 的 `instructions` —— 所以要求要么写在各工具 docstring 里，要么由**代码拒收时的原文**教会调用方。

## 开发 / 跑法

- **DSH** 以 stdio 启动：`command=<根>\.venv\Scripts\python.exe`、`args=[<根>\src\mcp_server\main.py]`；只认 URL 的客户端用 `serve_http.py`，端口 `8770` —— **注意别抢 8000**，那是官方UE的MCP插件使用的。
- 改完 `src/mcp_server/main.py` 或规划层：**由用户**跑

  ```powershell
  & .\.venv\Scripts\python.exe tests\preflight.py
  ```
- 排障：工具报**没有正文**的错（只有一句 `Error executing tool …`）→ 完整栈在宿主日志 `%APPDATA%\dsh-desktop\logs\harness.log`，搜工具名即可定位。
