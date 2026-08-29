# -*- mode: python ; coding: utf-8 -*-
# ============================================================
# 本地学习笔记站 · 桌面版 PyInstaller 打包配置
#
# 构建命令（Windows，项目根目录下执行）：
#   pyinstaller --noconfirm desktop_notes.spec
# 产物：dist/本地学习笔记站.exe（单文件，约 55-70MB）
#
# 设计要点：
#  - markdown 扩展 / Pygments 词法器 / pywebview Windows 后端
#    全部是运行时动态 import，静态分析发现不了，必须显式收集；
#  - tkinter / Qt 后端用不到，排除以减小体积；
#  - UPX 关闭：压缩壳极易触发杀软误报，得不偿失；
#  - 数据（notes.db / uploads）不打包——运行时落在 exe 同目录。
# ============================================================
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    # markdown 全部扩展（app.py 按字符串名动态加载 extra/codehilite/tables 等）
    collect_submodules("markdown")
    # Pygments 全部 lexers/styles（codehilite 按代码块语言动态选择词法器）
    + collect_submodules("pygments")
    # bleach 内嵌的 html5lib 副本（消毒引擎，动态导入）
    + collect_submodules("bleach")
    + [
        # pywebview 在 Windows 上的后端（webview.start() 运行时按平台选择）
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "clr",  # pythonnet：winforms 后端的 .NET 桥
    ]
)

a = Analysis(
    ["desktop_webview.py"],
    pathex=[],
    binaries=[],
    datas=[
        # 前端资源打进 exe；运行时解包到 sys._MEIPASS（app.py 已适配）
        ("templates", "templates"),
        ("static", "static"),
        ("sample_files", "sample_files"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",       # webview 壳不用 tkinter（desktop_app.py 精简版才会用）
        "tkinterweb",
        "desktop_app",   # 只打包 desktop_webview 入口
        # pywebview 的可选 Qt 后端（我们固定用 WebView2 / winforms）
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "matplotlib", "numpy", "pandas",
        "IPython", "pytest", "setuptools",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="LocalNotesStation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # 关闭 UPX：避免杀软误报
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # 窗口程序：无控制台黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=None,          # 如有图标改为 "app.ico"
)
