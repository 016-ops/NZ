# 在具有标准Rust MSVC及Python构建环境的Windows执行，不读取研究DEV凭据或部署服务。
param(
    [string]$ApiBaseUrl = 'http://127.0.0.1:18765',
    [int]$LoopbackHostPort = 0,
    [string]$OutputDirectory = 'launcher/bin/NZLauncher-DEV',
    [switch]$SkipFrontend,
    [switch]$Plan
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $repositoryRoot
try {
    # Python来自操作者激活的构建环境；此解释器不会成为发行包的外部依赖。
    $arguments = @('-m', 'tools.package_launcher', '--api-base-url', $ApiBaseUrl, '--output', $OutputDirectory)
    if ($LoopbackHostPort -gt 0) { $arguments += @('--loopback-host-port', "$LoopbackHostPort") }
    if ($SkipFrontend) { $arguments += '--skip-frontend' }
    if ($Plan) { $arguments += '--plan' }
    & python @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Launcher package build failed; inspect the build error.' }
} finally { Pop-Location }
