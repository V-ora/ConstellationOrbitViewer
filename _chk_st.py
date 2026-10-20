# -*- coding: utf-8 -*-
"""SpaceTrack 版专项自检（源码版，进程内直调后端，不起 HTTP 服务）。

用法：
  "…\\envs\\default\\Scripts\\python.exe" _chk_st.py

断言（2026-10-09 实测口径）：
  [S1] 两版隔离：DATA_SRC / SRC_LABEL / 独立缓存目录 cache_st
  [S2] 凭据就绪
  [S3] 三组取数：颗数与 CelesTrak 实时组接口**逐 NORAD 一致**
       （qianfan 248+批次补抓 vs CT 组；gw 213 恒等；gaogui 3）
  [S4] parse_tle 产物质量：NORAD 唯一、alt/incl 全有效
  [S5] GW 壳层口径：50°↔1149.3 / 86.5°↔1167.9（与 CT 版同一套判据，按倾角分壳核对）
  [S6] 缺星补抓（backfill_missing，ST NORAD 直查 + 空数组=负缓存路径）
  [S7] gp_history 按天中位：形状 + 与基线 sat_hist.json 同星同日数值吻合（≤50 km）
  [S8] fetch_group_meta：COSPAR 覆盖率、dev 覆盖（ST satcat 归一）
  [S9] 限流器：rate_stats 计数自洽（calls ≥ 查询数，logins 少）
"""
import io
import json
import os
import sys
import time
import urllib.request

os.environ["COV_DATA_SRC"] = "spacetrack"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import server  # noqa: E402

ok = bad = 0


def chk(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1
        print("  PASS", name, extra)
    else:
        bad += 1
        print("  FAIL", name, extra)


UAH = {"User-Agent": "Mozilla/5.0 COV-chk", "Accept-Encoding": "identity"}


def ct_group(group):
    """CT 版的**真实口径**参照：用 server 自己的 URL 逻辑 + drop 过滤
    （gw 的 CT 组接口 217 行里含 4 颗试验星，CT 版取数同样会剔除它们）。"""
    g = server.GROUPS[group]
    url = server.group_omm_url(g)
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UAH),
                                 timeout=60).read()
    arr = server.filter_group_rows(g, json.loads(raw.decode("utf-8", "replace")))
    return arr


server.load_launches()   # ★ 批次补抓依赖 LAUNCH_ROWS（2026-211 等批次清单在静态任务表里）
print("LAUNCH_ROWS：%d 条" % len(server.LAUNCH_ROWS))


print("=" * 70)
print("[S1] 双源隔离")
print("=" * 70)
chk("S1 DATA_SRC=spacetrack", server.DATA_SRC == "spacetrack")
chk("S1 SRC_LABEL", server.SRC_LABEL == "Space-Track.org")
chk("S1 缓存目录独立 cache_st", server.CACHE_DIR.replace("\\", "/").endswith("/cache_st"))

print()
print("=" * 70)
print("[S2] 凭据")
print("=" * 70)
ident, pwd = server.st_source.credentials()
chk("S2 spacetrack.txt 就绪", bool(ident and pwd))
chk("S2 限流器初始态", server.st_source.rate_stats()["calls"] == 0)

print()
print("=" * 70)
print("[S3] 三组取数 vs CelesTrak 实时组接口（逐 NORAD）")
print("=" * 70)
st_sets = {}
ct_sets = {}
for gk in ("qianfan", "gw", "gaogui"):
    t0 = time.time()
    rows = server.st_group_rows(gk)
    ids = sorted(int(r["NORAD_CAT_ID"]) for r in rows if r.get("NORAD_CAT_ID"))
    st_sets[gk] = set(ids)
    print("  ST %-8s %d 颗（%.1fs）" % (gk, len(ids), time.time() - t0))
for gk in ("qianfan", "gw", "gaogui"):
    try:
        arr = ct_group(gk)
        ct_sets[gk] = {int(r["NORAD_CAT_ID"]) for r in arr if r.get("NORAD_CAT_ID")}
    except Exception as e:
        print("  （CT 参照获取失败：%s——跳过该组对齐断言）" % e)
        ct_sets[gk] = None
for gk in ("qianfan", "gw", "gaogui"):
    if ct_sets[gk] is None:
        continue
    only_ct = sorted(ct_sets[gk] - st_sets[gk])
    only_st = sorted(st_sets[gk] - ct_sets[gk])
    chk("S3 %s 与 CT 逐 NORAD 一致（ST=%d CT=%d）"
        % (gk, len(st_sets[gk]), len(ct_sets[gk])),
        st_sets[gk] == ct_sets[gk],
        "" if st_sets[gk] == ct_sets[gk] else "仅CT:%s 仅ST:%s" % (only_ct[:6], only_st[:6]))

print()
print("=" * 70)
print("[S4] TLE 文本与 parse_tle 产物质量")
print("=" * 70)
for gk in ("qianfan", "gw", "gaogui"):
    rows = server.st_group_rows.cache if hasattr(server.st_group_rows, "cache") else None
    # 直接重新取（有 _omm_last 级别的复用但函数级无缓存；数量小，可接受）
    arr = server._omm_last.get(gk, {}).get("rows") or server.st_group_rows(gk)
    text = server.st_rows_to_tle_text(arr)
    sats = server.parse_tle(text)
    norads = [s["norad"] for s in sats]
    bad_fields = [s["norad"] for s in sats
                  if s.get("alt") is None or s.get("incl") is None]
    chk("S4 %s parse：%d 条、NORAD 唯一、alt/incl 全有效" % (gk, len(sats)),
        len(sats) == len(arr) and len(set(norads)) == len(norads) and not bad_fields,
        "rows=%d" % len(arr))
    # TLE 行格式抽查：前两列必须是 "1 "/"2 "，校验位自洽
    ok_sum = True
    for s in sats[:40]:
        if server.tle_checksum(s["l1"][:68]) != s["l1"][68:69]:
            ok_sum = False
        if server.tle_checksum(s["l2"][:68]) != s["l2"][68:69]:
            ok_sum = False
    chk("S4 %s TLE 校验位自洽（抽查 40 颗）" % gk, ok_sum)

print()
print("=" * 70)
print("[S5] GW 壳层口径（50°↔1149.3 / 86.5°↔1167.9）")
print("=" * 70)


def median_alt(xs):
    a = sorted(x["alt"] for x in xs if x.get("alt") is not None)
    return a[len(a) // 2] if a else None


arr = server._omm_last.get("gw", {}).get("rows") or server.st_group_rows("gw")
sats = server.parse_tle(server.st_rows_to_tle_text(arr))
lo = [s for s in sats if (s.get("incl") or 0) < 70]
hi = [s for s in sats if (s.get("incl") or 0) >= 70]
chk("S5 两壳层非空且互补", lo and hi and len(lo) + len(hi) == len(sats),
    "50°壳 %d / 86.5°壳 %d" % (len(lo), len(hi)))
m_lo, m_hi = median_alt(lo), median_alt(hi)
chk("S5 50°壳中位高度≈1149 km", m_lo is not None and 1000 < m_lo < 1300, m_lo)
chk("S5 86.5°壳中位高度≈1168 km", m_hi is not None and 1050 < m_hi < 1300, m_hi)

print()
print("=" * 70)
print("[S6] 缺星补抓（ST NORAD 直查）")
print("=" * 70)
# 用一颗确定存在的星（千帆第 1 颗）走一遍 backfill_missing 的取数路径；
# 再用一颗必然不存在的号验证「空数组 → 负缓存」路径
probe_n = sorted(st_sets["qianfan"])[0]
fake_n = 99999
sats_now = server.parse_tle(server.st_rows_to_tle_text(
    server._omm_last.get("qianfan", {}).get("rows") or []))
meta_map = {str(n): {"cospar": "2026-999Z"} for n in (probe_n, fake_n)}
merged, got = server.backfill_missing("qianfan", sats_now, meta_map, {"proxy": None})
chk("S6 缺星经 ST NORAD 直查找回", probe_n in {s["norad"] for s in merged})
chk("S6 不存在号进负缓存", fake_n in (server._noTLE_cache.get("qianfan", {}).get("norads") or []),
    str(server._noTLE_cache.get("qianfan", {}).get("norads")))

print()
print("=" * 70)
print("[S7] gp_history 按天中位 vs 基线 sat_hist.json")
print("=" * 70)
base = json.load(open(os.path.join(server.DATA_DIR, "sat_hist.json"), encoding="utf-8"))
gw_hist_norads = [n for n, e in base["sats"].items() if e.get("src") == "gw"]
probe = sorted(gw_hist_norads, key=lambda n: len(base["sats"][n].get("pts") or []))[-1]
daily = server.st_hist_daily(probe)
chk("S7 gp_history 有按天序列（%s：%d 天）" % (probe, len(daily)), len(daily) >= 30)
if daily:
    # 与基线同日比对（基线 pts 的 t = 自 2024-01-01 的天数，0.25 步长）
    from datetime import date, timedelta
    bpts = {round(float(t) * 86400): a for t, a in (base["sats"][probe].get("pts") or [])}
    bmap = {}
    for t, a in (base["sats"][probe].get("pts") or []):
        d = date(2024, 1, 1) + timedelta(days=int(float(t)))
        bmap.setdefault(d.isoformat(), float(a))
    common = [(d, a) for d, a in daily if d in bmap]
    diffs = [abs(a - bmap[d]) for d, a in common]
    chk("S7 同日数值吻合（可比 %d 天，最大差 %.1f km）"
        % (len(common), max(diffs) if diffs else -1),
        len(common) >= 10 and (not diffs or max(diffs) <= 50.0))

print()
print("=" * 70)
print("[S8] fetch_group_meta（ST satcat 归一 + 本地元信息 + 批次索引）")
print("=" * 70)
mm = server.fetch_group_meta("qianfan")
nocos = [k for k, v in mm.items() if not v.get("cospar")]
nodev = [k for k, v in mm.items() if not v.get("dev")]
chk("S8 qianfan 元信息 %d 条、无 COSPAR 缺失" % len(mm), len(mm) >= 240 and not nocos,
    "无dev %d" % len(nodev))
st_objtypes = {v.get("obj_type") for v in mm.values() if v.get("obj_type")}
chk("S8 obj_type 已归一为 CT 缩写", st_objtypes <= {"PAY", "R/B", "DEB", "UNK", ""},
    str(st_objtypes))
mm_gw = server.fetch_group_meta("gw")
chk("S8 gw 元信息 %d 条" % len(mm_gw), len(mm_gw) >= 200)
mm_gg = server.fetch_group_meta("gaogui")
chk("S8 gaogui 元信息 3 条（含高轨三星 COSPAR）", len(mm_gg) == 3
    and all(v.get("cospar") for v in mm_gg.values()))

print()
print("=" * 70)
print("[S9] 限流器自洽")
print("=" * 70)
rs = server.st_source.rate_stats()
chk("S9 calls ≥ 5 且 recent_calls == calls", rs["calls"] >= 5 and rs["recent_calls"] == rs["calls"],
    str(rs))
chk("S9 登录次数少（会话复用生效）", rs["logins"] <= 3, "logins=%d" % rs["logins"])
chk("S9 出口说明可取", bool(server.st_source.last_route_desc()))

print()
print("=" * 70)
print("== SpaceTrack 专项小结: %d 通过 / %d 失败 ==" % (ok, bad))
print("=" * 70)
sys.exit(1 if bad else 0)
