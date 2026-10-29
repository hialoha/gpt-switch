$ErrorActionPreference = 'Stop'
$bundled = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
$python = $null
if (Test-Path -LiteralPath $bundled) { $python = $bundled }
if (-not $python) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found -and $found.Source -notmatch 'WindowsApps') { $python = $found.Source }
}
if (-not $python) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show('Please install Python 3.11+ with Tcl/Tk, then launch again.', 'GPT Switch') | Out-Null
    exit 1
}
if ($args.Count -gt 0) {
    & $python (Join-Path $PSScriptRoot 'switch.py') @args
    exit $LASTEXITCODE
}
$guiPython = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $guiPython)) { $guiPython = $python }
Start-Process -FilePath $guiPython -ArgumentList @(('"' + (Join-Path $PSScriptRoot 'app.py') + '"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
