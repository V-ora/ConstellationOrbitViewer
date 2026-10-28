# -*- coding: utf-8 -*-
"""回填 data/sat_hist.json 的逐日历史缺口（走 CelesTrak graph-orbit-data）。

为什么需要它（2026-10-28）：
  · local_backfill.py 只把**已有条目**的末点追平到今天，不管"整颗星还没有历史"
    —— 新发射的批次因此只有 1 个点，画不出爬升曲线；
  · update_hist.py 能抓完整历史，但要先起本地服务、且默认全量重跑（几百颗，很慢）。
本脚本补中间那档：**只抓缺数据的星**（点数 <2 或末点太旧），不依赖任何服务，
可直接进 Actions 管线。解析口径与 update_hist.py 完全一致：
  `var plotData = "Date,RAAN,Inclination,Arg of Perigee,SMA,Eccentricity|…"`
  其中 SMA 列实为**平均高度（km）**；同一天多点取中位数。

用法：
  python backfill_hist_missing.py                 # 默认：补点数<2 或末点 >3 天没更新的
  python backfill_hist_missing.py --max-age 1     # 更激进
  python backfill_hist_missing.py --all --limit 50
  python backfill_hist_missing.py --dry           # 只报告，不写盘
"""
import collections
import concurrent.futures as cf
import datetime
import io
import json
import os
import re
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FP = os.path.join(HERE, "data", "sat_hist.json")
BASE_DATE = datetime.date(2024, 1, 1)
GRAPH = "https://celestrak.org/NORAD/elements/graph-orbit-data.php?CATNR="
TIMEOUT = 90
WORKERS = 4


def arg(name, default=None):
    for i, a in enumerate(sys.argv):
        if a == name:
            if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
                return sys.argv[i + 1]
            return True
    return default


ssl._create_default_https_context = ssl._create_unverified_context
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_PROXY = urllib.request.build_opener()
_GOOD = []


def http_get(url, timeout=TIMEOUT, tries=2):
    last = None
    order = (_GOOD + [_DIRECT, _PROXY]) if _GOOD else [_DIRECT, _PROXY]
    for _ in range(tries):
        for op in order:
            try:
                raw = op.open(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                              timeout=timeout).read()
                if _GOOD != [op]:
                    _GOOD[:] = [op]
                return raw
            except Exception as e:
                last = e
    raise last


def parse_plotdata(raw):
    """→ [(day_float, alt_km), ...]，与 update_hist.py 同口径。"""
    m = re.search(r'var plotData = "(.*?)";', raw.decode("utf-8", "replace"), re.S)
    if not m:
        return None
    g = collections.defaultdict(list)
    for row in m.group(1).split("|")[1:]:
        if not row.strip():
            continue
        f = row.split(",")
        if len(f) < 6:
            continue
        try:
            t = datetime.datetime.fromisoformat(f[0][:19])
            g[t.date()].append(float(f[4]))
        except Exception:
            continue
    if not g:
        return None
    out = []
    for d, vals in sorted(g.items()):
        day = round((d - BASE_DATE).days * 4) / 4.0
        out.append([day, round(statistics.median(vals), 1)])
    return out


def merge(entry, pts):
    """把抓到的 pts 并进 entry；同一天以**已有值**为准（我们自己的 GP 更新）。"""
    have = {round(float(p[0]), 2) for p in (entry.get("pts") or [])}
    added = 0
    arr = entry.setdefault("pts", [])
    for day, alt in pts:
        k = round(float(day), 2)
        if k in have:
            continue
        arr.append([day, alt])
        have.add(k)
        added += 1
    if added:
        arr.sort(key=lambda x: x[0])
    return added


def main():
    dry = "--dry" in sys.argv
    do_all = "--all" in sys.argv
    max_age = float(arg("--max-age", 3))
    limit = int(arg("--limit", 400))

    hist = json.load(io.open(FP, encoding="utf-8"))
    sats = hist["sats"]
    today = datetime.date.today()
    today_off = (today - BASE_DATE).days

    targets = []
    for n, e in sats.items():
        pts = e.get("pts") or []
        if do_all:
            targets.append(n)
            continue
        if len(pts) < 2:
            targets.append(n)
            continue
        last = BASE_DATE + datetime.timedelta(days=float(pts[-1][0]))
        if (today - last).days > max_age:
            targets.append(n)
    targets = targets[:limit]
    print("待回填 %d 颗（库内共 %d 颗；判据：点数<2 或末点 >%.0f 天）"
          % (len(targets), len(sats), max_age), flush=True)
    if not targets:
        return 0

    ok = fail = nomore = 0
    total_added = 0
    t0 = time.time()

    def work(n):
        raw = http_get(GRAPH + n)
        return n, parse_plotdata(raw)

    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(work, n): n for n in targets}
        done = 0
        for fut in cf.as_completed(futs):
            n = futs[fut]
            done += 1
            try:
                _, pts = fut.result()
            except Exception as e:
                fail += 1
                print("  !! %s 失败：%s" % (n, str(e)[:70]), flush=True)
                continue
            if not pts:
                nomore += 1
                continue
            if dry:
                ok += 1
                print("  %s 可取 %d 点（dry）" % (n, len(pts)), flush=True)
                continue
            added = merge(sats[n], pts)
            total_added += added
            ok += 1
            if added:
                print("  + %s %-22s 补 %d 点（共 %d 点）"
                      % (n, (sats[n].get("name") or "")[:22], added, len(sats[n]["pts"])), flush=True)
            if done % 25 == 0 and not dry:
                hist["now"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                json.dump(hist, io.open(FP, "w", encoding="utf-8", newline="\n"), ensure_ascii=False)
                print("  …已落盘（%d/%d）" % (done, len(targets)), flush=True)

    if not dry:
        hist["now"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        json.dump(hist, io.open(FP, "w", encoding="utf-8", newline="\n"), ensure_ascii=False)
    print("完成：成功 %d / 无数据 %d / 失败 %d，共补 %d 个点，用时 %.0fs"
          % (ok, nomore, fail, total_added, time.time() - t0), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
