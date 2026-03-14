@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title MiniMax MCP 环境安装

echo ==========================================
echo  MiniMax Coding Plan MCP 环境安装脚本
echo ==========================================
echo.

:: 检测 uv
where uv >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] 未找到 uv，请先安装: https://github.com/astral-sh/uv
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('uv --version 2^>^&1') do echo [OK] uv 已就绪: %%v

:: 安装依赖（含 minimax-coding-plan-mcp）
echo.
echo [*] Running uv sync...
pushd "%~dp0.."
uv sync
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] uv sync failed. Check network or pyproject.toml.
    popd
    pause
    exit /b 1
)
popd

echo.
echo [OK] Done. minimax-coding-plan-mcp is ready.
echo      Make sure "enabled": true in config/mcp_servers.json.
echo.
pause
