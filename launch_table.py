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
DERIVED_FP = os.path.join(HERE, "cache", "launches_derived.json")  # 自动识别的新批次占位


def norm_rocket(s):
    """'长征八号甲Y12' → '长征八号甲 Y12'；已带空格的原样返回。"""
    s = (s or "").strip()
    if not s:
        return s
    return re.sub(r"\s*[Yy](\d+)$", r" Y\1", s)


def load_merged(group_key, hand_fp):
    # 三层优先级（低 → 高）：自动识别占位 < 卫星百科同步 < 人工表
    #   · derived：enroll_new_batches.py 发现的新批次（只有 cospar/日期是确定的）
    #   · auto   ：卫星百科任务表（有组次/火箭/场站，覆盖 derived 的空字段）
    #   · hand   ：人工核对过的表（最高）
    out = {}
    try:
        with open(DERIVED_FP, encoding="utf-8") as f:
            derived = (json.load(f) or {}).get(group_key) or []
    except Exception:
        derived = []
    try:
        with open(AUTO_FP, encoding="utf-8") as f:
            auto = (json.load(f) or {}).get(group_key) or []
    except Exception:
        auto = []
    for r in derived:
        k = (r.get("cospar") or "")[:8]
        if k:
            out[k] = dict(r)
    for r in auto:
        k = (r.get("cospar") or "")[:8]
        if not k:
            continue
        base = out.get(k) or {}
        for kk, vv in r.items():
            if vv:
                base[kk] = vv
        out[k] = base
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
        r["site"] = norm_site(r.get("site"))
        rows.append(r)
    rows.sort(key=lambda x: (x.get("time") or "", x.get("cospar") or ""))
    return rows

def model_of(rocket):
    """从火箭串里取出**型号**，用于「运载火箭构成」统计。

    ★ 2026-10-28：'长征六号甲（遥号未公开）' 里没有「空格+遥」，
    原来用 split(' 遥')[0] 会把这整串当成型号，于是它和大部队「长征六号甲」
    被算成两个不同的火箭，在表头单独列一项。这里统一去掉括号说明与遥号。
    """
    s = (rocket or "").strip()
    s = re.split(r"[（(]", s)[0]
    s = re.split(r"\s*遥", s)[0]
    return s.strip()

# ---------------------------------------------------------------- 站点名归一
# 2026-10-28：三份源对同一发射场的写法各不相同 ——
#   人工表（千帆）  : 海南商发 LC-1      ← 与其它两处不一致
#   人工表（星网）  : 海南商发 LCC-1
#   卫星百科自动同步 : 海商 LCC-1
# 统一成「海南商发 LCC-n」。只对「海南商发/海商」前缀生效，
# 避免把「文昌 LC-101」「太原 LC-9A」这类误改成 LCC。
_SITE_PREFIX = ("海商 ", "海南商发 ")
_SITE_LC = ("海南商发 LC-", "海南商发 LCC-")


def norm_site(s):
    s = (s or "").strip()
    for a, b in zip(_SITE_PREFIX[::2], _SITE_PREFIX[1::2]):
        if s.startswith(a):
            s = b + s[len(a):]
            break
    if s.startswith(_SITE_LC[0]):
        s = _SITE_LC[1] + s[len(_SITE_LC[0]):]
    return s
