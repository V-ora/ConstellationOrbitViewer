# -*- coding: utf-8 -*-
"""
生成 data/ 下的本地元信息：
  data/launches.json      —— 千帆发射任务表（副本，源：..\\qianfan_orbit\\launches.json）
  data/qianfan_meta.json  —— NORAD -> {cospar, 发射日期, 组次, 火箭, 发射场, 轨道面}
数据源均来自本工作区此前已核实的抓取结果，可离线使用。
"""
import json
import os
import shutil

APP = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(APP)
DATA = os.path.join(APP, "data")
os.makedirs(DATA, exist_ok=True)

SRC_LAUNCH = os.path.join(WS, "qianfan_orbit", "launches.json")
SRC_SATCAT = os.path.join(WS, "qianfan_orbit", "qianfan_satcat.json")

with open(SRC_LAUNCH, "r", encoding="utf-8") as f:
    launches = json.load(f)
with open(SRC_SATCAT, "r", encoding="utf-8") as f:
    satcat = json.load(f)

shutil.copyfile(SRC_LAUNCH, os.path.join(DATA, "launches.json"))

by_cos = {r["cospar"]: r for r in launches}
meta = {}
matched = 0
for row in satcat:
    oid = (row.get("OBJECT_ID") or "").strip()
    norad = row.get("NORAD_CAT_ID")
    if not norad:
        continue
    rec = {
        "cospar": oid,
        "launch_date": row.get("LAUNCH_DATE") or "",
        "obj_name": row.get("OBJECT_NAME") or "",
    }
    key = None
    for c in by_cos:
        if oid.startswith(c):
            key = c
            break
    if key:
        rec.update(by_cos[key])
        matched += 1
    meta[str(norad)] = rec

with open(os.path.join(DATA, "qianfan_meta.json"), "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False, separators=(",", ":"))

print("launches.json %d 次发射" % len(launches))
print("qianfan_meta.json %d 颗（其中 %d 颗匹配到发射任务）" % (len(meta), matched))
