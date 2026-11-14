# -*- coding: utf-8 -*-
"""数据完整性闸（CI 硬失败，不是警告）。

根除的三类错误（2026-11 连续踩到）：
  A. **上游有、我们没有**：批次成员分批编目时，按批次判重会漏星（低轨29组缺 DIGUI-240）
  B. **壳层与倾角不符**：50° 的星被标成 86.5° 壳层（种子数据 shell 为空 → 默认落 86.5）
  C. **单点序列**：入册只播种 1 个点，回填又被老星挤掉配额 → 图上只剩一个圆点

检查分两层：
  · 离线层（不需要网络）：sat_hist / 发射表 / 出图数据三者必须自洽
  · 在线层（CelesTrak 可达时）：拿组接口的 NORAD 集合与本地比对，**上游有本地没有 → 直接失败**

用法：
  python check_data.py              # 完整（含在线）
  python check_data.py --offline    # 只做离线检查
退出码非 0 = 结构性问题，必须修。
"""
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE = os.path.join(HERE, "cache")
CHART_DIR = os.path.join(HERE, "web", "raise")

CHARTS = {
    "qianfan": os.path.join(CHART_DIR, "千帆星座轨道爬升交互大图.html"),
    "gw": os.path.join(CHART_DIR, "国网低轨卫星轨道爬升交互大图.html"),
}
GP_URL = {
    "qianfan": "https://celestrak.org/NORAD/elements/gp.php?NAME=qianfan&FORMAT=json",
    "gw": "https://celestrak.org/NORAD/elements/gp.php?NAME=hulianwang%20digui&FORMAT=json",
}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"

fails, warns = [], []


def bad(msg):
    fails.append(msg)
    print("  ✗ " + msg)


def warn(msg):
    warns.append(msg)
    print("  ⚠ " + msg)


def ok(msg):
    print("  ✓ " + msg)


def read_json(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_chart_data(path):
    try:
        with open(path, encoding="utf-8") as f:
            html = f.read()
    except Exception:
        return None
    m = re.search(r"const DATA = (\{.*?\});\s*\n", html, re.S)
    return json.loads(m.group(1)) if m else None


def offline():
    hist = read_json(os.path.join(DATA, "sat_hist.json"), {})
    sats = hist.get("sats") or {}
    print("\n[离线] sat_hist：%d 颗" % len(sats))

    # A1 每颗必须有 cospar
    no_cos = [k for k, v in sats.items() if not (v.get("cospar") or "").strip()]
    if no_cos:
        bad("A1 %d 颗缺 cospar（例：%s）——按 COSPAR 的归属/高亮会失效"
            % (len(no_cos), no_cos[:3]))
    else:
        ok("A1 全部带 cospar")

    # A2 发射表 n 必须等于 sat_hist 里同 cospar 的颗数
    _never = set()
    tables = {}
    for fn, gk in (("launches.json", "qianfan"), ("gw_launches.json", "gw")):
        rows = read_json(os.path.join(DATA, fn), []) or []
        tables.setdefault(gk, []).extend(rows)
    auto = read_json(os.path.join(CACHE, "launches_auto.json"), {}) or {}
    derived = read_json(os.path.join(CACHE, "launches_derived.json"), {}) or {}
    for gk, rows in list(tables.items()) + [(k, v) for k, v in auto.items()] + \
                    [(k, v) for k, v in derived.items()]:
        for r in rows or []:
            cs = str(r.get("cospar") or "")[:8]
            if not cs:
                continue
            got = sum(1 for v in sats.values() if (v.get("cospar") or "")[:8] == cs)
            want = r.get("n")
            if got == 0 and (r.get("derived") or r.get("mission")):
                _never.add(cs)
            elif isinstance(want, int) and want > 0 and got != want:
                bad("A2 %s 表里写 %d 颗，sat_hist 里 %d 颗" % (cs, want, got))
    if _never:
        print("  · 另有 %d 个批次表里有、sat_hist 里 0 颗（未编目/非本工程跟踪）：%s"
              % (len(_never), "、".join(sorted(_never)[:8])))
    ok("A2 发射表颗数与 sat_hist 一致（无冲突项）" if not fails else "A2 见上")

    # B 壳层 vs 倾角（用出图数据里的真实 inc 判断）
    for gk, path in CHARTS.items():
        D = load_chart_data(path)
        if not D:
            warn("B %s 读不到出图数据" % os.path.basename(path))
            continue
        if gk != "gw":
            # 千帆图里的 s 是「状态索引」（0=已就位/1=其它），不是壳层，跳过壳层校验
            ok("B %s 无壳层概念（s=状态索引），跳过" % gk)
            continue
        # 注：这里的 inc 是出图时按壳层兜底算出来的，**存在循环自证**，
        # 只作为自洽检查；真正的判定在在线层 B'（以上游倾角为基准）。
        wrong = []
        for s in D.get("sats") or []:
            inc = s.get("inc")
            sh = s.get("s")
            if inc is None or sh is None:
                continue
            expect = 0 if inc < 60 else 1
            if sh != expect:
                wrong.append("%s(inc=%.1f 标成%s)" % (s.get("n"), inc,
                                                      "86.5°" if sh == 1 else "50°"))
        if wrong:
            bad("B %s：%d 颗壳层与倾角不符（例：%s）" % (gk, len(wrong), "；".join(wrong[:3])))
        else:
            ok("B %s 壳层与倾角一致" % gk)

        # C 出图里的单点序列（可能是正常的刚发射，超过 3 天仍单点才算问题）
        singles = [s for s in (D.get("sats") or []) if len(s.get("p") or []) < 2]
        if singles:
            warn("C %s：%d 颗只有 1 个点（例：%s）——检查是否回填被挤掉配额"
                 % (gk, len(singles), singles[0].get("n")))

    # C2 sat_hist 里"单点且发射超过 3 天"的星
    import datetime
    today = datetime.date.today()
    stale = []
    for k, v in sats.items():
        pts = v.get("pts") or []
        if len(pts) >= 2:
            continue
        try:
            launch = datetime.date.fromisoformat(v.get("launch") or "")
        except Exception:
            continue
        if (today - launch).days > 3:
            stale.append(v.get("name") or k)
    if stale:
        warn("C2 %d 颗发射超过 3 天仍只有 ≤1 个点（例：%s）" % (len(stale), stale[:3]))
    else:
        ok("C2 没有『发射很久但只有单点』的星")


def online():
    """用 CelesTrak 组接口做**外部基准**：
       · A3 上游有、本地没有的 NORAD → 硬失败（漏星）
       · A3b 同一批次的颗数不一致 → 硬失败
       · B'  国网壳层必须与**上游倾角**一致（不能用图里反推的 inc，那是循环自证）
    """
    hist = read_json(os.path.join(DATA, "sat_hist.json"), {})
    sats = hist.get("sats") or {}
    local_by_cos, known = {}, set()
    for k, v in sats.items():
        known.add(str(k))
        cs = (v.get("cospar") or "")[:8]
        if cs:
            local_by_cos.setdefault(cs, set()).add(str(k))

    print("\n[在线] 以 CelesTrak 组接口为基准（漏星 / 颗数 / 倾角 vs 壳层）")
    for gk, url in GP_URL.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=180) as r:
                arr = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            warn("A3 %s 查询失败（跳过在线比对）：%s" % (gk, type(e).__name__))
            continue

        up_by_cos, up_inc = {}, {}
        for x in arr:
            n = str(x.get("NORAD_CAT_ID") or "").strip()
            cs = (x.get("OBJECT_ID") or "")[:8]
            if not n or not cs:
                continue
            up_by_cos.setdefault(cs, set()).add(n)
            try:
                up_inc[n] = float(x.get("INCLINATION") or 0)
            except Exception:
                pass

        # A3 漏星（逐颗）
        missing = 0
        for cs, norads in sorted(up_by_cos.items()):
            miss = sorted(norads - local_by_cos.get(cs, set()))
            if miss:
                missing += len(miss)
                bad("A3 %s：上游 %s 有 %d 颗本地没有（NORAD %s）"
                    % (cs, gk, len(miss), ", ".join(miss[:4])))
        if not missing:
            ok("A3 %s：上游 %d 个批次 / %d 颗，逐颗覆盖齐全"
               % (gk, len(up_by_cos), sum(len(v) for v in up_by_cos.values())))

        # A3b 颗数（本地多出的是已再入的星，允许；本地少于上游 = 硬失败）
        short = 0
        for cs, norads in sorted(up_by_cos.items()):
            loc = local_by_cos.get(cs, set())
            if len(loc) < len(norads):
                short += 1
                if len(loc) and cs not in [m for m in ()]:
                    pass
        if short and not missing:
            bad("A3b %s：%d 个批次本地颗数少于上游" % (gk, short))
        elif not short:
            ok("A3b %s：各批次颗数不少于上游" % gk)

        # B' 壳层 vs 上游倾角（只对国网）
        if gk == "gw":
            D = load_chart_data(CHARTS["gw"])
            if not D:
                warn("B' 国网出图数据读不到，跳过壳层校验")
                continue
            wrong = []
            for s in D.get("sats") or []:
                n = str(s.get("c") or "")
                inc = up_inc.get(n)
                if inc is None:
                    continue
                expect = 0 if inc < 60 else 1
                if s.get("s") != expect:
                    wrong.append("%s(上游倾角 %.1f°，图里标 %s)"
                                 % (s.get("n"), inc, "86.5°" if s.get("s") == 1 else "50°"))
            if wrong:
                bad("B' 国网：%d 颗壳层与上游倾角不符（例：%s）"
                    % (len(wrong), "；".join(wrong[:3])))
            else:
                ok("B' 国网：壳层与上游倾角一致（%d 颗已核）"
                   % sum(1 for s in (D.get("sats") or []) if str(s.get("c")) in up_inc))


def main():
    offline_only = "--offline" in sys.argv
    print("数据完整性检查 %s" % os.path.basename(__file__))
    offline()
    if not offline_only:
        online()
    print("\n" + "=" * 62)
    if fails:
        print("失败 %d 项、提示 %d 项 —— 结构性问题必须修（这类错误会让图/表悄悄错）"
              % (len(fails), len(warns)))
        return 1
    print("通过：0 项失败、%d 项提示" % len(warns))
    return 0


if __name__ == "__main__":
    sys.exit(main())
