param([ValidateSet('Init','Start','Stop','Status')][string]$Action = 'Status', [int]$Port = 3307,
      [string]$MySQLBin = 'C:\Program Files\MySQL\MySQL Server 9.0\bin')
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$instanceRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'runtime/mysql'))
if (-not $instanceRoot.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar)) { throw 'Unsafe instance directory' }
$serverExe = Join-Path $MySQLBin 'mysqld.exe'
$pythonExe = Join-Path $projectRoot '.venv/Scripts/python.exe'
$dataPath = Join-Path $instanceRoot 'data'
$configPath = Join-Path $instanceRoot 'my.ini'
if ($Action -eq 'Init') {
    if (Test-Path -LiteralPath $dataPath) { throw 'Instance already exists; initialization will not overwrite it.' }
    if (Test-Path -LiteralPath (Join-Path $projectRoot '.env.local')) { throw '.env.local already exists; initialization will not overwrite it.' }
    New-Item -ItemType Directory -Path $instanceRoot -Force | Out-Null
    $dataConfig = $dataPath.Replace('\','/')
    $baseConfig = (Split-Path $MySQLBin -Parent).Replace('\','/')
    @"
[mysqld]
basedir=$baseConfig
datadir=$dataConfig
port=$Port
bind-address=127.0.0.1
mysqlx=OFF
character-set-server=utf8mb4
collation-server=utf8mb4_0900_bin
default-storage-engine=InnoDB
skip-log-bin
log-error=$($instanceRoot.Replace('\','/'))/mysql-error.log
pid-file=$($instanceRoot.Replace('\','/'))/mysql.pid
"@ | Set-Content -LiteralPath $configPath -Encoding utf8NoBOM
    & $serverExe "--defaults-file=$configPath" --initialize-insecure
    if ($LASTEXITCODE -ne 0) { throw 'MySQL initialization failed; inspect runtime/mysql/mysql-error.log.' }
    Start-Process -FilePath $serverExe -ArgumentList @("--defaults-file=`"$configPath`"") -WindowStyle Hidden
    & $pythonExe (Join-Path $PSScriptRoot 'mysql_credentials.py') init --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Credential initialization failed; do not use the instance until credentials are configured.' }
} elseif ($Action -eq 'Start') {
    if (-not (Test-Path -LiteralPath $configPath)) { throw 'Run Init first.' }
    if (Test-Path -LiteralPath (Join-Path $instanceRoot 'mysql.pid')) {
        & $pythonExe (Join-Path $PSScriptRoot 'mysql_credentials.py') status
        if ($LASTEXITCODE -eq 0) { exit 0 }
    }
    Start-Process -FilePath $serverExe -ArgumentList @("--defaults-file=`"$configPath`"") -WindowStyle Hidden
    & $pythonExe (Join-Path $PSScriptRoot 'mysql_credentials.py') wait
    if ($LASTEXITCODE -ne 0) { throw 'MySQL start failed.' }
} else {
    & $pythonExe (Join-Path $PSScriptRoot 'mysql_credentials.py') $Action.ToLowerInvariant()
    if ($LASTEXITCODE -ne 0) { throw "MySQL $Action failed." }
}
