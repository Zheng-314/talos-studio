@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Talos Studio

rem 优先用自带运行时，用户机器不需要装 Python
set "PY=%~dp0runtime\python.exe"
set "PYW=%~dp0runtime\pythonw.exe"
set "ENGINE=%~dp0talos-stream"
if not exist "%ENGINE%\server.py" set "ENGINE=%~dp0..\talos-stream"

if exist "%PY%" goto ready

rem 没有自带运行时，退回本机 Python 建环境
set "VENV=%ENGINE%\.venv"
set "PY=%VENV%\Scripts\python.exe"
set "PYW=%VENV%\Scripts\pythonw.exe"
if exist "%PY%" goto ready

echo [1/2] 正在准备运行环境（仅首次，2-4 分钟）
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m venv "%VENV%" || goto nopython
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        python -m venv "%VENV%" || goto nopython
    ) else (
        goto nopython
    )
)
echo [2/2] 安装依赖
"%PY%" -m pip install --upgrade pip -q -i https://pypi.tuna.tsinghua.edu.cn/simple
"%PY%" -m pip install -q -r "%ENGINE%\requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
"%PY%" -m pip install -q -r "%~dp0requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple

:ready
rem 有 pythonw 就不弹黑框，直接开独立窗口
rem 自带运行时用 Edge 应用窗口，避免 .NET WebView2 初始化失败
set "TALOS_WEBVIEW=edge"

if exist "%PYW%" (
    start "" "%PYW%" desktop.py
    goto :eof
)
"%PY%" desktop.py
goto :eof

:nopython
echo.
echo   没找到运行环境，也没有自带的 Python。
echo   请到 https://www.python.org/downloads/windows/ 安装，
echo   安装时勾选 Add python.exe to PATH。
echo.
pause
