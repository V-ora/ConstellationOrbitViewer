# -*- coding: utf-8 -*-
# PyInstaller 打包配置：星座轨道实时查看器
# 用法（在工程目录下执行）：
#   pyinstaller ^
#       --noconfirm --clean spec_onefile.spec
#
# 说明：
#  - onefile：单个 exe，双击即用，最省事
#  - web/（页面+satellite.js+world.js+两张爬升大图）、data/（发射任务表）打进包，
#    运行时解包到 _MEIPASS；TLE 缓存写到 %LOCALAPPDATA%\ConstellationOrbitViewer\cache
#  - 无控制台窗口（console=False，2026-09-29 用户要求）：日志重定向到
#    %LOCALAPPDATA%\ConstellationOrbitViewer\run.log；退出走页面右下「退出程序」按钮（/quit）

import os

a = os.path.abspath
SRC = a(".")
DIST = a(os.path.join(SRC, "dist"))

# 变体打包开关（不改动默认行为，默认 = 原来的 web/ 与原来的产物名）：
#   COV_WEB_SRC=web_nopass  打「不含过境预报」那套前端（由 _make_nopass.py 生成）
#   COV_EXE_NAME=…          改产物名，避免两套 exe 互相覆盖
#   COV_RUNTIME_HOOK=…      运行时钩子脚本（SpaceTrack 版用它注入 COV_DATA_SRC=spacetrack）
WEB_SRC = os.environ.get("COV_WEB_SRC", "web")
EXE_NAME = os.environ.get("COV_EXE_NAME", "星座轨道实时查看器")
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

a = Analysis(
    [os.path.join(SRC, "server.py")],
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[RUNTIME_HOOK] if RUNTIME_HOOK else [],
    excludes=["tkinter", "unittest", "pydoc_data", "xmlrpc", "sqlite3",
              "curses", "turtle", "distutils"],
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
