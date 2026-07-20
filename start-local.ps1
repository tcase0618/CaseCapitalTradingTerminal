$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendEnv = Join-Path $root "backend\.env"
$backendExample = Join-Path $root "backend\.env.example"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not installed or is not available on PATH. Install Docker Desktop, then rerun this script."
}

if (-not (Test-Path -LiteralPath $backendEnv)) {
    Copy-Item -LiteralPath $backendExample -Destination $backendEnv
    Write-Host "Created backend\.env from backend\.env.example."
    Write-Host "Add ANTHROPIC_API_KEY, Telegram, or Alpaca keys there when you want those features."
}

Push-Location $root
try {
    docker compose up --build
}
finally {
    Pop-Location
}
