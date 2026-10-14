# 本地完整启动入口：数据库由独立脚本管理，维护进程随前台服务退出，不接管系统服务。
param([string]$PythonPath, [switch]$TestProfile, [int]$Port = 8765, [switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $PythonPath) { $PythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath)) { throw 'Install requirements.txt in .venv first, or specify -PythonPath.' }
Push-Location $projectRoot
$maintenanceProcess = $null
$previousProfile = $env:NZ_PROFILE
try {
    $env:NZ_PROFILE = if ($TestProfile) { 'synthetic' } else { 'current' }
    & (Join-Path $PSScriptRoot 'mysql-local.ps1') -Action Start
    if (-not $SkipBuild) {
        Push-Location (Join-Path $projectRoot 'admin')
        try {
            & npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
        } finally { Pop-Location }
    }
    & $PythonPath manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
    # Start-Process 使用隐藏窗口；日志位于忽略目录，PID 对象只用于清理本次启动的维护进程。
    $runtimePath = Join-Path $projectRoot 'runtime'
    New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
    $maintenanceProcess = Start-Process -FilePath $PythonPath -ArgumentList @('manage.py','maintain') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimePath 'maintain.log') -RedirectStandardError (Join-Path $runtimePath 'maintain-error.log')
    & $PythonPath manage.py serve --port $Port
    if ($LASTEXITCODE -ne 0) { throw "Local service exited with code $LASTEXITCODE" }
} finally {
    if ($maintenanceProcess -and -not $maintenanceProcess.HasExited) { Stop-Process -Id $maintenanceProcess.Id }
    $env:NZ_PROFILE = $previousProfile
    Pop-Location
}
