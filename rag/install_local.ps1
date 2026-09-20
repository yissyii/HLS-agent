param(
    [string]$InstallRoot = 'F:/Workspace/hls-rag',
    [string]$BasePython = 'C:/Users/24229/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$installPath = [IO.Path]::GetFullPath($InstallRoot)
New-Item -ItemType Directory -Path $installPath -Force | Out-Null
$env:PIP_CACHE_DIR = Join-Path $installPath 'pip-cache'
$python = Join-Path $installPath 'venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & $BasePython -m venv (Join-Path $installPath 'venv')
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}
& $python -m pip install 'torch==2.8.0' --index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -ne 0) { throw 'CPU PyTorch installation failed' }
& $python -m pip install -r (Join-Path $PSScriptRoot 'requirements.lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'RAG dependency installation failed' }
& $python -m pip freeze | Set-Content -LiteralPath (Join-Path $installPath 'requirements.lock.txt') -Encoding utf8
& $python -X utf8 -B (Join-Path $PSScriptRoot 'download_model.py') --output (Join-Path $installPath 'models/Qwen3-Embedding-0.6B') --revision 97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3 --range-download
if ($LASTEXITCODE -ne 0) { throw 'Model download failed' }
