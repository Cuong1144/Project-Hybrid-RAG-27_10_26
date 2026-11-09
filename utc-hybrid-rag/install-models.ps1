$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& '.\.venv\Scripts\python.exe' -m utc_rag models
if ($LASTEXITCODE -ne 0) { throw 'Tai embedding/tokenizer that bai' }
if (-not (Test-Path -LiteralPath '.tools\ollama\ollama.exe')) {
    & '.\.venv\Scripts\python.exe' scripts\download_ollama.py
    if ($LASTEXITCODE -ne 0) { throw 'Tai Ollama that bai' }
}
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_TRANSFER_STREAMS = '24'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_MODELS = Join-Path $PSScriptRoot '.cache\ollama'
try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null }
catch {
    Start-Process -FilePath (Join-Path $PSScriptRoot '.tools\ollama\ollama.exe') -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot '.cache\ollama.stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot '.cache\ollama.stderr.log') | Out-Null
    Start-Sleep -Seconds 3
}
& '.\.tools\ollama\ollama.exe' pull 'hf.co/Qwen/Qwen3-4B-GGUF:Q4_K_M'
if ($LASTEXITCODE -ne 0) { throw 'Tai Qwen3 that bai' }
Write-Host 'Model local da san sang.'
