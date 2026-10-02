$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $pythonExecutable = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonExecutable)) { throw 'Create .venv and pip install -e ".[dev]" first.' }
    & $pythonExecutable -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed; packaging stopped.' }
    & $pythonExecutable -m PyInstaller --noconfirm CodexDispatcher.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
    Write-Output 'Built dist/CodexDispatcher/CodexDispatcher.exe. Distribute the whole directory.'
} finally { Pop-Location }
