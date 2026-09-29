# =============================================================================
# 一条命令：把环境备好 + 把所有已知客户端的 MCP 配置写好
#
#   cd <解压出来的目录>
#   .\setup.ps1
#
# 它做五件事：
#   ① 找 Python（有 uv 用 uv，没有回退 python -m venv + pip）
#   ② 建 .venv 装依赖（mcp[cli]>=2.2.0 / anyio>=4.0）
#   ③ 跑 tests/preflight.py（**脚本方式** —— 复现宿主是怎么起 server 的）
#   ④ **把已知客户端的配置文件都写好**（已存在的先备份，再加/并我们这一条，不动别人）：
#        - <根>\mcp.json            ← 豆包 PC（实测它的工作区根就是读这个）/ 通用 stdio 客户端
#        - <根>\.trae\mcp.json      ← Trae（用 ${workspaceFolder}，**不含绝对路径**）
#        - <根>\.cursor\mcp.json    ← Cursor 项目级（按 Cursor 文档的位置，**未实测**）
#        - <根>\.vscode\mcp.json    ← VS Code 项目级（⚠ 它的键名是 `servers`，**不是** `mcpServers`）
#        - <根>\connect-config.json ← 汇总：DSH 片段 / 通用片段 / 豆包 HTTP 备选 / 自检命令
#      ⚠ **"一份通吃"不存在**：各家读的**文件名不同**、键名也可能不同（VS Code 是 `servers`）——
#        所以这里给每家各写一份；仓库里**不提交**其中任何一份（它们都是本机生成物，见 .gitignore），
#        只提交一份 `mcp.json.example` 模板。要不要哪一份由你决定。
#   ⑤ 打印"下一步该干什么"
#
# ⚠ 首次要联网（装依赖）。⚠ 解压路径**别带空格**（Trae 的 command 不允许含空格）。
# ⚠ DSH 不读工作区文件（它的 MCP 是应用内插件配置）→ 脚本把片段写进 connect-config.json，
#   你把它填进 DSH 的设置里即可。
# ⚠ 本脚本由 agent 代写（2026-09-24），**没在别的机器上实测过** —— 出问题把原始输出贴回来。
# =============================================================================

[CmdletBinding()]
param(
    [switch]$SkipPreflight,     # 只建环境 + 写配置，不跑预检
    [switch]$SkipClientConfig   # 不碰任何客户端配置文件（只出 connect-config.json）
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

function Say([string]$m) { Write-Host "[setup] $m" }
function Warn([string]$m) { Write-Host "[setup] $m" -ForegroundColor Yellow }
function Have([string]$cmd) { $null -ne (Get-Command $cmd -ErrorAction SilentlyContinue) }

Say "项目根：$root"
if ($root -match ' ') {
    Warn '路径里有空格：Trae 的 command 字段不允许含空格 —— 建议解压到没有空格的目录。'
}

$venvPy  = Join-Path $root '.venv\Scripts\python.exe'
$mainPy  = Join-Path $root 'src\mcp_server\main.py'
$servePy = Join-Path $root 'serve_http.py'

# --- ① ② 建环境 ---------------------------------------------------------------
if (Test-Path $venvPy) {
    Say '.venv 已存在 —— 跳过建环境（要重建就先删掉 .venv 再跑本脚本）'
} elseif (Have 'uv') {
    Say '用 uv sync 建环境并装依赖…'
    & uv sync
    if ($LASTEXITCODE -ne 0) { Write-Host "[setup] uv sync 失败（退出码 $LASTEXITCODE）" -ForegroundColor Red; exit 1 }
} elseif (Have 'python') {
    Say '没找到 uv —— 回退到 python -m venv + pip…'
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host '[setup] 建 venv 失败' -ForegroundColor Red; exit 1 }
    & $venvPy -m pip install --upgrade pip
    & $venvPy -m pip install 'mcp[cli]>=2.2.0' 'anyio>=4.0'
    if ($LASTEXITCODE -ne 0) { Write-Host '[setup] 装依赖失败' -ForegroundColor Red; exit 1 }
} else {
    Write-Host '[setup] 既没有 uv 也没有 python —— 请先装 Python 3.11+ 或 uv 再来。' -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $venvPy)) {
    Write-Host "[setup] 环境建完了但找不到 $venvPy —— 先别配客户端，把上面的输出贴回来。" -ForegroundColor Red
    exit 1
}

# --- ③ 预检 -------------------------------------------------------------------
$preflightOk = $null
if (-not $SkipPreflight) {
    Say '跑预检 tests\preflight.py（脚本方式）…'
    & $venvPy (Join-Path $root 'tests\preflight.py')
    $preflightOk = ($LASTEXITCODE -eq 0)
    Say ("预检退出码 = {0}" -f $LASTEXITCODE)
}

# --- ④ 写各客户端配置 ----------------------------------------------------------
# 合并规则：文件已存在 → 先备份 → 解析 → **只加/替换我们这一条**（别人的 server 一个都不动）；
#           解析不了 → 备份后当新文件写（并在最后提醒你去看备份）。
function Set-McpEntry {
    param(
        [string]$Path,
        [string]$ServerName,
        [System.Collections.IDictionary]$Entry,
        # 服务清单挂在哪个**根键**下：多数客户端是 `mcpServers`，**VS Code 是 `servers`**。
        # （2026-09-29 加：VS Code 的 .vscode/mcp.json 用的就是这个不同的键名。）
        [string]$RootKey = 'mcpServers'
    )
    $dir = Split-Path $Path -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }

    $doc = $null
    if (Test-Path $Path) {
        $bak = "$Path.bak-$(Get-Date -Format 'yyyyMMddHHmmss')"
        Copy-Item $Path $bak -Force
        Say "已备份原配置：$bak"
        try { $doc = Get-Content $Path -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable }
        catch { Warn "原配置解析不了（坏 JSON？）—— 本次按新文件写，原文件见备份：$bak"; $doc = $null }
    }
    if ($null -eq $doc) { $doc = @{} }
    if (-not $doc.Contains($RootKey)) { $doc[$RootKey] = @{} }
    $doc[$RootKey][$ServerName] = $Entry
    ($doc | ConvertTo-Json -Depth 12) | Set-Content -Path $Path -Encoding utf8
    Say "已写入：$Path（根键 = $RootKey，server 名 = $ServerName）"
}

$serverName = 'scene-stage'

# ① 工作区根 mcp.json —— 豆包 PC 实测就读这个；也是很多 stdio 客户端的通用约定
$genericEntry = [ordered]@{ command = $venvPy; args = @($mainPy) }
# ② Trae 项目级：用 ${workspaceFolder}，**不含绝对路径**（换机器/换目录都不用改）
#    ⚠ 单引号是故意的：双引号里 PowerShell 会把 ${workspaceFolder} 当自己的变量展开成空串。
$traeVenv  = '${workspaceFolder}/.venv/Scripts/python.exe'
$traeMain  = '${workspaceFolder}/src/mcp_server/main.py'
$traeEntry = [ordered]@{ command = $traeVenv; args = @($traeMain) }

if (-not $SkipClientConfig) {
    Set-McpEntry -Path (Join-Path $root 'mcp.json')          -ServerName $serverName -Entry $genericEntry
    Set-McpEntry -Path (Join-Path $root '.trae\mcp.json')    -ServerName $serverName -Entry $traeEntry
    # Cursor 项目级：按 Cursor 文档的位置写；它大概率不认 ${workspaceFolder}，所以用绝对路径
    Set-McpEntry -Path (Join-Path $root '.cursor\mcp.json')  -ServerName $serverName -Entry $genericEntry
    # VS Code 项目级：⚠ 它的**根键是 `servers`**（不是 `mcpServers`）—— 所以必须传 -RootKey。
    # 这里同样用**绝对路径**（不赌它认不认 ${workspaceFolder}；你要可移植就自己换成变量形式）。
    # ⚠ **单独包一层 try/catch**（2026-09-29 加）：这一份是后加的、还要**新建 `.vscode` 目录** ——
    #   万一它失败（权限 / 目录被占 / 名字冲突），**不许把前面三份与收尾提示一起带崩**
    #   （脚本顶层是 `$ErrorActionPreference='Stop'`，不拦就会中断在第 ④ 步）。
    try {
        Set-McpEntry -Path (Join-Path $root '.vscode\mcp.json') -ServerName $serverName -Entry $genericEntry -RootKey 'servers'
    } catch {
        Warn "VS Code 那份没写成（$($_.Exception.Message)）—— 其余三份与 connect-config.json 不受影响；要它的话手动建 .vscode\mcp.json（根键 servers）。"
    }
}

# ③ 汇总片段（给不读工作区文件的客户端，例如 DSH；也是你手动核对用的）
$cfg = [ordered]@{
    '生成时间' = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    '项目根'   = $root
    '说明'     = '本目录已自动写好 mcp.json / .trae/mcp.json / .cursor/mcp.json / .vscode/mcp.json；下面这份是给"不读工作区文件的客户端"（DSH 等）和手动核对的。⚠ 仓库里**不提交**这四份（都是本机生成物），只提交 mcp.json.example 模板。'
    'DSH（stdio，字段来自 dsh-mcp-client 插件 schema）' = [ordered]@{
        transport  = 'stdio'
        serverName = $serverName
        command    = $venvPy
        args       = @($mainPy)
        env        = @{}
        cwd        = $root
    }
    'DSH（streamable-http，要先起 HTTP）' = [ordered]@{
        transport  = 'streamable-http'
        serverName = $serverName
        url        = 'http://127.0.0.1:8770/mcp'
        headers    = @{}
    }
    '豆包 PC（若连接器只让填 URL，起 HTTP 后按这个填）' = [ordered]@{
        '传输类型'      = 'HTTP'
        '服务器URL'    = 'http://127.0.0.1:8770/mcp'
        '启动命令'      = "& `"$venvPy`" `"$servePy`""
        '自定义Headers' = '留空'
    }
    '通用 mcpServers（Cursor/其它 stdio 客户端）' = @{ mcpServers = @{ $serverName = @{ command = $venvPy; args = @($mainPy) } } }
    '自检' = [ordered]@{
        '预检' = "& `"$venvPy`" `"$(Join-Path $root 'tests\preflight.py')`""
        '状态' = "`$env:PYTHONPATH='$root\src'; & `"$venvPy`" -m mcp_server.planning.plan"
    }
}
$out = Join-Path $root 'connect-config.json'
$cfg | ConvertTo-Json -Depth 12 | Set-Content -Path $out -Encoding utf8
Say "汇总片段已写入：$out"

# --- ⑤ 下一步 -----------------------------------------------------------------
Write-Host ''
Write-Host '下一步：' -ForegroundColor Cyan
Write-Host '  · 豆包 PC ：重启客户端（它按启动时的配置挂载）→ 问它"看看有什么工具"'
Write-Host '  · Trae    ：设置 → MCP → 打开「启用项目级 MCP」（.trae/mcp.json 已写好，路径不用改）'
Write-Host '  · Cursor  ：重载窗口（.cursor/mcp.json 已写好）'
Write-Host '  · VS Code ：重载窗口（.vscode/mcp.json 已写好；它那份的根键是 servers）'
Write-Host '  · DSH     ：把 connect-config.json 里「DSH（stdio）」那段填进 MCP 设置 → 重启会话'
Write-Host '  · 连上后第一件事：让 agent 调 get_plan() —— 返回得出来就说明链路通了'
Write-Host ''
if ($preflightOk -eq $false) {
    Warn '预检没过：先修再连（原始输出就在上面）。'
    exit 1
}
exit 0
