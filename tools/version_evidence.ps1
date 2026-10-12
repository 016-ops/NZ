param([string]$ClientRoot = './nz', [string]$Output = './evidence/private/version-evidence.json')
$ErrorActionPreference = 'Stop'
$clientPath = (Resolve-Path -LiteralPath $ClientRoot).Path.TrimEnd([char]92)
$outputPath = [IO.Path]::GetFullPath($Output)
if ($outputPath.StartsWith($clientPath + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Output must be outside client.' }
$paths = @('TCLS/version.ini','TCLS/Client.exe','TCLS/Tenio/TCLS.dll','Binaries/Win32/TGame.exe','Binaries/Win64/TGame.exe')
$evidence = foreach ($relative in $paths) {
    $path = Join-Path $clientPath $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { [pscustomobject]@{Path=$relative;Status='MISSING'}; continue }
    $file = Get-Item -LiteralPath $path
    $signature = if ($file.Extension -in '.exe','.dll') { Get-AuthenticodeSignature -LiteralPath $path } else { $null }
    [pscustomobject]@{
        Path=$relative; Length=$file.Length; SHA256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        FileVersion=$file.VersionInfo.FileVersion; ProductVersion=$file.VersionInfo.ProductVersion
        SignatureStatus=if ($signature) { [string]$signature.Status } else { 'NOT_APPLICABLE' }
        Signer=if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { $null }
        VersionConfig=if ($relative -eq 'TCLS/version.ini') { Get-Content -LiteralPath $path -Raw } else { $null }
    }
}
New-Item -ItemType Directory -Path (Split-Path -Parent $outputPath) -Force | Out-Null
$evidence | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $outputPath -Encoding utf8
Write-Output $outputPath
