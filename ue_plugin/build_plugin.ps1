# =============================================================================
# UEPLANE · 插件构建 / 同步脚本
#
# 为什么必须有它（**防分叉的唯一入口**）：
#   插件要自包含（能上传 Fab），所以它里面**必然有一份**产品业务代码的拷贝：
#       Content/Python/ueplane/{main,schemas,surfaces}.py + planning/
#   但那四个文件**不是插件的资产** —— 它们是产品仓库 `src/mcp_server/` 里的同一份东西。
#   一旦有人只改了一边，就变成**两套代码**（这个项目最忌讳的就是这个）。
#   所以：**改产品 → 跑这个脚本**，别手改插件里的那四份拷贝。
#   ⚠ 反向也不许：插件里那四份**永远是从产品拷来的**，改它们等于下一次同步就被覆盖。
#
# 用法（在本文件所在目录跑）：
#   .\build_plugin.ps1                              # 同步（产品 → 插件树）
#   .\build_plugin.ps1 -Check                       # **只比对不写**；有差异就退出码 1（CI / 上架前用）
#   .\build_plugin.ps1 -Deploy "E:\MyProj\Plugins"  # 同步 + 把这个插件整个部署到某个工程的 Plugins\
#   .\build_plugin.ps1 -Deploy "E:\MyProj\Plugins" -SkipSync   # 只部署（不同步）
#
# ⚠ 不同步的东西（**它们属于插件自己，一个字都不该被覆盖**）：
#   `bridge.py`（插件版平台层）/ `tools.py`（工具面）/ `_shims.py`（pydantic·mcp·anyio 替身）/
#   `init_unreal.py` / `__init__.py` / `UEPLANE.uplugin` / `Config\` / `Resources\`
# =============================================================================

param(
    [string]$Deploy = "",
    [switch]$Check,
    [switch]$SkipSync
)

$ErrorActionPreference = "Stop"

$Repo       = Split-Path $PSScriptRoot -Parent          # 仓库根
$PluginRoot = Join-Path $PSScriptRoot "UEPLANE"         # 要打出去的那棵树
$PyDir      = Join-Path $PluginRoot "Content\Python\ueplane"
$CfgDir     = Join-Path $PluginRoot "Content\config"

# ── 要同步的清单（**每一行都是"产品是源、插件是拷贝"**）────────────────────────
$Jobs = @(
    @{ From = (Join-Path $Repo "src\mcp_server\main.py");     To = (Join-Path $PyDir "main.py") },
    @{ From = (Join-Path $Repo "src\mcp_server\schemas.py");  To = (Join-Path $PyDir "schemas.py") },
    @{ From = (Join-Path $Repo "src\mcp_server\surfaces.py"); To = (Join-Path $PyDir "surfaces.py") }
)
foreach ($f in (Get-ChildItem (Join-Path $Repo "src\mcp_server\planning") -Filter *.py)) {
    $Jobs += @{ From = $f.FullName; To = (Join-Path $PyDir "planning\$($f.Name)") }
}
foreach ($f in (Get-ChildItem (Join-Path $Repo "config") -Filter *.json)) {
    $Jobs += @{ From = $f.FullName; To = (Join-Path $CfgDir $f.Name) }
}

function Get-Hash12($path) {
    if (-not (Test-Path $path)) { return "MISSING     " }
    return (Get-FileHash $path -Algorithm SHA256).Hash.Substring(0, 12)
}

# ── ① 源文件自检（缺了别硬拷，报出来）─────────────────────────────────────────
$missing = @()
foreach ($j in $Jobs) { if (-not (Test-Path $j.From)) { $missing += $j.From } }
if ($missing.Count) {
    Write-Error ("产品侧缺文件，**没有同步**：`n  " + ($missing -join "`n  "))
    exit 2
}

# ── ② 比对（`-Check` 只到这一步）──────────────────────────────────────────────
$drift = 0
Write-Output "文件                                             产品侧        插件侧"
foreach ($j in ($Jobs | Sort-Object { $_.To })) {
    $h1 = Get-Hash12 $j.From
    $h2 = Get-Hash12 $j.To
    $mark = if ($h1 -eq $h2) { "  " } else { "! " }
    if ($h1 -ne $h2) { $drift++ }
    Write-Output ("{0}{1,-46} {2}  {3}" -f $mark, $j.To.Substring($PluginRoot.Length), $h1, $h2)
}
if ($drift) { Write-Output ("`n不一样的有 $drift 个") } else { Write-Output "`n全部一致" }

if ($Check) {
    if ($drift) { Write-Output "**校验失败**：插件里的拷贝与产品不一致 —— 跑一次不带 -Check 的同步。"; exit 1 }
    Write-Output "校验通过。"; exit 0
}

# ── ③ 同步 ────────────────────────────────────────────────────────────────────
if (-not $SkipSync) {
    New-Item -ItemType Directory -Force -Path (Join-Path $PyDir "planning") | Out-Null
    New-Item -ItemType Directory -Force -Path $CfgDir | Out-Null
    foreach ($j in $Jobs) {
        Copy-Item $j.From $j.To -Force
    }
    Write-Output "`n已同步 $($Jobs.Count) 个文件（产品 → 插件）"
}

# ── ④ 插件必备件自检（上架要用的，缺了就点名）──────────────────────────────────
$required = @(
    (Join-Path $PluginRoot "UEPLANE.uplugin"),
    (Join-Path $PluginRoot "Content\Python\init_unreal.py"),
    (Join-Path $PyDir "bridge.py"),
    (Join-Path $PyDir "tools.py"),
    (Join-Path $PyDir "_shims.py"),
    (Join-Path $PluginRoot "Config\FilterPlugin.ini")
)
$lack = @($required | Where-Object { -not (Test-Path $_) })
if ($lack.Count) {
    Write-Warning ("插件必备件还缺（上架前要补齐）：`n  " + ($lack -join "`n  "))
}

# ── ⑤ 部署到某个 UE 工程的 Plugins\ ───────────────────────────────────────────
if ($Deploy -ne "") {
    if (-not (Test-Path $Deploy)) { Write-Error "部署目标不存在：$Deploy"; exit 2 }
    $dst = Join-Path $Deploy "UEPLANE"
    robocopy $PluginRoot $dst /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
    $rc = $LASTEXITCODE
    if ($rc -ge 8) { Write-Error "robocopy 失败（退出码 $rc）"; exit 1 }
    # ⚠ 清掉 .pyc：源码换了、缓存没换的话 UE 会加载旧的（实测踩过）
    Get-ChildItem $dst -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Output "`n已部署 → $dst"
    Write-Output "⚠ 插件代码改了必须**重启 UE**（实测：没有热重载通道）"
}

# ⚠ 显式 exit 0：同步/部署路径里最后一条外部命令是 robocopy（成功时返回 1~7），
#   不写这句的话脚本会把那个码当自己的退出码 —— 2026-10-11 实测两次（MakePlugin 与这里）。
#   ⚠ -Check 那条路**有**自己的 exit 0/1，不受这句影响（它在前面就返回了）。
exit 0
