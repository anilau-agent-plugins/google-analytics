$ErrorActionPreference = 'Stop'

$entrypoint = Join-Path $PSScriptRoot 'google_analytics.py'
$probe = 'import sys; ok=sys.implementation.name.encode().hex()==hex(0x63707974686f6e)[2:] and (3,10)<=sys.version_info[:2]<(3,14); raise SystemExit(0 if ok else 1)'
$candidates = @()

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py -and $py.Source -notmatch '(?i)[\\/]WindowsApps[\\/]') {
    foreach ($minor in 13,12,11,10) {
        $candidates += [pscustomobject]@{ Executable = $py.Source; Prefix = @("-3.$minor") }
    }
}
foreach ($name in 'python3','python') {
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notmatch '(?i)[\\/]WindowsApps[\\/]') {
        $candidates += [pscustomobject]@{ Executable = $command.Source; Prefix = @() }
    }
}

foreach ($candidate in $candidates) {
    $executable = $candidate.Executable
    $prefix = @($candidate.Prefix)
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    & $executable @prefix -I -B -X utf8 -c $probe *> $null
    $probeExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousPreference
    if ($probeExitCode -eq 0) {
        & $executable @prefix -I -B -X utf8 $entrypoint @args
        exit $LASTEXITCODE
    }
}

[Console]::Out.WriteLine('{"schemaVersion":1,"cliVersion":"0.6.0","ok":false,"command":"bootstrap","status":"error","data":{},"warnings":[],"errors":[{"code":"PYTHON_RUNTIME_UNAVAILABLE","message":"Supported CPython 3.10-3.13 was not found.","retryable":false,"details":{},"nextAction":"Install a standard 64-bit CPython from https://www.python.org/downloads/windows/ after explicit consent, then run again."}]}')
exit 2
