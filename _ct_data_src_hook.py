# -*- coding: utf-8 -*-
"""PyInstaller runtime hook：CelesTrak 版 exe 启动前注入数据源开关。

与 Space-Track 版的唯一差别就是 COV_DATA_SRC（以及缓存目录 cache_ct / cache_st）。
"""
import os

os.environ["COV_DATA_SRC"] = "celestrak"