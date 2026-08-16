# 启动中考辅导管理台（Windows）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $root) { $root = "D:\yuki-study" }
Set-Location $root

$py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) {
  $py = "python"
}

Write-Host "启动管理台: http://127.0.0.1:8787"
& $py -m uvicorn web.backend.main:app --host 127.0.0.1 --port 8787
