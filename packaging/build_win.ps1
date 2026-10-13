# Build dist\vox.exe on Windows. Needs uv and the WebView2 runtime
# (built into Windows 11 and current Windows 10).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

uv sync
uv run python packaging/make_icons.py
uv run pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/vox.spec
Write-Host "Built dist\vox.exe"
