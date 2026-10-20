# -*- coding: utf-8 -*-
"""清理 data/sat_hist.json 的两处历史遗留：
  ① 完全重复点（同 t 同高）—— 139 颗星 9070 个
  ② 同一 t 的离群读数（同 t 两个高度，差 >50 km）—— 1 个（DIGUI-103 @ t=707.5）
规则：同一个 t 多个读数时，保留「与前后邻点趋势最接近」的那个；全相同则留一个。
默认 dry-run，加 --apply 才写盘。写盘前先备份到 .整理备份\\。
"""
import io, json, os, shutil, sys, collections, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
APPLY = "--apply" in sys.argv
P = os.path.join(HERE, "data", "sat_hist.json")
BAK_DIR = os.path.join(os.path.dirname(HERE), ".整理备份")

src = json.load(open(P, encoding="utf-8"))
sats = src["sats"]
n_dup = n_out = 0
dup_sats, out_sats = set(), []
for name, e in sats.items():
    pts = e.get("pts") or []
    if not pts:
        continue
    groups = collections.OrderedDict()
    for t, a in pts:
        groups.setdefault(round(float(t), 6), []).append(round(float(a), 1))
    changed = False
    out = []
    for i, (t, alts) in enumerate(groups.items()):
        if len(alts) == 1:
            out.append([t, alts[0]])
            continue
        changed = True
        if len(set(alts)) == 1:
            # ① 完全重复（同 t 同高）：只留一个，纯去废
            n_dup += len(alts) - 1
            dup_sats.add(name)
            out.append([t, alts[0]])
            continue
        if max(alts) - min(alts) <= 50:
            # ② 同 t 微差（不同来源同一天的均值，差 0.3~2 km）：不同取值都保留，
            #    但组内完全相同的取值仍只留一个
            seen_v = set()
            for v in alts:
                if v in seen_v:
                    n_dup += 1
                    dup_sats.add(name)
                    continue
                seen_v.add(v)
                out.append([t, v])
            continue
        # ③ 同 t 且相差 >50 km：真离群点，挑与前后邻点最接近的值
        prev_a = out[-1][1] if out else None
        nxt_a = None
        keys = list(groups)
        if i + 1 < len(keys):
            na = groups[keys[i + 1]]
            nxt_a = sorted(na)[0]
        ref = [x for x in (prev_a, nxt_a) if x is not None]
        keep = min(alts, key=lambda v: sum(abs(v - r) for r in ref)) if ref else max(alts)
        dropped = [v for v in alts if v != keep]
        n_out += len(dropped)
        out_sats.append((name, e.get("name"), t, alts, prev_a, nxt_a, keep, dropped))
        out.append([t, keep])
    if changed:
        e["pts"] = out

print("完全重复点：删除 %d 个（涉及 %d 颗星）" % (n_dup, len(dup_sats)))
print("同 t 离群读数：删除 %d 个" % n_out)
for r in out_sats:
    print("  %s(%s) t=%s 原读数=%s 前邻=%s 后邻=%s → 保留 %s 丢弃 %s" % r)

if not APPLY:
    print("\n[dry-run] 未写盘。加 --apply 才落盘。")
    sys.exit(0)

os.makedirs(BAK_DIR, exist_ok=True)
stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
bak = os.path.join(BAK_DIR, "sat_hist_清理前备份_%s.json" % stamp)
shutil.copy2(P, bak)
src["now"] = src.get("now") or ""
tmp = P + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(src, f, ensure_ascii=False, separators=(",", ":"))
os.replace(tmp, P)
old = os.path.getsize(bak)
new = os.path.getsize(P)
tot = sum(len(e.get("pts") or []) for e in sats.values())
print("\n已写盘。备份 → %s" % bak)
print("体积 %d B → %d B（省 %d B）" % (old, new, old - new))
print("现有 %d 颗星 / 共 %d 个点" % (len(sats), tot))
