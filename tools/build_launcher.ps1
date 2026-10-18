# 将同仓 React 页面与 Tauri 壳构建为当前机器入口；依赖目录可通过参数选择，不修改全局环境。
param([string]$ToolRoot, [switch]$SkipFrontend)
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
if (-not $ToolRoot) { $ToolRoot = Join-Path $repositoryRoot 'runtime/tools/launcher-build' }
$ToolRoot = (Resolve-Path -LiteralPath $ToolRoot).Path
$env:CARGO_HOME = Join-Path $ToolRoot 'cargo'
$env:RUSTUP_HOME = Join-Path $ToolRoot 'rustup'
$env:RUSTUP_TOOLCHAIN = '1.99.1-x86_64-pc-windows-msvc'
$env:CARGO_HTTP_MULTIPLEXING = 'false' # 当前代理链的 HTTP/2 多路下载会停滞，保留 TLS 校验并使用普通连接。
$env:Path = (Join-Path $env:CARGO_HOME 'bin') + ';' + $env:Path
# VS 批处理输出在中文工作区路径下发生代码页损坏；直接用已安装目录配置本进程，避免乱码路径。
$vcRoot = Get-ChildItem -LiteralPath (Join-Path $ToolRoot 'msvc/VC/Tools/MSVC') -Directory | Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName
$sdkRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10'
$sdkVersion = Get-ChildItem -LiteralPath (Join-Path $sdkRoot 'Lib') -Directory | Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty Name
$vcBin = Join-Path $vcRoot 'bin/Hostx64/x64'
$env:VCToolsInstallDir = $vcRoot + '\'
$env:WindowsSdkDir = $sdkRoot + '\'
$env:WindowsSDKVersion = $sdkVersion + '\'
$env:INCLUDE = (@((Join-Path $vcRoot 'include')) + @('ucrt','shared','um','winrt','cppwinrt' | ForEach-Object { Join-Path $sdkRoot "Include/$sdkVersion/$_" })) -join ';'
$env:LIB = @((Join-Path $vcRoot 'lib/x64'), (Join-Path $sdkRoot "Lib/$sdkVersion/ucrt/x64"), (Join-Path $sdkRoot "Lib/$sdkVersion/um/x64")) -join ';'
$env:Path = $vcBin + ';' + (Join-Path $sdkRoot "bin/$sdkVersion/x64") + ';' + $env:Path
$env:CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER = Join-Path $vcBin 'link.exe'
Push-Location -LiteralPath $repositoryRoot
try {
    # 显示桥的x86编译环境在函数内恢复，避免污染后续Tauri x64链接。
    & tools/build_frame_bridge.ps1 -Package -ToolRoot $ToolRoot
    # 固定官方图形兼容包；只入工具缓存，运行时才由恢复守卫安装到工作副本。
    & .venv/Scripts/python.exe -m tools.prepare_graphics_compat
    if ($LASTEXITCODE -ne 0) { throw 'Graphics compatibility package verification failed' }
    if (-not $SkipFrontend) {
        & npm.cmd --prefix admin run build
        if ($LASTEXITCODE -ne 0) { throw 'React build failed' }
    }
    & cargo test --manifest-path launcher/Cargo.toml --release --locked
    if ($LASTEXITCODE -ne 0) { throw 'Tauri tests failed' }
    & cargo build --manifest-path launcher/Cargo.toml --release --locked
    if ($LASTEXITCODE -ne 0) { throw 'Tauri build failed' }
    $destination = Join-Path $repositoryRoot 'launcher/bin'
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $repositoryRoot 'launcher/target/release/nz-launcher.exe') -Destination (Join-Path $destination 'NZLauncher.exe')
    Get-FileHash -LiteralPath (Join-Path $destination 'NZLauncher.exe') -Algorithm SHA256 | Select-Object Hash,Path
} finally { Pop-Location }
