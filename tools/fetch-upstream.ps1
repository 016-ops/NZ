$ErrorActionPreference = 'Stop'
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $upstreamRoot = 'upstream/af-emulator'
    $upstreamUrl = 'https://github.com/armangido/af-emulator.git'
    $upstreamCommit = '4bbabab50ac6bfdbcd1f8e88bc6ea804e5978495'
    if (Test-Path -LiteralPath $upstreamRoot) {
        $currentCommit = & git -C $upstreamRoot rev-parse HEAD
        if ($LASTEXITCODE -ne 0 -or $currentCommit -ne $upstreamCommit) { throw '现有目录不是预期的固定提交，不进行覆盖。' }
        $remoteUrl = & git -C $upstreamRoot remote get-url origin
        if ($LASTEXITCODE -ne 0 -or $remoteUrl -ne $upstreamUrl) { throw '现有源码来源不匹配。' }
    } else {
        & git clone --no-checkout $upstreamUrl $upstreamRoot
        if ($LASTEXITCODE -ne 0) { throw '获取上游失败。' }
        & git -C $upstreamRoot checkout --detach $upstreamCommit
        if ($LASTEXITCODE -ne 0) { throw '固定上游提交失败。' }
    }
    Write-Output "固定上游：$upstreamCommit。未安装依赖、未运行启动器、未修改客户端。"
} finally { Pop-Location }
