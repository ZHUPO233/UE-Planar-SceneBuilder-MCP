# Temp probe: full MCP handshake over HTTP (127.0.0.1:8000/mcp), call one toolset tool,
# print the decoded returnValue (the toolset wraps results twice: text -> {returnValue: "..."}).
# usage: pwsh -File mcp_probe.ps1 -Tool probe_asyncio
#        pwsh -File mcp_probe.ps1 -Tool check_build_target -Args '{"decision":"","target_level":""}'
#        pwsh -File mcp_probe.ps1 -Tool list_toolsets -Toolset ""     (top-level MCP tool)
param(
  [string]$Tool    = "",
  [string]$Toolset = "ueplane.tools.UeplaneTools",
  [string]$ToolArgs = "{}",
  [string]$Base    = "http://127.0.0.1:8000/mcp",
  [switch]$Raw
)

$ErrorActionPreference = "Stop"

function Send-Json([string]$body, [string]$sid) {
  $h = @{ "Accept" = "application/json, text/event-stream"; "Content-Type" = "application/json" }
  if ($sid) { $h["mcp-session-id"] = $sid }
  return Invoke-WebRequest -Uri $Base -Method Post -Body $body -Headers $h -UseBasicParsing
}

function Parse-Sse([string]$raw) {
  $m = [regex]::Match($raw, '(?s)data:\s*(\{.*?\})\s*$')
  if ($m.Success) { return $m.Groups[1].Value }
  return $raw
}

$init = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"ueplane-probe","version":"1.0"}}}'
$r = Send-Json $init $null
$sid = [string]$r.Headers["mcp-session-id"]
if (-not $sid) { Write-Output "!! no mcp-session-id"; exit 1 }
try { Send-Json '{"jsonrpc":"2.0","method":"notifications/initialized"}' $sid | Out-Null } catch {}

$callArgs = @{ tool_name = $Tool; arguments = ($ToolArgs | ConvertFrom-Json) }
if ($Toolset) { $callArgs["toolset_name"] = $Toolset }
$call = '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":' +
        (@{ name = "call_tool"; arguments = $callArgs } | ConvertTo-Json -Depth 12 -Compress) + '}'
$r2 = Send-Json $call $sid
$outer = (Parse-Sse $r2.Content) | ConvertFrom-Json

if ($outer.result.isError) {
  Write-Output "!! TOOL ERROR:"
  Write-Output $outer.result.content[0].text
  exit 2
}

$text = $outer.result.content[0].text
if ($Raw) { Write-Output $text; exit 0 }

# text 是 {"returnValue": "<工具自己返回的 JSON 字符串>"}
$mid = $text | ConvertFrom-Json
Write-Output ($mid.returnValue | ConvertFrom-Json | ConvertTo-Json -Depth 12)
