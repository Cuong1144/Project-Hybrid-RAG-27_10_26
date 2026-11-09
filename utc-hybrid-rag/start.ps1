param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_MODELS = Join-Path $PSScriptRoot '.cache\ollama'
New-Item -ItemType Directory -Path '.cache\tmp' -Force | Out-Null
$env:TEMP = (Resolve-Path '.cache\tmp').Path
$env:TMP = $env:TEMP
if (Test-Path -LiteralPath '.tools\ollama\ollama.exe') {
    try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null }
    catch { Start-Process -FilePath (Join-Path $PSScriptRoot '.tools\ollama\ollama.exe') -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot '.cache\ollama.stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot '.cache\ollama.stderr.log') | Out-Null }
}
$worker = Start-Process -FilePath (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -ArgumentList '-m','utc_rag','worker' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $PSScriptRoot '.cache\worker.stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot '.cache\worker.stderr.log')
Write-Host "Mo http://127.0.0.1:$Port ; Ctrl+C de dung API va worker."
try { & '.\.venv\Scripts\python.exe' -m utc_rag serve --port $Port }
finally { if (-not $worker.HasExited) { Stop-Process -Id $worker.Id } }
