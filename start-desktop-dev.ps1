$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$vsDevCmd = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat"

if (!(Test-Path $python)) {
    throw "Backend venv not found at $python"
}

if (!(Test-Path $vsDevCmd)) {
    throw "Visual Studio Build Tools not found. Install MSVC Build Tools, then rerun this script."
}

$existing = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (!$existing) {
    Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001") `
        -WorkingDirectory $backend `
        -WindowStyle Hidden
    Start-Sleep -Seconds 4
}

$command = @"
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%" &&
set "RUSTUP_TOOLCHAIN=stable-x86_64-pc-windows-msvc" &&
call "$vsDevCmd" -arch=x64 -host_arch=arm64 &&
cd /d "$frontend" &&
npm run desktop:dev
"@ -replace "(`r?`n)+", " "

cmd /c $command
