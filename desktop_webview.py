#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地离线学习笔记站 · 桌面版（pywebview 壳）

- 后台启动本地 Flask（复用 app.py 的所有逻辑与数据），再用系统 WebView2 内核
  打开一个桌面窗口，加载 Web 前端界面。
- 界面即 templates/index.html + static/ 那套护眼主题，观感与 Web 版一致。
- HTML/SPA 文档用系统浏览内核（WebView2 / Qt WebEngine）渲染，脚本、样式、
  相对资源都能完整加载，从而解决"本地也能看 HTML"的诉求。
- 关闭窗口即退出，纯本机 127.0.0.1 回环地址，不对外暴露，可离线运行。
- PyInstaller 打包后（frozen）：前端资源在 _MEIPASS 解包目录（app.py 内处理），
  数据（notes.db / uploads）与 exe 同目录。
"""
import os
import sys
import socket
import threading

# 兼容两种形态：
#  - 源码运行：把项目根目录加入 sys.path，保证可 import app
#  - PyInstaller 打包：app 等模块已被收集进 exe，直接 import 即可
if not getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)


def find_free_port(start=18488, end=18990):
    """找一个本机空闲端口，避免与其它实例或其他进程冲突。"""
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def run_flask(port):
    """在后台线程里跑 Flask 服务。"""
    try:
        import app
        app.init_db()
        app.seed_samples()
        # 仅监听本机回环；必须关闭 auto-reload，否则 werkzeug 会再开一个进程抢 UI 线程
        app.app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
    except BaseException as e:  # noqa: BLE001 — 打包后无控制台，错误要回显到窗口
        import traceback
        detail = traceback.format_exc()
        # 写一份错误日志到 exe/项目 同级目录，便于排查
        try:
            import app as _app_err
            log_dir = _app_err.DATA_DIR
        except Exception:
            log_dir = os.path.dirname(os.path.abspath(__file__))
        try:
            with open(os.path.join(str(log_dir), "desktop_error.log"),
                      "w", encoding="utf-8") as f:
                f.write(detail)
        except Exception:
            pass
        print("[本地学习笔记站] 启动失败：\n" + detail, file=sys.stderr)


def main():
    try:
        import webview
    except ImportError:
        print("[ERROR] pywebview 未安装，请先执行：")
        print("    python -m pip install pywebview")
        input("按回车退出...")
        sys.exit(1)

    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    # 后台线程启动 Flask，UI 线程交给 webview
    server = threading.Thread(target=run_flask, args=(port,), daemon=True)
    server.start()

    webview.create_window(
        "本地离线学习笔记站",
        url=url,
        width=1280,
        height=840,
        min_size=(960, 620),
        resizable=True,
        background_color="#F5F3EC",  # 与 main.css --bg 一致，避免白屏闪烁
        text_select=True,
    )
    webview.start()


if __name__ == "__main__":
    main()