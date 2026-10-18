@echo off
rem wcauto 桥启动入口（Windows / uv 项目）
rem 首次部署：uv python install 3.12 ^&^& uv sync --frozen
rem 自检：run_bridge.bat --doctor
setlocal
cd /d %~dp0

where uv >nul 2>nul
if errorlevel 1 (
    echo [wcauto] 未找到 uv。请先安装：https://docs.astral.sh/uv/getting-started/installation/
    exit /b 1
)

if not exist wcauto_bridge.json (
    copy wcauto_bridge.example.json wcauto_bridge.json >nul
    echo [wcauto] 已生成 wcauto_bridge.json，请修改 token 后重跑本脚本
    exit /b 1
)

set "PYTHONUTF8=1"
uv run --frozen python -m wcauto_bridge --config wcauto_bridge.json %*
exit /b %ERRORLEVEL%
