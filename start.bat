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

:: ── 崩溃守护 ─────────────────────────────────────────────────────────
:: Windows 致命异常退出码：0xC0000005 访问违例 / 0xC0000409 栈缓冲区溢出
:: / 0xC0000374 堆损坏（有符号 32 位表示）与 CRT abort(3)；其余非零不自动重启
set CRASH_COUNT=0
set MAX_CRASH_COUNT=5

:run_loop
%RUN_CMD% launch.py %*
set EXIT_CODE=%ERRORLEVEL%

if %EXIT_CODE% EQU 42 (
    echo.
    echo  [重启] 收到重启信号，3 秒后重新启动...
    set CRASH_COUNT=0
    timeout /t 3 /nobreak >nul
    echo  [重启] 正在重新启动...
    echo.
    goto run_loop
)

set IS_CRASH=0
if %EXIT_CODE% EQU -1073741819 set IS_CRASH=1
if %EXIT_CODE% EQU -1073740791 set IS_CRASH=1
if %EXIT_CODE% EQU -1073740940 set IS_CRASH=1
if %EXIT_CODE% EQU 3 set IS_CRASH=1
if %IS_CRASH% EQU 0 goto not_crash

set /a CRASH_COUNT+=1
if not exist "%ROOT%logs" mkdir "%ROOT%logs"
> "%ROOT%logs\crash_state.json" echo {"exit_code": %EXIT_CODE%, "signal": "WINDOWS_FATAL", "crashed_at": "%date% %time%", "crash_count": %CRASH_COUNT%}
if %CRASH_COUNT% GEQ %MAX_CRASH_COUNT% (
    echo.
    echo  [!] 崩溃保护：连续 %CRASH_COUNT% 次崩溃，停止自动拉起
    echo      崩溃状态已写入 logs\crash_state.json，请排查后手动启动
    pause
    goto end
)
set /a BACKOFF=5*CRASH_COUNT
if %BACKOFF% GTR 60 set BACKOFF=60
echo.
echo  [!] 进程崩溃（退出码 %EXIT_CODE%），%BACKOFF% 秒后自动重新拉起（连续第 %CRASH_COUNT% 次）
timeout /t %BACKOFF% /nobreak >nul
echo  [重启] 正在重新启动...
echo.
goto run_loop

:not_crash
if %EXIT_CODE% NEQ 0 (
    echo.
    echo  [!] 服务异常退出，错误码: %EXIT_CODE%
    pause
)

:end
