@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

:: secrets-restore.bat -- Pull latest from private repo and restore personal files

set "ROOT=%~dp0.."
set "VAULT=%ROOT%\.secrets"

if not exist "%VAULT%\.git" (
    echo [error] .secrets\ is not a git repo. Initialize it first.
    exit /b 1
)

echo [restore] Pulling latest from private repo...
cd /d "%VAULT%"
git pull --ff-only
echo.

if not exist "%ROOT%\config\personas" mkdir "%ROOT%\config\personas"
if not exist "%ROOT%\config\memory" mkdir "%ROOT%\config\memory"
if not exist "%ROOT%\config\tasks" mkdir "%ROOT%\config\tasks"
if not exist "%ROOT%\channels\telegram" mkdir "%ROOT%\channels\telegram"
if not exist "%ROOT%\channels\qq" mkdir "%ROOT%\channels\qq"
if not exist "%ROOT%\channels\feishu" mkdir "%ROOT%\channels\feishu"

set COUNT=0

for %%F in (
    config\llm_clients.json
    config\mcp_servers.json
    config\app_config.json
    config\mind_config.json
    config\heartbeat.json
    config\webui.json
    config\personas\mengli.json
    channels\telegram\channel_config.json
    channels\qq\channel_config.json
    channels\feishu\channel_config.json
) do (
    if exist "%VAULT%\%%F" (
        copy /Y "%VAULT%\%%F" "%ROOT%\%%F" >nul
        echo   [ok] %%F
        set /a COUNT+=1
    ) else (
        echo   [skip] %%F (not in backup)
    )
)

if exist "%VAULT%\config\memory" (
    xcopy /E /I /Y /Q "%VAULT%\config\memory" "%ROOT%\config\memory" >nul
    echo   [ok] config\memory\ (synced)
    set /a COUNT+=1
)

if exist "%VAULT%\config\tasks" (
    xcopy /E /I /Y /Q "%VAULT%\config\tasks" "%ROOT%\config\tasks" >nul
    echo   [ok] config\tasks\ (synced)
    set /a COUNT+=1
)

echo.
echo [done] !COUNT! items restored from .secrets\
