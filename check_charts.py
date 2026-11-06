# -*- coding: utf-8 -*-
"""两张爬升大图的**数据一致性闸**（CI 必跑，失败即中止）。

为什么需要它（2026-11-04）：
  同一个"发射↔卫星"归属 bug 在千帆、国网两份**互相复制**的渲染代码里各犯了一次：
    · 千帆：按发射日高亮，而两发在 UTC 同一天 → 多算 9 颗
    · 国网：按发射日高亮，而 x.t 是 UTC、s.l 是北京时间 → 少算 9 颗（0 命中）
  两次都是"悬停某次发射，高亮的卫星不对"。数据本身没错，错在匹配口径。
  这个脚本在**数据层**把能查的都查掉，让下次改动立刻暴露，而不是等用户发现。

检查项：
  1. 每颗卫星都有 cs（COSPAR 前缀）——高亮按它匹配，缺了就必然错
  2. 每次发射的 cospar 至少能对上 1 颗卫星（对不上说明表和星图脱节）
  3. 每次发射的 n 与实际匹配数一致（防止匹配逻辑悄悄改口径）
  4. 有几次发射落在同一个 UTC 日 → 明确列出来（这类必须靠 COSPAR 区分）
  5. 每颗卫星的 launch 日期与其发射的 UTC 日期相差 ≤1 天（跨午夜容差）
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CHARTS = [
    ("千帆", os.path.join(HERE, "web", "raise", "千帆星座轨道爬升交互大图.html")),
    ("国网", os.path.join(HERE, "web", "raise", "国网低轨卫星轨道爬升交互大图.html")),
]

fail = []
warn = []


def load(path):
    with open(path, encoding="utf-8") as f:
        html = f.read()
    m = re.search(r"const DATA = (\{.*?\});\s*\n", html, re.S)
    if not m:
        raise SystemExit("!! %s 里找不到 const DATA" % os.path.basename(path))
    return json.loads(m.group(1)), html


for name, path in CHARTS:
    if not os.path.isfile(path):
        fail.append("%s：文件不存在 %s" % (name, path))
        continue
    D, html = load(path)
    sats = D.get("sats") or []
    launches = D.get("launches") or []
    print("\n== %s == 星 %d · 发射 %d" % (name, len(sats), len(launches)))

    # 1) cs 覆盖
    no_cs = [s for s in sats if not s.get("cs")]
    print("  1) 缺 COSPAR 的卫星：%d" % len(no_cs))
    if no_cs:
        fail.append("%s：%d 颗卫星没有 cs（按 COSPAR 高亮会失效）例：%s"
                    % (name, len(no_cs), [s.get("n") for s in no_cs[:3]]))

    # 2/3) 每次发射的匹配数
    by_cs = {}
    for s in sats:
        by_cs.setdefault(s.get("cs"), []).append(s)
    mism = []
    for x in launches:
        cs = x.get("cospar") or ""
        got = len(by_cs.get(cs, [])) if cs and cs != "—" else 0
        if got == 0:
            mism.append("%s(%s) 匹配 0 颗，表里写 n=%s" % (x.get("grp"), cs, x.get("n")))
        elif x.get("n") is not None and got != x.get("n"):
            mism.append("%s(%s) 表里 n=%s 实际 %d 颗" % (x.get("grp"), cs, x.get("n"), got))
    print("  2/3) 发射↔卫星 匹配异常：%d" % len(mism))
    for m in mism[:6]:
        print("       -", m)
    if mism:
        fail.append("%s：%d 次发射的卫星数与表不符" % (name, len(mism)))

    # 4) 同 UTC 日的多次发射（用 bjt-8h 求真实 UTC 日期，而不是表里的 date 字段）
    import datetime as _dt
    from collections import defaultdict
    by_day = defaultdict(list)
    for x in launches:
        key = x.get("date") or ""
        bjt = (x.get("bjt") or "").strip()
        if bjt:
            try:
                key = (_dt.datetime.strptime(bjt, "%Y-%m-%d %H:%M")
                       - _dt.timedelta(hours=8)).date().isoformat()
            except Exception:
                pass
        by_day[key].append(x.get("grp"))
    dup = {k: v for k, v in by_day.items() if len(v) > 1}
    print("  4) 同一 UTC 日多次发射：%d 组 %s" % (len(dup), dup or ""))
    for d, grps in dup.items():
        warn.append("%s：%s 有 %d 次发射（%s）——高亮必须按 COSPAR，不能按日"
                    % (name, d, len(grps), "、".join(grps)))

    # 5) launch 日期与发射日相差 ≤1 天
    import datetime
    bad = []
    for x in launches:
        cs, d0 = x.get("cospar"), x.get("date")
        if not cs or not d0:
            continue
        try:
            ud = datetime.date.fromisoformat(d0)
        except Exception:
            continue
        for s in by_cs.get(cs, []):
            try:
                sd = datetime.date.fromisoformat(s.get("l") or "")
            except Exception:
                bad.append("%s 的 l 字段异常：%r" % (s.get("n"), s.get("l")))
                continue
            if abs((sd - ud).days) > 1:
                bad.append("%s(%s) l=%s 与发射日 %s 相差 %d 天"
                           % (s.get("n"), cs, sd, ud, (sd - ud).days))
    print("  5) 发射日期偏差 >1 天：%d" % len(bad))
    for b in bad[:5]:
        print("       -", b)
    if bad:
        warn.append("%s：%d 颗卫星的 launch 日期与发射日相差 >1 天（高亮按日匹配会错）"
                    % (name, len(bad)))

    # 6) 渲染代码必须走公共核心（防退回本地复制）
    if "ChartCore.highlightBatch" not in html:
        fail.append("%s：渲染代码没有调用 ChartCore.highlightBatch —— 高亮逻辑又各自复制了" % name)
    else:
        print("  6) 高亮走 ChartCore 公共核心 ✓")
    if "global.ChartCore" not in html:
        fail.append("%s：页面里没有内联 chart_core.js" % name)

    # 7) 渲染骨架必须存在（2026-11-06：抽核心时误删曲线循环，图整个空白，
    #    而旧版校验只看数据，完全没发现 ⇒ 现在静态检查绘制骨架）
    skeleton = [("const order = DATA.sats.map", "曲线排序 order"),
                ("s.p.length; k++", "曲线绘制主循环"),
                ("bubble", "发射气泡层（若该图有）")]
    missing = [d for k, d in skeleton[:-1] if k not in html]
    print("  7) 绘制骨架齐全：%s" % ("是" if not missing else "缺 " + "、".join(missing)))
    if missing:
        fail.append("%s：绘制骨架缺失（%s）——图会画不出来" % (name, "、".join(missing)))

print("\n" + "=" * 60)
for w in warn:
    print("  ⚠ " + w)
for f in fail:
    print("  ✗ " + f)
if fail:
    print("\n校验失败：%d 项硬错误、%d 项提示" % (len(fail), len(warn)))
    sys.exit(1)
print("\n校验通过：%d 项提示（同 UTC 日多发射等，属正常，靠 COSPAR 区分）" % len(warn))
