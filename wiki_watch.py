# -*- coding: utf-8 -*-
"""卫星百科「业务星表」巡检：每天一次比对变化，新批次进入 2 天观察期。

设计（2026-11-09 用户口径）：
  · 每天 06:00 检查「星网」「千帆星座」两个词条的业务星表是否变化
  · 有变化 → 记录 COSPAR 国际编号（供 CelesTrak 侧按批次取数）
  · 新出现的批次进入 **2 天观察期**：期间每次运行都重新比对，
    因为卫星百科刚写上去常会改（组次/火箭/数量都可能修正）
  · 非观察期、且距上次检查不足 12 小时 → **一次请求都不发**

状态文件 cache/wiki_watch.json：
  {
    "pages": {
      "qianfan": {"hash": "...", "last_check": ts, "watch": {"2026-248": until_ts}},
      ...
    },
    "cospar": ["2026-248", ...]        # 已知批次（业务星口径）
  }

用法：
  python wiki_watch.py             # 按策略决定是否联网
  python wiki_watch.py --force     # 强制检查（无视 12 小时间隔）
  python wiki_watch.py --dry       # 只打印计划，不联网、不写文件
"""
import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CACHE = os.path.join(HERE, "cache")
STATE = os.path.join(CACHE, "wiki_watch.json")
AUTO = os.path.join(CACHE, "launches_auto.json")
COSPAR_FP = os.path.join(CACHE, "wiki_cospar.json")
MIN_GAP = 12 * 3600          # 非观察期最短检查间隔
WATCH_DAYS = 2               # 新批次观察期

# 页面对应的分组键（与 server.WIKI_PAGES 的 gkey 一致）
PAGES = ("gw", "qianfan")


def read_json(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def rows_hash(rows):
    """业务星行集合的指纹：只取会变的字段，忽略顺序。"""
    items = []
    for r in rows or []:
        items.append("|".join(str(r.get(k) or "") for k in
                              ("cospar", "mission", "rocket", "time", "site", "dev")))
    items.sort()
    return hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()[:16]


def merge_into_auto(gk, rows):
    """把词条解析结果并入 cache/launches_auto.json（按 cospar；已有字段非空则不覆盖）。"""
    auto = read_json(AUTO, {})
    if not isinstance(auto, dict):
        auto = {}
    cur = list(auto.get(gk) or [])
    by_cos = {str(r.get("cospar")): r for r in cur}
    added, updated = [], []
    for r in rows:
        cs = str(r.get("cospar") or "")
        if not cs:
            continue
        old = by_cos.get(cs)
        if old is None:
            cur.append(dict(r))
            by_cos[cs] = cur[-1]
            added.append(cs)
        else:
            changed = False
            for k, v in r.items():
                if v and not old.get(k):
                    old[k] = v
                    changed = True
            if changed:
                updated.append(cs)
    auto[gk] = cur
    write_json(AUTO, auto)
    return added, updated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="无视 12 小时间隔")
    ap.add_argument("--dry", action="store_true", help="只打印计划，不联网不写文件")
    ap.add_argument("--seed-only", action="store_true", help="只初始化状态文件，不联网")
    ap.add_argument("--if-watch", action="store_true",
                    help="只在有观察期批次时才联网（用于 18:00 的补检）")
    args = ap.parse_args()

    now = time.time()
    state = read_json(STATE, {})
    pages = state.get("pages") or {}
    known = set(state.get("cospar") or [])
    # 首次运行/状态缺失时，用现有自动任务表打底，避免"已知批次 0 个"
    if not known:
        for _gk, _rows in (read_json(AUTO, {}) or {}).items():
            for _r in _rows or []:
                if _r.get("cospar"):
                    known.add(str(_r["cospar"]))
        print("  状态初始化：从自动任务表继承 %d 个已知批次" % len(known))

    plan, skipped = [], []
    for gk in PAGES:
        st = pages.get(gk) or {}
        watch = {cs: until for cs, until in (st.get("watch") or {}).items() if until > now}
        # ★ 门槛看"最后一次尝试"而不是"最后一次成功"：抓取失败也要等到下一轮，
        #   否则每次开机都会连发请求（今天实测：失败后 12 小时门槛形同虚设）
        last_try = float(st.get("last_attempt") or st.get("last_check") or 0)
        due_daily = (now - last_try) >= MIN_GAP
        if args.force or due_daily or watch:
            plan.append((gk, bool(watch)))
        else:
            skipped.append((gk, int((MIN_GAP - (now - last_try)) / 3600)))

    if args.if_watch and not any((pages.get(g) or {}).get("watch") for g in PAGES):
        print("没有处于观察期的批次，本轮不联网。")
        return 0

    if args.seed_only:
        state["cospar"] = sorted(known)
        state.setdefault("pages", {})
        state["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write_json(STATE, state)
        write_json(COSPAR_FP, {"updated": state["updated"], "cospar": sorted(known)})
        print("已初始化状态：%d 个已知批次 → cache/wiki_watch.json" % len(known))
        return 0

    if args.dry or not plan:
        print("巡检计划：%s" % ("、".join("%s%s" % (g, "（观察期）" if w else "") for g, w in plan) or "无"))
        if skipped:
            print("跳过的页面：%s" % "、".join("%s（%d 小时后到期）" % s for s in skipped))
        if not plan:
            print("本轮不联网（省请求）。")
        return 0

    import server  # 复用它的 cookie 会话 / 限速 / 解析

    print("开始巡检：%s" % "、".join(g for g, _ in plan))
    new_total, fix_total = [], []
    for gk, in_watch in plan:
        page = None
        for item in server.WIKI_PAGES:
            if item[0] == gk:
                page = item[1]
                break
        if not page:
            print("  %s：找不到词条配置" % gk)
            continue
        html = server.wiki_fetch_page(page)
        if not html:
            st_fail = pages.get(gk) or {}
            st_fail["last_attempt"] = now          # 失败也记时间，避免连发
            pages[gk] = st_fail
            print("  %s：抓取失败（保持原状态；下次尝试要等 %d 小时）" % (gk, MIN_GAP // 3600))
            continue
        parsed = (server.parse_wiki_launches(html) or {}).get(gk) or []
        h = rows_hash(parsed)
        st = pages.get(gk) or {}
        old_h = st.get("hash")
        watch = {cs: until for cs, until in (st.get("watch") or {}).items() if until > now}

        added, updated = merge_into_auto(gk, parsed)
        for cs in added:
            watch[cs] = now + WATCH_DAYS * 86400
            new_total.append(cs)
            print("  + %s 新批次 %s（进入 %d 天观察期）" % (gk, cs, WATCH_DAYS))

        # 观察期内：报告字段修正
        if in_watch and old_h and h != old_h:
            old_rows = {str(r.get("cospar")): r for r in (st.get("rows") or [])}
            for r in parsed:
                cs = str(r.get("cospar") or "")
                if cs not in watch:
                    continue
                o = old_rows.get(cs) or {}
                diff = [(k, o.get(k), r.get(k)) for k in
                        ("mission", "rocket", "time", "site", "dev")
                        if o.get(k) and r.get(k) and o.get(k) != r.get(k)]
                if diff:
                    fix_total.append((cs, diff))
                    print("     修正 %s：%s" % (cs, "；".join("%s %r→%r" % d for d in diff)))

        pages[gk] = {"hash": h, "last_check": now, "last_attempt": now, "watch": watch,
                     "rows": [{k: r.get(k) for k in
                               ("cospar", "mission", "rocket", "time", "site", "dev")}
                              for r in parsed]}
        known |= {str(r.get("cospar")) for r in parsed if r.get("cospar")}

    state["pages"] = pages
    state["cospar"] = sorted(known)
    state["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_json(STATE, state)
    write_json(COSPAR_FP, {"updated": state["updated"], "cospar": sorted(known)})

    print("巡检完成：新增 %d 个批次、修正 %d 处；已知批次共 %d 个"
          % (len(new_total), len(fix_total), len(known)))
    if new_total or fix_total:
        print("下一步：跑 make_qf_html.py / make_gw_html.py 重出图，再提交")
    return 0


if __name__ == "__main__":
    sys.exit(main())
