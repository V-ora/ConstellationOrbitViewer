# -*- coding: utf-8 -*-
"""PyInstaller runtime hook：SpaceTrack 版 exe 启动前注入数据源开关。

打包方式（两个 spec 都支持）：
  COV_EXE_NAME=星座轨道实时查看器_SpaceTrack版  COV_RUNTIME_HOOK=_st_data_src_hook.py \
      pyinstaller --noconfirm spec_onefile.spec
exe 与 CelesTrak 版的**唯一行为差异**就是数据源（server.py 顶部 COV_DATA_SRC 分派），
前端同一套页面（数据源标注由 payload.source 动态显示）。
"""
import os

os.environ["COV_DATA_SRC"] = "spacetrack"
