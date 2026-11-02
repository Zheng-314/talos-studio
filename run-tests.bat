@echo off
chcp 65001 >nul
cd /d %~dp0
if exist "runtime\python.exe" (
    runtime\python.exe -m pytest tests -q
) else (
    ..\talos-stream\.venv\Scripts\python.exe -m pytest tests -q
)
pause
