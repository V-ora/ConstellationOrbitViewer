# -*- coding: utf-8 -*-
"""用 CelesTrak 的「批量根数」接口把 data/sat_hist.json 的曲线末点追平到今天。

为什么需要它：graph-orbit-data.php（逐日历史重接口）**很慢**（实测 35~37 s/颗，
2026-10-03 复核定案：不是无响应，是慢；timeout 给够就能通）。逐颗抓几百颗要等很久，
所以日常「追平末点」这条快路径改走批量根数：
    每颗星取最新 GP 根数（OMM/JSON）→ 平均运动 n 换算半长轴 a=(mu/n^2)^(1/3)
    → 平均高度 a-6378.137 → 作为「今天」的数据点追加到曲线末尾。
对已抵达工作轨道的星，这与真实高度等价（高度本就稳定）；对仍在爬升的星，
上次历史末点（多为 09-26）到今天之间是一段直线，真实爬升细节可用 update_hist.py
（现在预检阈值已放宽到 60 s，能正常跑）重新校准。

★ 落盘两处：项目 data/sat_hist.json（生成大图的源）+ 运行实例的 sat_hist_extra.json
  （源码态 cache\\、exe 态 %LOCALAPPDATA%\\ConstellationOrbitViewer\\cache\\），
  这样**不重打包也能立刻在查看器里看到**。

用法：python local_backfill.py [--dry]     # --dry 只报告不落盘
"""
import urllib.request, ssl, json, io, os, math, sys, datetime, time
import concurrent.futures as cf

HERE = os.path.dirname(os.path.abspath(__file__))
FP = os.path.join(HERE, "data", "sat_hist.json")
BK = os.path.join(HERE, "data", "sat_hist.json.bak")
BASE_DATE = datetime.date(2024, 1, 1)
MU = 398600.4418
R_EARTH = 6378.137
GP = "https://celestrak.org/NORAD/elements/gp.php?"


def _extra_paths():
    """运行实例真正会读的「补抓历史」文件路径（可能有好几个）。

    ★ 2026-10-03 修复：本脚本原来只写项目里的 data/sat_hist.json，而运行实例
      （尤其打包后的 exe）只读「包内快照 + cache/sat_hist_extra.json」——
      手动补的历史对正在跑的查看器**完全不可见**，除非重打包。
    源码态运行时 CACHE_DIR = <项目>\\cache；exe 运行时 =
      %LOCALAPPDATA%\\ConstellationOrbitViewer\\cache，所以两处都要写。

    ★★ 2026-10-20 修复（SpaceTrack 版的逐日历史一直没长）：缓存目录**有两套** ——
      默认版用 ``cache``，``COV_DATA_SRC=spacetrack`` 用 ``cache_st``
      （见 server.py 的 `_CACHE_LEAF`）。本函数原来只写 ``cache``，
      于是 ST 版的服务端去 ``cache_st`` 读 extras 永远读不到，补抓白跑。
      现在两个 leaf 都写；不存在的目录不创建（避免给用不到的版本凭空造目录）。
    """
    out = []
    roots = []
    la = os.environ.get("LOCALAPPDATA")
    if la:
        roots.append((os.path.join(la, "ConstellationOrbitViewer"), True))   # exe 侧
    roots.append((HERE, False))                                            # 工程侧
    for root, is_exe in roots:
        for leaf in ("cache", "cache_st"):
            d = os.path.join(root, leaf)
            if is_exe or os.path.isdir(d):
                out.append(os.path.join(d, "sat_hist_extra.json"))
    return out


def write_extras(appended, base_sats=None):
    """把本次追加过点的条目并进 sat_hist_extra.json（保留原有内容）。

    appended: {norad(str): entry}。extras 的 entry 结构 = server.py 的
    _hist_entry_for() 产物（name/src/shell/launch/target/status/cospar/pts）。

    ★ 2026-10-15：并完之后**剔除「与项目库逐点相同」的条目**（仅针对**工程内 cache\ 那份**）。
      extras 的职责只是「库外的增量」，而本函数原来是纯 update、从不清理 —— 每轮 refresh 都给
      几乎全部 473 颗追加当天点 → 文件滚成整份库的副本（2.57 MB）。

    ★★ 2026-10-19 修正：**exe 侧（%LOCALAPPDATA%）不能按「磁盘库」去冗余**。
      两个入口的「基线」不是同一份：
        - 源码 server：基线 = 磁盘 ``data/sat_hist.json``（每轮刚更新）⇒ 重复点该剔；
        - exe：基线 = **打包时打进包的** ``data/sat_hist.json``（会越来越旧）
          ⇒ 与磁盘库相同的点，对 exe 恰恰是「它还没有的新点」。
      按磁盘库剔除 = 把 exe 该看到的增量清空，**exe 里的逐日历史会静默停在打包日**
      （实测 10-19：包内基线 10-15，extras 被清成 0 颗）。
      所以 exe 侧改成**只保留最近 30 天的点**（480 颗 × 30 天 ≈ 200 KB，不会滚成整库），
      由服务端 ``_hist_append_pts`` 的「完全重复不追加」去兜重复 —— 重复在这里是无害的。
    """
    n_ok = 0
    _local = (os.environ.get("LOCALAPPDATA") or "").lower()
    _cut = (datetime.date.today() - BASE_DATE).days - 30
    for path in _extra_paths():
        is_exe_side = bool(_local) and path.lower().startswith(_local)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            state = {}
            if os.path.exists(path):
                try:
                    state = json.load(io.open(path, encoding="utf-8")) or {}
                except Exception:
                    state = {}
            if not isinstance(state, dict):
                state = {}
            state.setdefault("sats", {})
            state.setdefault("attempts", {})
            state["sats"].update(appended or {})
            dropped = 0
            if is_exe_side:
                # exe 侧：**从库直接重建**「最近 30 天」的点。
                #   不依赖 `appended`（那只在「本轮确实追加了」时才有内容；
                #   同一天重复跑 refresh 时追加 0 颗，靠它就会漏掉）。
                for k, e in (base_sats or {}).items():
                    pts = [p for p in (e.get("pts") or []) if p[0] >= _cut]
                    if not pts:
                        continue
                    cur = state["sats"].get(k)
                    if not isinstance(cur, dict):
                        cur = {}
                    cur.update({kk: vv for kk, vv in e.items() if kk != "pts"})
                    cur["pts"] = pts
                    state["sats"][k] = cur
            elif base_sats:
                for k in list(state["sats"]):
                    b = base_sats.get(k)
                    if b is not None and (state["sats"][k].get("pts") or []) == (b.get("pts") or []):
                        state["sats"].pop(k)
                        dropped += 1
            state["now"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            io.open(path, "w", encoding="utf-8").write(
                json.dumps(state, ensure_ascii=False, separators=(",", ":")))
            print("已并入运行实例 -> %s（本次 %d 颗，%s，余 %d 颗，%.0f KB）"
                  % (path, len(appended or {}),
                     "exe 侧重建近 30 天" if is_exe_side else
                     ("剔除与库相同 %d 颗" % dropped),
                     len(state["sats"]), os.path.getsize(path) / 1024.0), flush=True)
            n_ok += 1
        except Exception as e:
            print("!! 写 extras 失败 %s: %s" % (path, e), flush=True)
    if not n_ok:
        print("!! 没有可写的 extras 路径（运行实例可能看不到本次结果）", flush=True)
    return n_ok

ssl._create_default_https_context = ssl._create_unverified_context
# ★ 2026-10-19：**直连优先**。原来固定「代理 → 直连」，而本机环境代理时好时坏，
#   每次请求都可能先白等一轮超时（实测 dry-run 跑出 82 秒，正常只要几秒）。
#   并用 _GOOD 记住「上次成功的出口」，后续请求一步到位。
_OPENERS = [urllib.request.build_opener(urllib.request.ProxyHandler({})),  # 直连
            urllib.request.build_opener()]                                 # 走环境代理
_GOOD = [None]      # 上次成功的 opener 下标（进程内记忆）


def fetch(url, timeout=25):
    last = None
    order = list(range(len(_OPENERS)))
    if _GOOD[0] is not None:
        order = [_GOOD[0]] + [i for i in order if i != _GOOD[0]]
    for att in range(3):
        for i in order:
            try:
                raw = _OPENERS[i].open(urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0"}), timeout=timeout).read()
                _GOOD[0] = i
                return raw
            except Exception as e:
                last = e
    raise last


def get_json(url):
    """取回并解析 JSON（并发任务里用，保持和原来 json.loads(fetch(...)) 完全一致）。"""
    return json.loads(fetch(url).decode("utf-8"))


def alt_of(gp):
    n = float(gp["MEAN_MOTION"]) * 2 * math.pi / 86400.0
    return (MU / (n * n)) ** (1.0 / 3.0) - R_EARTH


def main():
    dry = "--dry" in sys.argv
    hist = json.load(io.open(FP, encoding="utf-8"))
    sats = hist["sats"]
    today = datetime.date.today()
    t_today = (today - BASE_DATE).days

    # ---- 1) 拉当前高度 ----
    # ★ 2026-10-19 提速：原来「2 个组接口 → 高轨 3 颗 → 兜底 N 颗」全串行，
    #   实测单次 1.1~2.8 s、整体 11.9~82 s（网络差时更惨）。改成两段并发：
    #   A 段 = 两个组接口 + 高轨单星（互不依赖，一起发）；B 段 = 兜底单星（要先有 A 段结果）。
    #   ★ `CATNR` **不支持逗号批量**（实测回 "CATNR=1,2,3 is not an integer"），
    #     所以只能并发、不能合并请求。
    cur = {}
    gaogui = [n for n, e in sats.items() if e.get("src") == "gaogui"]

    # —— A 段
    _t_all = time.time()
    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        fg = {g: ex.submit(get_json, GP + "GROUP=%s&FORMAT=json" % g)
              for g in ("qianfan", "hulianwang")}
        fc = {n: ex.submit(get_json, GP + "CATNR=%s&FORMAT=json" % n) for n in gaogui}
        for g, f in fg.items():
            try:
                data = f.result()
            except Exception as e:
                print("!! GROUP=%s 拉取失败: %s" % (g, e), flush=True)
                return 1
            for s in data:
                cur[str(s["NORAD_CAT_ID"])] = round(alt_of(s), 1)
            print("GROUP=%-11s %d 颗（中位高度 %.1f km）"
                  % (g, len(data), sorted(alt_of(s) for s in data)[len(data) // 2]), flush=True)
        for n, f in fc.items():
            try:
                data = f.result()
                if data:
                    cur[n] = round(alt_of(data[0]), 1)
            except Exception as e:
                print("!! 高轨 %s 拉取失败: %s" % (n, e), flush=True)
    print("高轨 %d 颗已取高度" % sum(1 for n in gaogui if n in cur), flush=True)
    print("[时序] A 段（2 组接口 + 高轨）%.1fs" % (time.time() - _t_all), flush=True)
    _t_b = time.time()

    # —— B 段：兜底（历史库里有、但已不在任何星座组里 → 按 CATNR 单颗抓，数量极少）
    miss = [n for n, e in sats.items() if n not in cur and (e.get("pts") or [])]
    if miss:
        with cf.ThreadPoolExecutor(max_workers=5) as ex:
            fm = {n: ex.submit(get_json, GP + "CATNR=%s&FORMAT=json" % n) for n in miss}
            for n, f in fm.items():
                try:
                    data = f.result()
                    if data:
                        cur[n] = round(alt_of(data[0]), 1)
                        print("  兜底取到 %s %s: %.1f km"
                              % (n, data[0].get("OBJECT_NAME"), cur[n]), flush=True)
                except Exception as e:
                    print("!! 兜底 %s 失败: %s" % (n, e), flush=True)
    extra = [n for n in cur if n not in sats]
    print("[时序] B 段（兜底 %d 颗）%.1fs；取数合计 %.1fs"
          % (len(miss), time.time() - _t_b, time.time() - _t_all), flush=True)
    if extra:
        print("CelesTrak 组里有、本地历史库尚无的星 %d 颗: %s"
              % (len(extra), ", ".join(sorted(extra)[:20])), flush=True)

    # ---- 2) 追加「今天」的点 ----
    n_app, n_same, n_miss, n_newer = 0, 0, 0, 0
    appended = {}          # ★ 同步给运行实例（cache/sat_hist_extra.json）
    for n, e in sats.items():
        pts = e.get("pts") or []
        if not pts:
            continue
        if n not in cur:
            n_miss += 1
            continue
        if pts[-1][0] >= t_today:
            n_newer += 1
            continue
        e["pts"] = pts + [[t_today, cur[n]]]
        appended[n] = e
        n_app += 1
    print("追加 %d 颗 · 已有今天点 %d 颗 · 无当前高度(跳过) %d 颗" % (n_app, n_newer, n_miss), flush=True)
    if n_miss:
        print("  跳过的 NORAD: %s" % ", ".join(
            n for n in sats if n not in cur and (sats[n].get("pts") or []) and
            sats[n]["pts"][-1][0] < t_today)[:400], flush=True)

    if dry:
        print("(--dry，未落盘)", flush=True)
        return 0

    # ---- 3) 备份 + 落盘 ----
    if not os.path.exists(BK):
        io.open(BK, "w", encoding="utf-8").write(io.open(FP, encoding="utf-8").read())
        print("备份 -> %s" % BK, flush=True)
    hist["now"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    io.open(FP, "w", encoding="utf-8").write(
        json.dumps(hist, ensure_ascii=False, separators=(",", ":")))
    print("已写入 %s（now=%s，日期 %s）" % (FP, hist["now"], today.isoformat()), flush=True)

    # ---- 4) 同步给正在运行的查看器（否则要重打包才生效）----
    #    ★ 2026-10-15：即使本次没有新点也照样跑一遍 —— write_extras 会顺带把
    #      「与库逐点相同」的冗余条目剔掉（旧实现只增不减，哪天就把 extras 滚成整库副本）。
    write_extras(appended, sats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
