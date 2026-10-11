# =============================================================================
# UEPLANE · Fab 上架打包脚本（插件版）
#
# 用法：
#   .\MakePlugin.ps1                      # 用 .uplugin 里现有的 VersionName
#   .\MakePlugin.ps1 -Version 1.0.1       # 顺便改写包内 .uplugin 的 VersionName
#   .\MakePlugin.ps1 -SkipSyncCheck       # 跳过"产品↔插件拷贝是否一致"那道闸（**别随便用**）
#
# 产物：dist\UEPLANE-<Version>.zip（解压后就是 Plugins\UEPLANE\ 该有的样子）
#
# ⚠ **第一步是硬闸**：先跑 `build_plugin.ps1 -Check` —— 插件里那四份产品代码的拷贝
#   （main / schemas / surfaces / planning）**必须与产品仓库逐字节一致**；
#   不一致就**拒绝打包**（否则就是把一份"看着像、其实不是那一版"的东西发出去）。
#
# ⚠ 这个脚本照的是官方 VibeUE 的 MakePlugin.ps1 的骨架，但**刻意避开它踩过的两个坑**：
#   ① 它的排除清单里有**裸文件名**（robocopy /XF BUILD_PLUGIN.md）——
#      那依赖 robocopy 的匹配语义，文件一改路径就漏发。这里**全部用显式相对路径**。
#   ② 它把「LICENSE 是否随包」这个决定**只写在脚本注释里，没写进自查清单**。
#      这里那个决定**同时**写在下面 $ExcludeDevFiles 与 FAB-上架材料.md 的清单里，两边一致。
#
# ⚠ **本文件必须存成 UTF-8 with BOM**：Windows PowerShell 5.1 读无 BOM 的 .ps1 会按
#   ANSI(GBK) 解，中文注释会把语法读崩（2026-10-11 实测：报一堆 Unexpected token）。
#   仓库里能跑的 `setup.ps1` / `sync-to-D.ps1` 都是 UTF8-BOM —— 照它们。
# =============================================================================

param(
    [string]$Version = "",
    [switch]$SkipSyncCheck
)

$ErrorActionPreference = "Stop"

$PluginName = "UEPLANE"
$Root       = $PSScriptRoot                                  # ue_plugin\
$PluginDir  = Join-Path $Root $PluginName                    # ue_plugin\UEPLANE\
$DistDir    = Join-Path $Root "dist"
$StagingRoot = Join-Path $DistDir "_staging"
$Staging    = Join-Path $StagingRoot $PluginName             # 打出来的包内根目录
$ZipPath    = Join-Path $DistDir "$PluginName-$Version.zip"

# ── ⓪ 硬闸：产品 ↔ 插件 的拷贝必须一致 ─────────────────────────────────────────
if (-not $SkipSyncCheck) {
    Write-Output "── 先核对：产品仓库 ↔ 插件的拷贝是否一致 ──"
    & (Join-Path $Root "build_plugin.ps1") -Check
    if ($LASTEXITCODE -ne 0) {
        Write-Error "**拒绝打包**：插件里的产品代码拷贝与产品仓库不一致。先跑 .\build_plugin.ps1 同步。"
        exit 1
    }
}

# ── 排除清单（**显式路径**，不用裸文件名）───────────────────────────────────────
$ExcludeDirs = @(
    "Binaries", "Intermediate", "Packaged",        # 构建产物
    ".git", ".github", ".vs", ".vscode", ".cursor", # VCS / IDE
    "__pycache__", ".pytest_cache", ".venv", "venv", # Python
    "build", "dist",                                # 本地构建目录
    "docs",                                         # 开发文档（面向买家的在 Resources/ 与 README）
    "tools"                                         # 我们的验证脚本（mcp_probe.ps1），不是插件的一部分
)
$ExcludeFiles = @(
    "*.log", "*.tmp", "*.pdb", "*.lib", "*.exp", "*.ilk",
    "*.exe", "*.dll",                    # ⚠ Fab 明确不允许 .exe
    "*.pyc", "*.pyo", "*~", ".DS_Store", "Thumbs.db", "Desktop.ini"
)
# ⚠ 「LICENSE 不随包」这个决定（VibeUE 的注释原文：
#    "Custom MIT license — Fab requires the Fab License only; kept in the Git repo, stripped from the Fab package"）
#    —— 这里**显式列出来**，并且与 FAB-上架材料.md 的清单保持一致。
$ExcludeDevFiles = @(
    "LICENSE", "MakePlugin.ps1", "build_plugin.ps1", "移植实测记录.md",
    "FAB-上架材料.md", "FAB-DESCRIPTION.md", "FAB_Tech_Details.md", "FAB-Checklist.md",
    ".gitignore"
)

# ── ① 必备项自检（缺任一项直接停）─────────────────────────────────────────────
$required = @(
    (Join-Path $PluginDir "$PluginName.uplugin"),
    (Join-Path $PluginDir "README.md"),
    (Join-Path $PluginDir "Config\FilterPlugin.ini")
)
foreach ($f in $required) {
    if (-not (Test-Path $f)) { Write-Error "缺少必备文件：$f"; exit 1 }
}
foreach ($d in @("Content", "Config", "Resources")) {
    $p = Join-Path $PluginDir $d
    if (-not (Test-Path $p)) { Write-Warning "少了惯用目录：$p（Fab 审核会看结构）" }
}
# ⚠ Fab 要图标；缺了只警告不拦（本地打包仍然有用），但**上架前必须补**
if (-not (Test-Path (Join-Path $PluginDir "Resources\Icon128.png"))) {
    Write-Warning "缺 Resources\Icon128.png —— Fab 上架**必须有**图标（128x128 PNG）"
}

# ── ② 清场 + 可选改写版本号 ────────────────────────────────────────────────────
if (Test-Path $StagingRoot) { Remove-Item -Recurse -Force $StagingRoot }
New-Item -ItemType Directory -Force -Path $Staging | Out-Null
if (-not (Test-Path $DistDir)) { New-Item -ItemType Directory -Force -Path $DistDir | Out-Null }

$upluginSrc = Join-Path $PluginDir "$PluginName.uplugin"
$upluginText = Get-Content -Raw -Encoding UTF8 $upluginSrc
if ($Version -ne "") {
    $upluginText = [regex]::Replace($upluginText, '"VersionName"\s*:\s*"[^"]*"', "`"VersionName`": `"$Version`"")
}

# ── ③ robocopy 复制（排除清单生效）─────────────────────────────────────────────
$xdArgs = @(); foreach ($d in $ExcludeDirs)  { $xdArgs += @("/XD", (Join-Path $PluginDir $d)) }
$xfArgs = @(); foreach ($f in $ExcludeFiles) { $xfArgs += @("/XF", $f) }
$xfArgs += @("/XF") + $ExcludeDevFiles

$robocopyArgs = @($PluginDir, $Staging, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP") + $xdArgs + $xfArgs
& robocopy @robocopyArgs | Out-Null
$rc = $LASTEXITCODE
if ($rc -ge 8) { Write-Error "robocopy 失败（退出码 $rc）"; exit 1 }   # robocopy: ≤7 都算成功
# 把改过版本号的 .uplugin 写回暂存区（源文件不动）
Set-Content -Path (Join-Path $Staging "$PluginName.uplugin") -Value $upluginText -Encoding UTF8 -NoNewline

# ── ③b 清 Python 缓存（**真清，不只警告**）─────────────────────────────────────
# 为什么：源码换了、缓存没换 ⇒ 买家加载到**旧代码**（实测踩过）。
# 而且 robocopy 的 /XD 只挡得住**显式路径**那一层，嵌套的 __pycache__ 会漏进来
# （2026-10-11 实测：语法闸生成的缓存被带进包，第 ④ 步才警告出来）。
Get-ChildItem $Staging -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $Staging -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in ".pyc", ".pyo" } |
    Remove-Item -Force -ErrorAction SilentlyContinue

# ── ④ 排除项不许残留 ──────────────────────────────────────────────────────────
$leftover = @()
foreach ($d in $ExcludeDirs)  { if (Test-Path (Join-Path $Staging $d)) { $leftover += $d } }
foreach ($f in $ExcludeDevFiles) { if (Test-Path (Join-Path $Staging $f)) { $leftover += $f } }
if ($leftover.Count -gt 0) { Write-Warning ("排除项仍残留在包里：" + ($leftover -join ", ")) }
# ⚠ 包内**不许**有 __pycache__ / .pyc（源码换了、缓存没换 → 买家加载到旧代码）
$pyc = Get-ChildItem $Staging -Recurse -Include "*.pyc", "__pycache__" -ErrorAction SilentlyContinue
if ($pyc) { Write-Warning ("包内还有 Python 缓存 " + $pyc.Count + " 项 —— 应该被排除掉的") }

# ── ⑤ 统计 + 打 zip + 清临时目录 ───────────────────────────────────────────────
$files = Get-ChildItem -Recurse -File $Staging
$mb = [math]::Round((($files | Measure-Object Length -Sum).Sum / 1MB), 3)
Write-Output ("包内：{0} 个文件 / {1} MB" -f $files.Count, $mb)
# ⚠ 路径长度：Fab 要求「从插件目录起的相对路径 ≤ 170 字符」——这里按包内相对路径核
$tooLong = $files | Where-Object { ($_.FullName.Substring($Staging.Length)).Length -gt 170 }
if ($tooLong) { Write-Warning ("有路径超过 170 字符：" + ($tooLong[0].FullName)) }

if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($StagingRoot, $ZipPath, [System.IO.Compression.CompressionLevel]::Optimal, $false)
Remove-Item -Recurse -Force $StagingRoot
Write-Output ("OK 打好了：{0}" -f $ZipPath)
Write-Output "⚠ 下一步（人工）：在【干净的 UE 5.8 工程】里解压安装、跑一遍，再上传 Fab。"

# ⚠ 显式 exit 0：否则脚本退出码会继承最后一条外部命令（robocopy 成功时返回 1~7）。
#  2026-10-11 实测：打包明明成功，退出码却是 1 —— 那会骗到 CI 与上架脚本。
exit 0
