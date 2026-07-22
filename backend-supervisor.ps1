$ErrorActionPreference = "Continue"

$root = "C:\Case Capital\stock-intel"
$backend = Join-Path $root "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$logs = Join-Path $root "logs"
$supervisorLog = Join-Path $logs "backend-supervisor.log"
$backendOut = Join-Path $logs "supervised-backend.out.log"
$backendErr = Join-Path $logs "supervised-backend.err.log"

New-Item -ItemType Directory -Path $logs -Force | Out-Null

function Write-SupervisorLog {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    Add-Content -LiteralPath $supervisorLog -Value "[$stamp] $Message" -Encoding UTF8
}

function Test-BackendHealthy {
    try {
        $status = Invoke-RestMethod "http://127.0.0.1:8001/api/status" -TimeoutSec 5
        return [bool]($status.bot.online -and $status.bot.db_available)
    } catch {
        Write-SupervisorLog "Health check failed: $($_.Exception.Message)"
        return $false
    }
}

function Get-UvicornProcesses {
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "python.exe" -and $_.CommandLine -match "uvicorn server:app"
    }
}

function Start-Backend {
    if (!(Test-Path $python)) {
        Write-SupervisorLog "ERROR: backend Python not found at $python"
        return
    }

    $existing = Get-UvicornProcesses | Select-Object -First 1
    if ($existing) {
        Write-SupervisorLog "Backend process already exists pid=$($existing.ProcessId); waiting for health."
        return
    }

    Write-SupervisorLog "Starting backend from $backend"
    $proc = Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8001") `
        -WorkingDirectory $backend `
        -RedirectStandardOutput $backendOut `
        -RedirectStandardError $backendErr `
        -PassThru `
        -WindowStyle Hidden
    Write-SupervisorLog "Start requested pid=$($proc.Id)"
}

Write-SupervisorLog "Supervisor started. User=$env:USERNAME Root=$root"

while ($true) {
    if (!(Test-BackendHealthy)) {
        Start-Backend
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 2
            if (Test-BackendHealthy) {
                Write-SupervisorLog "Backend healthy."
                break
            }
        }
    }
    Start-Sleep -Seconds 15
}
