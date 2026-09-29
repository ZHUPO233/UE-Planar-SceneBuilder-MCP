# SETUP · 把这个包装上（一条命令 + 最后一次动作）

**一句话**：跑 `.\setup.ps1`，再按你的客户端做最后一次动作。
（**装完怎么用、要点几次头、现在做不到什么 —— 看 `交付说明.md`。**）

**它做五件事**：① 找 Python（优先 `uv`，没有就 `python -m venv` + `pip`）；② 建 `.venv` 装依赖；③ 跑 `tests/preflight.py`（**脚本方式**复现宿主启动，绿了才谈得上连）；④ **写好客户端配置**（已存在先备份，只加 / 替换我们这一条，不动别的 server）；⑤ 打印下一步。⚠ 首次要联网；⚠ **解压路径别带空格**（Trae 的 `command` 不允许含空格）。

> ⚠ **用哪个 PowerShell 都行**：`setup.ps1` 带 **UTF-8 BOM**（2026-09-29 加）—— Windows PowerShell 5.1 与 `pwsh` 7 都能正确解析。
> 若哪天有人把这个 BOM 去掉：**5.1 会把脚本里的中文读成乱码，引号被吞 → 直接 7 条语法错、脚本根本起不来**（2026-09-29 实测，就是这条被当成"setup 跑不起来"）。
> 判断口径：**无 BOM 的 `.ps1` 只有 `pwsh` 7 能跑**；带 BOM 才能两边都跑。

> ⚠ **但更建议用 `pwsh`（PowerShell 7）跑**：脚本在**合并已有配置**时用了 `ConvertFrom-Json -AsHashtable`，
> 那是 **PS 6+ 才有的参数**（2026-09-29 实测：本机 5.1 的 `Get-Command ConvertFrom-Json` 参数表里没有它）。
> 在 5.1 上它会走 `catch` 分支 → **把那一份配置整份重写**：原文件**会**先备份成 `<配置>.bak-<时间戳>`，
> 但**里面别人的 server 条目会被顶掉**。
> ⚠ **首次安装不受影响**（那几个文件本来就不存在 → 不会走解析分支）；**重跑前先看一眼有没有 `.bak-` 文件**。

**配置写到哪**：`<根>\mcp.json`（豆包 PC / 通用 stdio，绝对路径）、`<根>\.trae\mcp.json`（Trae，用 `${workspaceFolder}` 变量，换机换目录不用改）、`<根>\.cursor\mcp.json`（Cursor 项目级，**未实测**）、`<根>\.vscode\mcp.json`（VS Code 项目级 —— ⚠ **它的根键是 `servers`，不是 `mcpServers`**）、`<根>\connect-config.json`（DSH 等**不读工作区文件**的客户端 + 手动核对）。
⚠ **这四份都是本机生成物**（含本机绝对路径 / 各家专有文件名），`.gitignore` 已挡住、**不进版本库**；仓库里只提交一份通用模板 **`mcp.json.example`**（它的 `_doc` 写明各家读哪个文件、键名差在哪）。**"一份通吃"不存在** —— 所以由脚本给每家各写一份。

**最后一次动作**：豆包 PC **重启客户端**；Trae 打开「**启用项目级 MCP**」；Cursor 重载窗口；**VS Code 重载窗口**；DSH 把 `connect-config.json` 里 stdio 那段填进 MCP 设置并重启会话。⚠ 各家文件名与键名不一样（VS Code 那份的根键是 `servers`），一份文件通吃做不到。

**验证**：让 agent 调 `get_plan()`，返回 `status` 就说明链路通了；连上应有 **18 个工具**。⚠ **没开 UE 时**只有离线工具能用；要「找资产 / 验路径 / 往关卡里摆」必须有 UE 编辑器 + 官方 Unreal MCP 在 `127.0.0.1:8000`。

**包里没有**（故意的）：`.venv`、`.git`、`views/archive/`、参考图、**以及任何客户端成品配置**（`mcp.json` / `.trae/` / `.cursor/` / `.vscode/` / `connect-config.json`）。根 `mcp.json` 必须写接收方的绝对路径，所以由 `setup.ps1` 现场生成；仓库里只有 `mcp.json.example` 模板。
