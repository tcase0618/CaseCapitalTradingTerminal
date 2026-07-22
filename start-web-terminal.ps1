$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$logs = Join-Path $root "logs"
$frontendLog = Join-Path $logs "web-frontend.out.log"
$frontendErr = Join-Path $logs "web-frontend.err.log"
$backendLog = Join-Path $logs "web-backend.out.log"
$backendErr = Join-Path $logs "web-backend.err.log"

New-Item -ItemType Directory -Path $logs -Force | Out-Null

function Test-Backend {
    try {
        $status = Invoke-RestMethod "http://127.0.0.1:8001/api/status" -TimeoutSec 5
        return [bool]$status.bot.online
    } catch {
        return $false
    }
}

if (!(Test-Path $python)) {
    throw "Backend Python was not found at $python"
}

if (!(Test-Backend)) {
    Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8001") `
        -WorkingDirectory $backend `
        -RedirectStandardOutput $backendLog `
        -RedirectStandardError $backendErr `
        -WindowStyle Hidden

    $ready = $false
    for ($i = 0; $i -lt 75; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Backend) {
            $ready = $true
            break
        }
    }

    if (!$ready) {
        throw "Backend did not answer on 127.0.0.1:8001 after 75 seconds."
    }
}

$env:REACT_APP_BACKEND_URL = "http://127.0.0.1:8001"
$existingFrontend = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (!$existingFrontend) {
    Start-Process -FilePath "cmd.exe" `
        -ArgumentList @("/d", "/s", "/c", "npm start") `
        -WorkingDirectory $frontend `
        -RedirectStandardOutput $frontendLog `
        -RedirectStandardError $frontendErr `
        -WindowStyle Hidden
}

Start-Process "http://localhost:3000/scanner"
