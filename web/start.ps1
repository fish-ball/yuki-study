# 启动中考辅导管理台（Windows）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $root) { $root = "D:\yuki-study" }
Set-Location $root

$py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) {
  $py = "python"
}

$npm = Join-Path $env:ProgramFiles "nodejs\npm.cmd"
$frontend = Join-Path $root "web\frontend"
if (Test-Path $npm) {
  Push-Location $frontend
  if (-not (Test-Path "node_modules")) {
    Write-Host "安装前端依赖..."
    & $npm install
    if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
  }
  Write-Host "构建前端 dist..."
  & $npm run build
  if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
  Pop-Location
}

Write-Host "启动管理台: http://127.0.0.1:8787"
& $py -m uvicorn web.backend.main:app --host 127.0.0.1 --port 8787
