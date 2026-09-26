# SETUP · 把这个包装上（一条命令 + 最后一次动作）

**一句话**：跑 `.\setup.ps1`，再按你的客户端做最后一次动作。
（**装完怎么用、要点几次头、现在做不到什么 —— 看 `交付说明.md`。**）

**它做五件事**：① 找 Python（优先 `uv`，没有就 `python -m venv` + `pip`）；② 建 `.venv` 装依赖；③ 跑 `tests/preflight.py`（**脚本方式**复现宿主启动，绿了才谈得上连）；④ **写好客户端配置**（已存在先备份，只加 / 替换我们这一条，不动别的 server）；⑤ 打印下一步。⚠ 首次要联网；⚠ **解压路径别带空格**（Trae 的 `command` 不允许含空格）。

**配置写到哪**：`<根>\mcp.json`（豆包 PC / 通用 stdio，绝对路径）、`<根>\.trae\mcp.json`（Trae，用 `${workspaceFolder}` 变量，换机换目录不用改）、`<根>\.cursor\mcp.json`（Cursor 项目级，**未实测**）、`<根>\connect-config.json`（DSH 等**不读工作区文件**的客户端 + 手动核对）。

**最后一次动作**：豆包 PC **重启客户端**；Trae 打开「**启用项目级 MCP**」；Cursor 重载窗口；DSH 把 `connect-config.json` 里 stdio 那段填进 MCP 设置并重启会话。⚠ 各家文件名与键名不一样，一份文件通吃做不到。

**验证**：让 agent 调 `get_plan()`，返回 `status` 就说明链路通了；连上应有 **13 个工具**。⚠ **没开 UE 时**只有离线工具能用；要「找资产 / 验路径 / 往关卡里摆」必须有 UE 编辑器 + 官方 Unreal MCP 在 `127.0.0.1:8000`。

**包里没有**（故意的）：`.venv`、`.git`、`views/archive/`、参考图。根 `mcp.json` 必须写接收方的绝对路径，所以由 `setup.ps1` 现场生成。
