#!/usr/bin/env bash
# Termux（安卓手机）安装与启动脚本
# 用法：bash termux_setup.sh
# 之后每次启动：bash termux_setup.sh（依赖已装会直接启动）
set -e
cd "$(dirname "$0")"

echo "[1/3] 检查并安装 Python ..."
command -v python >/dev/null 2>&1 || { echo "安装 python ..."; pkg update -y && pkg install -y python; }

echo "[2/3] 安装依赖（核心依赖均为纯 Python）..."
python -m pip install -r requirements.txt

echo "[3/3] 启动笔记站 ..."
echo "  手机浏览器访问: http://127.0.0.1:8848"
echo "  按 Ctrl+C 停止"
exec python app.py