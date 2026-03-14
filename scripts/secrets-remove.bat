@echo off
setlocal

:: secrets-remove.bat -- Move sensitive config files to .secrets/ backup
:: Usage: Run before making repo public or committing

set "ROOT=%~dp0.."
set "VAULT=%ROOT%\.secrets"

echo [secrets-remove] Moving sensitive files to %VAULT%
echo.

if not exist "%VAULT%\config\personas" mkdir "%VAULT%\config\personas"
if not exist "%VAULT%\config\memory" mkdir "%VAULT%\config\memory"
if not exist "%VAULT%\channels\telegram" mkdir "%VAULT%\channels\telegram"
if not exist "%VAULT%\channels\qq" mkdir "%VAULT%\channels\qq"
if not exist "%VAULT%\channels\feishu" mkdir "%VAULT%\channels\feishu"

set COUNT=0

if exist "%ROOT%\config\llm_clients.json" (
    move /Y "%ROOT%\config\llm_clients.json" "%VAULT%\config\" >nul
    echo   [ok] config/llm_clients.json
    set /a COUNT+=1
)
if exist "%ROOT%\config\mcp_servers.json" (
    move /Y "%ROOT%\config\mcp_servers.json" "%VAULT%\config\" >nul
    echo   [ok] config/mcp_servers.json
    set /a COUNT+=1
)
if exist "%ROOT%\config\app_config.json" (
    move /Y "%ROOT%\config\app_config.json" "%VAULT%\config\" >nul
    echo   [ok] config/app_config.json
    set /a COUNT+=1
)
if exist "%ROOT%\config\memory.md" (
    move /Y "%ROOT%\config\memory.md" "%VAULT%\config\" >nul
    echo   [ok] config/memory.md
    set /a COUNT+=1
)
if exist "%ROOT%\config\personas\mengli.json" (
    move /Y "%ROOT%\config\personas\mengli.json" "%VAULT%\config\personas\" >nul
    echo   [ok] config/personas/mengli.json
    set /a COUNT+=1
)
if exist "%ROOT%\channels\telegram\channel_config.json" (
    move /Y "%ROOT%\channels\telegram\channel_config.json" "%VAULT%\channels\telegram\" >nul
    echo   [ok] channels/telegram/channel_config.json
    set /a COUNT+=1
)
if exist "%ROOT%\channels\qq\channel_config.json" (
    move /Y "%ROOT%\channels\qq\channel_config.json" "%VAULT%\channels\qq\" >nul
    echo   [ok] channels/qq/channel_config.json
    set /a COUNT+=1
)
if exist "%ROOT%\channels\feishu\channel_config.json" (
    move /Y "%ROOT%\channels\feishu\channel_config.json" "%VAULT%\channels\feishu\" >nul
    echo   [ok] channels/feishu/channel_config.json
    set /a COUNT+=1
)

if exist "%ROOT%\config\memory" (
    xcopy /E /I /Y /Q "%ROOT%\config\memory" "%VAULT%\config\memory" >nul
    rmdir /S /Q "%ROOT%\config\memory"
    echo   [ok] config/memory/ (entire directory)
    set /a COUNT+=1
)

echo.
echo [done] Moved %COUNT% sensitive items to .secrets/
echo        Run scripts\secrets-restore.bat to restore.
