$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $pythonExecutable = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonExecutable)) { throw 'Create .venv and pip install -e ".[dev]" first.' }
    & $pythonExecutable -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed; packaging stopped.' }
    $appVersion = & $pythonExecutable -c 'from codex_dispatcher import __version__; print(__version__)'
    $outputDirectory = Join-Path 'dist' "v$appVersion"
    & $pythonExecutable -m PyInstaller --noconfirm --distpath $outputDirectory CodexDispatcher.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
    & $pythonExecutable scripts/package_release.py --folder (Join-Path $outputDirectory 'CodexDispatcher')
    if ($LASTEXITCODE -ne 0) { throw 'ZIP packaging failed.' }
    Write-Output "Built $outputDirectory/CodexDispatcher/CodexDispatcher.exe. Distribute the whole directory."
} finally { Pop-Location }
