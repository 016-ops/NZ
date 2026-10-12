param([string]$PythonPath, [switch]$TestProfile, [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
if (-not $PythonPath) {
    $candidate = Get-Command python -ErrorAction SilentlyContinue
    if ($candidate) { $PythonPath = $candidate.Source }
    else { throw '请用 -PythonPath 指定 Python 3.12+ 解释器。' }
}
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $database = if ($TestProfile) { 'runtime/test.db' } else { 'runtime/current.db' }
    $pythonArguments = @('-m','revival','--db',$database)
    if ($TestProfile) { $pythonArguments += '--test-profile' }
    $pythonArguments += @('serve','--port',[string]$Port)
    & $PythonPath @pythonArguments
    if ($LASTEXITCODE -ne 0) { throw "本地服务退出，状态 $LASTEXITCODE" }
} finally { Pop-Location }
