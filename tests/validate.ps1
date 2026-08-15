$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $root
try {
    & (Join-Path $PSScriptRoot "run_python_limited.ps1") -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & (Join-Path $root "scripts\google-analytics.ps1") version --json
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & (Join-Path $root "scripts\google-analytics.ps1") runtime detect --json
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & (Join-Path $root "scripts\google-analytics.ps1") runtime install-guide --json
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
