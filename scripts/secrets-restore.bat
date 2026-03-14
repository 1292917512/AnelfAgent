@echo off
setlocal

:: secrets-restore.bat -- Restore sensitive config files from .secrets/ backup
:: Usage: Run after clone or after secrets-remove

set "ROOT=%~dp0.."
set "VAULT=%ROOT%\.secrets"

if not exist "%VAULT%" (
    echo [error] .secrets/ directory not found.
    echo         Run secrets-remove.bat first or place backup manually.
    exit /b 1
)

echo [secrets-restore] Restoring sensitive files from %VAULT%
echo.

set COUNT=0

if exist "%VAULT%\config\llm_clients.json" (
    move /Y "%VAULT%\config\llm_clients.json" "%ROOT%\config\" >nul
    echo   [ok] config/llm_clients.json
    set /a COUNT+=1
)
if exist "%VAULT%\config\mcp_servers.json" (
    move /Y "%VAULT%\config\mcp_servers.json" "%ROOT%\config\" >nul
    echo   [ok] config/mcp_servers.json
    set /a COUNT+=1
)
if exist "%VAULT%\config\app_config.json" (
    move /Y "%VAULT%\config\app_config.json" "%ROOT%\config\" >nul
    echo   [ok] config/app_config.json
    set /a COUNT+=1
)
if exist "%VAULT%\config\memory.md" (
    move /Y "%VAULT%\config\memory.md" "%ROOT%\config\" >nul
    echo   [ok] config/memory.md
    set /a COUNT+=1
)
if exist "%VAULT%\config\personas\mengli.json" (
    move /Y "%VAULT%\config\personas\mengli.json" "%ROOT%\config\personas\" >nul
    echo   [ok] config/personas/mengli.json
    set /a COUNT+=1
)
if exist "%VAULT%\channels\telegram\channel_config.json" (
    move /Y "%VAULT%\channels\telegram\channel_config.json" "%ROOT%\channels\telegram\" >nul
    echo   [ok] channels/telegram/channel_config.json
    set /a COUNT+=1
)
if exist "%VAULT%\channels\qq\channel_config.json" (
    move /Y "%VAULT%\channels\qq\channel_config.json" "%ROOT%\channels\qq\" >nul
    echo   [ok] channels/qq/channel_config.json
    set /a COUNT+=1
)
if exist "%VAULT%\channels\feishu\channel_config.json" (
    move /Y "%VAULT%\channels\feishu\channel_config.json" "%ROOT%\channels\feishu\" >nul
    echo   [ok] channels/feishu/channel_config.json
    set /a COUNT+=1
)
if exist "%VAULT%\config\memory" (
    if not exist "%ROOT%\config\memory" mkdir "%ROOT%\config\memory"
    xcopy /E /I /Y /Q "%VAULT%\config\memory" "%ROOT%\config\memory" >nul
    rmdir /S /Q "%VAULT%\config\memory"
    echo   [ok] config/memory/ (entire directory)
    set /a COUNT+=1
)

for /f %%a in ('dir /b /a "%VAULT%" 2^>nul ^| find /c /v ""') do set REMAINING=%%a
if "%REMAINING%"=="0" (
    rmdir /S /Q "%VAULT%" 2>nul
    echo.
    echo   .secrets/ directory cleaned up.
)

echo.
echo [done] Restored %COUNT% sensitive items.
