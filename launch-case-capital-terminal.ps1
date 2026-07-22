$ErrorActionPreference = "Stop"

$root = "C:\Case Capital\stock-intel"
$backend = Join-Path $root "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$installedExe = "C:\Users\tcase\AppData\Local\CaseCapitalTradingTerminal\case_capital_trading_terminal.exe"
$builtExe = Join-Path $root "frontend\src-tauri\target\x86_64-pc-windows-msvc\release\case_capital_trading_terminal.exe"
$logs = Join-Path $root "logs"
$launcherLog = Join-Path $logs "desktop-launcher.log"
$backendOut = Join-Path $logs "desktop-backend.out.log"
$backendErr = Join-Path $logs "desktop-backend.err.log"

New-Item -ItemType Directory -Path $logs -Force | Out-Null

function Write-LaunchLog {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    Add-Content -LiteralPath $launcherLog -Value "[$stamp] $Message" -Encoding UTF8
}

function Test-BackendPort {
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $iar = $client.BeginConnect("127.0.0.1", 8001, $null, $null)
        $connected = $iar.AsyncWaitHandle.WaitOne(750, $false)
        if ($connected) {
            $client.EndConnect($iar)
        }
        $client.Close()
        return $connected
    } catch {
        return $false
    }
}

function Test-BackendHttp {
    try {
        $status = Invoke-RestMethod "http://127.0.0.1:8001/api/status" -TimeoutSec 5
        return [bool]$status.bot.online
    } catch {
        Write-LaunchLog "HTTP status failed: $($_.Exception.Message)"
        return $false
    }
}

function Stop-UvicornBackend {
    $uvicorn = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "python.exe" -and $_.CommandLine -match "uvicorn server:app"
    }
    foreach ($proc in $uvicorn) {
        Write-LaunchLog "Stopping stale backend pid=$($proc.ProcessId)"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($uvicorn) {
        Start-Sleep -Seconds 2
    }
}

try {
    Write-LaunchLog "Launcher started. User=$env:USERNAME Root=$root"

    if (!(Test-Path $python)) {
        throw "Backend Python was not found at $python"
    }

    if (!(Test-BackendHttp)) {
        if (Test-BackendPort) {
            Write-LaunchLog "Port 8001 was open but HTTP status failed. Recycling uvicorn."
            Stop-UvicornBackend
        }

        Write-LaunchLog "Starting backend with $python"
        $proc = Start-Process -FilePath $python `
            -ArgumentList @("-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8001") `
            -WorkingDirectory $backend `
            -RedirectStandardOutput $backendOut `
            -RedirectStandardError $backendErr `
            -PassThru `
            -WindowStyle Hidden
        Write-LaunchLog "Backend start requested. pid=$($proc.Id)"

        $ready = $false
        for ($i = 0; $i -lt 75; $i++) {
            Start-Sleep -Seconds 1
            if (Test-BackendHttp) {
                $ready = $true
                Write-LaunchLog "Backend HTTP ready after $($i + 1)s."
                break
            }
        }

        if (!$ready) {
            $tail = ""
            if (Test-Path $backendErr) {
                $tail = (Get-Content -LiteralPath $backendErr -Tail 25 -ErrorAction SilentlyContinue) -join " "
            }
            throw "Backend did not answer on 127.0.0.1:8001 after 75 seconds. stderr tail: $tail"
        }
    } else {
        Write-LaunchLog "Backend already healthy."
    }

    $exe = if (Test-Path $installedExe) { $installedExe } else { $builtExe }
    if (!(Test-Path $exe)) {
        throw "Desktop executable was not found at $installedExe or $builtExe"
    }

    Write-LaunchLog "Opening desktop app: $exe"
    Start-Process -FilePath $exe
    Write-LaunchLog "Launcher complete."
} catch {
    Write-LaunchLog "FAILED: $($_.Exception.Message)"
    throw
}
