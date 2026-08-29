#!/usr/bin/env bash
# 离线启动脚本（Linux / macOS）
# 用法：bash run.sh
set -e

cd "$(dirname "$0")"

# 创建虚拟环境（若不存在）
if [ ! -d ".venv" ]; then
    echo "首次运行，创建虚拟环境..."
    python3 -m venv .venv
fi

# 激活并安装依赖（离线环境请用 pip download 预先打包到 vendor/）
source .venv/bin/activate

if [ -d "vendor" ]; then
    echo "从本地 vendor/ 安装依赖..."
    pip install --no-index --find-links=vendor -r requirements.txt
else
    pip install -r requirements.txt
fi

echo "启动笔记站: http://127.0.0.1:8848"
exec python app.py
