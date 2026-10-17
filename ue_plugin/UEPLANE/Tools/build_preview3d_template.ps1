# 生成阶段二 three.js 页的**真模板**：壳（Tools/preview3d.shell.html）+ 内联 three.min.js
#   → Content/Python/ueplane/planning/web/preview3d.html
#
# 为什么要有这一步（2026-10-17 实测）：DSH 文档预览窗是 sandbox="allow-scripts" 的 blob: iframe，
#   继承宿主 CSP `script-src 'unsafe-inline'`（不含 blob:/self）⇒ 外挂 <script src="three.min.js">
#   会被**静默拦掉**：页面只剩 CSS、黑屏、不报错。内联之后实测 r155 正常。
#
# 用法：powershell -File Tools\build_preview3d_template.ps1
# 改页面 = 改壳 → 重跑本脚本；**别直接手改 660 KB 的产出模板**。
$ErrorActionPreference = 'Stop'
$here     = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$plugin   = Split-Path -Parent $here
$shell    = Join-Path $here "preview3d.shell.html"
$threeSrc = Join-Path $plugin "Content\Python\ueplane\planning\web\three.min.js"
$out      = Join-Path $plugin "Content\Python\ueplane\planning\web\preview3d.html"

foreach ($f in @($shell, $threeSrc)) { if (-not (Test-Path $f)) { throw "缺文件：$f" } }

$shellText = [System.IO.File]::ReadAllText($shell, [System.Text.Encoding]::UTF8)
$three     = [System.IO.File]::ReadAllText($threeSrc, [System.Text.Encoding]::UTF8)

# 模板与生成器之间的唯一契约（figure3d.py 的 SLOT）；壳里只许出现一次
$slotCount = ([regex]::Matches($shellText, [regex]::Escape('/*__DATA__*/'))).Count
$phCount   = ([regex]::Matches($shellText, [regex]::Escape('__THREE__'))).Count
if ($slotCount -eq 0) { throw "壳里找不到数据槽位 —— figure3d.py 会拒收" }
if ($slotCount -gt 1) { throw "壳里数据槽位出现了 $slotCount 次（只许 1 次：注释里再写一遍会被注入撕开）" }
if ($phCount   -gt 1) { throw "壳里 __THREE__ 占位出现了 $phCount 次（只许 1 次）" }

# three.min.js 里不许出现能提前结束 script 的序列
foreach ($bad in @('</script', '<!--')) {
  if ($three.Contains($bad)) { throw "three.min.js 里含 $bad —— 内联会破坏 HTML 解析" }
}

$html = $shellText.Replace('__THREE__', $three)
$expected = $shellText.Length + $three.Length - (12 * 1)   # 占位 11 字符被替换成库长度；长度对得上 = 只替换了一处
[System.IO.File]::WriteAllText($out, $html, (New-Object System.Text.UTF8Encoding($false)))

$kb = [math]::Round((Get-Item $out).Length / 1KB)
Write-Output ("模板已生成：{0}  ({1} KB)" -f $out, $kb)
Write-Output ("槽位 1 处 = {0}   占位替换后长度 = {1}（应等于 壳+库-占位）" -f ($slotCount -eq 1), $html.Length)
