# -*- coding: utf-8 -*-
"""补抓星网高轨三星(59069/60327/61503)的逐日高度历史，并入 data/sat_hist.json。

数据源：CelesTrak graph-orbit-data.php?CATNR=<NORAD> 的 var plotData（与千帆大图同源）。
只新增/更新高轨三星条目，不碰已有 443 星。幂等：已存在且 pts 非空则跳过（--force 可重抓）。
"""
import urllib.request, ssl, json, io, os, re, datetime, time, sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_DATE = datetime.date(2024, 1, 1)
GAOGUI = {
    "59069": {"launch": "2024-02-29", "cospar": "2024-040A"},
    "60327": {"launch": "2024-08-01", "cospar": "2024-135A"},
    "61503": {"launch": "2024-10-10", "cospar": "2024-181A"},
}
TARGET = 35786.0
NAMES = {
    "59069": "卫星互联网高轨卫星01星",
    "60327": "卫星互联网高轨卫星02星",
    "61503": "卫星互联网高轨卫星03星",
}

ssl._create_default_https_context = ssl._create_unverified_context


def t_of(date_str):
    d = datetime.date.fromisoformat(date_str[:10])
    return round((d - BASE_DATE).days * 4) / 4


def fetch(catnr):
    u = "https://celestrak.org/NORAD/elements/graph-orbit-data.php?CATNR=" + catnr
    for att in range(5):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            d = urllib.request.urlopen(req, timeout=60).read()
            if b"plotData" in d:
                return d
            raise ValueError("no plotData (%d bytes)" % len(d))
        except Exception as e:
            print("  retry", catnr, att, e, flush=True)
            time.sleep(2 + 2 * att)
    raise SystemExit("fetch failed: " + catnr)


def parse(html_bytes):
    """返回 [(iso_date, alt_median)] 按日期排序（与 qianfan_orbit/analyze.py 同口径：逐日中位数）。"""
    m = re.search(r'var plotData = "(.*?)";', html_bytes.decode("utf-8", "replace"), re.S)
    if not m:
        raise ValueError("plotData not found")
    import collections, statistics
    g = collections.defaultdict(list)
    for r in m.group(1).split("|")[1:]:
        if not r.strip():
            continue
        f = r.split(",")
        if len(f) < 6:
            continue
        try:
            t = datetime.datetime.fromisoformat(f[0][:19])
            g[t.date()].append(float(f[4]))
        except Exception:
            continue
    return [(d.isoformat(), round(statistics.median(v), 1)) for d, v in sorted(g.items())]


def main():
    force = "--force" in sys.argv
    fp = os.path.join(HERE, "data", "sat_hist.json")
    hist = json.load(io.open(fp, encoding="utf-8"))
    sats = hist.setdefault("sats", {})
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    n_new = 0
    for catnr, meta in GAOGUI.items():
        old = sats.get(catnr)
        if old and old.get("pts") and not force:
            print(catnr, "already has", len(old["pts"]), "pts, skip")
            continue
        print("fetching", catnr, "...", flush=True)
        daily = parse(fetch(catnr))
        pts = [[t_of(d), a] for d, a in daily]
        if len(pts) < 2:
            raise SystemExit("too few points for " + catnr)
        sats[catnr] = {
            "name": NAMES[catnr], "src": "gaogui", "shell": "GEO",
            "launch": meta["launch"], "target": TARGET,
            "status": "已抵达工作轨道", "cospar": meta["cospar"],
            "pts": pts,
        }
        n_new += 1
        print("  ->", len(pts), "pts,", daily[0][0], "to", daily[-1][0])
    hist["now"] = now
    hist["gaogui_added"] = now
    io.open(fp, "w", encoding="utf-8").write(json.dumps(hist, ensure_ascii=False, separators=(",", ":")))
    print("total sats:", len(sats), "| new:", n_new, "->", fp)


if __name__ == "__main__":
    main()
