# Fast lint + test (~3s after .venv exists). Full crawl deps: pip install -r src/requirements-dev.txt
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = Join-Path $PWD ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error "No .venv. Run: python -m venv .venv; .\.venv\Scripts\pip install -r src/requirements-ci.txt"
}

& $py -m ruff check .
& $py -m ruff format --check .
& $py -m pytest -q --durations=5
Write-Host "OK: lint + test finished."
