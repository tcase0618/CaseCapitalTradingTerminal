$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontend = Join-Path $root "frontend"

Push-Location $frontend
try {
    npm run desktop:build
}
finally {
    Pop-Location
}
