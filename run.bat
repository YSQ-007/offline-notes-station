@echo off
REM 离线启动脚本（Windows）
cd /d "%~dp0"

if not exist ".venv" (
    echo 首次运行，创建虚拟环境...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

if exist "vendor\" (
    echo 从本地 vendor\ 安装依赖...
    pip install --no-index --find-links=vendor -r requirements.txt
) else (
    pip install -r requirements.txt
)

echo 启动笔记站: http://127.0.0.1:8848
python app.py
pause
