$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
New-Item -ItemType Directory -Path '.cache\tmp' -Force | Out-Null
$env:TEMP = (Resolve-Path '.cache\tmp').Path
$env:TMP = $env:TEMP
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    py -3.13 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Khong tao duoc Python venv' }
}
& '.\.venv\Scripts\python.exe' -m pip install --cache-dir '.cache\pip' -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Cai thu vien that bai' }
if (-not (Test-Path -LiteralPath '.env')) {
    $tokenBytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($tokenBytes)
    $token = [Convert]::ToHexString($tokenBytes).ToLowerInvariant()
    $content = (Get-Content -LiteralPath '.env.example' -Raw).Replace('UTC_ADMIN_TOKEN=', 'UTC_ADMIN_TOKEN=' + $token)
    Set-Content -LiteralPath '.env' -Value $content -Encoding utf8
}
Write-Host 'Da cai thu vien. Chay install-models.ps1 de tai model local.'
