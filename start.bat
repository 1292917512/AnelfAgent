@echo off
chcp 65001 >nul
title AnelfAgent

set "ROOT=%~dp0"
cd /d "%ROOT%"

echo.
echo  ┌─────────────────────────────────────┐
echo  │          AnelfAgent                  │
echo  └─────────────────────────────────────┘
echo.

:: 检测 uv（优先） 或 python
where uv >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "RUN_CMD=uv run python"
    for /f "tokens=*" %%v in ('uv --version 2^>^&1') do echo  [运行器] %%v

    echo  [环境]   正在同步 Python 依赖...
    uv sync --quiet
    if %ERRORLEVEL% EQU 0 (
        echo  [环境]   Python 依赖已就绪
    ) else (
        echo  [警告]   uv sync 失败，将使用当前环境继续
    )
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo  [错误] 未找到 uv 或 python，请先安装运行环境
        echo         uv 安装: https://github.com/astral-sh/uv
        pause
        exit /b 1
    )
    set "RUN_CMD=python"
    for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo  [运行器] %%v
)

:: 同步前端依赖
where npm >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    if exist "%ROOT%web\frontend\package.json" (
        echo  [环境]   正在同步前端依赖...
        npm install --prefix "%ROOT%web\frontend" --silent >nul 2>&1
        if %ERRORLEVEL% EQU 0 (
            echo  [环境]   前端依赖已就绪
        ) else (
            echo  [警告]   npm install 失败，前端功能可能异常
        )
    )
)

echo  [目录] %ROOT%
echo.
echo  WebUI 地址: http://127.0.0.1:8092/webui/
echo  按 Ctrl+C 停止服务
echo  ─────────────────────────────────────────
echo.

%RUN_CMD% launch.py %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  [!] 服务异常退出，错误码: %ERRORLEVEL%
    pause
)
