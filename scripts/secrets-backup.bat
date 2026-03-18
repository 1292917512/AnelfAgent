@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

:: secrets-backup.bat -- Sync personal files to .secrets\ and push to private repo

set "ROOT=%~dp0.."
set "VAULT=%ROOT%\.secrets"

if not exist "%VAULT%\.git" (
    echo [error] .secrets\ is not a git repo. Initialize it first.
    exit /b 1
)

echo [backup] Syncing personal files to .secrets\
echo.

if not exist "%VAULT%\config\personas" mkdir "%VAULT%\config\personas"
if not exist "%VAULT%\config\memory" mkdir "%VAULT%\config\memory"
if not exist "%VAULT%\config\tasks" mkdir "%VAULT%\config\tasks"
if not exist "%VAULT%\channels\telegram" mkdir "%VAULT%\channels\telegram"
if not exist "%VAULT%\channels\qq" mkdir "%VAULT%\channels\qq"
if not exist "%VAULT%\channels\feishu" mkdir "%VAULT%\channels\feishu"

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
    if exist "%ROOT%\%%F" (
        copy /Y "%ROOT%\%%F" "%VAULT%\%%F" >nul
        echo   [ok] %%F
        set /a COUNT+=1
    )
)

if exist "%ROOT%\config\memory" (
    xcopy /E /I /Y /Q "%ROOT%\config\memory" "%VAULT%\config\memory" >nul
    echo   [ok] config\memory\ (synced)
    set /a COUNT+=1
)

if exist "%ROOT%\config\tasks" (
    xcopy /E /I /Y /Q "%ROOT%\config\tasks" "%VAULT%\config\tasks" >nul
    echo   [ok] config\tasks\ (synced)
    set /a COUNT+=1
)

echo.
echo [backup] %COUNT% items synced. Pushing to private repo...
echo.

cd /d "%VAULT%"
git add -A
git diff --cached --quiet
if %ERRORLEVEL% EQU 0 (
    echo [backup] No changes to commit.
) else (
    if "%~1"=="" (
        for /f "tokens=1-5 delims=/ " %%a in ('date /t') do set D=%%a-%%b-%%c
        for /f "tokens=1-2 delims=: " %%a in ('time /t') do set T=%%a:%%b
        set "MSG=backup !D! !T!"
    ) else (
        set "MSG=%~1"
    )
    git commit -m "!MSG!"
    git push
    echo.
    echo [done] Backup pushed to private repo.
)
