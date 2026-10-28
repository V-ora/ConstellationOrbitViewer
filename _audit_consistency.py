# -*- coding: utf-8 -*-
"""跨位置一致性核查：项目 cache/ ↔ %LOCALAPPDATA% cache/ ↔ data/ 静态表。
只读，不改任何文件。"""
import io, json, os, re, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
LOC = os.path.join(os.environ.get("LOCALAPPDATA") or "", "ConstellationOrbitViewer", "cache")
PC = os.path.join(HERE, "cache")

ok = bad = 0


def chk(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1
        print("PASS", name, extra)
    else:
        bad += 1
        print("FAIL", name, extra)


def rd(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"__err__": str(e)}


print("=" * 70)
print("A. 项目 cache/ 与 %LOCALAPPDATA%/cache/ 的同名文件")
print("=" * 70)
for fn in ("launches_auto.json", "qianfan_metamap.json", "gw_metamap.json",
           "gaogui_metamap.json", "intdes_idx.json", "route_proxy.json"):
    p1, p2 = os.path.join(PC, fn), os.path.join(LOC, fn)
    if not (os.path.exists(p1) and os.path.exists(p2)):
        print("SKIP", fn, "存在性: 项目=%s 运行=%s" % (os.path.exists(p1), os.path.exists(p2)))
        continue
    a, b = rd(p1), rd(p2)
    if fn.endswith("_metamap.json"):
        ma, mb = (a.get("map") or {}), (b.get("map") or {})
        da = {k: v.get("dev") for k, v in ma.items()}
        db = {k: v.get("dev") for k, v in mb.items()}
        only_a = set(da) - set(db)
        only_b = set(db) - set(da)
        diffdev = [k for k in set(da) & set(db) if da[k] != db[k]]
        print("%-22s 项目 %d 条 / 运行 %d 条；仅项目有 %d，仅运行有 %d，dev 取值不同 %d"
              % (fn, len(ma), len(mb), len(only_a), len(only_b), len(diffdev)))
        for k in diffdev[:5]:
            print("      dev 不同 NORAD %s：项目=%r 运行=%r" % (k, da[k], db[k]))
    elif fn == "launches_auto.json":
        for g in set(a) | set(b):
            ra = {r.get("cospar"): r for r in (a.get(g) or [])}
            rb = {r.get("cospar"): r for r in (b.get(g) or [])}
            only = set(ra) ^ set(rb)
            fd = [c for c in set(ra) & set(rb) if ra[c].get("dev") != rb[c].get("dev")]
            print("%-22s [%s] 项目 %d / 运行 %d；cospar 差异 %d，dev 差异 %d"
                  % (fn, g, len(ra), len(rb), len(only), len(fd)))
            for c in sorted(only)[:5]:
                print("      仅一侧有：", c, "项目" if c in ra else "运行")
    else:
        print("%-22s 项目=%s 运行=%s 内容相同=%s" % (fn, "有", "有", a == b))

print()
print("=" * 70)
print("B. 静态任务表 vs 自动同步表（同一 cospar 的字段必须一致）")
print("=" * 70)
auto = rd(os.path.join(PC, "launches_auto.json"))
import re as _re
for fn, g in (("launches.json", "qianfan"), ("gw_launches.json", "gw")):
    stat = {r["cospar"]: r for r in rd(os.path.join(HERE, "data", fn))}
    am = {r["cospar"]: r for r in (auto.get(g) or [])}
    # 自动表只认 YYYY-NNN；`YYYY-Fnn`（发射失败未入轨）本就不会被同步，属预期
    miss = {c for c in (set(stat) - set(am)) if not _re.match(r"^\d{4}-F\d+", str(c))}
    skipped = sorted({c for c in (set(stat) - set(am))} - miss)
    extra = set(am) - set(stat)
    devdiffs = []
    phrased = []
    for c in set(stat) & set(am):
        a, b = stat[c].get("dev") or "", am[c].get("dev") or ""
        if a and b and a != b:
            devdiffs.append((c, a, b))
        for k in ("rocket", "site"):
            x, y = stat[c].get(k) or "", am[c].get(k) or ""
            if x and y and x != y:
                phrased.append((c, k, x, y))
    chk("B1 %s ⊂ %s 无缺漏" % (fn, "launches_auto"), not miss, sorted(miss)[:5])
    if skipped:
        print("    自动表按设计跳过的批次（F 系列=未入轨）:", skipped)
    print("    自动表多出的批次（新发射，正常）:", sorted(extra) or "无")
    chk("B2 %s 与自动表 dev 无冲突" % fn, not devdiffs, devdiffs[:5])
    # rocket/site 措辞不同是设计差异：静态表是人工整理的展示口径，运行时以静态表为准
    print("    （设计差异）rocket/site 措辞不同 %d 处，运行时取静态表：" % len(phrased),
          phrased[:3])

print()
print("=" * 70)
print("C. metamap ↔ 静态任务表（dev 必须一致）")
print("=" * 70)
for grp, fn, key in (("qianfan", "launches.json", "group"),
                     ("gw", "gw_launches.json", "mission")):
    mm = (rd(os.path.join(PC, grp + "_metamap.json")).get("map") or {})
    stat = {r["cospar"]: r.get("dev") or "" for r in rd(os.path.join(HERE, "data", fn))}
    wrong, nodev, nocos = [], [], 0
    for norad, rec in mm.items():
        cos = (rec.get("cospar") or "")[:8]
        if len(cos) < 8:
            nocos += 1
            continue
        want = stat.get(cos)
        got = rec.get("dev") or ""
        if not got:
            nodev.append((norad, cos))
        elif want and got != want:
            wrong.append((norad, cos, got, want))
    chk("C1 %s metamap 与静态表 dev 一致" % grp, not wrong, wrong[:4])
    chk("C2 %s metamap 全部有 dev" % grp, not nodev, nodev[:4])
    print("    %s：条目 %d，无 cospar %d" % (grp, len(mm), nocos))

print()
print("=" * 70)
print("D. 逐日历史库 vs 本地补抓库（覆盖与冲突）")
print("=" * 70)
base = rd(os.path.join(HERE, "data", "sat_hist.json"))
extra_p = os.path.join(LOC, "sat_hist_extra.json")
print("base：%d 颗 / %s" % (len(base.get("sats") or {}), base.get("now")))
if not os.path.exists(extra_p):
    print("extra：不存在（本轮已清成「只存增量」口径，跑起来后才会按需生成）")
else:
    extra = rd(extra_p)
    es = extra.get("sats") or {}
    ap = extra.get("appends") or {}
    print("extra：新星 %d 颗 / 增量条目 %d 个 / %.1f KB"
          % (len(es), len(ap), os.path.getsize(extra_p) / 1024))
    chk("D1 extra 只存增量（体积远小于整库 %.0f KB）"
        % (os.path.getsize(os.path.join(HERE, "data", "sat_hist.json")) / 1024),
        os.path.getsize(extra_p) < os.path.getsize(os.path.join(HERE, "data", "sat_hist.json")) / 4,
        "%.1f KB" % (os.path.getsize(extra_p) / 1024))

print()
print("=" * 70)
print("E. 分发物：工程内 exe 副本 / 大图 HTML 两处 / 成品件齐套")
print("   （2026-10-09 起**成品即本工程目录**，原 交付物\\04_星座… 已并入并留档撤销——")
print("    E 段从「三处/两处跨目录副本」改为「工程内两处副本 + 成品件齐套」。")
print("    大图 PNG 时效放宽到 7 天——与查看器改动无关，数据来自 hist 缓存）")
print("=" * 70)
WS = os.path.dirname(HERE)
EXES = [("星座轨道实时查看器_CelesTrak版.exe", os.path.join(HERE, "")),
        ("星座轨道实时查看器_CelesTrak版_桌面版.exe", os.path.join(HERE, "")),
        # SpaceTrack 版（2026-10-09 新增；业务差异只在数据源，同样根/dist 两处一致）
        ("星座轨道实时查看器_SpaceTrack版.exe", os.path.join(HERE, "")),
        ("星座轨道实时查看器_SpaceTrack版_桌面版.exe", os.path.join(HERE, ""))]
for name, subdir in EXES:
    paths = [os.path.join(HERE, "dist", name), os.path.join(subdir, name)]
    exist = [p for p in paths if os.path.exists(p)]
    if len(exist) != 2:
        print("  缺失副本：", [p for p in paths if not os.path.exists(p)])
    md5s = [__import__("hashlib").md5(open(p, "rb").read()).hexdigest() for p in exist]
    size = os.path.getsize(exist[0]) if exist else 0
    tag = "dist+根" if subdir == os.path.join(HERE, "") else "dist+不含过境预报版"
    chk("E-exe %s 两处副本 MD5 一致（%s）" % (name, tag),
        len(exist) == 2 and len(set(md5s)) == 1,
        "%s  %d B" % (md5s[0][:12] if md5s else "-", size))

# 成品件齐套（并入后本目录应有：说明 / README / 4 份报告 / 预览图 / 制作记录树）
for rel in ("说明.md", "README.md",
            "数据与代码自检报告_20261006.md", "数据与代码自检报告_20261007.md",
            "漏洞与安全自检报告_20261006.md", "自更新能力自检_20261003.md"):
    chk("E-成品 %s 存在" % rel, os.path.isfile(os.path.join(HERE, rel)))
# ★ 2026-10-21：原来写死 `NPV == 15`，功能一多就假红（实测已 20 张）。
#   改成关系断言：README 引用的每一张预览图都必须真的存在，且总数不少于 15。
#   （同《关键经验与踩坑清单》§附 6：「会随功能自然变大的数字别写进断言」。）
NPV = sorted(f for f in os.listdir(HERE) if f.startswith("预览_") and f.endswith(".png"))
_ref = re.findall(r"预览_[^\"'()<>\s]+\.png",
                  open(os.path.join(HERE, "README.md"), encoding="utf-8").read())
_missing = sorted(set(_ref) - set(NPV))
chk("E-成品 预览图与 README 引用一致（>=15 张）",
    len(NPV) >= 15 and not _missing,
    "%d 张 / README 引用 %d 张 / 缺 %s" % (len(NPV), len(set(_ref)), _missing or "无"))
chk("E-成品 制作记录\\README.md 存在",
    os.path.isfile(os.path.join(HERE, "制作记录", "README.md")))

for f in ("千帆星座轨道爬升交互大图.html", "国网低轨卫星轨道爬升交互大图.html"):
    p1 = os.path.join(HERE, "web", "raise", f)
    p2 = os.path.join(WS, "交付物", "01_卫星轨道核查", f)
    chk("E-大图 %s 两处一致" % f,
        os.path.exists(p2) and open(p1, "rb").read() == open(p2, "rb").read())
import datetime as _dt
for f in ("千帆星座轨道爬升大图.png", "国网低轨卫星轨道爬升大图.png"):
    p2 = os.path.join(WS, "交付物", "01_卫星轨道核查", f)
    if os.path.exists(p2):
        age_h = (_dt.datetime.now() - _dt.datetime.fromtimestamp(os.path.getmtime(p2))).total_seconds() / 3600
        chk("E-截图 %s 存在且不太旧（<7 天）" % f, age_h < 168, "%.1f h" % age_h)
    else:
        chk("E-截图 %s 存在" % f, False, "缺失")

print()
print("== 一致性小结: %d 通过 / %d 失败 ==" % (ok, bad))
