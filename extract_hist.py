# -*- coding: utf-8 -*-
"""抽取两份爬升大图内嵌的单星逐日高度历史，生成 data/sat_hist.json
数据源头均为 CelesTrak 逐日积累的 TLE/GP（由 qianfan_orbit / sat_orbit_check 脚本抓取分析）。

统一格式:
{
  "base": "2024-01-01",            # pts 里 t = 距 base 的天数(0.25 步长取整)
  "now": "2026-09-26 22:40",       # 数据截至
  "sats": {
    "<NORAD>": {
      "name": "...", "src": "qianfan|gw", "shell": "50°倾角壳层"|None,
      "launch": "YYYY-MM-DD", "target": 1068.5, "status": "...",
      "pts": [[t, alt], ...]
    }, ...
  }
}
"""
import json, io, os, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_DATE = datetime.date(2024, 1, 1)

def t_of(date_str):
    d = datetime.date.fromisoformat(date_str[:10])
    return round((d - BASE_DATE).days * 4) / 4

out = {"base": BASE_DATE.isoformat(), "now": "", "sats": {}}

# ---- 千帆: qianfan_orbit/analysis.json ----
qa = json.load(io.open(os.path.join(os.path.dirname(HERE), "qianfan_orbit", "analysis.json"), encoding="utf-8"))
out["now"] = qa.get("now", "")
for s in qa["series"]:
    if not s.get("pts"):
        continue
    pts = []
    for d, alt in s["pts"]:
        if alt is None:
            continue
        pts.append([t_of(d), round(float(alt), 1)])
    if len(pts) < 2:
        continue
    out["sats"][str(s["catnr"])] = {
        "name": s["name"], "src": "qianfan", "shell": None,
        "launch": s.get("launch") or "", "target": 1068.5,
        "status": "", "pts": pts,
    }
n_qf = len(out["sats"])

# ---- 国网: 大图 HTML 内嵌 const DATA = {...} ----
gw_path = os.path.join(HERE, "web", "raise", "国网低轨卫星轨道爬升交互大图.html")
txt = io.open(gw_path, encoding="utf-8").read()
i = txt.index("const DATA = ") + len("const DATA = ")
depth = 0; j = i; in_str = False; esc = False
while j < len(txt):
    c = txt[j]
    if in_str:
        if esc: esc = False
        elif c == "\\": esc = True
        elif c == '"': in_str = False
    else:
        if c == '"': in_str = True
        elif c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0: break
    j += 1
D = json.loads(txt[i:j + 1])
out["now"] = out["now"] or D.get("now", "")
t0 = datetime.datetime.fromisoformat(D["t0"].replace("T", " ")[:19])
for s in D["sats"]:
    pts = []
    for t, alt in s.get("p") or []:
        if alt is None:
            continue
        # t = 距 t0 的天数 → 距 base 的天数，0.25 步长取整
        tt = round((t + (t0.date() - BASE_DATE).days) * 4) / 4
        pts.append([tt, round(float(alt), 1)])
    if len(pts) < 2:
        continue
    out["sats"][str(s["c"])] = {
        "name": s["n"], "src": "gw",
        # ★ 壳层标签：国网 DATA 里 s=0 是 50° 壳层（target 1149.3）、s=1 是 86.5° 壳层（target 1167.9）。
        #   这里曾写成 `"50°" if s.get("s") == 1 else "86.5°"`，把两个壳层的标签**整体写反**，
        #   导致单星爬升图把 86.5° 的星标成「50°倾角壳层」（target 数值本身是对的）。
        "shell": "50°倾角壳层" if s.get("s") == 0 else "86.5°倾角壳层",
        "launch": s.get("l") or "", "target": float(s.get("t") or 0),
        "status": s.get("st") or "", "pts": pts,
    }
n_gw = len(out["sats"]) - n_qf

dst = os.path.join(HERE, "data", "sat_hist.json")
io.open(dst, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
print("千帆 %d 星 / 国网 %d 星，共 %d → %s (%.1f MB)"
      % (n_qf, n_gw, len(out["sats"]), dst, os.path.getsize(dst) / 1048576))
