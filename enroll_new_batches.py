# -*- coding: utf-8 -*-
"""新批次自动入册：扫上游 GP，发现未登记的 COSPAR 前缀 → 建发射行 + 播种历史点。

为什么需要（2026-11-06 用户问「为什么没有自动拉取 2026-248」）：
  原来的管线只解决「已有批次的刷新」：
    sync_launches(卫星百科任务表) → local_backfill(末点) → backfill_hist_missing(缺口) → 出图
  但没有任何一步会**发现上游多了一个批次**。卫星百科没收录（或还没更新）时，
  这个批次就永远进不了表、进不了历史库 —— 2026-248（QIANFAN 17，19 个物体）就是这样漏了。

它的产出（三条，都只写"待补"占位，不冒充官方信息）：
  1) cache/launches_derived.json[group] —— 发射行占位（mission/rocket/site 留空，等卫星百科/人工补）
  2) data/sat_hist.json —— 每颗星播种 1 个当前高度点（否则出图时被 `not e['pts']` 过滤掉）
  3) 之后 backfill_hist_missing.py 会自动把这些星的历史补齐（它们已是"点数<2"的目标）

幂等：已登记的前缀一律跳过；重复跑不会重复插入。
"""
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE = os.path.join(HERE, "cache")
DERIVED = os.path.join(CACHE, "launches_derived.json")
HIST = os.path.join(DATA, "sat_hist.json")
BASE = (2024, 1, 1)
MU = 398600.4418
R_EARTH = 6378.137

GROUPS = {
    "qianfan": {"tle": "qianfan.tle", "src": "qianfan", "target": 1068.5,
                "label": "千帆", "name": "qianfan"},
    "gw": {"tle": "gw.tle", "src": "gw", "target": 1149.3,
            "label": "星网", "name": "hulianwang digui"},
}

UA = "Mozilla/5.0 ConstellationOrbitViewer/1.0"


def read_json(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def write_json(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, p)


def day_index(d):
    import datetime
    return (d - datetime.date(*BASE)).days


def alt_from_mm(mm):
    try:
        n = float(mm) * 2 * 3.141592653589793 / 86400.0
        if n <= 0:
            return None
        return round((MU / n ** 2) ** (1 / 3) - R_EARTH, 1)
    except Exception:
        return None


def _shell_of(src, inc):
    """按倾角给壳层标签（出图脚本靠字符串里的 '50'/'86' 判壳层）。"""
    try:
        inc = float(inc or 0)
    except Exception:
        inc = 0.0
    if src == "gw":
        if inc and inc < 60:
            return "50°倾角壳层"
        if inc:
            return "86.5°倾角壳层"
        return None
    return None


def intdes_to_date(s):
    """'26061A' → 2026-03-02（UTC 发射日）。TLE 里 intdes 是 8 列：YYNNNPPP。"""
    import datetime
    s = (s or "").strip()
    # 两种写法都要认：OMM 的 OBJECT_ID 是 "2026-248A"，TLE 里是 "26248A"
    m = re.match(r"^(\d{4})-(\d{3})", s)
    if m:
        y, doy = int(m.group(1)), int(m.group(2))
    else:
        m = re.match(r"^(\d{2})(\d{3})", s)
        if not m:
            return None
        yy, doy = int(m.group(1)), int(m.group(2))
        y = 2000 + yy if yy < 57 else 1900 + yy
    try:
        return datetime.date(y, 1, 1) + datetime.timedelta(days=doy - 1)
    except Exception:
        return None


def satcat_launch_date(prefix):
    """问 CelesTrak satcat 要该批次的真实发射日（UTC）。失败返回 None。"""
    url = ("https://celestrak.org/satcat/records.php?INTDES=%s&FORMAT=json" % prefix)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=90) as r:
            arr = json.loads(r.read().decode("utf-8", "replace"))
        ds = sorted({(x.get("LAUNCH_DATE") or "")[:10] for x in arr if x.get("LAUNCH_DATE")})
        import datetime
        return datetime.date.fromisoformat(ds[0]) if ds else None
    except Exception:
        return None


def parse_tle_text(text):
    """3 行组 → [{name, norad, intdes, alt}]"""
    lines = [ln.rstrip("\r") for ln in text.replace("\r\n", "\n").split("\n")]
    lines = [ln for ln in lines if ln.strip()]
    out, i = [], 0
    while i + 2 < len(lines) + 1 and i + 2 <= len(lines) - 1:
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            i += 1
            continue
        out.append({
            "name": name.strip(),
            "norad": l1[2:7].strip(),
            "intdes": l1[9:17].strip(),
            "alt": alt_from_mm(l2[52:63]),
        })
        i += 3
    return out


def fetch_rows(group):
    """主动向 CelesTrak 取**最新 OMM(JSON)**（新批次只有新数据里才有；缓存一定过时）。

    JSON 而不是 TLE：FORMAT=tle 会漏星（本工程 2026-10-21 已踩过，见 omm_to_tle 注释）。
    失败时退回本地 TLE 缓存，保证不阻断管线。
    """
    cfg = GROUPS[group]
    url = ("https://celestrak.org/NORAD/elements/gp.php?NAME=%s&FORMAT=json"
           % urllib.request.quote(cfg["name"]))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=240) as r:
            arr = json.loads(r.read().decode("utf-8", "replace"))
        rows = []
        for x in arr if isinstance(arr, list) else []:
            oid = (x.get("OBJECT_ID") or "").strip()
            try:
                mm = float(x.get("MEAN_MOTION") or 0)
            except Exception:
                mm = 0
            if not oid:
                continue
            try:
                inc = float(x.get("INCLINATION") or 0)
            except Exception:
                inc = 0.0
            rows.append({"name": (x.get("OBJECT_NAME") or "").strip(),
                         "norad": str(x.get("NORAD_CAT_ID") or "").strip(),
                         "intdes": oid,
                         "alt": alt_from_mm(mm),
                         "inc": inc})
        if rows:
            return rows, "CelesTrak 最新"
    except Exception as e:
        print("  !! %s 取 OMM 失败（改用缓存）：%s" % (group, e))
    p = os.path.join(CACHE, cfg["tle"])
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                rows = parse_tle_text(f.read())
            if rows:
                return rows, "本地 TLE 缓存"
        except Exception:
            pass
    return [], "失败"


def main():
    hist = read_json(HIST, {})
    hsats = hist.get("sats") or {}
    derived = read_json(DERIVED, {})
    if not isinstance(derived, dict):
        derived = {}
    _changed = [False]
    today = day_index(__import__("datetime").date.today())

    total_new = 0
    for gk, cfg in GROUPS.items():
        rows, srcdesc = fetch_rows(gk)
        if not rows:
            continue
        # 已登记的 COSPAR 前缀：来自手工表 + 卫星百科自动表 + 已派生的占位表
        known = set()
        for fn in ("launches.json", "gw_launches.json"):
            for r in read_json(os.path.join(DATA, fn), []) or []:
                cs = (r.get("cospar") or "")[:8]
                if cs:
                    known.add(cs)
        for r in (read_json(os.path.join(CACHE, "launches_auto.json"), {}) or {}).get(gk) or []:
            cs = (r.get("cospar") or "")[:8]
            if cs:
                known.add(cs)
        # ★ 2026-11-06：只有**历史库里真有这个批次的星**才算已入册。
        #   光有发射行（手工/百科/派生）不算 —— 否则「表里有、图里 0 颗」永远补不上。
        #   例：低轨29组(2026-253) 卫星百科已记录，但 CelesTrak 还没编目，
        #   等上游出现物体后本脚本会自动播种；之前因为"表里有"而永久跳过。
        hist_cos = set()
        for _k, _v in hsats.items():
            if isinstance(_v, dict) and _v.get("cospar"):
                hist_cos.add(_v["cospar"][:8])
        known = hist_cos

        # 按 COSPAR 前缀分组
        by_pre = {}
        for r in rows:
            d = intdes_to_date(r["intdes"])
            if not d:
                continue
            pre = "%04d-%03d" % (d.year, d.timetuple().tm_yday)
            by_pre.setdefault(pre, []).append(r)

        # ★ 无论新旧，先把 canpos 补到历史点里（老数据没有这个字段，
        #   补齐后 check_charts 的「按 cospar 归属」和出图的 cs 兜底才可靠）
        for pre, sats in by_pre.items():
            for s in sats:
                e = hsats.get(s["norad"])
                if isinstance(e, dict) and not e.get("cospar"):
                    e["cospar"] = pre
                    _changed[0] = True

        # ★ 2026-11-10：先做一轮**按卫星**补种。
        #   原来只看"批次是否已登记"，导致同一批次后来补编目的卫星永远进不来
        #   （低轨29组 2026-253 就是 8 颗先编目、第 9 颗 DIGUI-240 后补，结果一直缺一颗）。
        seeded = 0
        for pre, sats in by_pre.items():
            d_pre = None
            for s in sats:
                if s["norad"] in hsats:
                    continue
                if d_pre is None:
                    d_pre = satcat_launch_date(pre) or intdes_to_date(s["intdes"]) or \
                        __import__("datetime").date.today()
                hsats[s["norad"]] = {
                    "name": s["name"],
                    "src": cfg["src"],
                    "cospar": pre,
                    "shell": _shell_of(cfg["src"], s.get("inc")),
                    "launch": d_pre.isoformat(),
                    "target": cfg["target"],
                    "status": "",
                    "pts": [[float(today), s["alt"]]] if s["alt"] else [],
                }
                seeded += 1
                print("  ~ %s 补种卫星 %s %s（%s）" % (gk, s["norad"], s["name"][:26], pre))
        if seeded:
            _changed[0] = True

        new_here = 0
        for pre, sats in sorted(by_pre.items()):
            if pre in known:
                # 已登记：只补历史点里缺失的 cospar（老数据没有这个字段）
                for s in sats:
                    e = hsats.get(s["norad"])
                    if isinstance(e, dict) and not e.get("cospar"):
                        e["cospar"] = pre
                        _changed[0] = True
                continue
            # ★ 发射日必须问 satcat：COSPAR 的 NNN 是「年度第几次发射」，
            #   不是年积日（2026-248 是 11-03 发射，按年积日会算成 09-05）。
            d = satcat_launch_date(pre) or intdes_to_date(sats[0]["intdes"])
            label = ""
            m = re.search(r"([A-Z]+)\s+(\d+)", sats[0]["name"] or "")
            if m:
                label = "%s %s" % (m.group(1), m.group(2))
            row = {
                "cospar": pre,
                "group": label or "新批次",     # 出图脚本按这个字段显示组次
                "mission": "",                       # 留空：等卫星百科/人工补，不冒充官方组次
                "rocket": "",
                "time": d.isoformat() + " 00:00",    # 只有日期是确定的
                "site": "",
                "orbit": "",
                "dev": "",
                "derived": True,
                "derived_name": label,
                "derived_from": srcdesc,
            }
            derived.setdefault(gk, []).append(row)
            # 播种历史点
            for s in sats:
                n = s["norad"]
                if n in hsats:
                    continue
                hsats[n] = {
                    "name": s["name"],
                    "src": cfg["src"],
                    "cospar": pre,
                    "shell": _shell_of(cfg["src"], s.get("inc")),
                    "launch": d.isoformat(),
                    "target": cfg["target"],
                    "status": "",
                    "pts": [[float(today), s["alt"]]] if s["alt"] else [],
                }
            new_here += 1
            print("  + %s 新批次 %s：%d 颗（%s …）" % (gk, pre, len(sats), sats[0]["name"][:22]))
        if new_here:
            print("  %s：新增 %d 个批次" % (gk, new_here))
        total_new += new_here

    changed = total_new > 0 or _changed[0]
    if changed:
        hist["sats"] = hsats
        write_json(HIST, hist)
        write_json(DERIVED, derived)
        print("已写入：sat_hist %d 颗 / launches_derived %d 组"
              % (len(hsats), sum(len(v) for v in derived.values())))
    else:
        print("没有发现新批次（已登记的都覆盖了）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
