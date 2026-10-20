# -*- coding: utf-8 -*-
"""补齐 sat_hist.json 里缺失的逐日高度历史（通用版，2026-09-30 自检新增）。

背景：sat_hist.json 是静态打包数据，CelesTrak 新编目的星（如 GUOWANG 24/25/26）
不在其中 → /api/batch_hist 对这些批次 404、爬升图空白。
本脚本：从运行中的查看器 /api/tle 枚举各分组缺历史的星 → 逐星抓 CelesTrak
graph-orbit-data → 解析逐日中位数高度 → 写回 data/sat_hist.json。
幂等：只补缺的，已有条目不重抓（--force 全量重抓）。

用法：
  python fetch_missing_hist.py [base_url]     # 默认 http://127.0.0.1:8771/
"""
import urllib.request, ssl, json, io, os, re, datetime, time, sys, collections, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_URL = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "http://127.0.0.1:8771"
BASE_DATE = datetime.date(2024, 1, 1)
TARGET = {"qianfan": 1068.5, "gaogui": 35786.0}
GW_TARGET = {50: 1149.3, 86: 1167.9}   # 50°壳层 / 86.5°壳层
LABEL = {"qianfan": "千帆", "gw": "国网低轨", "gaogui": "星网高轨"}

ssl._create_default_https_context = ssl._create_unverified_context


def t_of(date_str):
    return round((datetime.date.fromisoformat(date_str[:10]) - BASE_DATE).days * 4) / 4


_op_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 绕过系统代理直连


def http_get(url, timeout=60, retries=4):
    for att in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            if att >= 2:
                # 代理连续失败 → 后两次尝试绕过代理直连（server.py 同款做法）
                return _op_direct.open(req, timeout=timeout).read()
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:
            if att == retries - 1:
                raise
            print("   retry", att + 1, e, flush=True)
            time.sleep(2 + 2 * att)


def parse_plotdata(html_bytes):
    m = re.search(r'var plotData = "(.*?)";', html_bytes.decode("utf-8", "replace"), re.S)
    if not m:
        return None
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
    if not g:
        return None
    return [(d.isoformat(), round(statistics.median(v), 1)) for d, v in sorted(g.items())]


def main():
    force = "--force" in sys.argv
    fp = os.path.join(HERE, "data", "sat_hist.json")
    hist = json.load(io.open(fp, encoding="utf-8"))
    sats = hist.setdefault("sats", {})

    n_new = 0
    for g in ["qianfan", "gw", "gaogui"]:
        payload = json.loads(http_get(BASE_URL + "/api/tle?group=" + g, timeout=90))
        miss = [s for s in payload["sats"] if str(s["norad"]) not in sats or force]
        if not miss:
            print(g, "all covered")
            continue
        print(g, ":", len(miss), "missing,", LABEL.get(g, g))
        for s in miss:
            norad = str(s["norad"])
            meta = s.get("meta") or {}
            if norad in sats and not force:
                continue
            print("  fetch", norad, s["name"], "...", flush=True)
            try:
                daily = parse_plotdata(http_get(
                    "https://celestrak.org/NORAD/elements/graph-orbit-data.php?CATNR=" + norad))
            except Exception as e:
                print("    SKIP:", e)
                continue
            if not daily or len(daily) < 2:
                print("    SKIP: no plotData points")
                continue
            if g == "gw":
                shell = 50 if (s.get("incl") or 0) < 70 else 86
                target, shell_txt = GW_TARGET[shell], ("%d°倾角壳层" % shell)
            else:
                target, shell_txt = TARGET.get(g), "GEO" if g == "gaogui" else None
            pts = [[t_of(d), a] for d, a in daily]
            sats[norad] = {
                "name": s["name"], "src": g, "shell": shell_txt,
                "launch": meta.get("launch_date") or "",
                "target": float(target or 0),
                "status": "", "cospar": meta.get("cospar") or "",
                "pts": pts,
            }
            n_new += 1
            hist["now"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            io.open(fp, "w", encoding="utf-8").write(json.dumps(hist, ensure_ascii=False, separators=(",", ":")))
            print("    -> %d pts, %s ~ %s (saved)" % (len(pts), daily[0][0], daily[-1][0]))
            time.sleep(1.0)   # 对 CelesTrak 客气一点
    hist["now"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    io.open(fp, "w", encoding="utf-8").write(json.dumps(hist, ensure_ascii=False, separators=(",", ":")))
    print("done. new:", n_new, "total:", len(sats))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
