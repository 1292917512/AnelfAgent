@echo off
rem wcauto 桥启动脚本（Windows）
rem 用法：把本 bridge 目录整体拷到 Windows，pip install -r requirements.txt 后双击本文件
cd /d %~dp0
if not exist wcauto_bridge.json (
    copy wcauto_bridge.example.json wcauto_bridge.json >nul
    echo 已生成 wcauto_bridge.json，请先编辑 token 后重跑本脚本
    pause
    exit /b 1
)
python -m wcauto_bridge --config wcauto_bridge.json %*
pause
