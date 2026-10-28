# -*- coding: utf-8 -*-
"""发射任务表合并：卫星百科自动同步（cache/launches_auto.json）+ 人工维护（data/*.json）。

规则（2026-10-28 定）：
  · 以自动同步表为**底**（每 24 小时由 wiki_launch_sync 更新，新发射当天就会进来）；
  · 再用人工表**逐字段覆盖非空值**（人工核对过的口径优先，但不会把自动值清空）；
  · rocket 统一成「长征八号甲 Y12」的写法（自动源是「长征八号甲Y12」，大图按空格拆分型号/遥号）。

注意：两张表都用 COSPAR 前 8 位（2026-243）作键。
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO_FP = os.path.join(HERE, "cache", "launches_auto.json")


def norm_rocket(s):
    """'长征八号甲Y12' → '长征八号甲 Y12'；已带空格的原样返回。"""
    s = (s or "").strip()
    if not s:
        return s
    return re.sub(r"\s*[Yy](\d+)$", r" Y\1", s)


def load_merged(group_key, hand_fp):
    out = {}
    try:
        with open(AUTO_FP, encoding="utf-8") as f:
            auto = (json.load(f) or {}).get(group_key) or []
    except Exception:
        auto = []
    for r in auto:
        k = (r.get("cospar") or "")[:8]
        if k:
            out[k] = dict(r)
    try:
        with open(hand_fp, encoding="utf-8") as f:
            hand = json.load(f) or []
    except Exception:
        hand = []
    for r in hand:
        k = (r.get("cospar") or "")[:8]
        if not k:
            continue
        base = out.get(k) or {}
        for kk, vv in r.items():
            if vv:
                base[kk] = vv
        out[k] = base
    rows = []
    for r in out.values():
        r["rocket"] = norm_rocket(r.get("rocket"))
        rows.append(r)
    rows.sort(key=lambda x: (x.get("time") or "", x.get("cospar") or ""))
    return rows
