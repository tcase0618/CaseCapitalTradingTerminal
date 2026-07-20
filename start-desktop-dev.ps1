$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$python = Join-Path $backend ".venv\Scripts\python.exe"

if (!(Test-Path $python)) {
    throw "Backend venv not found at $python"
}

$existing = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (!$existing) {
    Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001") `
        -WorkingDirectory $backend `
        -WindowStyle Hidden
    Start-Sleep -Seconds 4
}

Push-Location $frontend
try {
    npm run desktop:dev
}
finally {
    Pop-Location
}
