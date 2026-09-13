$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
Push-Location $PSScriptRoot
try { & python -m evaluation.single_task @args --repair-attempts 2; $code = $LASTEXITCODE }
finally { Pop-Location }
exit $code
