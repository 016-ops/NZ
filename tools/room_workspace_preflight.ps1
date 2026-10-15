<#
只读检查已标记的单节点研究目录和 Windows 资源。
不启动游戏、安装组件、访问数据库、修改服务或防火墙，也不读取进程命令行和用户文档。
输出只是部署前环境证据；目录存在、SSH 可达和合成 API 可用均不代表原生房间实例就绪。
#>
param([string]$Root = 'C:\NZResearch')
$ErrorActionPreference = 'Stop'
$researchRoot = [System.IO.Path]::GetFullPath($Root)
if ($researchRoot -ne 'C:\NZResearch') { throw 'Only the dedicated research root is accepted' }
if (-not (Test-Path -LiteralPath $researchRoot -PathType Container)) { throw 'Research root does not exist' }
if ((Get-Item -LiteralPath $researchRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'Research root must not be a reparse point'
}
$markerPath = Join-Path $researchRoot '.nz-research-owned.json'
$marker = Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($marker.project -ne 'nz-revival' -or $marker.purpose -ne 'ROOM_NODE_RESEARCH_ONLY' -or $marker.version -ne 1) {
    throw 'Expected research ownership marker is absent'
}
# 只检查预先约定的子目录，不递归扫描磁盘，也不检查其他应用的配置或私有文件。
$directories = [ordered]@{}
foreach ($name in @('incoming', 'releases', 'runtime', 'evidence', 'client-workspaces')) {
    $path = Join-Path $researchRoot $name
    $exists = Test-Path -LiteralPath $path -PathType Container
    $directories[$name] = [ordered]@{
        exists = $exists
        reparse_point = $(if ($exists) { [bool]((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) } else { $false })
    }
}
$os = Get-CimInstance Win32_OperatingSystem
$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$python = Get-Command python -ErrorAction SilentlyContinue
$report = [ordered]@{
    kind = 'ROOM_WORKSPACE_PREFLIGHT_ONLY'
    native_instance_started = $false
    native_node_ready = $false
    service_changes = 0
    firewall_changes = 0
    credentials_read = $false
    root = $researchRoot
    directories = $directories
    os_version = $os.Version
    os_build = $os.BuildNumber
    operating_system_64bit = [Environment]::Is64BitOperatingSystem
    process_64bit = [Environment]::Is64BitProcess
    memory_total_bytes = [long]$os.TotalVisibleMemorySize * 1024
    memory_free_bytes = [long]$os.FreePhysicalMemory * 1024
    disk_free_bytes = [long]$disk.FreeSpace
    # WindowsApps 的执行别名不能证明 Python 已安装；不执行该入口，避免触发商店或其他程序。
    python_command_present = [bool]$python
    python_windows_app_alias = [bool]($python -and $python.Source -like '*WindowsApps*')
}
$report | ConvertTo-Json -Depth 5 -Compress
