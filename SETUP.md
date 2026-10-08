# SETUP · 把这个包装上（一条命令 + 最后一次动作）

**一句话**：跑 `.\setup.ps1`，再按你的客户端做最后一次动作。
（**装完怎么用、要点几次头、各阶段现在能做什么 —— 看 `使用说明.md`。**）

**它做五件事**：① 找 Python（优先 `uv`，没有就 `python -m venv` + `pip`）；② 建 `.venv` 装依赖；③ 跑 `tests/preflight.py`（**脚本方式**复现Agent启动，绿了才谈得上连）；④ **写好客户端配置**（已存在先备份，只加 / 替换我们这一条，不动别的 server）；⑤ 打印下一步。⚠ 首次要联网；⚠ **解压路径别带空格**（Trae 的 `command` 不允许含空格）。



**配置写到哪**：`<根>\mcp.json`（通用 stdio 客户端，绝对路径）、`<根>\.trae\mcp.json`（Trae，用 `${workspaceFolder}` 变量，换机换目录不用改）、`<根>\.cursor\mcp.json`（Cursor 项目级，**未实测**）、`<根>\.vscode\mcp.json`（VS Code 项目级 —— ⚠ **它的根键是 `servers`，不是 `mcpServers`**）、`<根>\connect-config.json`（DSH 等**不读工作区文件**的客户端 + 手动核对）。
⚠ **这四份都是本机生成物**（含本机绝对路径 / 各家专有文件名）——**本仓库**用 `.gitignore` 挡着它们、只提交一份通用模板 **`mcp.json.example`**（它的 `_doc` 写明各家读哪个文件、键名差在哪）。
⚠ **这份使用包里没带 `.gitignore`**（按要求去掉）：你要是把包放进 git / 推上 GitHub，**自己先排掉这四份**（仓库根目录那份 `.gitignore` 可照抄）。**"一份通吃"不存在** —— 所以由脚本给每家各写一份。

**最后一次动作**：通用 stdio 客户端**重启客户端**；Trae 打开「**启用项目级 MCP**」；Cursor 重载窗口；**VS Code 重载窗口**；DSH 把 `connect-config.json` 里 stdio 那段填进 MCP 设置并重启会话。⚠ 各家文件名与键名不一样（VS Code 那份的根键是 `servers`），一份文件通吃做不到。

**验证**：让 agent 调 `get_plan()`，返回 `status` 就说明链路通了；连上应有 **20 个工具**。⚠ **没开 UE 时**只有离线工具能用；要「找资产 / 验路径 / 往关卡里摆 / 落位对账」必须有 UE 编辑器 + 官方 Unreal MCP 在 `127.0.0.1:8000`（⚠ `evaluate_layout` 连不上时**不报错**：它只出 `plan ↔ 台账` 那半，并在 `warnings` 里点名"关卡那一维没查"）。

**包里没有**（故意的）：`.venv`、`.git`、`views/archive/`、参考图、**以及任何客户端成品配置**（`mcp.json` / `.trae/` / `.cursor/` / `.vscode/` / `connect-config.json`）。根 `mcp.json` 必须写接收方的绝对路径，所以由 `setup.ps1` 现场生成；仓库里只有 `mcp.json.example` 模板。
⚠ 也**没有** `.gitignore` / `.gitattributes`（它们只服务版本控制、**与运行无关**）—— 代价是这份副本**没有忽略规则**，放进 git 前你得自己排一下（仓库里那两份可照抄）。


---

## 常见问题（装的时候会撞到的四件事）

**Q：能不能用 Windows PowerShell 5.1？**
A：**首次安装可以**。但**重跑**时脚本要用 `ConvertFrom-Json -AsHashtable`（**PS 6+ 才有**）——
在 5.1 上它会走 `catch` 分支，**把那份配置整份重写**：原文件**会**先备份成 `<配置>.bak-<时间戳>`，
但**别人的 server 条目会被顶掉**。所以：**更建议用 `pwsh`（PowerShell 7）**；重跑前先看一眼有没有 `.bak-` 文件。

**Q：`setup.ps1` 开头那个 BOM 是多余的？能删吗？**
A：**别删，是故意的**。判断口径：**没有 BOM 的 `.ps1` 只有 `pwsh` 7 能跑** —— 5.1 会把脚本里的中文读成乱码、
引号被吞 → **7 条语法错、脚本根本起不来**（这条被误当成过"setup 跑不起来"）。带 BOM 才能两种 PowerShell 都跑。

**Q：装完连不上 / 工具报错？**
A：① 预检过没过（`setup.ps1` 第 ③ 步；非 0 就别连）；② UE 编辑器开着？官方插件启用？`127.0.0.1:8000` 在听？
③ 工具报**没有正文**的错 → 完整栈在 `%APPDATA%\dsh-desktop\logs\harness.log`。

**Q：这份副本要放进 git 吗？**
A：可以，但它**没有带 `.gitignore`**（故意的）—— 请自己排除这五份**本机生成物**：
`mcp.json`、`.trae/`、`.cursor/`、`.vscode/`、`connect-config.json`（它们含本机绝对路径）。