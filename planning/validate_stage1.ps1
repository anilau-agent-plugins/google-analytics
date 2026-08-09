$ErrorActionPreference = "Stop"

$planning = $PSScriptRoot
$root = Split-Path -Parent $planning

& (Join-Path $planning "run_python_limited.ps1") (Join-Path $planning "validate_stage1.py")
if ($LASTEXITCODE -ne 0) {
    throw "Stage 1 structural and semantic validation failed."
}

$contracts = Join-Path $planning "contracts"
$schemaFiles = Get-ChildItem -LiteralPath $contracts -Filter "*.schema.json" -File
$results = @()

foreach ($schemaFile in $schemaFiles) {
    $name = $schemaFile.Name -replace "\.schema\.json$", ""
    $validPath = Join-Path $contracts "fixtures\valid\$name.json"
    $invalidPath = Join-Path $contracts "fixtures\invalid\$name.json"

    $validAccepted = (Get-Content -LiteralPath $validPath -Raw) | Test-Json -SchemaFile $schemaFile.FullName
    $invalidAccepted = (Get-Content -LiteralPath $invalidPath -Raw) | Test-Json -SchemaFile $schemaFile.FullName -ErrorAction SilentlyContinue

    $results += [ordered]@{
        contract = $name
        validAccepted = [bool]$validAccepted
        invalidRejected = -not [bool]$invalidAccepted
    }
}

$failed = @($results | Where-Object { -not $_.validAccepted -or -not $_.invalidRejected })
[ordered]@{
    ok = $failed.Count -eq 0
    validator = "PowerShell Test-Json Draft 2020-12"
    contracts = $results
} | ConvertTo-Json -Depth 5

if ($failed.Count -gt 0) {
    throw "Stage 1 JSON Schema fixture validation failed."
}
