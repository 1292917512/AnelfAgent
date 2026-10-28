@echo off
setlocal
chcp 65001 >nul
title AnelfAgent

set "ROOT=%~dp0"
if defined ANELF_PROJECT_ROOT set "ROOT=%ANELF_PROJECT_ROOT%\"
set "LAUNCH_ENTRY=launch.py"
if defined ANELF_LAUNCH_ENTRY set "LAUNCH_ENTRY=%ANELF_LAUNCH_ENTRY%"
cd /d "%ROOT%"

set "CONTROL_ACTION="
if /I "%~1"=="--manager" set "CONTROL_ACTION=web"
if /I "%~1"=="stop" set "CONTROL_ACTION=stop"
if /I "%~1"=="restart" set "CONTROL_ACTION=restart"
if /I "%~1"=="update" set "CONTROL_ACTION=update"
if /I "%~1"=="status" set "CONTROL_ACTION=status"
if defined CONTROL_ACTION goto control

echo.
echo   AnelfAgent
echo.

:: APP 已通过所选源完成同步，直接复用 uv 管理的项目环境。
if "%ANELF_ENV_READY%"=="1" if exist "%ROOT%.venv\Scripts\python.exe" if exist "%ROOT%web\frontend\dist\index.html" (
    set RUN_CMD="%ROOT%.venv\Scripts\python.exe"
    goto environment_ready
)

:: 检测 uv（优先） 或 python
where uv >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "RUN_CMD=uv run python"
    for /f "tokens=*" %%v in ('uv --version 2^>^&1') do echo  [运行器] %%v

    echo  [环境]   正在同步 Python 依赖...
    uv sync
    if errorlevel 1 (
        echo  [警告]   uv sync 失败，将使用当前环境继续
    ) else (
        echo  [环境]   Python 依赖已就绪
    )
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo  [错误] 未找到 uv 或 python，请先安装运行环境
        echo         uv 安装: https://github.com/astral-sh/uv
        pause
        exit /b 1
    )
    set "RUN_CMD=python"
    for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo  [运行器] %%v
)

:: 同步前端依赖（npm 是批处理脚本，必须 call；否则执行权被接管、本脚本不再继续）
where npm >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    if exist "%ROOT%web\frontend\package.json" (
        echo  [环境]   正在同步前端依赖...
        call npm install --prefix "%ROOT%web\frontend" --silent >nul 2>&1
        if errorlevel 1 (
            echo  [警告]   npm install 失败，前端功能可能异常
        ) else (
            echo  [环境]   前端依赖已就绪
            if not exist "%ROOT%web\frontend\dist\index.html" (
                echo  [环境]   首次运行，正在构建前端...
                call npm run build --prefix "%ROOT%web\frontend" >nul 2>&1
                if exist "%ROOT%web\frontend\dist\index.html" (
                    echo  [环境]   前端构建完成
                ) else (
                    echo  [警告]   前端构建失败，WebUI 将显示降级提示页
                )
            )
        )
    )
)

:environment_ready
echo  [目录] %ROOT%
echo.
echo  WebUI 地址: http://127.0.0.1:8092/webui/
echo  按 Ctrl+C 停止服务
echo  ------------------------------------------
echo.

:: ── 崩溃守护 ─────────────────────────────────────────────────────────
:: Windows 致命异常退出码：0xC0000005 访问违例 / 0xC0000409 栈缓冲区溢出
:: / 0xC0000374 堆损坏（有符号 32 位表示）与 CRT abort(3)；其余非零不自动重启
set CRASH_COUNT=0
set MAX_CRASH_COUNT=5

:run_loop
%RUN_CMD% "%LAUNCH_ENTRY%" %*
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
exit /b %EXIT_CODE%

:control
if exist "%ROOT%.venv\Scripts\python.exe" goto control_venv
where uv >nul 2>&1
if errorlevel 1 goto control_python
uv run --no-project --python 3.12 python -m app --root "%ROOT%." %CONTROL_ACTION%
exit /b %ERRORLEVEL%

:control_venv
"%ROOT%.venv\Scripts\python.exe" -m app --root "%ROOT%." %CONTROL_ACTION%
exit /b %ERRORLEVEL%

:control_python
python -m app --root "%ROOT%." %CONTROL_ACTION%
exit /b %ERRORLEVEL%
