# 编译可复现的x86帧显示桥；只生成隔离产物，验证固定摘要后才可打包至Launcher。
param([switch]$Package, [string]$ToolRoot)
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
if (-not $ToolRoot) { $ToolRoot = Join-Path $repositoryRoot 'runtime/tools/launcher-build' }
$vcRoot = Get-ChildItem -LiteralPath (Join-Path $ToolRoot 'msvc/VC/Tools/MSVC') -Directory | Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName
$sdkRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10'
$sdkVersion = Get-ChildItem -LiteralPath (Join-Path $sdkRoot 'Lib') -Directory | Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty Name
$priorInclude = $env:INCLUDE
$priorLib = $env:LIB
Push-Location -LiteralPath $repositoryRoot
try {
    $env:INCLUDE = @((Join-Path $vcRoot 'include'), (Join-Path $sdkRoot "Include/$sdkVersion/ucrt"), (Join-Path $sdkRoot "Include/$sdkVersion/shared"), (Join-Path $sdkRoot "Include/$sdkVersion/um"), (Join-Path $sdkRoot "Include/$sdkVersion/winrt")) -join ';'
    $env:LIB = @((Join-Path $vcRoot 'lib/x86'), (Join-Path $sdkRoot "Lib/$sdkVersion/ucrt/x86"), (Join-Path $sdkRoot "Lib/$sdkVersion/um/x86")) -join ';'
    New-Item -ItemType Directory -Path 'runtime/tools/frame-bridge/probe' -Force | Out-Null
    & "$vcRoot/bin/Hostx64/x86/cl.exe" /nologo /utf-8 /EHsc /std:c++17 /MT /LD /W4 /Brepro tools/native_d3d9_flip_bridge.cpp /Fe:runtime/tools/frame-bridge/probe/d3d9.dll /Fo:runtime/tools/frame-bridge/frame-bridge.obj /link /Brepro /DEF:tools/native_d3d9_flip_bridge.def d3d11.lib dxgi.lib user32.lib
    if ($LASTEXITCODE -ne 0) { throw 'Frame bridge build failed' }
    if ($Package) {
        # 模块先验证固定SHA256；编译器或源码变化需重新验收并显式更新版本，不能静默信任新DLL。
        & .venv/Scripts/python.exe -m tools.native_frame_bridge --package runtime/tools/frame-bridge/probe/d3d9.dll
        if ($LASTEXITCODE -ne 0) { throw 'Frame bridge package verification failed' }
    }
} finally {
    $env:INCLUDE = $priorInclude
    $env:LIB = $priorLib
    Pop-Location
}
