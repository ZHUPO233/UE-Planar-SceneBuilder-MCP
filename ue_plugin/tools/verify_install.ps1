# =============================================================================
# UEPLANE · 安装验收脚本（**跑在 MCP 这一侧，不需要你操作 UE**）
#
# 前提：UE 编辑器已经打开、UEPLANE 插件已装好、MCP 端点在监听（默认 127.0.0.1:8000）。
#
# 用法：
#   .\verify_install.ps1
#   .\verify_install.ps1 -Base "http://127.0.0.1:8000/mcp"
#
# 它做 6 项检查，逐项打印 [通过] / [失败] 与**原始证据**，退出码 0 = 全过。
# ⚠ 判据只有两个：**退出码** + 那几行 [通过]/[失败]。空输出 ≠ 通过。
#
# 为什么要它：安装这件事**不能靠肉眼**（"插件列表里能看到" ≠ "工具注册上了"）。
# 这 6 项分别覆盖：端点通不通 / 工具集在不在 / 20 个工具齐不齐 /
# 链路闸的钥匙拿不拿得到 / 八阶段总览读不读得出 / 碰 UE 那条路通不通。
#
# ⚠ 本文件必须存成 **UTF-8 with BOM**：Windows PowerShell 5.1 读无 BOM 的 .ps1 会按
#   ANSI(GBK) 解，中文注释会把语法读崩（2026-10-11 实测）。
# =============================================================================

param(
    [string]$Base    = "http://127.0.0.1:8000/mcp",
    [string]$Toolset = "ueplane.tools.UeplaneTools"
)

$ErrorActionPreference = "Stop"
$FAILED = @()

function Check($name, $ok, $detail = "") {
    $line = "[{0}] {1}" -f ($(if ($ok) { "通过" } else { "失败" })), $name
    if (-not $ok -and $detail) { $line += "   " + $detail }
    Write-Output $line
    if (-not $ok) { $script:FAILED += $name }
}

function Send-Json([string]$body, [string]$sid) {
    $h = @{ "Accept" = "application/json, text/event-stream"; "Content-Type" = "application/json" }
    if ($sid) { $h["mcp-session-id"] = $sid }
    Invoke-WebRequest -Uri $Base -Method Post -Body $body -Headers $h -UseBasicParsing
}
function Parse-Sse([string]$raw) {
    $m = [regex]::Match($raw, '(?s)data:\s*(\{.*?\})\s*$')
    if ($m.Success) { return $m.Groups[1].Value }
    return $raw
}

# ── ① 握手（顺带证明端点活着）────────────────────────────────────────────────
$sid = ""
try {
    $init = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"ueplane-verify","version":"1.0"}}}'
    $r = Send-Json $init $null
    $sid = [string]$r.Headers["mcp-session-id"]
    Check "① MCP 端点握手（UE 在跑 + ModelContextProtocol 在工作）" ([bool]$sid) "没有 mcp-session-id 响应头"
} catch {
    Check "① MCP 端点握手（UE 在跑 + ModelContextProtocol 在工作）" $false $_.Exception.Message
}
if (-not $sid) {
    Write-Output ""
    Write-Output "**先解决这个**：端点都连不上，后面的检查没有意义。"
    Write-Output "  · UE 编辑器开着吗？"
    Write-Output "  · ModelContextProtocol 插件启了吗？"
    Write-Output "  · 8000 端口在监听吗？（插件重装后要**重启编辑器**）"
    exit 1
}
try { Send-Json '{"jsonrpc":"2.0","method":"notifications/initialized"}' $sid | Out-Null } catch {}

function Call-Mcp([string]$tool, $arguments, [string]$ts = "") {
    $ca = @{ tool_name = $tool; arguments = $arguments }
    if ($ts) { $ca["toolset_name"] = $ts }
    $body = '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":' +
            (@{ name = "call_tool"; arguments = $ca } | ConvertTo-Json -Depth 12 -Compress) + '}'
    $resp = Send-Json $body $sid
    $outer = (Parse-Sse $resp.Content) | ConvertFrom-Json
    if ($outer.result.isError) { return @{ isError = $true; text = $outer.result.content[0].text } }
    return @{ isError = $false; text = $outer.result.content[0].text }
}
function Call-Plugin([string]$tool, $arguments) {
    $got = Call-Mcp $tool $arguments $Toolset
    if ($got.isError) { return @{ ok = $false; raw = $got.text } }
    # ⚠ 工具集工具的返回是**两层**：content[0].text 是 `{"returnValue": "<工具自己返回的 JSON 串>"}`
    #   —— 第一次写这个脚本时**少解了一层**，于是 ④⑤⑥ 全读成空、报了一堆假失败（2026-10-11）。
    $mid = $got.text | ConvertFrom-Json
    if (($mid.PSObject.Properties.Name -contains 'returnValue') -and ($mid.returnValue -is [string])) {
        return ($mid.returnValue | ConvertFrom-Json)
    }
    return $mid
}

# ── ② 工具集注册上了没有 ─────────────────────────────────────────────────────
$toolsets = Call-Mcp "list_toolsets" @{}
$found = (-not $toolsets.isError) -and ($toolsets.text -match [regex]::Escape($Toolset))
Check "② 工具集 `"$Toolset`" 已注册" $found ($(if ($toolsets.isError) { $toolsets.text } else { "list_toolsets 里没有它" }))

# ── ③ 20 个工具齐不齐 ────────────────────────────────────────────────────────
$expected = @("official_status", "get_plan", "get_asset_list", "confirm_elements", "plan_assets",
              "rename_assets", "confirm_assets", "generate_plan", "request_plan_change", "confirm_plan",
              "check_build_target", "execute_build", "adopt_user_edits", "apply_surfaces", "create_surfaces",
              "setup_environment", "evaluate_layout", "capture_preview", "export_layout", "check_exchange")
$desc = Call-Mcp "describe_toolset" @{ toolset_name = $Toolset }
$names = @()
if (-not $desc.isError) {
    $o = $desc.text | ConvertFrom-Json
    $names = @($o.tools | ForEach-Object { ($_.name -split '\.')[-1] })
}
$diff = @(Compare-Object $expected $names)
Check "③ 20 个工具全部注册（实际 $($names.Count) 个）" ($diff.Count -eq 0) `
      ("缺/多：" + (($diff | ForEach-Object { "$($_.SideIndicator)$($_.InputObject)" }) -join " "))

# ── ④ 链路闸的钥匙 ───────────────────────────────────────────────────────────
$st = Call-Plugin "official_status" @{}
Check "④ official_status 报连上（它是链路闸的钥匙，不通则别的全被拒）" `
      ([bool]($st.ok -and $st.result.reachable)) ("ok=$($st.ok) reachable=$($st.result.reachable) err=$($st.result.error)")

# ── ⑤ 八阶段总览读得出来（纯离线那条路）──────────────────────────────────────
$gp = Call-Plugin "get_plan" @{ include_plan = $false }
$nStages = if ($gp.ok) { @($gp.result.stages).Count } else { 0 }
Check "⑤ get_plan 读出八阶段总览（实际 $nStages 条）" ($gp.ok -and $nStages -eq 8) `
      ("ok=$($gp.ok) " + $(if (-not $gp.ok) { $gp.refused + $gp.error } else { "stages=$nStages" }))

# ── ⑥ 碰 UE 那条路通不通（只读）─────────────────────────────────────────────
$bt = Call-Plugin "check_build_target" @{ decision = ""; target_level = ""; user_quote = "" }
$lvl = if ($bt.ok) { $bt.result.current_level } else { "" }
Check "⑥ check_build_target 读到了当前关卡（碰 UE 的只读路径）" ([bool]($bt.ok -and $lvl)) `
      ("ok=$($bt.ok) level='$lvl' " + $(if (-not $bt.ok) { $bt.refused + $bt.error } else { "" }))

# ── 汇总 ─────────────────────────────────────────────────────────────────────
Write-Output ""
if ($FAILED.Count -eq 0) {
    Write-Output "**6 项全过** —— 这个包在干净工程里装得起来、工具面完整、闸和链路都活着。"
    exit 0
}
Write-Output ("**有 {0} 项没过**：" -f $FAILED.Count)
$FAILED | ForEach-Object { Write-Output ("   · " + $_) }
Write-Output ""
Write-Output "⚠ 把上面**原始输出整段**留档再问 —— 尤其是 [失败] 那一行的 detail。"
exit 1
