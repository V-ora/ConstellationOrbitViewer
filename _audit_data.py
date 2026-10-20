# -*- coding: utf-8 -*-
"""数据准确性核查（2026-10-06 修正版）
走真实 HTTP 接口 + 直接读源文件 + 解析两份大图里内嵌的 DATA。只读，不改任何东西。
用法：先起源码版查看器（默认 8770 或自定），再 `python _audit_data.py [base_url]`。
"""
import io, json, os, re, sys, collections, datetime as dt, urllib.request, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8840"
BASE0 = dt.date(2024, 1, 1)          # 全站逐日历史的时间原点
TODAY = dt.date.today()
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ok = bad = warn = 0


def chk(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1
        print("  PASS", name, extra)
    else:
        bad += 1
        print("  FAIL", name, extra)


def wn(name, extra=""):
    global warn
    warn += 1
    print("  WARN", name, extra)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=240) as r:
        return json.load(r)


def rd(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def extract_data(html):
    """从大图 HTML 里安全取出内嵌的 DATA 对象（括号配平，json.raw_decode）。"""
    m = re.search(r"\bDATA\s*=\s*", html)
    if not m:
        return None
    i = html.index("{", m.end())
    return json.JSONDecoder().raw_decode(html[i:])[0]


# ─────────────────────────────────────────────────────────────────────────
print("=" * 74)
print("E. 三组接口数据：颗数 / 编目数 / 字段完整性 / NORAD 唯一 / 研制方覆盖")
print("=" * 74)
payloads = {}
for g in ("qianfan", "gw", "gaogui"):
    d = get("/api/tle?group=" + g)
    payloads[g] = d
    s = d.get("sats") or []
    cat = d.get("catalog_total")
    norads = [x["norad"] for x in s]
    nocos = [x["norad"] for x in s if not (x.get("meta") or {}).get("cospar")]
    nodev = [x["norad"] for x in s if not (x.get("meta") or {}).get("dev")]
    print("  %-8s 显示 %3d / 编目 %s / no_tle=%s / 无研制方 %d"
          % (g, len(s), cat, d.get("no_tle"), len(nodev)))
    chk("E-%s NORAD 无重复" % g, len(set(norads)) == len(norads))
    chk("E-%s 每颗都有 COSPAR" % g, not nocos, nocos[:5])
    chk("E-%s 每颗都有研制方 dev" % g, not nodev, nodev[:5])
    if cat is not None:
        chk("E-%s 编目数 = 显示数 + no_tle" % g,
            cat == len(s) + (d.get("no_tle") or 0),
            "cat=%s sats=%d no_tle=%s" % (cat, len(s), d.get("no_tle")))

total = sum(len(payloads[g]["sats"]) for g in payloads)
print("  三组合计 %d 颗" % total)
# ★ 2026-10-09：不再硬编码 473（CT 编目组成员随时间自然增减，当日就实测到 256→257 的恢复）。
#   改为与 CelesTrak 实时编目**逐 NORAD 对齐**（比固定合计更强）：CT 版数据源就是同一份；
#   SpaceTrack 版亦应对齐（CT 分组人工维护、偶有数小时滞后——若服务端多出新编目星，
#   多为 CT 分组滞后，隔时重跑即可）。
try:
    CT_URLS = {
        "qianfan": "https://celestrak.org/NORAD/elements/gp.php?GROUP=qianfan&FORMAT=json",
        "gw": "https://celestrak.org/NORAD/elements/gp.php?GROUP=hulianwang&FORMAT=json",
        "gaogui": "https://celestrak.org/NORAD/elements/gp.php?NAME=hulianwan%20gaogui&FORMAT=json",
    }
    for g, u in CT_URLS.items():
        with urllib.request.urlopen(urllib.request.Request(
                u, headers={"User-Agent": "Mozilla/5.0 COV-audit"}), timeout=60) as r:
            arr = json.load(r)
        if g == "gw":
            # CT 组接口带 4 颗技术试验星，服务端两版都会按名字关键字剔除（与组内口径一致）
            arr = [x for x in arr
                   if not any(d in (x.get("OBJECT_NAME") or "").upper()
                              for d in ("TEST", "JISHU SHIYAN"))]
        ct_ids = {int(x["NORAD_CAT_ID"]) for x in arr if x.get("NORAD_CAT_ID")}
        sv_ids = {x["norad"] for x in payloads[g]["sats"]}
        miss = sorted(ct_ids - sv_ids)
        extra = sorted(sv_ids - ct_ids)
        _no_tle = payloads[g].get("no_tle") or 0
        if sv_ids == ct_ids:
            chk("E-%s 与 CelesTrak 实时编目逐 NORAD 一致（CT=%d）" % (g, len(ct_ids)), True)
        elif not extra and miss and len(miss) == _no_tle:
            # ★ 2026-10-19：CT 已把这几颗编目，但官方**还没有 GP 根数** ⇒ 服务端按设计
            #   不显示（payload.no_tle）。缺的颗数正好等于 no_tle，就是这种情况，不是丢了星。
            wn("E-%s 缺 %d 颗 = no_tle=%d（编目有、官方未发根数）：%s"
               % (g, len(miss), _no_tle, miss[:8]))
        elif not miss and extra:
            # 服务端多、CT 组暂时没收录 → CT 分组人工维护的滞后（编目已进 ST/CT 总表、
            # 还没挂进组页）。数据无缺，降级 WARN；缺星（CT 有服务端无）才是真 FAIL。
            wn("E-%s 服务端多 %d 颗（CT 分组滞后，编目已在总表）：%s"
               % (g, len(sv_ids - ct_ids), extra[:8]))
        else:
            chk("E-%s 与 CelesTrak 实时编目逐 NORAD 一致（CT=%d）" % (g, len(ct_ids)),
                False, "缺:%s 多:%s" % (miss[:8], extra[:8]))
except Exception as e:
    wn("E-CelesTrak 实时对齐（取 CT 参照失败，跳过）：%s" % e)

print()
print("=" * 74)
print("F. 壳层口径交叉验证（声明壳层 ↔ 目标高度 ↔ 实测倾角 ↔ 实测高度）")
print("=" * 74)
hist = rd(os.path.join(HERE, "data", "sat_hist.json"))
HS = hist["sats"]
# 声明口径来自 sat_hist.json（接口 meta 里没有 shell/target）
cnt_shell = collections.Counter(e.get("shell") for e in HS.values())
cnt_tgt = collections.Counter(e.get("target") for e in HS.values())
print("  sat_hist.json shell 分布:", dict(cnt_shell))
print("  sat_hist.json target 分布:", dict(cnt_tgt))
# ★ 2026-10-19：壳层颗数不写死（220 颗时代 160/60，补入 100975/100976 后 162/60）。
#   这里真正要守的是「声明壳层 ↔ 目标高度」一一对应，颗数本身会随发射自然增长。
_s50, _s86 = cnt_shell.get("50°倾角壳层", 0), cnt_shell.get("86.5°倾角壳层", 0)
chk("F1 50°倾角壳层颗数 == target 1149.3 的颗数（%d 颗）" % _s50,
    _s50 > 0 and _s50 == cnt_tgt.get(1149.3), "%s / target=%s" % (_s50, cnt_tgt.get(1149.3)))
chk("F2 86.5°倾角壳层颗数 == target 1167.9 的颗数（%d 颗）" % _s86,
    _s86 > 0 and _s86 == cnt_tgt.get(1167.9), "%s / target=%s" % (_s86, cnt_tgt.get(1167.9)))
chk("F1b 50°壳层没有卫星跑错 target 高度",
    not (cnt_shell.get("50°倾角壳层") and cnt_tgt.get(1149.3) == 0), cnt_tgt.get(1149.3))
chk("F2b 86.5°壳层没有卫星跑错 target 高度",
    not (cnt_shell.get("86.5°倾角壳层") and cnt_tgt.get(1167.9) == 0), cnt_tgt.get(1167.9))

# 用「库里的声明壳层」去卡「接口实测倾角」——★ 必须按 NORAD 对齐：
# 库里国网叫 DIGUI-01、接口叫 HULIANWANG DIGUI-01，按名字对齐只剩 27 颗能配上。
gw = payloads["gw"]["sats"]
by_name = {str(x["norad"]): x for x in gw}
bad_inc, checked = [], 0
for n, e in HS.items():
    if e.get("src") != "gw":
        continue
    x = by_name.get(str(n))
    if not x or x.get("incl") is None:
        continue
    checked += 1
    sh = e.get("shell")
    if sh and sh.startswith("50"):
        if not (40 < x["incl"] < 70):
            bad_inc.append((e["name"], sh, x["incl"]))
    elif sh and sh.startswith("86"):
        if not (x["incl"] > 70):
            bad_inc.append((e["name"], sh, x["incl"]))
print("  声明壳层 × 实测倾角 交叉验证了 %d 颗" % checked)
chk("F3 声明壳层与实测倾角全部自洽", not bad_inc, bad_inc[:5])

# 已就位判据用 sat_hist.json 的 status（接口 meta 里没有 status）
settled = [n for n in HS if HS[n].get("status") == "已抵达工作轨道"]
overs, offs = [], []
for n in settled:
    e = HS[n]
    x = by_name.get(str(n))
    if not x or x.get("alt") is None or not e.get("target"):
        continue
    d = x["alt"] - e["target"]
    if d > 30:
        overs.append((n, x["alt"], e["target"]))
    if abs(d) > 20:
        offs.append((n, x["alt"], e["target"], round(d, 1)))
print("  库里 status=已抵达工作轨道 的星 %d 颗" % len(settled))
chk("F4 已抵达星的实测高度贴近自家 target(<20km)", not offs, offs[:5])
chk("F5 没有星明显超过自家 target(>30km，恐口径写反)", not overs, overs[:5])
for tag, tgt in (("50", 1149.3), ("86", 1167.9)):
    grp = [(n, e) for n, e in HS.items()
           if e.get("src") == "gw" and (e.get("shell") or "").startswith(tag)]
    got = [by_name[str(n)]["alt"] for n, e in grp
           if e.get("status") == "已抵达工作轨道" and str(n) in by_name
           and by_name[str(n)].get("alt") is not None]
    print("  %s°壳层：库内 %d 颗，其中已抵达 %d 颗" % (tag, len(grp), len(got)))
    if got:
        med = statistics.median(got)
        chk("F6 壳层%s 已抵达星高度中位贴近 target" % tag, abs(med - tgt) < 15,
            "中位=%.1f target=%.1f 差=%.1f (%d 颗)" % (med, tgt, med - tgt, len(got)))

# 全星座统一不变式：目标高度是「上界」，谁都不该明显超过它（千帆 status 字段为空，只能这么卡）
allhigh = []
for g in ("qianfan", "gw", "gaogui"):
    for x in payloads[g]["sats"]:
        e = HS.get(str(x["norad"]))
        if not e or not e.get("target") or x.get("alt") is None:
            continue
        if x["alt"] - e["target"] > 40:
            allhigh.append((g, x["norad"], x["alt"], e["target"]))
chk("F7 全星座没有星超出自家 target 40 km 以上", not allhigh, allhigh[:5])

print()
print("=" * 74)
print("G. 逐日历史库：覆盖 / 末点新鲜度 / 单调性")
print("=" * 74)
print("  base=%s now=%s 共 %d 颗" % (hist.get("base"), hist.get("now"), len(HS)))
for g in ("qianfan", "gw", "gaogui"):
    nc = [n for n in HS if HS[n].get("src") == g]
    missing = [n for n in nc if not HS[n].get("pts")]
    half = [n for n in nc if HS[n].get("pts") and len(HS[n]["pts"]) < 5]

    # ★ 2026-10-15：**刚发射的批次允许点数不足** —— 官方还没发布逐日历史
    #   （星网低轨27组 2026-10-10 入轨后，CelesTrak 与 Space-Track 都只有那一个历元，
    #     库里只写得进「当前点」）。这类用发射日期判定，别把真正的缺数据一起放过。
    def _fresh(e):
        try:
            return (TODAY - dt.date.fromisoformat(str(e.get("launch"))[:10])).days <= 10
        except Exception:
            return False

    half_bad = [n for n in half if not _fresh(HS[n])]
    print("  %-8s 历史库 %3d 颗 / 无点 %d / 点<5 %d（其中刚发射 ≤10 天 %d 颗，不计）"
          % (g, len(nc), len(missing), len(half), len(half) - len(half_bad)))
    chk("G-%s 每颗都有 pts 且 ≥5 点（刚发射 ≤10 天的批次除外）" % g,
        not missing and not half_bad, (missing + half_bad)[:5])
lasts = collections.Counter(HS[n]["pts"][-1][0] for n in HS if HS[n].get("pts"))
exp_t = (TODAY - BASE0).days
print("  末点 t 分布（t = 距 %s 的天数；今天=%.0f）:" % (hist.get("base"), exp_t), lasts.most_common(4))
only_t = list(lasts)[0] if len(lasts) == 1 else None
chk("G1 末点 t 唯一且 = 今天", only_t is not None and abs(only_t - exp_t) <= 1,
    "末点 t=%s 期望=%d" % (only_t, exp_t))
# 单调非递减（同一天可有两个读数 → t 可能相等，但不允许倒序）
bad_order = []
for n in HS:
    p = HS[n].get("pts") or []
    for i in range(len(p) - 1):
        if p[i][0] > p[i + 1][0]:
            bad_order.append((n, p[i][0], p[i + 1][0]))
            break
chk("G2 每颗的 t 单调非递减（允许同日重复、不允许倒序）", not bad_order, bad_order[:5])

# G3 完全重复的点（同 t 同高）—— 无害但说明追加时没去重
dup_sat, dup_pts = 0, 0
for n in HS:
    p = HS[n].get("pts") or []
    seen, extra = set(), 0
    for t, a in p:
        k = (round(float(t), 6), round(float(a), 1))
        if k in seen:
            extra += 1
        seen.add(k)
    if extra:
        dup_sat += 1
        dup_pts += extra
print("  G3 完全重复点(同 t 同高)：%d 颗星 / 共 %d 个（追加未去重，渲染无害）" % (dup_sat, dup_pts))

# G4 同 t 但高差 >50km —— 真·脏点（会在大图上戳出一根尖刺）
spikes = []
for n in HS:
    g = collections.defaultdict(list)
    for t, a in (HS[n].get("pts") or []):
        g[round(float(t), 6)].append(round(float(a), 1))
    for t, al in g.items():
        if max(al) - min(al) > 50:
            spikes.append((n, HS[n].get("name"), t, sorted(al)))
chk("G4 无「同 t 两个读数高差>50km」的脏点", not spikes, spikes[:4])

# G5 孤立离群点：某点与前后邻点都差 >150km（爬升初期与 GEO 变轨除外，按前 3 点跳过）
outs = []
for n in HS:
    p = HS[n].get("pts") or []
    for i in range(3, len(p) - 1):
        a0, a1, a2 = p[i - 1][1], p[i][1], p[i + 1][1]
        if abs(a1 - a0) > 150 and abs(a1 - a2) > 150 and abs(a2 - a0) < 200:
            outs.append((n, HS[n].get("name"), p[i - 1], p[i], p[i + 1]))
chk("G5 无孤立离群点（前后相邻都差>150km 的尖刺）", not outs, outs[:4])

print()
print("=" * 74)
print("H. 两份大图内嵌 DATA ↔ sat_hist.json（图与库必须同源；按各自 t0 归一）")
print("=" * 74)
for fn, name in (("千帆星座轨道爬升交互大图.html", "千帆"),
                 ("国网低轨卫星轨道爬升交互大图.html", "国网")):
    p = os.path.join(HERE, "web", "raise", fn)
    html = open(p, encoding="utf-8").read()
    D = extract_data(html)
    if not D:
        wn("H-%s 未能定位内嵌 DATA" % name)
        continue
    t0 = D.get("t0")
    shift = (dt.date.fromisoformat(t0) - BASE0).days
    sats = D.get("sats") or []
    print("  %s：t0=%s → 平移 %+d 天；内嵌 %d 颗；库 now=%s / 图 now=%s"
          % (name, t0, shift, len(sats), hist.get("now"), D.get("now")))
    srcmap = {str(e.get("name")): e for e in HS.values()}
    matched = mism_alt = notfound = noentry = 0
    stale = []
    cover = []
    for s in sats:
        e = srcmap.get(str(s.get("n")))
        if not e:
            noentry += 1
            continue
        lib = collections.defaultdict(list)
        for t, a in (e.get("pts") or []):
            lib[round(float(t), 6)].append(round(float(a), 1))
        cp = s.get("p") or []
        for t, a in cp:
            lt = round(float(t) + shift, 6)
            if lt not in lib:
                notfound += 1
            elif not any(abs(x - round(float(a), 1)) <= 0.05 for x in lib[lt]):
                mism_alt += 1
            else:
                matched += 1
        if cp and e.get("pts"):
            ct = round(float(cp[-1][0]) + shift, 6)
            lt = round(float(e["pts"][-1][0]), 6)
            if ct < lt:
                stale.append((s.get("n"), len(cp), len(e["pts"]), ct, lt))
            cover.append(len(cp) / max(1, len(e["pts"])))
    tot = matched + mism_alt + notfound
    print("    点级核对：同=%d 高度不符=%d 库中无此 t=%d ／ 库里找不到该星=%d"
          % (matched, mism_alt, notfound, noentry))
    if cover:
        print("    图内点数/库点数 覆盖中位=%.2f 最小=%.2f（快照漂移会让它 <1）"
              % (statistics.median(cover), min(cover)))
    chk("H-%s 图内每个点都能在库中找到且高度一致" % name,
        mism_alt == 0 and notfound == 0 and noentry == 0,
        "mism_alt=%d notfound=%d noentry=%d" % (mism_alt, notfound, noentry))
    if stale:
        wn("H-%s 图内末点落后于库（图是从旧快照生成的）%d 颗，例：%s"
           % (name, len(stale), stale[:2]))

print()
print("=" * 74)
print("I. 缓存冗余：sat_hist_extra.json 是否整份复制了 sat_hist.json")
print("=" * 74)
base_hist = rd(os.path.join(HERE, "data", "sat_hist.json"))
cands = [os.path.join(HERE, "cache", "sat_hist_extra.json")]
if os.environ.get("LOCALAPPDATA"):
    cands.append(os.path.join(os.environ["LOCALAPPDATA"],
                              "ConstellationOrbitViewer", "cache", "sat_hist_extra.json"))
for ep in cands:
    if not os.path.exists(ep):
        print("  %s （不存在）" % ep)
        continue
    ex = rd(ep)
    ES = ex.get("sats") or {}
    bs, es = base_hist["sats"], ES
    same = sum(1 for n in es if n in bs and (es[n].get("pts") or []) == (bs[n].get("pts") or []))
    print("  %s\n    %d 颗 / %.2f MB（库 %d 颗）" % (ep, len(es),
                                          os.path.getsize(ep) / 1e6, len(bs)))
    print("    与库逐点相同 %d 颗 / 库里没有 %d 颗" % (same, sum(1 for n in es if n not in bs)))
    chk("I-extra 不是库的整份复制（应只装库外的增量）",
        not (len(es) >= len(bs) and same == len(bs)),
        "extra=%d 库=%d 逐点相同=%d" % (len(es), len(bs), same))

print()
print("== 准确性小结：%d 通过 / %d 失败 / %d 存疑 ==" % (ok, bad, warn))
