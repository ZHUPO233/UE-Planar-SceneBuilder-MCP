# sync-to-D.ps1 -- one-way sync: F (source repo) --> D (live test copy), with a hard-coded exclude list.
#
# WHY A SCRIPT (instead of judging by hand every round):
#   Two files must NEVER be overwritten, and both mistakes are unrecoverable-class accidents:
#     * config\surface_materials.json -- D holds the real key->material table for your project;
#       F holds an EMPTY template. Overwriting it = wiping the table.
#     * views\                       -- D is the LIVE state (plan / build ledger / evaluate report /
#       confirmation svg / your _draw_*.py helpers); F is a 2026-09-30 snapshot.
#       Overwriting it = destroying the scene of record.
#   So they are excluded IN CODE, not by memory.
#
# SAFETY (three rules, do not remove):
#   1. Before writing, back up every file that is about to be overwritten into
#      $Target\.sync-backup-<timestamp>\ (append-only).
#   2. Overwrite only, never delete: a file is written only if it exists in scope and its hash differs.
#      Anything extra on the target side is left untouched.
#   3. The exclude list is never touched -- not one byte.
#
# USAGE:
#   & "F:\MCP_Server\sync-to-D.ps1"          # dry run (default): print the plan only
#   & "F:\MCP_Server\sync-to-D.ps1" -Apply   # actually sync
#   Afterwards YOU run preflight on D, then restart the client and open a NEW chat.
#
# NOTE: this file is deliberately ASCII-only. A .ps1 containing Chinese needs a UTF-8 BOM and CRLF
#       to survive Windows PowerShell 5.1 (see SETUP.md); ASCII sidesteps that whole class of failure.

[CmdletBinding()]
param(
    [string]$Source = 'F:\MCP_Server',
    [string]$Target = 'D:\UEMCP-v0.8.0',
    [switch]$Apply,
    # 2026-10-04: by default the target's config\surface_materials.json is LEFT ALONE (it is WORK DATA --
    # the target may already hold its own element->material table). Pass this switch to push it too.
    # views\ is NEVER pushed (it is the live scene of record, not code) -- overwriting it would
    # immediately orphan the target's ledger.
    [switch]$IncludeConfig
)

# ---------- 1. what to include ----------
$IncludeDirs = @('src', 'tests', 'docs')

$IncludeTopFiles = @('AGENTS.md', 'README.md', '使用说明.md', '使用版-AGENTS.md', 'SETUP.md', 'setup.ps1', 'serve_http.py', 'pyproject.toml', 'uv.lock', '.python-version', 'LICENSE', 'mcp.json.example')

$IncludePatterns = @('HANDOVER-*.md', '更新说明-*.md')

# config: only these two (surface_materials.json is work data -- see exclude list)
$IncludeConfigFiles = @('environments.json', 'asset_categories.json')

# ---------- 2. never touch these ----------
# directory level: relative path's first segment matches -> skipped entirely
$ExcludeDirs = @('views', 'catalog', 'dist', '.venv', '.git', '.trae', '.cursor', '.vscode', '__pycache__')

# file level: matched by file name
#   surface_materials.json = WORK DATA -> excluded unless -IncludeConfig is given
$ExcludeFile = @('mcp.json', 'connect-config.json')
if (-not $IncludeConfig) { $ExcludeFile += 'surface_materials.json' }

# Build the plan: missing on target = new; hash differs = modified; hash equal = skipped.
function Get-SyncPlan {
    $rel = New-Object System.Collections.ArrayList

    foreach ($d in $IncludeDirs) {
        $srcDir = Join-Path $Source $d
        if (-not (Test-Path $srcDir)) { continue }
        Get-ChildItem $srcDir -Recurse -File | ForEach-Object {
            [void]$rel.Add($_.FullName.Substring($Source.Length + 1))
        }
    }
    foreach ($f in $IncludeTopFiles) {
        if (Test-Path (Join-Path $Source $f)) { [void]$rel.Add($f) }
    }
    foreach ($pat in $IncludePatterns) {
        Get-ChildItem (Join-Path $Source $pat) -File -ErrorAction SilentlyContinue | ForEach-Object {
            [void]$rel.Add($_.Name)
        }    }
    foreach ($f in $IncludeConfigFiles) {
        $c = Join-Path 'config' $f
        if (Test-Path (Join-Path $Source $c)) { [void]$rel.Add($c) }
    }

    $plan = New-Object System.Collections.ArrayList
    foreach ($r in $rel) {
        $first = ($r -split '[\\/]')[0]
        if ($ExcludeDirs -contains $first) { continue }
        # nested build junk: __pycache__ anywhere in the path, and any .pyc
        if ($r -match '(^|[\\/])__pycache__([\\/]|$)') { continue }
        $name = Split-Path $r -Leaf
        if ($ExcludeFile -contains $name) { continue }
        if ($name -like '*.pyc') { continue }

        $s = Join-Path $Source $r
        $t = Join-Path $Target $r
        if (-not (Test-Path $s)) { continue }
        if (Test-Path $t) {
            if ((Get-FileHash $s).Hash -eq (Get-FileHash $t).Hash) { continue }
            [void]$plan.Add([pscustomobject]@{ Kind = 'M'; Rel = $r })
        } else {
            [void]$plan.Add([pscustomobject]@{ Kind = '+'; Rel = $r })
        }
    }
    return $plan
}

if (-not (Test-Path $Target)) { throw "Target not found: $Target" }

$plan = Get-SyncPlan
$mod = @($plan | Where-Object { $_.Kind -eq 'M' })
$new = @($plan | Where-Object { $_.Kind -eq '+' })

Write-Host "Source = $Source"
Write-Host "Target = $Target"
Write-Host ("To sync: {0} modified / {1} new" -f $mod.Count, $new.Count)
Write-Host ""
Write-Host "-- MODIFIED (backed up before overwrite) --"
$mod | ForEach-Object { Write-Host ("   M  " + $_.Rel) }
Write-Host "-- NEW --"
$new | ForEach-Object { Write-Host ("   +  " + $_.Rel) }
Write-Host ""
Write-Host "-- EXCLUDED (never touched) --"
Write-Host ("   dirs : " + ($ExcludeDirs -join ', '))
Write-Host ("   files: " + ($ExcludeFile -join ', '))

if (-not $Apply) {
    Write-Host ""
    Write-Host "(dry run: nothing was written. Re-run with -Apply to sync.)"
    exit 0
}

# ---------- 3. apply: back up first, then write ----------
# FAIL-CLOSED: if ANY backup fails, nothing is written. A sync without a rollback copy is not a sync.
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $Target ('.sync-backup-' + $stamp)
$errs = New-Object System.Collections.ArrayList

try {
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
} catch {
    Write-Host ""
    Write-Host ("ABORT: cannot create backup dir -> " + $backup)
    Write-Host ("        " + $_.Exception.Message)
    Write-Host "        Nothing was written. (Check write permission on the target.)"
    exit 1
}

$backed = 0
foreach ($p in $mod) {
    $t = Join-Path $Target $p.Rel
    $b = Join-Path $backup $p.Rel
    $bd = Split-Path $b -Parent
    try {
        if (-not (Test-Path $bd)) { New-Item -ItemType Directory -Path $bd -Force | Out-Null }
        Copy-Item $t $b -Force -ErrorAction Stop
        $backed++
    } catch {
        [void]$errs.Add($p.Rel + "  [backup] " + $_.Exception.Message)
    }
}
if ($errs.Count -gt 0) {
    Write-Host ""
    Write-Host ("ABORT: {0} file(s) could not be backed up -- nothing was written (fail-closed):" -f $errs.Count)
    $errs | ForEach-Object { Write-Host ("   " + $_) }
    exit 1
}

$copied = 0
$failed = New-Object System.Collections.ArrayList
foreach ($p in $plan) {
    $s = Join-Path $Source $p.Rel
    $t = Join-Path $Target $p.Rel
    $td = Split-Path $t -Parent
    try {
        if (-not (Test-Path $td)) { New-Item -ItemType Directory -Path $td -Force | Out-Null }
        Copy-Item $s $t -Force -ErrorAction Stop
        $copied++
    } catch {
        [void]$failed.Add($p.Rel + "  " + $_.Exception.Message)
    }
}

Write-Host ""
Write-Host ("Result: {0} written / {1} FAILED (planned {2}); backup of {3} -> {4}" -f $copied, $failed.Count, $plan.Count, $backed, $backup)
if ($failed.Count -gt 0) {
    Write-Host "FAILED files:"
    $failed | ForEach-Object { Write-Host ("   " + $_) }
    Write-Host "The target copy is now PARTIAL -- treat it as dirty and re-run after fixing access."
    exit 1
}
Write-Host ""
Write-Host "Next (do it yourself):"
Write-Host ('  & "' + (Join-Path $Target '.venv\Scripts\python.exe') + '" "' + (Join-Path $Target 'tests\preflight.py') + '"')
Write-Host "  Then restart the client and open a NEW chat."
exit 0
