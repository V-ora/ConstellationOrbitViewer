# -*- coding: utf-8 -*-
"""把「研制方（dev）」写进 data/launches.json 与 data/gw_launches.json。

- dev 值一律取自 _probe_dev.json（线上词条按表头列位机器抽出的，不是我手打）。
- 采用逐行插入，diff 只该多出 "dev":"…"，不动其他任何字节。
- 写完逐行核对：每个 cospar 都拿到了非空 dev（2025-F05 失败任务除外，词条本身没给）。
"""
import io, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

probe = json.load(open(os.path.join(HERE, "_probe_dev.json"), encoding="utf-8"))

# 组次级映射：cospar -> dev
DEV = {}
for gkey, rows in probe.items():
    got = {}
    for _title, name, cos, dev in rows:
        if not dev:
            continue
        # 千帆：只认星座主线的「千帆极轨..组」行；同 COSPAR 的试验搭车星（EUHT 等）不覆盖
        if gkey == "qianfan" and "千帆" not in name:
            continue
        got.setdefault(cos, dev)
    DEV[gkey] = got
    print("%s：研制方 %d 个批次" % (gkey, len(got)))

TARGETS = (("data/launches.json", "qianfan"), ("data/gw_launches.json", "gw"))
for fn, gkey in TARGETS:
    p = os.path.join(HERE, fn)
    with open(p, encoding="utf-8") as f:
        lines = f.read().split("\n")
    out, n, miss = [], 0, []
    for ln in lines:
        m = re.search(r'"cospar":"([^"]+)"', ln)
        if m and '"dev"' not in ln:
            cos = m.group(1)
            dev = DEV[gkey].get(cos, "")
            if dev:
                ln = ln.replace('"cospar":"%s",' % cos,
                                '"cospar":"%s","dev":"%s",' % (cos, dev), 1)
                n += 1
            else:
                miss.append(cos)
        out.append(ln)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out))
    print("%s：写入 dev %d 条；未取到 dev 的批次 %s" % (fn, n, miss or "无"))

# 复核：回读并检查
print()
for fn, gkey in TARGETS:
    d = json.load(open(os.path.join(HERE, fn), encoding="utf-8"))
    no = [r["cospar"] for r in d if not r.get("dev")]
    print("%s：%d 行，其中无 dev %s" % (fn, len(d), no or "无（全部有）"))
    for r in d:
        print("   %s | %-24s | %s" % (r["cospar"], r.get("group") or r.get("mission"),
                                      r.get("dev") or "—"))
