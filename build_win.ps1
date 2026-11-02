# 在 Windows 上打包安装包
# 前置：装 Inno Setup 6（winget install JRSoftware.InnoSetup）
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== 1/3 准备自带运行时 =="
if (-not (Test-Path "build\runtime\python.exe")) {
    Write-Host "缺 build\runtime：请先把 embeddable Python 解压到 build\runtime 并装好依赖"
    exit 1
}

Write-Host "== 2/3 暂存安装文件 =="
$src = "build\installer-src"
if (Test-Path $src) { Remove-Item $src -Recurse -Force }
New-Item $src -ItemType Directory | Out-Null
$skip = @(".git",".venv","__pycache__","profiles","generated","build","dist","docs",".shot","tests",".github")
Get-ChildItem -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring((Get-Location).Path.Length + 1)
    $parts = $rel -split "\\"
    if ($parts | Where-Object { $skip -contains $_ }) { return }
    if ($_.Name -in @("installer.py","payload.zip","run.log","sessions.json","desktop.log","talos.iss")) { return }
    $dst = Join-Path $src $rel
    New-Item (Split-Path $dst -Parent) -ItemType Directory -Force | Out-Null
    Copy-Item $_.FullName $dst
}
Copy-Item "build\runtime" (Join-Path $src "runtime") -Recurse -Force

Write-Host "== 3/3 编译 =="
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "installer\talos.iss"
Write-Host "完成：dist\Talos-Studio-Setup.exe"
