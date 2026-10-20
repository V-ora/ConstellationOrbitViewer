# -*- coding: utf-8 -*-
"""整体刷新 data/sat_hist.json 的逐日历史到 CelesTrak 最新（2026-10-01）。

背景：包内历史是打包时快照（千帆停在 09-26），自动补齐逻辑只追加"当天点"和补缺星；
本脚本一次性把全部 473 颗的完整逐日历史从 CelesTrak graph-orbit-data 重新拉一遍，
让曲线追平到 CelesTrak 数据龄（通常 1~2 天）。此后日常生长由 server.py 的追加逻辑负责，
本脚本只在需要整体校准（或 CelesTrak 大规模回填）时手动跑。

用法：python update_hist.py [base_url]   # base_url 默认 http://127.0.0.1:8770（取当前编目+元信息）
幂等：末点距今天 ≤1 天的星自动跳过；逐星落盘，中断后重跑即续传。
"""
import urllib.request, ssl, json, io, os, re, datetime, time, sys, collections, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_URL = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "http://127.0.0.1:8770"
BASE_DATE = datetime.date(2024, 1, 1)
GRAPH = "https://celestrak.org/NORAD/elements/graph-orbit-data.php?CATNR="
PROBE_LIMIT = 60.0       # 预检延迟上限（秒）。★2026-10-03 由 8.0 提到 60.0：
                         #   实测该端点正常时也要 35~37 s（3/3 成功），原来的 8 s
                         #   阈值会让预检每次都误判「被限流」而直接退出，脚本永远跑不动。
                         #   判据改成「能取回数据即通过，只看是否离谱地慢」。
TARGET = {"qianfan": 1068.5, "gaogui": 35786.0}
GW_TARGET = {50: 1149.3, 86: 1167.9}

ssl._create_default_https_context = ssl._create_unverified_context
_op_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_get(url, timeout=60, retries=4):
    last = None
    for att in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            if att >= 2:
                return _op_direct.open(req, timeout=timeout).read()   # 代理连续失败 → 直连
            return urllib.request.urlopen(req, timeout=timeout).read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                print("   %d，歇 60s 再试" % e.code, flush=True)
                time.sleep(60)
            last = e
        except Exception as e:
            last = e
        if att < retries - 1:
            time.sleep(3 + 3 * att)
    raise last


def probe(rounds=2, timeout=60):
    """开跑前测速：分别用「代理」「直连」各试 rounds 次历史接口，返回 (最快可用秒数, 明细)。
    全部失败/过慢说明该出口 IP 被 CelesTrak 限流（典型表现：同站 gp.php 秒回、
    但 graph-orbit-data.php 拖到很久）。
    ★ timeout 必须 ≥ 端点真实时延（实测 35~37 s），否则会把自己正常的表现判成超时。"""
    url = GRAPH + "62243"
    detail, good = {}, []
    for label, op in (("代理", urllib.request.build_opener()), ("直连", _op_direct)):
        ts = []
        for _ in range(rounds):
            t0 = time.time()
            try:
                op.open(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                        timeout=timeout).read()
                d = time.time() - t0
                ts.append(d)
                good.append(d)
            except Exception:
                ts.append(None)
        detail[label] = ts
    return (min(good) if good else None), detail


def parse_plotdata(raw):
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
    return [(d.isoformat(), round(statistics.median(v), 1)) for d, v in sorted(g.items())]


def t_of(d_iso):
    d = datetime.datetime.fromisoformat(d_iso).date()
    return round((d - BASE_DATE).days * 4) / 4


def main():
    if "--noprobe" not in sys.argv:
        print("预检 CelesTrak 历史接口（代理/直连各 2 次）…", flush=True)
        best, detail = probe()
        for label, ts in detail.items():
            print("  %s: %s" % (label, ", ".join("超时" if t is None else "%.1fs" % t for t in ts)), flush=True)
        if best is None or best > PROBE_LIMIT:
            print("!! 疑似被限流：最快一次 %s（阈值 %.0fs）。当前出口 IP 对历史接口被拖慢，"
                  "换 IP / 换代理出口后重跑即可；确要强行跑加 --noprobe。" %
                  ("全部超时" if best is None else "%.1fs" % best, PROBE_LIMIT), flush=True)
            return 2
        print("  预检通过：最快 %.1fs，开始刷新。\n" % best, flush=True)

    fp = os.path.join(HERE, "data", "sat_hist.json")
    hist = json.load(io.open(fp, encoding="utf-8"))
    sats = hist.setdefault("sats", {})
    today = datetime.date.today()
    n_upd = n_skip = n_new = n_fail = 0
    consec_fail = 0
    stopped = ""
    for g in ["qianfan", "gw", "gaogui"]:
        if stopped:
            break
        payload = json.loads(http_get(BASE_URL + "/api/tle?group=" + g, timeout=90).decode("utf-8"))
        print("%s: %d sats" % (g, len(payload["sats"])), flush=True)
        for s in payload["sats"]:
            n = str(s["norad"])
            e = sats.get(n)
            # 幂等：末点距今天 ≤1 天就跳过
            if e and e.get("pts") and (today - (BASE_DATE + datetime.timedelta(days=e["pts"][-1][0]))).days <= 1:
                n_skip += 1
                continue
            meta = s.get("meta") or {}
            try:
                daily = parse_plotdata(http_get(GRAPH + n, timeout=60))
            except Exception as ex:
                print("  FAIL %s %s: %s" % (n, s["name"], ex), flush=True)
                n_fail += 1
                continue
            if not daily or len(daily) < 2:
                # ★ 2026-10-15：刚发射的批次在 graph-orbit-data 里**还没有 plotData** ——
                #   星网低轨27组 10-10 入轨后至今一个根数都没更新（CelesTrak 的 gp 与
                #   Space-Track 的 gp_history 都只有那一个 10-10 历元），逐日高度自然拿不到。
                #   但编目里已有当前根数（/api/tle 的 alt + epoch），所以退化成「单个当前点」：
                #   这样这一批**至少会出现在大图与组次面板里**（一个小圆点，不是整批消失），
                #   等官方发布新根数后重跑本脚本就自动长成完整爬升曲线。
                #   只对「本地历史库还没有这颗星」的情况生效——已有曲线的星绝不用单点覆盖。
                if e is None and s.get("alt") and s.get("epoch"):
                    d0 = str(s["epoch"])[:10]
                    daily = [(d0, round(float(s["alt"]), 1))]
                    print("  SINGLE %s %s（官方尚无逐日历史，只写当前点 %s @ %.1f km）"
                          % (n, s["name"], d0, float(s["alt"])), flush=True)
                else:
                    print("  EMPTY %s %s" % (n, s["name"]), flush=True)
                    n_fail += 1
                    continue
            pts = [[t_of(d), a] for d, a in daily]
            if e is None:
                if g == "gw":
                    shell = 50 if (s.get("incl") or 0) < 70 else 86
                    shell_txt, target = "%d°倾角壳层" % shell, float(GW_TARGET[shell])
                elif g == "gaogui":
                    shell_txt, target = "GEO", float(TARGET["gaogui"])
                else:
                    shell_txt, target = None, float(TARGET.get(g) or 0)
                e = {"name": s["name"], "src": g, "shell": shell_txt,
                     "launch": meta.get("launch_date") or "", "target": target,
                     "status": "", "cospar": meta.get("cospar") or "", "pts": []}
                n_new += 1
            old_last = e["pts"][-1] if e.get("pts") else None
            e["pts"] = pts
            e["name"] = e.get("name") or s["name"]
            sats[n] = e
            n_upd += 1
            hist["now"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            io.open(fp, "w", encoding="utf-8").write(json.dumps(hist, ensure_ascii=False, separators=(",", ":")))
            print("  %s %-32s %s -> %s (%d pts)%s" % (
                n, s["name"][:32], old_last, pts[-1], len(pts),
                " [新增]" if n_new and e["pts"] is not None and old_last is None else ""), flush=True)
            time.sleep(1.2)
    print("done. 更新 %d / 新增 %d / 跳过(已新) %d / 失败 %d，共 %d 颗" % (n_upd, n_new, n_skip, n_fail, len(sats)))


if __name__ == "__main__":
    main()
