# -*- coding: utf-8 -*-
"""把「卫星百科任务表」同步到 cache/launches_auto.json（供大图与查看器共用）。

为什么要独立脚本：wiki 同步原来只在大图生成**之后**的服务端启动时才跑，
导致新发射要晚一轮（12 小时）才进表。这里提前单独跑一次，让同轮的大图就能用上。

用法：python sync_launches.py [--dry]
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import server  # noqa: E402  （import 只定义函数，不启动任何线程；线程都在 main() 里）

dry = "--dry" in sys.argv
before = {}
try:
    with open(os.path.join(HERE, "cache", "launches_auto.json"), encoding="utf-8") as f:
        before = {k: len(v or []) for k, v in (json.load(f) or {}).items()}
except Exception:
    pass
print("同步前：", before)

if dry:
    print("--dry：不实际抓取")
    sys.exit(0)

try:
    n = server.wiki_launch_sync(force=True)
except Exception as e:
    print("!! 卫星百科同步异常（不影响后续步骤）：%s" % e)
    sys.exit(1)

server.load_launches()   # 热更新任务索引（COSPAR 扩展 / 研制方）

after = {}
try:
    with open(os.path.join(HERE, "cache", "launches_auto.json"), encoding="utf-8") as f:
        after = {k: len(v or []) for k, v in (json.load(f) or {}).items()}
except Exception:
    pass
print("同步后：", after)
print("新增批次：%d" % (n or 0))
