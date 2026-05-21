<#
.SYNOPSIS
    Build a standalone CPA binary using PyInstaller on Windows.
.DESCRIPTION
    Builds a one-file CPA executable from cpa.spec.
.PARAMETER PipInstall
    Run pip install -e . before building.
.EXAMPLE
    .\build.ps1
    .\build.ps1 -PipInstall
#>

param(
    [switch]$PipInstall
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# Check for venv
if (-not $env:VIRTUAL_ENV) {
    if (Test-Path ".venv") {
        & ".venv\Scripts\Activate.ps1"
    } elseif (Test-Path ".venv-bokeh") {
        & ".venv-bokeh\Scripts\Activate.ps1"
    } else {
        Write-Host "Creating virtual environment..."
        python -m venv .venv
        & ".venv\Scripts\Activate.ps1"
    }
}

# Optional pip install
if ($PipInstall) {
    Write-Host "Installing package..."
    pip install -e .
}

# Ensure PyInstaller is available
$pyinst = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (-not $pyinst) {
    Write-Host "Installing PyInstaller..."
    pip install pyinstaller
}

# Build
Write-Host "Building CPA standalone binary..."
pyinstaller --clean --onefile cpa.spec

Write-Host ""
Write-Host "Build complete! Binary at: dist\cpa.exe"
Write-Host ""
Write-Host "Verify with:"
Write-Host "  dist\cpa.exe --help"
Write-Host ""
