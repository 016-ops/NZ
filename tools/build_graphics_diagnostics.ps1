# 构建自有x86图形诊断，不复制任何DLL到客户端，也不修改驱动或全局工具链配置。
param([string]$ToolRoot)
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
if (-not $ToolRoot) { $ToolRoot = Join-Path $repositoryRoot 'runtime/tools/launcher-build' }
$vcRoot = Get-ChildItem -LiteralPath (Join-Path $ToolRoot 'msvc/VC/Tools/MSVC') -Directory | Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName
$sdkRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10'
$sdkVersion = Get-ChildItem -LiteralPath (Join-Path $sdkRoot 'Lib') -Directory | Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty Name
$env:INCLUDE = @((Join-Path $vcRoot 'include'), (Join-Path $sdkRoot "Include/$sdkVersion/ucrt"), (Join-Path $sdkRoot "Include/$sdkVersion/shared"), (Join-Path $sdkRoot "Include/$sdkVersion/um")) -join ';'
$env:LIB = @((Join-Path $vcRoot 'lib/x86'), (Join-Path $sdkRoot "Lib/$sdkVersion/ucrt/x86"), (Join-Path $sdkRoot "Lib/$sdkVersion/um/x86")) -join ';'
Push-Location -LiteralPath $repositoryRoot
try {
    # Windows子系统避免额外控制台遮挡测试窗口；入口仍为Unicode wmain。
    foreach ($version in @('d3d','d3d11')) {
        $library = if ($version -eq 'd3d') { 'd3d9.lib' } else { 'd3d11.lib' }
        & "$vcRoot/bin/Hostx64/x86/cl.exe" /nologo /utf-8 /EHsc /MT "tools/native_${version}_present.cpp" "/Fe:runtime/native-${version}-present.exe" "/Fo:runtime/native-${version}-present.obj" /link $library user32.lib gdi32.lib /SUBSYSTEM:WINDOWS /ENTRY:wmainCRTStartup
        if ($LASTEXITCODE -ne 0) { throw "Graphics diagnostic build failed: $version" }
    }
} finally { Pop-Location }
