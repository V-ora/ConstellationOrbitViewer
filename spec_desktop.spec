# -*- coding: utf-8 -*-
# PyInstaller 打包配置：星座轨道实时查看器 —— 桌面版（pywebview 独立窗口，不依赖浏览器）
# 用法（在工程目录下执行）：
#   pyinstaller ^
#       --noconfirm --clean spec_desktop.spec
#
# 说明：
#  - 入口 desktop.py：进程内跑 HTTP 服务（复用 server.py 全部后端），pywebview(WebView2)
#    开独立应用窗口。窗口关闭 = 退出；页面「退出程序」按钮（/quit）同样直接结束进程。
#  - web/ 与 data/ 打进包（_MEIPASS）；TLE 缓存照旧写 %LOCALAPPDATA%\ConstellationOrbitViewer\cache
#  - WebView2 运行时是系统组件（Win10/11 自带），不打包
#  - pywebview 依赖 pythonnet/clr_loader，需要 hiddenimports + 数据文件收集（见下）

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

a = os.path.abspath
SRC = a(".")
DIST = a(os.path.join(SRC, "dist"))

# 变体打包开关（默认 = 原来的 web/ 与原来的产物名）：
#   COV_WEB_SRC=web_nopass  → 打「不含过境预报」那套前端（_make_nopass.py 生成）
#   COV_EXE_NAME=…          → 改产物名
#   COV_RUNTIME_HOOK=…      → 运行时钩子脚本（SpaceTrack 版用它注入 COV_DATA_SRC=spacetrack）
WEB_SRC = os.environ.get("COV_WEB_SRC", "web")
EXE_NAME = os.environ.get("COV_EXE_NAME", "星座轨道实时查看器_桌面版")
RUNTIME_HOOK = os.environ.get("COV_RUNTIME_HOOK", "")

def _data_files(src_dir, dest):
    """收集目录，排除备份/日志等杂物（sat_hist.json.bak 有 2.6 MB，白占体积）。"""
    out = []
    for root, _dirs, files in os.walk(src_dir):
        rel = os.path.relpath(root, src_dir)
        dest_dir = dest if rel == "." else os.path.join(dest, rel)
        for f in files:
            if os.path.splitext(f)[1].lower() in (".bak", ".log", ".tmp", ".pyc"):
                continue
            out.append((os.path.join(root, f), dest_dir))
    return out


datas = [
    (os.path.join(SRC, WEB_SRC), "web"),
] + _data_files(os.path.join(SRC, "data"), "data")
datas += collect_data_files("webview")      # WebView2Loader.dll 等运行库
datas += collect_data_files("clr_loader")   # Python.Runtime.dll / 运行时配置
datas += collect_data_files("pythonnet")

hiddenimports = (
    collect_submodules("webview")           # webview.platforms.edgechromium / winforms 等
    + collect_submodules("clr_loader")
    + ["pythonnet", "clr"]
)

a = Analysis(
    [os.path.join(SRC, "desktop.py")],
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[RUNTIME_HOOK] if RUNTIME_HOOK else [],
    excludes=["tkinter", "unittest", "pydoc_data", "xmlrpc", "sqlite3",
              "curses", "turtle"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
