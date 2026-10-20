# -*- coding: utf-8 -*-
"""
星座轨道实时查看器 —— 本地后端服务
================================================
职责：
  1. 提供静态页面（web/index.html + 依赖 js）
  2. 代理 CelesTrak 抓取 TLE（千帆 / 国网·星网），落盘缓存到 cache/
  3. 合并本地已知的发射任务信息（data/*.json），供前端展示
  4. 自动探测可用网络代理（本机常见代理端口），并把探测结果记进 cache/proxy.json

只监听 127.0.0.1，纯标准库，无第三方依赖。

用法：
  python server.py                 # 默认 127.0.0.1:8770
  python server.py --port 9000
  python server.py --proxy http://127.0.0.1:7890
"""

import argparse
import json
import math
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 控制台可能是 cp936（中文 cmd）也可能是 utf-8（bat 里 chcp 65001），
# 保持原编码只兜住编码错误，避免中文日志把进程打崩
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

APP_DIR = os.path.dirname(os.path.abspath(__file__))
FROZEN = bool(getattr(sys, "frozen", False))

# ---------------------------------------------------------------- 数据来源（双源）
# COV_DATA_SRC=celestrak（默认，行为与历史版本完全一致）| spacetrack。
# SpaceTrack 版 exe 由 PyInstaller runtime hook（_st_data_src_hook.py）注入此变量，
# 源码运行也可以 `set COV_DATA_SRC=spacetrack` 手动切换。两版**独立缓存目录**，
# 互不覆盖（根数内容同源近似，但 meta「刚刷新过」的时间戳混写会让两版互相踩）。
DATA_SRC = (os.environ.get("COV_DATA_SRC") or "celestrak").strip().lower()
if DATA_SRC not in ("celestrak", "spacetrack"):
    DATA_SRC = "celestrak"
SRC_LABEL = "Space-Track.org" if DATA_SRC == "spacetrack" else "CelesTrak"
_CACHE_LEAF = "cache_st" if DATA_SRC == "spacetrack" else "cache"

# 打包成 exe 后：只读资源（web/data）在解包目录，缓存必须写用户可写目录
BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)
if FROZEN:
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    CACHE_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or APP_DIR,
                             "ConstellationOrbitViewer", _CACHE_LEAF)
else:
    CACHE_DIR = os.path.join(APP_DIR, _CACHE_LEAF)
WEB_DIR = os.path.join(BUNDLE_DIR, "web")
DATA_DIR = os.path.join(BUNDLE_DIR, "data")
os.makedirs(CACHE_DIR, exist_ok=True)

# 本次启动的唯一标识：页面每 10 秒核对一次，发现变化就提示「程序已更新，点击刷新」，
# 防止用户浏览器里一直开着旧版页面（旧页面里新功能不存在、旧 bug 还在，用户会误报"没反应"）
BOOT_ID = time.strftime("%Y%m%d%H%M%S")

# 无控制台打包（spec console=False）下 sys.stdout/stderr 是 None，print 会直接报错：
# 统一重定向到 %LOCALAPPDATA%\ConstellationOrbitViewer\run.log，日志照常落盘可查
if sys.stdout is None or sys.stderr is None:
    _log_dir = os.path.dirname(CACHE_DIR)
    os.makedirs(_log_dir, exist_ok=True)
    try:
        _runlog = open(os.path.join(_log_dir, "run.log"), "a",
                       buffering=1, encoding="utf-8", errors="replace")
        sys.stdout = _runlog
        sys.stderr = _runlog
    except Exception:
        try:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
            sys.stderr = sys.stdout
        except Exception:
            pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 ConstellationOrbitViewer/1.0")

# 网络出口探针 URL：按数据源走各自的站（resolve_network 只判通不通，不校验内容）
_NET_PROBE_URL = ("https://www.space-track.org/" if DATA_SRC == "spacetrack"
                  else "https://celestrak.org/NORAD/elements/gp.php?NAME=qianfan&FORMAT=tle")

TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?NAME={name}&FORMAT=tle"
OMM_URL = "https://celestrak.org/NORAD/elements/gp.php?NAME={name}&FORMAT=json"
GPJSON_URL = "https://celestrak.org/NORAD/elements/gp.php?NAME={name}&FORMAT=json"
SATCAT_URL = "https://celestrak.org/satcat/records.php?NAME={name}&FORMAT=json"

# ---------------------------------------------------------------- 星座分组定义
GROUPS = {
    "qianfan": {
        "label": "千帆星座",
        "sub": "QIANFAN · 上海垣信",
        "name": "qianfan",
        "color": "#f5a524",
        "ttl": 2 * 3600,
        "backfill": True,   # NAME 检索漏掉的已编目星，用 CATNR 逐颗补抓
    },
    "gw": {
        "label": "国网 / 星网 GW 星座",
        "sub": "HULIANWANG DIGUI · 中国星网",
        "name": "hulianwang digui",
        "color": "#3b9dff",
        "ttl": 2 * 3600,
        "backfill": True,
        "business_only": True,   # 只录业务星（跳过 mission 含「试验」的批次）
        # 高轨三星归 gaogui 分组，避免重复
        "cospar_skip": {"2024-040", "2024-135", "2024-181"},
        # ★ 快路径（2026-10-07）：NAME=hulianwang digui 只覆盖旧名 186 颗；2026 下半年
        #   新批次改名 GUOWANG NN OBJECT X 后名字检索抓不到 → 老链路要 42 次 SATCAT
        #   国际标识符检索 + 27 次 CATNR 逐颗补抓（约 70 个串行请求，冷启动 1~3 分钟）。
        #   改用编目组接口 gp.php?GROUP=hulianwang 一次拿全 217 行（含 GUOWANG 新名），
        #   剔除 4 颗技术试验星后正好 213 颗，与老链路集合逐 NORAD 核对过完全一致。
        #   冷启动请求数降到 1~2 个，约 2~8 秒。
        #   ★ 2026-10-07：CelesTrak 把这 4 颗从旧名「GUOWANG TEST OBJECT A~D」(2026-120)
        #     改成了现名「HULIANWANG JISHU SHIYAN 10A~10D」→ 原来只按 "TEST" 过滤就失效了
        #     （国网会虚增到 217）。现同时按 "TEST" 与 "JISHU SHIYAN" 过滤，恢复 213。
        "group": "hulianwang",
        "drop_name_contains": ("TEST", "JISHU SHIYAN"),
    },
    "gaogui": {
        "label": "星网高轨（同步轨道）",
        "sub": "卫星互联网高轨卫星 01/02/03 星",
        "name": "hulianwan gaogui",
        "color": "#ef4444",
        "ttl": 2 * 3600,
    },
}

# ---------------------------------------------------------------- 取数 URL（组 / 名字）
GROUP_OMM_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={g}&FORMAT=json"
GROUP_TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={g}&FORMAT=tle"


def group_omm_url(g):
    """OMM(JSON) 取数 URL：有编目组名就用组接口（一次拿全），否则按名字检索。"""
    if g.get("group"):
        return GROUP_OMM_URL.format(g=urllib.parse.quote(g["group"]))
    return OMM_URL.format(name=urllib.parse.quote(g["name"]))


def group_tle_url(g):
    """FORMAT=tle 回退 URL，规则同上。"""
    if g.get("group"):
        return GROUP_TLE_URL.format(g=urllib.parse.quote(g["group"]))
    return TLE_URL.format(name=urllib.parse.quote(g["name"]))


def filter_group_rows(g, arr):
    """组接口会把本组内不属于业务星座的对象一起带出来（如 GUOWANG TEST OBJECT），
    按名字关键字剔除。arr 非列表时原样返回，由调用方判空。"""
    if not isinstance(arr, list):
        return arr
    drops = tuple((x or "").upper() for x in (g.get("drop_name_contains") or ()))
    if not drops:
        return arr
    out = []
    for r in arr:
        if not isinstance(r, dict):
            continue
        nm = (r.get("OBJECT_NAME") or "").upper()
        if any(d in nm for d in drops):
            continue
        out.append(r)
    return out


def filter_group_tle_text(g, text):
    """同样的剔除规则，作用于 FORMAT=tle 的文本行（每 3 行一组：名称/L1/L2）。"""
    drops = tuple((x or "").upper() for x in (g.get("drop_name_contains") or ()))
    if not drops:
        return text
    lines = [ln for ln in text.splitlines() if ln.strip()]
    keep = []
    for i in range(0, len(lines) - 2, 3):
        blk = lines[i:i + 3]
        if any(d in (blk[0] or "").upper() for d in drops):
            continue
        keep.extend(blk)
    return "".join(ln + "\n" for ln in keep)


PROXY_PORTS = [57186, 51558, 7897, 7890, 10809, 10808, 1080, 8888, 8080, 3128, 2080, 7078]
PROXY_HOSTS = ["127.0.0.1"]

_proxy_lock = threading.Lock()
_proxy_state = {"proxy": None, "mode": "unknown", "checked_at": None}
_NET_PROBE_COOLDOWN = 300   # 「无可用出口」状态的重探冷却（秒），期间秒回缓存


# ---------------------------------------------------------------- 工具函数
def now_local():
    return datetime.now().astimezone()


def iso_local(dt):
    return dt.astimezone().isoformat(timespec="seconds")


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# 单星爬升历史（extract_hist.py 生成，源头为 CelesTrak 逐日积累；缺失时接口返回 404）
SAT_HIST = read_json(os.path.join(DATA_DIR, "sat_hist.json"),
                     {"base": "2024-01-01", "now": "", "sats": {}})

# ---------------------------------------------------------------------------
# 爬升历史自动补齐（2026-10-01）：包内 sat_hist.json 是打包时快照，CelesTrak 新编目的
# 星永远不在里面 → 批次爬升图 404。这里让服务端自己发现缺历史的星、自己从 CelesTrak
# graph-orbit-data 补抓逐日高度，存到用户可写目录（CACHE_DIR/sat_hist_extra.json），
# 并入内存 SAT_HIST 立即生效——**无需重打包**。每 3 小时检查一轮，每颗星每天最多试一次
# （刚发射还没积累历元的星，CelesTrak 那边暂时也没有 plotData，隔天再试即可）。
_HIST_EXTRA_PATH = os.path.join(CACHE_DIR, "sat_hist_extra.json")
_HIST_GRAPH_URL = "https://celestrak.org/NORAD/elements/graph-orbit-data.php?CATNR="
_HIST_BASE_DATE = datetime(2024, 1, 1).date()
_HIST_TARGET = {"qianfan": 1068.5, "gaogui": 35786.0}
_HIST_GW_TARGET = {50: 1149.3, 86: 1167.9}   # 50°壳层 / 86.5°壳层


# ★ 包内基线每颗星的原始点数（启动时快照）。extras 只存「基线之外的部分」，
#   否则每追加一个点就整条复制 → 文件涨成整库的副本（2.6 MB），每轮还要反复写盘。
_HIST_BASE_LEN = {}


def _hist_append_pts(entry, pts):
    """把 pts 里基线没有的点按序补进 entry。返回实际新增点数。
    两条护栏：① 完全重复的 (t, 高度) 不再追加（旧写法会逐日堆重复点）；
             ② 同一 t 已有读数、且新读数与它差 >50 km → 判为脏点拒收
                （实测 DIGUI-103 @ t=707.5 冒出过一个 447.3 km 的假读数，
                  会让大图戳出一根向下的尖刺；旧的 extras 里还留着它）。"""
    out = entry.setdefault("pts", [])
    by_t = {}
    for t, a in out:
        by_t.setdefault(round(float(t), 6), []).append(round(float(a), 1))
    added = 0
    for t, a in pts or []:
        tt, aa = round(float(t), 6), round(float(a), 1)
        alts = by_t.get(tt)
        if alts is not None:
            if any(abs(x - aa) <= 0.05 for x in alts):
                continue                                    # ① 完全重复
            if min(abs(x - aa) for x in alts) > 50:
                continue                                    # ② 同 t 离群读数
            alts.append(aa)
        else:
            by_t[tt] = [aa]
        out.append([t, a])
        added += 1
    if added:
        # 旧 extras 可能把补抓点塞在末尾（t 反而更小）→ 会让曲线倒序、图上出现回折。
        # 稳定排序既保序又不打乱同 t 的先后。
        try:
            out.sort(key=lambda q: float(q[0]))
        except Exception:
            pass
    return added


def _hist_extras_doc(state):
    """按当前 SAT_HIST 重建 extras 文档：基线里没有的新星存整条，基线里有的只存多出来的点。"""
    sats_out, app_out = {}, {}
    for n, e in (SAT_HIST.get("sats") or {}).items():
        bl = _HIST_BASE_LEN.get(n)
        if bl is None:                       # 基线快照里没有 → 本地补抓的新星，整条存
            sats_out[n] = e
            continue
        pts = e.get("pts") or []
        if len(pts) > bl:                    # 基线里有的星 → 只存 baseline 之后追加的点
            app_out[n] = pts[bl:]
    if sats_out:
        state["sats"] = sats_out
    else:
        state.pop("sats", None)
    if app_out:
        state["appends"] = app_out
    else:
        state.pop("appends", None)
    return state


def _hist_save_extras(state):
    try:
        write_json(_HIST_EXTRA_PATH, _hist_extras_doc(dict(state)))
    except Exception as e:
        log("历史补齐：extras 落盘失败 %s" % e)


def _hist_load_extras():
    """启动时把用户目录里的补抓历史并入内存 SAT_HIST（包内快照 + 本地补抓 = 完整库）。
    同时兼容旧的「整库复制」格式：整条塞回来的条目只取基线之外多出来的点。"""
    global _HIST_BASE_LEN
    base = SAT_HIST.setdefault("sats", {})
    _HIST_BASE_LEN = {n: len(e.get("pts") or []) for n, e in base.items()}
    state = read_json(_HIST_EXTRA_PATH) or {}
    n_new = n_add = 0
    for n, e in (state.get("sats") or {}).items():
        if n not in base:
            base[n] = e
            n_new += 1
        else:
            n_add += _hist_append_pts(base[n], e.get("pts") or [])
    for n, pts in (state.get("appends") or {}).items():
        if n in base:
            n_add += _hist_append_pts(base[n], pts)
    if n_new or n_add:
        log("历史库：并入本地补抓（新星 %d 颗 / 追加 %d 点，%s）"
            % (n_new, n_add, _HIST_EXTRA_PATH))
    # ★ 启动自愈（2026-10-07）：旧版本（或仍跑着旧代码的实例）会把**整库副本**落盘
    #   （实测 2.5 MB / 473 颗逐点与库相同）。读完之后无条件按当前基线重写一次 ——
    #   只写真正的增量，文件立刻缩回几十字节~几 KB。数据无损：extras 的内容此刻
    #   全都已经在内存 SAT_HIST 里（基线 + 并入的增量）。写失败只记日志，不影响启动。
    _hist_save_extras({})
    return state


def _hist_parse_plotdata(raw_bytes):
    """graph-orbit-data 页面里的 var plotData → [(iso_date, alt_median)]（逐日中位数）。"""
    m = re.search(r'var plotData = "(.*?)";', raw_bytes.decode("utf-8", "replace"), re.S)
    if not m:
        return None
    import collections as _collections
    import statistics as _statistics
    g = _collections.defaultdict(list)
    for row in m.group(1).split("|")[1:]:
        if not row.strip():
            continue
        f = row.split(",")
        if len(f) < 6:
            continue
        try:
            t = datetime.fromisoformat(f[0][:19])
            g[t.date()].append(float(f[4]))
        except Exception:
            continue
    if not g:
        return None
    return [(d.isoformat(), round(_statistics.median(v), 1)) for d, v in sorted(g.items())]


def _hist_entry_for(group_key, sat, meta_map_entry, daily):
    base = _HIST_BASE_DATE
    pts = []
    for d_iso, alt in daily:
        d = datetime.fromisoformat(d_iso).date()
        pts.append([round((d - base).days * 4) / 4, alt])
    if group_key == "gw":
        shell = 50 if (sat.get("incl") or 0) < 70 else 86
        shell_txt, target = "%d°倾角壳层" % shell, float(_HIST_GW_TARGET[shell])
    elif group_key == "gaogui":
        shell_txt, target = "GEO", float(_HIST_TARGET["gaogui"])
    else:
        shell_txt, target = None, float(_HIST_TARGET.get(group_key) or 0)
    return {
        "name": sat.get("name") or ("NORAD " + str(sat.get("norad"))),
        "src": group_key,
        "shell": shell_txt,
        "launch": (meta_map_entry or {}).get("launch_date") or "",
        "target": target,
        "status": "",
        "cospar": (meta_map_entry or {}).get("cospar") or "",
        "pts": pts,
    }


def _hist_backfill_once(max_fetch=40):
    """每轮做两件事：
    ① 追加当天数据点：用已抓取的 OMM 根数平均高度，给所有「还没有今天这个点」的
       历史条目追加 [t_today, alt] —— 零额外请求，曲线每天自动生长（2026-10-01 用户
       反馈"千帆数据停在 9 月 26 日"的根因就是旧逻辑只补缺星、不刷新已有星）。
    ② 缺整颗历史的星（新编目）→ 从 CelesTrak graph-orbit-data 补抓完整逐日历史。
    """
    try:
        net = resolve_network()
    except Exception:
        return 0
    if net.get("mode") == "none":
        return 0
    known = SAT_HIST.setdefault("sats", {})
    t_today = float((datetime.now().date() - _HIST_BASE_DATE).days)
    missing = []
    append_cands = []   # (norad, entry, alt)
    for g_key in GROUPS:
        try:
            tle_f, _ = cache_paths(g_key)
            with open(tle_f, "r", encoding="utf-8", errors="replace") as f:
                sats = parse_tle(f.read())
        except Exception:
            continue
        if not sats:
            continue
        try:
            meta_map = fetch_group_meta(g_key, allow_network=False) or {}
        except Exception:
            meta_map = {}
        for s in sats:
            n = str(s["norad"])
            e = known.get(n)
            if e is None:
                missing.append((g_key, s, meta_map.get(n)))
            elif e.get("pts") and e["pts"][-1][0] < t_today - 0.25 and s.get("alt"):
                append_cands.append((n, e, float(s["alt"])))
    if not missing and not append_cands:
        return 0

    # ① 追加当天数据点（零请求）。extras 由 _hist_save_extras 按基线快照自动只存增量，
    #    不再把整条历史塞回去（旧写法等于每天复制一份整库，2.6 MB）。
    n_app = 0
    if append_cands:
        state = read_json(_HIST_EXTRA_PATH) or {"attempts": {}}
        state.setdefault("attempts", {})
        for n, e, alt in append_cands:
            n_app += _hist_append_pts(e, [[t_today, round(alt, 1)]])
        if n_app:
            state["now"] = iso_local(now_local())
            _hist_save_extras(state)
            log("历史追加：%d 颗补上今天的数据点（曲线尾部 → 第 %.0f 天）" % (n_app, t_today))

    # ② 缺整颗历史的星 → graph 补抓（每天每星最多试一次，单轮限量）
    if not missing:
        return n_app
    log("历史补齐：发现 %d 颗卫星缺爬升历史，开始补抓（本轮最多 %d 颗）"
        % (len(missing), max_fetch))
    state = read_json(_HIST_EXTRA_PATH) or {"attempts": {}}
    state.setdefault("attempts", {})
    today = datetime.now().date().isoformat()
    n_new = 0
    for g_key, s, m_entry in missing:
        if n_new >= max_fetch:
            break
        n = str(s["norad"])
        if state["attempts"].get(n) == today:
            continue   # 今天已经试过（比如 CelesTrak 还没有这颗星的 plotData）
        state["attempts"][n] = today
        try:
            if DATA_SRC == "spacetrack":
                # ST：gp_history 全量历元 → 按天中位（与 CT plotData 同源同形状）
                daily = st_hist_daily(n)
            else:
                raw = http_get(_HIST_GRAPH_URL + n, proxy=net.get("proxy"), timeout=45)
                daily = _hist_parse_plotdata(raw)
        except Exception as e:
            log("历史补齐：%s（%s）抓取失败：%s" % (s.get("name"), n, e))
            _hist_save_extras(state)
            continue
        if not daily or len(daily) < 2:
            # 刚编目还没积累历元 → 明天再试（attempts 已记录今天试过）
            _hist_save_extras(state)
            continue
        entry = _hist_entry_for(g_key, s, m_entry, daily)
        SAT_HIST["sats"][n] = entry      # 落盘时按基线快照自动判为「新星」，整条存进 extras
        state["now"] = iso_local(now_local())
        n_new += 1
        _hist_save_extras(state)   # 逐星落盘，中途被杀不丢已抓数据
        log("历史补齐：%s（%s）+%d 点（%s ~ %s）"
            % (s.get("name"), n, len(entry["pts"]), daily[0][0], daily[-1][0]))
        time.sleep(1.0)
    if n_new:
        log("历史补齐：本轮新增 %d 颗，历史库现有 %d 颗" % (n_new, len(SAT_HIST["sats"])))
    return n_new + n_app


def start_hist_backfill(delay=90.0, interval=3 * 3600):
    """启动时并入本地补抓历史（同步），并拉起每 3 小时一轮的自动补齐线程。"""
    _hist_load_extras()

    def _loop():
        time.sleep(delay)
        while True:
            try:
                _hist_backfill_once()
            except Exception as e:
                log("历史补齐异常（下轮继续）：%s" % e)
            time.sleep(interval)

    threading.Thread(target=_loop, daemon=True, name="hist-backfill").start()


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ---------------------------------------------------------------- 请求来源校验
# 三件事都只监听本机，但**本机不等于安全**：
#   ① DNS 重绑定：恶意页面把自己的域名解析到 127.0.0.1，浏览器就把它当同源，
#      于是能读走本服务的全部接口数据、还能调 /quit。→ 校验 Host 头必须是回环名。
#   ② CSRF：任意网页写一句 `<img src="http://127.0.0.1:8770/quit">`，
#      浏览器照发 GET，程序就被关掉（实测确实能把程序关掉）。→ 状态变更端点改为 POST + 同源校验。
_LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}
_ALLOWED_HOSTS = set(_LOOPBACK_NAMES)
_LAN_MODE = False      # 绑到 0.0.0.0 时置 True：额外放行私有网段 **IP 字面量**
_BIND_HOST = "127.0.0.1"
_BOUND_PORT = 0

# 私有网段（RFC1918）+ 链路本地（169.254/16：Windows 没拿到 DHCP 时的自分配地址）
_PRIVATE_V4 = re.compile(
    r"^(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|169\.254\.\d{1,3}\.\d{1,3})$")


def is_lan_literal(host):
    """私有网段 IPv4 / IPv6 链路本地与 ULA 的**字面量**。

    为什么放行 IP 字面量不会削弱防 DNS 重绑定：那类攻击的前提是攻击者只能用**域名**
    （他把自己的域名解析到 192.168.x.x）。Host 头写死成 `192.168.1.7` 时，
    这个字符串就是他自己的内网地址，解析不到别人机器上去。域名仍然一律拒绝。
    """
    h = (host or "").strip().lower().strip("[]")
    if not h:
        return False
    if _PRIVATE_V4.match(h):
        return True
    return h.startswith("fe80:") or h.startswith("fc") or h.startswith("fd")


def init_allowed_hosts(bind_host):
    """按实际绑定的 host 收紧允许的 Host 头。默认 127.0.0.1 → 只认回环名。

    ★ 2026-10-20 修：原来绑 0.0.0.0 时 `_ALLOWED_HOSTS` 仍然是「仅回环」，
      于是手机用 `http://192.168.1.5:8770/` 打开会被 Host 校验 403 ——
      症状是「电脑上一切正常，手机转圈后白屏」，极难往安全校验上想。
    """
    global _ALLOWED_HOSTS, _LAN_MODE, _BIND_HOST
    _ALLOWED_HOSTS = set(_LOOPBACK_NAMES)
    _LAN_MODE = False
    _BIND_HOST = (bind_host or "127.0.0.1").strip()
    h = _BIND_HOST.lower()
    if h in ("0.0.0.0", "::", "", "*"):
        _LAN_MODE = True
    elif h:
        _ALLOWED_HOSTS.add(h)


def lan_urls(port):
    """本机在局域网里的可访问地址（纯标准库探测，不发包、不联网）。"""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))   # UDP connect 只让内核选出口，不会真的发出数据
            ips.append(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ["http://%s:%d/" % (ip, port) for ip in ips]



def header_hostname(value):
    """`Host: 127.0.0.1:8770` → `127.0.0.1`；`[::1]:8770` → `[::1]`。"""
    v = (value or "").strip().lower()
    if not v:
        return ""
    if v.startswith("["):
        return v.split("]")[0] + "]"
    if v.count(":") == 1:
        return v.rsplit(":", 1)[0]
    return v


def static_target(base, rel):
    """把请求里的相对路径安全地拼到 base 下；越界（含同级同前缀目录，如 web 与 web_evil）返回 None。
    旧写法用 str.startswith(base) 判断，`web_evil\\x` 会被误判为在 `web` 内。"""
    root = os.path.normpath(base)
    target = os.path.normpath(os.path.join(root, (rel or "").replace("\\", "/")))
    try:
        if os.path.commonpath([root, target]) != root:
            return None
    except ValueError:      # 不同盘符
        return None
    return target


# ---------------------------------------------------------------- 大图的「磁盘覆盖层」
# ★ 2026-10-15（用户报「星网大图怎么没更新」）：exe 把 web/ 打进包（_MEIPASS），**打包那一刻
#   就冻结**了 —— refresh_deliverables.py 之后把 web/raise/*.html 刷多少遍，包内那份都不会变，
#   于是用户看到的爬升大图永远停在打包日（实测 exe 里是 2026-10-10，磁盘上已是 10-14）。
#   对策：exe 同目录下允许放一份 web_live\raise\，/web/raise/ 的请求优先读它；
#   没有就回落到包内那份（exe 单独拷给别人也照常能跑）。
#   ★ 覆盖范围**故意只限 raise/**：web_nopass（「不含过境预报」变体）与 web 只差一个
#     index.html、raise/ 内容完全一致，所以这份覆盖对四个变体都安全，不会把变体页换错。
WEB_LIVE = os.path.join(APP_DIR, "web_live")
WEB_LIVE_RAISE = os.path.join(WEB_LIVE, "raise")


def live_raise_target(rel):
    """rel 以 `raise/` 开头且 web_live\\raise\\ 里有对应文件 → 返回磁盘路径，否则 None。"""
    rel = (rel or "").replace("\\", "/")
    if not rel.startswith("raise/"):
        return None
    p = static_target(WEB_LIVE_RAISE, rel[len("raise/"):])
    if p and os.path.isfile(p):
        return p
    return None


def probe_tcp(host, port, timeout=0.35):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


# ---------------------------------------------------------------- 网络代理
def build_opener(proxy=None):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        # none = 完全不走代理
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def http_get(url, proxy=None, timeout=25, opener=None, retries=3):
    """返回 bytes；失败抛异常。proxy=None 且 opener=None 时表示直连。

    走代理访问 celestrak 时偶发 502/超时是常态（尤其国内网络），所以默认带
    指数退避重试；404 这类确定性错误立刻抛出，交给上层判断「确实没有」。
    """
    last = None
    for i in range(max(1, retries)):
        try:
            op = opener or build_opener(proxy)
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "*/*",
                "Accept-Encoding": "identity",
                "Connection": "close",
            })
            with op.open(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                raise
        except Exception as e:
            last = e
        if i < retries - 1:
            time.sleep(1.0 * (i + 1))
    raise last


def _proxy_candidates():
    cands = []

    def add(p):
        if p and p not in cands:
            cands.append(p)

    # 1) 命令行 / proxy.txt
    add(_cli_proxy)
    try:
        with open(os.path.join(APP_DIR, "proxy.txt"), "r", encoding="utf-8") as f:
            add(f.read().strip())
    except Exception:
        pass
    # 2) 环境变量
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        add(os.environ.get(k))
    # 2b) 系统代理设置（Windows 下 urllib 会读注册表里的 Internet 选项）
    try:
        sysp = urllib.request.getproxies()
        for k in ("https", "http"):
            v = sysp.get(k)
            if v and v != "direct://":
                add(v if "://" in v else "http://" + v)
    except Exception:
        pass
    # 3) 上次探测成功的
    add((read_json(os.path.join(CACHE_DIR, "proxy.json")) or {}).get("proxy"))
    # 4) 扫描常见端口
    for port in PROXY_PORTS:
        for host in PROXY_HOSTS:
            if probe_tcp(host, port):
                add("http://%s:%d" % (host, port))
    return cands


_local_ports_cache = {"t": 0.0, "ports": []}


def localhost_ports(max_n=80):
    """枚举本机回环上的监听端口（缓存 5 分钟）。

    为什么要这个：系统/工具自带的本地代理端口**每台机器、每次会话都可能变**
    （本机实测出现过 51558 / 57186 / 57674 …），硬编码端口表必然漏。
    直接问 netstat 要答案，比猜端口靠谱得多。（2026-10-03 新增）
    """
    if time.time() - _local_ports_cache["t"] < 300:
        return _local_ports_cache["ports"]
    ports = []
    try:
        import subprocess
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000   # CREATE_NO_WINDOW，别闪黑框
        raw = subprocess.run(["netstat", "-ano"], capture_output=True,
                             timeout=6, **kw).stdout
        txt = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")
        seen = set()
        for m in re.finditer(r"127\.0\.0\.1:(\d+)\s+\S+\s+LISTENING", txt):
            p = int(m.group(1))
            if p not in seen:
                seen.add(p)
                ports.append(p)
    except Exception:
        ports = []
    ports = sorted(ports)[:max_n]
    _local_ports_cache.update({"t": time.time(), "ports": ports})
    return ports


def proxy_probe_candidates(net=None):
    """为「必须走代理的站点」（卫星百科）单独排候选出口。

    ★ 不能复用全局单出口：`resolve_network()` 只拿 CelesTrak 的 gp.php 当探针，
      直连能通就定 mode=direct/proxy=None；而卫星百科直连**必 403**（Cloudflare），
      于是同步代码退化成「直连试两次」→ 永远失败（2026-10-03 定位：25 次 0 成功）。
    顺序：CLI → 上次成功 → 全局出口 → 环境/系统/常见端口 → 本机监听端口 → 直连。
    """
    out, seen = [], set()

    def add(p):
        k = p or "__direct__"
        if k in seen:
            return
        seen.add(k)
        out.append(p)

    add(_cli_proxy)
    add((read_json(os.path.join(CACHE_DIR, "route_proxy.json")) or {}).get("proxy"))
    add((net or {}).get("proxy"))
    for p in _proxy_candidates():
        add(p)
    for port in localhost_ports():
        add("http://127.0.0.1:%d" % port)
    add(None)          # 直连兜底，永远放最后
    return out


_cli_proxy = None


def resolve_network(force=False):
    """确定最终可用出口：直连 or 某个代理。结果缓存在 cache/proxy.json。"""
    global _proxy_state
    with _proxy_lock:
        st = _proxy_state
        # 已确认的出口直接复用；「无出口」状态带 5 分钟冷却，期间所有刷新
        # 走秒回缓存，不再反复撞超时（否则一个分组要等 4~8 分钟才降级）。
        if not force and st["mode"] not in ("unknown", "none"):
            return st
        if (not force and st["mode"] == "none"
                and time.time() - st.get("checked_ts", 0) < _NET_PROBE_COOLDOWN):
            return st

        saved = read_json(os.path.join(CACHE_DIR, "proxy.json")) or {}
        # ★ 直连优先（2026-09-29 提速）：过去把所有代理候选（含上次会话遗留的
        #   死代理，每个等 12s 超时）全试完才轮到直连，冷启动光「确定出口」就
        #   花 18s。绝大多数场景直连本来是通的 → 直连放最前、探测超时缩到 6s，
        #   代理候选只在直连失败时才逐个尝试。CLI 显式指定代理仍最优先。
        try_order = []
        if _cli_proxy:
            try_order.append(("proxy", _cli_proxy))
        try_order.append(("direct", None))
        for p in _proxy_candidates():
            try_order.append(("proxy", p))

        seen = set()
        for mode, proxy in try_order:
            key = proxy or "__direct__"
            if key in seen:
                continue
            seen.add(key)
            try:
                # 探针 URL 按数据源走各自的站（只判「通不通」，不校验内容）
                http_get(_NET_PROBE_URL, proxy=proxy, timeout=6, retries=1)
                _proxy_state = {"proxy": proxy, "mode": mode, "checked_at": iso_local(now_local())}
                write_json(os.path.join(CACHE_DIR, "proxy.json"),
                           {"proxy": proxy, "mode": mode, "checked_at": _proxy_state["checked_at"]})
                log("网络出口确定：%s%s" % (mode, (" · " + proxy) if proxy else ""))
                return _proxy_state
            except Exception as e:
                last = e
                continue

        # 全部出口失败：老老实实标记 none，绝不把 proxy.json 里上次成功的
        # 死代理当现役出口返回——否则 load_tle 的「无出口→秒切缓存」快速
        # 通道永远不触发，页面会在超时重试里卡好几分钟。
        _proxy_state = {"proxy": None, "mode": "none", "checked_ts": time.time(),
                        "checked_at": iso_local(now_local()), "error": str(locals().get("last", ""))}
        log("警告：%s 直连与代理均不可用，将只使用本地缓存（%d 秒后重新探测）。"
            % (SRC_LABEL, _NET_PROBE_COOLDOWN))
        return _proxy_state


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------- TLE 解析
ALPHA5 = "ABCDEFGHJKLMNPQRSTUVWXYZ"   # Alpha-5 字母表（跳过 I、O）；A=10 … Z=33


def norad_to_field(n):
    """NORAD 号 -> TLE 第 3-7 列（5 字符）。超过 99999 走 Alpha-5 编码。"""
    n = int(n)
    if n < 100000:
        return str(n).rjust(5)
    return ALPHA5[n // 10000 - 10] + str(n % 10000).zfill(4)


def field_to_norad(s):
    """TLE 第 3-7 列 -> NORAD 号（支持 Alpha-5）；解析不了返回 None。"""
    s = (s or "").strip()
    if not s:
        return None
    if s[0].isalpha():
        i = ALPHA5.find(s[0].upper())
        if i < 0:
            return None
        try:
            return (10 + i) * 10000 + int(s[1:])
        except ValueError:
            return None
    try:
        return int(s)
    except ValueError:
        return None


def tle_checksum(line68):
    """TLE 校验位：前 68 列的数字求和，'-' 记 1，取模 10。"""
    t = 0
    for ch in line68:
        if ch.isdigit():
            t += int(ch)
        elif ch == "-":
            t += 1
    return str(t % 10)


def exp_field(v, ndigits=5):
    """TLE 中「5 位隐含小数尾数 + 指数」字段（BSTAR / MEAN_MOTION_DDOT）。
    例：1.5256349e-05 -> ' 15256-4'（即 0.15256e-4）"""
    if not v:
        return " " + "0" * ndigits + "+0"
    sign = "-" if v < 0 else " "
    a = abs(v)
    e = int(math.floor(math.log10(a))) + 1        # 使 a/10^e ∈ [0.1, 1)
    mant = int(round(a / (10.0 ** e) * (10 ** ndigits)))
    if mant >= 10 ** ndigits:
        mant //= 10
        e += 1
    return sign + str(mant).zfill(ndigits) + ("+" if e >= 0 else "-") + str(abs(e))[-1:]


def omm_to_tle(row):
    """CelesTrak 的 OMM(JSON) 根数 -> (名称, TLE 第1行, TLE 第2行)。

    为什么需要它：gp.php?NAME=<x>&FORMAT=tle 会漏掉一部分已编目卫星——凡是
    NORAD 号进入 6 位（Alpha-5）区间的都会漏，例如 QIANFAN-239~248、
    QIANFAN 16 OBJECT A~K、HULIANWANG DIGUI-178~186。而 gp.php?FORMAT=json
    是完整的（千帆 257、星网 186、高轨 3）。本地按列位拼回 TLE 即可 100% 覆盖。

    列位严格对齐 satellite.js 的 substring 切法，已对 238 颗「既有 JSON 又有
    TLE」的卫星做过逐字符比对：line1/line2 完全一致（含校验位）。
    """
    norad = int(row["NORAD_CAT_ID"])
    num = norad_to_field(norad)

    # —— 国际编号（8 字符左对齐）：2024-240A -> '24240A  '
    oid = (row.get("OBJECT_ID") or "").strip()
    intdes = " " * 8
    if oid:
        head, _, tail = oid.partition("-")
        if head and tail:
            launch = tail[:3].zfill(3) if tail[:3].isdigit() else "001"
            piece = tail[3:] or "A"
            intdes = (head[-2:].rjust(2, "0") + launch + piece[:3]).ljust(8)[:8]
        else:
            intdes = oid.ljust(8)[:8]

    # —— 历元：2026-09-27T05:48:03.532320 -> '26270.24170755'
    yy, days = "  ", "000.00000000"
    try:
        date_s, time_s = (row.get("EPOCH") or "").replace("Z", "").split("T")
        y, mo, d = (int(x) for x in date_s.split("-"))
        hh, mi, rest = time_s.split(":")
        doy = int(time.strftime("%j", time.strptime("%04d-%02d-%02d" % (y, mo, d), "%Y-%m-%d")))
        frac = (int(hh) * 3600 + int(mi) * 60 + float(rest)) / 86400.0
        if frac >= 1.0:
            frac -= 1.0
            doy = min(doy + 1, 366)
        yy = "%02d" % (y % 100)
        days = "%03d.%08d" % (doy, int(round(frac * 1e8)))
    except Exception:
        pass

    cls = (row.get("CLASSIFICATION_TYPE") or "U").strip()[:1] or "U"
    ndot = float(row.get("MEAN_MOTION_DOT") or 0.0)
    dot = ("-" if ndot < 0 else " ") + "." + str(int(round(abs(ndot) * 1e8))).zfill(8)

    l1 = "1 %s%s %s %s %s %s %s %d %4d" % (
        num, cls, intdes, yy + days, dot[:10],
        exp_field(float(row.get("MEAN_MOTION_DDOT") or 0.0)),
        exp_field(float(row.get("BSTAR") or 0.0)),
        int(row.get("EPHEMERIS_TYPE") or 0),
        int(row.get("ELEMENT_SET_NO") or 999))
    l1 = (l1 + " " * 68)[:68]
    l1 += tle_checksum(l1)

    # —— 离心率：CelesTrak 的 TLE 是截断到 7 位（不是四舍五入）。
    #    用 Decimal 走字符串，避免 0.0014845*1e7 掉成 14844.999… 的浮点坑。
    ecc = int((Decimal(str(row.get("ECCENTRICITY") or "0")) * 10 ** 7)
              .quantize(Decimal(1), rounding=ROUND_DOWN))
    l2 = "2 %s %8.4f %8.4f %07d %8.4f %8.4f %11.8f%5d" % (
        num,
        float(row.get("INCLINATION") or 0.0),
        float(row.get("RA_OF_ASC_NODE") or 0.0),
        max(0, min(ecc, 9999999)),
        float(row.get("ARG_OF_PERICENTER") or 0.0),
        float(row.get("MEAN_ANOMALY") or 0.0),
        float(row.get("MEAN_MOTION") or 0.0),
        int(row.get("REV_AT_EPOCH") or 0))
    l2 = (l2 + " " * 68)[:68]
    l2 += tle_checksum(l2)

    return (row.get("OBJECT_NAME") or ("NORAD %d" % norad)).strip(), l1, l2


def tle_epoch(l1):
    """TLE 第1行第 19-32 列 = 历元（YYDDD.DDDDDDDD），返回 aware datetime(UTC)"""
    try:
        yy = int(l1[18:20])
        doy = float(l1[20:32])
        year = 2000 + yy if yy < 57 else 1900 + yy
        return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1)
    except Exception:
        return None


def parse_tle(text):
    """解析 TLE 文本为记录列表；自动兼容 3 行（名称+两行）与 2 行格式"""
    lines = [ln.rstrip("\r\n") for ln in text.splitlines()]
    lines = [ln for ln in lines if ln.strip()]
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
            name, l1, l2 = "", ln, lines[i + 1]
            i += 2
        elif i + 2 < len(lines) and lines[i + 1].startswith("1 ") and lines[i + 2].startswith("2 "):
            name, l1, l2 = ln, lines[i + 1], lines[i + 2]
            i += 3
        else:
            i += 1
            continue

        norad = field_to_norad(l1[2:7])
        ep = tle_epoch(l1)
        rec = {
            "name": name.strip() or ("NORAD %s" % norad),
            "norad": norad,
            "l1": l1.rstrip(),
            "l2": l2.rstrip(),
            "epoch": ep.strftime("%Y-%m-%dT%H:%M:%SZ") if ep else None,
            "epoch_ms": int(ep.timestamp() * 1000) if ep else None,
        }
        # 轨道要素（便于列表排序 / 筛选，真正的传播由前端 SGP4 做）
        try:
            rec["incl"] = round(float(l2[8:16]), 3)
            rec["raan"] = round(float(l2[17:25]), 3)
            rec["ecc"] = float("0." + l2[26:33].strip())
            rec["mm"] = round(float(l2[52:63]), 6)  # rev/day
            MU = 398600.4418
            n_rad = rec["mm"] * 2 * 3.141592653589793 / 86400.0
            a = (MU / (n_rad * n_rad)) ** (1.0 / 3.0)
            rec["alt"] = round(a - 6378.137, 1)  # km，平均高度（不含摄动）
            rec["period"] = round(1440.0 / rec["mm"], 2)  # min
        except Exception:
            rec["incl"] = rec["raan"] = rec["ecc"] = rec["mm"] = rec["alt"] = rec["period"] = None
        out.append(rec)
    return out


# ---------------------------------------------------------------- 本地发射信息
_meta_cache = {}   # group_key -> {"t": ts, "v": {norad: {...}}}


def local_meta(group_key):
    """读 data/<group_key>_meta.json: {"<norad>": {...}}（由 build_meta.py 生成）。

    必须按分组读取：早先这里把 qianfan_meta.json / gw_meta.json 合并成一张全局表，
    结果千帆的 257 条元信息被灌进了星网分组，连带让 CATNR 补抓把千帆卫星当成
    「星网缺星」抓了回来（表现为星网里凭空多出 17 颗 89° 倾角卫星）。
    """
    c = _meta_cache.get(group_key)
    if c and time.time() - c["t"] < 300:
        return c["v"]
    d = read_json(os.path.join(DATA_DIR, group_key + "_meta.json")) or {}
    v = {str(k): val for k, val in d.items()} if isinstance(d, dict) else {}
    _meta_cache[group_key] = {"t": time.time(), "v": v}
    return v


_satcat_lock = threading.Lock()
_satcat_cache = {}  # group -> {"t": ts, "map": {norad: {...}}}
_omm_last = {}      # group -> {"t": ts, "rows": [...]}  刚抓到的 OMM，供 fetch_group_meta 复用


def _tle_has(group_key, norad):
    """该分组当前 TLE 缓存里是否有这颗星（TLE 第一行形如 "1 59069U..."）"""
    try:
        tle_f, _ = cache_paths(group_key)
        with open(tle_f, encoding="utf-8", errors="replace") as f:
            return ("1 %dU" % norad) in f.read()
    except Exception:
        return False


def _apply_local_meta(group_key, mapping):
    """把 data/<group>_meta.json 的本地核实信息并入 mapping，返回是否有变更。
    CelesTrak 名字检索可能整颗漏掉且 OMM 的 OBJECT_ID 为空（实例：
    HULIANWAN GAOGUI-01 / 59069，2026-09-30）——只要该星确有本组 TLE 缓存
    就新建条目并入；否则跳过（避免本地表里已衰减/未编目的条目虚增已编目数）。"""
    changed = False
    for norad, local in (local_meta(group_key) or {}).items():
        try:
            n = int(norad)
        except (TypeError, ValueError):
            continue
        if not isinstance(local, dict) or not local:
            continue
        rec = mapping.get(str(n))
        if rec is None:
            if not _tle_has(group_key, n):
                continue
            rec = mapping[str(n)] = {}
        for k, v in local.items():
            if v and rec.get(k) != v:
                rec[k] = v
                changed = True
    return changed


def _merge_launch_index(mapping):
    """给每条记录补上「发射批次」信息：按 COSPAR 前段（如 2024-240）归并，
    再按发射日期排序得出该星座的第 N 次发射。纯数据推导，不做外部猜测。

    顺带把任务表里的**研制方**（dev）按同一 COSPAR 前缀并进来——它在卫星百科里
    是「组次级」字段，只在 LOCAL_LAUNCHES 里，不会出现在单星元信息里（2026-10-06）。
    返回是否有字段发生变化（供缓存落盘判断）。"""
    changed = False
    batches = {}
    for rec in mapping.values():
        cos = rec.get("cospar") or ""
        if len(cos) < 8:
            continue
        b = batches.setdefault(cos[:8], {"date": "", "site": "", "n": 0})
        b["n"] += 1
        # 同一批次里只要有一颗星带日期/场地就补全整批（首星常常在 satcat 中缺失）
        if not b["date"] and rec.get("launch_date"):
            b["date"] = rec["launch_date"]
        if not b["site"] and rec.get("launch_site"):
            b["site"] = rec["launch_site"]
    order = sorted(batches.items(), key=lambda kv: (kv[1]["date"] or "9999", kv[0]))
    rank = {}
    for i, (key, b) in enumerate(order, 1):
        b["index"] = i
        rank[key] = i
    for rec in mapping.values():
        cos = rec.get("cospar") or ""
        if len(cos) < 8:
            continue
        b = batches.get(cos[:8])
        if b:
            rec["batch"] = cos[:8]
            rec["batch_index"] = b["index"]
            rec["batch_total"] = len(order)
            rec["batch_size"] = b["n"]
            rec["batch_date"] = b["date"]
            rec["batch_site"] = b["site"]
        dev = (LOCAL_LAUNCHES.get(cos[:8]) or {}).get("dev")
        if dev and rec.get("dev") != dev:
            rec["dev"] = dev
            changed = True
    return changed


def fetch_group_meta(group_key, allow_network=True):
    """建立 NORAD -> 元信息 映射。
    1) gp.php?FORMAT=json  → OBJECT_ID(COSPAR)、规范名（全覆盖，含边缘星）
    2) satcat/records.php   → 发射日期、发射场、衰减日期（部分星在 satcat 中缺失）
    3) 本地 qianfan_meta.json / gw_meta.json → 已核实的任务/火箭/遥号/轨道面
    4) 本地 data/launches.json → 按 COSPAR 前段匹配发射任务
    结果按 TTL 缓存（内存 + 磁盘），失败时返回已缓存内容或空表。
    allow_network=False 时只用缓存（内存/磁盘），绝不联网——给「秒开」路径用。"""
    g = GROUPS[group_key]
    with _satcat_lock:
        c = _satcat_cache.get(group_key)
        if c and time.time() - c["t"] < g["ttl"]:
            return c["map"]
    # 磁盘持久化：程序重启后元信息表直接可用，不必重新联网
    disk = read_json(os.path.join(CACHE_DIR, group_key + "_metamap.json")) or {}
    disk_map = disk.get("map") if isinstance(disk.get("map"), dict) else None
    if disk_map and disk.get("t") and time.time() - disk["t"] < g["ttl"]:
        mmap = disk_map
        # 本地核实元信息（data/<group>_meta.json）可能比磁盘缓存新——
        # 例如刚补齐的高轨三星资料：不联网也要立即生效并重算批次（2026-09-30）
        # ★ 批次索引 + 研制方务必**每次都并**（不只是本地元信息变更时）：任务表
        #   （launches_auto.json 的 dev）更新后，磁盘里的旧 metamap 得跟着补齐。
        ch = _apply_local_meta(group_key, mmap)
        ch = _merge_launch_index(mmap) or ch
        if ch:
            try:
                write_json(os.path.join(CACHE_DIR, group_key + "_metamap.json"),
                           {"t": time.time(), "map": mmap})
            except Exception:
                pass
        with _satcat_lock:
            _satcat_cache[group_key] = {"t": disk["t"], "map": mmap}
        return mmap

    if not allow_network:
        with _satcat_lock:
            c = _satcat_cache.get(group_key)
        if c:
            return c["map"]
        # ★ 磁盘那份**即便过期也比空表强**（2026-10-06 修）：批次 / COSPAR / 研制方
        #   都是「一次定死、不会放旧」的字段。旧写法在这里直接 `return {}`，于是冷启动
        #   恰好撞上网络慢或断网时，整个星座的 COSPAR 与研制方会凭空消失；**离线时更是
        #   永久消失**（联网分支永远跑不到，磁盘那份也永远不返回）。实测国网 213 颗的
        #   研制方就是这样整组消失的。这里退回磁盘旧表，并就地并上本地元信息与任务索引。
        if disk_map:
            mmap = disk_map
            try:
                _apply_local_meta(group_key, mmap)
                _merge_launch_index(mmap)
            except Exception:
                pass
            with _satcat_lock:
                _satcat_cache[group_key] = {"t": disk.get("t") or 0, "map": mmap}
            return mmap
        return {}

    net = resolve_network()
    if net.get("mode") == "none":
        # 无可用网络出口：用已有的元信息缓存（或空表），绝不在线抓
        with _satcat_lock:
            c = _satcat_cache.get(group_key)
        if c:
            return c["map"]
        return {}
    proxy = net.get("proxy")
    mapping = {}

    # 1) gp JSON：COSPAR（若 load_tle 刚抓过 OMM，直接复用，省一次请求）
    #    celestrak 模式 = gp.php FORMAT=json；spacetrack 模式 = GP 名字/批次查询（同构字段）
    try:
        cached_omm = _omm_last.get(group_key)
        if cached_omm and time.time() - cached_omm["t"] < 600:
            arr = cached_omm["rows"]
        elif DATA_SRC == "spacetrack":
            arr = st_group_rows(group_key)
            _omm_last[group_key] = {"t": time.time(), "rows": arr}
        else:
            raw = http_get(group_omm_url(g), proxy=proxy, timeout=30)
            arr = filter_group_rows(g, json.loads(raw.decode("utf-8", "replace")))
        for row in arr if isinstance(arr, list) else []:
            norad = row.get("NORAD_CAT_ID")
            if not norad:
                continue
            mapping[str(norad)] = {
                "cospar": (row.get("OBJECT_ID") or "").strip(),
                "obj_name": (row.get("OBJECT_NAME") or "").strip(),
            }
        log("gp.json %s：%d 条" % (group_key, len(mapping)))
    except Exception as e:
        log("gp.json %s 抓取失败：%s" % (group_key, e))

    # 2) satcat：发射日期 / 场地（ST 模式走 satcat 表名字包含检索，字段已归一；
    #    gaogui 的 ST 名字 HG-xx 检索太泛 → 跳过，其发射信息由本地元数据兜底）
    try:
        if DATA_SRC == "spacetrack":
            arr = (st_satcat_rows("~~QIANFAN" if group_key == "qianfan" else "~~HULIANWANG")
                   if group_key != "gaogui" else [])
        else:
            raw = http_get(SATCAT_URL.format(name=urllib.parse.quote(g["name"])), proxy=proxy, timeout=30)
            arr = json.loads(raw.decode("utf-8", "replace"))
        got = 0
        # CT satcat 首行是表头（arr[1:] 跳过）；ST 返回的是干净行列表，原样用
        rows_iter = (arr if DATA_SRC == "spacetrack" else arr[1:]) if isinstance(arr, list) else []
        for row in rows_iter:
            if not isinstance(row, dict):
                continue
            norad = str(row.get("NORAD_CAT_ID") or "")
            if not norad:
                continue
            rec = mapping.setdefault(norad, {})
            if row.get("OBJECT_ID"):
                rec.setdefault("cospar", row["OBJECT_ID"].strip())
            rec["launch_date"] = row.get("LAUNCH_DATE") or ""
            rec["launch_site"] = row.get("LAUNCH_SITE") or ""
            rec["decay"] = row.get("DECAY_DATE") or ""
            rec["owner"] = row.get("OWNER") or ""
            rec["obj_type"] = row.get("OBJECT_TYPE") or ""
            got += 1
        log("satcat %s：补全 %d 条" % (group_key, got))
    except Exception as e:
        log("satcat %s 抓取失败：%s" % (group_key, e))

    # 2.5) COSPAR 扩展：卫星百科业务星批次表 → SATCAT 总表检索，
    #      把名字检索没覆盖的已编目星并入 mapping（补抓机制随后自动取根数）
    try:
        cospar_expand(group_key, mapping, net)
    except Exception as e:
        log("COSPAR 扩展 %s 失败（不影响主体数据）：%s" % (group_key, e))

    # 3) 本地已核实信息（最高优先级，仅本分组；只并入确有本组 TLE 缓存的星，
    #    避免本地表里已衰减/未编目的条目把「已编目数」算虚）
    try:
        _apply_local_meta(group_key, mapping)
    except Exception as e:
        log("本地元信息合并 %s 失败（不影响主体数据）：%s" % (group_key, e))

    # 4) 按 COSPAR 前段匹配本工作区核实过的发射任务表
    #    发射日期以卫星百科（北京时间）为准：CelesTrak 的 LAUNCH_DATE 是 UTC，
    #    凌晨发射会差一天，所以只要百科带出了发射时间就用它反推日期覆盖。
    for rec in mapping.values():
        cos = rec.get("cospar") or ""
        if len(cos) >= 8:
            lch = LOCAL_LAUNCHES.get(cos[:8])
            if lch:
                for k, v in lch.items():
                    if v and not rec.get(k):
                        rec[k] = v
                if rec.get("launch_time"):
                    d = _wiki_date(rec["launch_time"])
                    if d:
                        rec["launch_date"] = d

    _merge_launch_index(mapping)

    # 只保留至少有一个字段的记录
    mapping = {k: v for k, v in mapping.items() if any(v.values())}
    with _satcat_lock:
        _satcat_cache[group_key] = {"t": time.time(), "map": mapping}
    try:
        write_json(os.path.join(CACHE_DIR, group_key + "_metamap.json"),
                   {"t": time.time(), "map": mapping})
    except Exception:
        pass
    return mapping


def fetch_satcat(group_key):
    """兼容旧调用名"""
    return fetch_group_meta(group_key)


# ===================================================================== Space-Track 数据源
# （COV_DATA_SRC=spacetrack 时启用；传输层见 st_source.py：凭据 + 会话复用 + 全局限流）
# 查询口径 2026-10-09 用 _probe_st*.py 实测定，关键事实：
#   qianfan  gp OBJECT_NAME ~~QIANFAN → 248。CT 组接口 257（2026-10-09 实测），差的 9 颗
#            是 QIANFAN 16 OBJECT A~H/K（NORAD 100702~100709/100711，COSPAR 2026-211A~K）
#            ——ST 目录里这批名字就叫「OBJECT A」、OBJECT_TYPE=UNKNOWN，名字检索不到
#            → 按批次表 COSPAR 前缀用 OBJECT_ID 前缀补抓（每批 1 次请求，
#            收 PAYLOAD+UNKNOWN、拒 R/B——2026-211 批里混着 1 个 GRAVITY-1 R/B）。
#   gw       gp OBJECT_NAME ~~HULIANWANG → 213，与 CT 组接口剔除 4 颗试验星后
#            **逐 NORAD 完全一致**（实测 ST 侧连一颗 TEST/JISHU 都没有，drop 过滤保留作保险）。
#   gaogui   高轨三星在 ST 叫 HG-01/02/03（~~GAOGUI 0 行、~~HULIANWAN 也只回 DIGUI），
#            名字检索走不通 → 按 data/gaogui_meta.json 的 NORAD 键清单直查。
# TLE 拼装：**直接用官方 TLE_LINE1/TLE_LINE2**（GP 行自带）。本地 omm_to_tle 有两处
# 系统性差异（MEAN_MOTION_DDOT=0 时 ST 官方用 " 00000-0" 而拼装给 " 00000+0"，校验位
# 随之差 1；偏心率 JSON 只给 8 位小数、官方 TLE 用全精度 → 截断/四舍五入都可能差 1），
# 实测 461 行全量比对「拼装 vs 官方行」无一完全相等 → ST 模式不做本地拼装。
import st_source  # noqa: E402  （纯标准库，顶部常量就绪后在本文件内配置）

st_source.configure(APP_DIR, CACHE_DIR, log)
if DATA_SRC == "spacetrack":
    log("数据来源：Space-Track.org（COV_DATA_SRC=spacetrack；凭据读 spacetrack.txt）")

ST_Q = ("https://www.space-track.org/basicspacedata/query/class/{cls}/"
        "{pred}/{val}{extra}/format/json")
_ST_OBJTYPE_MAP = {"PAYLOAD": "PAY", "ROCKET BODY": "R/B", "ROCKETBODY": "R/B",
                   "DEBRIS": "DEB", "UNKNOWN": "UNK"}


def _st_objtype(t):
    """ST satcat 的 OBJECT_TYPE 是全称（PAYLOAD/ROCKET BODY/DEBRIS/UNKNOWN），
    服务端各处按 CT 缩写（PAY/R/B/DEB/UNK）判断 → 统一归一。"""
    t = (t or "").strip()
    return _ST_OBJTYPE_MAP.get(t.upper(), t)


def st_json(url, timeout=180):
    """一次 ST 查询 → JSON（list）。失败抛异常，由调用方按各自策略兜底。"""
    raw, desc = st_source.st_query(url, timeout=timeout)
    return json.loads(raw.decode("utf-8", "replace"))


def st_rows_to_tle_text(rows):
    """ST GP 行 → 3 行式 TLE 文本。直接用官方 TLE_LINE1/2，零拼装。"""
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        l1 = (r.get("TLE_LINE1") or "").rstrip()
        l2 = (r.get("TLE_LINE2") or "").rstrip()
        if not l1 or not l2:
            continue
        out.append("%s\n%s\n%s\n" % ((r.get("OBJECT_NAME") or "").strip(), l1, l2))
    return "".join(out)


def st_group_rows(group_key):
    """ST 模式组取数：名字包含查询 + drop 过滤 + 未覆盖批次 OBJECT_ID 前缀补抓。
    返回 OMM/GP 行列表（字段与 CT 的 OMM JSON 同构，fetch_group_meta 直接可用）。"""
    g = GROUPS[group_key]
    if group_key == "gaogui":
        # HG-01/02/03 名字检索走不通 → 按 gaogui_meta.json 的 NORAD 键直查（1 次请求）。
        # 键即 NORAD 号（"_说明" 非数字自动滤掉）；清单为空时用 2026-10-09 实测的三颗兜底。
        norads = sorted(int(k) for k in local_meta(group_key) if str(k).isdigit())
        if not norads:
            norads = [59069, 60327, 61503]
        arr = st_json(ST_Q.format(cls="gp", pred="NORAD_CAT_ID",
                                  val=",".join(map(str, norads)),
                                  extra="/orderby/NORAD_CAT_ID"))
    else:
        pat = {"qianfan": "~~QIANFAN", "gw": "~~HULIANWANG"}.get(group_key)
        if not pat:
            raise RuntimeError("Space-Track 模式暂不支持分组 %s" % group_key)
        arr = st_json(ST_Q.format(cls="gp", pred="OBJECT_NAME", val=pat,
                                  extra="/orderby/NORAD_CAT_ID"))
        arr = filter_group_rows(g, arr if isinstance(arr, list) else [])
    if not isinstance(arr, list):
        arr = []
    # ★ CT 的编目组只收载荷；名字包含查询偶会带出同前缀的 R/B/碎片（ST 侧无组概念）
    arr = [r for r in arr if not isinstance(r, dict) or r.get("OBJECT_TYPE") in (None, "PAYLOAD")
           or _st_objtype(r.get("OBJECT_TYPE")) == "PAY"]
    # —— 批次补抓：批次表（LAUNCH_ROWS）里有、名字检索没覆盖的 COSPAR 前缀，
    #    按 OBJECT_ID 前缀整批拉回（每批 1 次请求）。与 cospar_expand 同一套过滤规则，
    #    但在「取根数」这层补——补回来的直接就是带 TLE_LINE 的 GP 行。
    covered = {(r.get("OBJECT_ID") or "").strip()[:8] for r in arr if isinstance(r, dict)}
    covered.discard("")
    skip = set(g.get("cospar_skip") or ())
    biz_only = bool(g.get("business_only"))
    have = {r.get("NORAD_CAT_ID") for r in arr if isinstance(r, dict)}
    n_extra = 0
    for lr in LAUNCH_ROWS:
        if group_key not in lr["groups"] or not lr["cos"] or lr["cos"] in skip:
            continue
        if biz_only and "试验" in (lr["row"].get("mission") or lr["row"].get("group") or ""):
            continue
        pre = lr["cos"][:8]
        if pre in covered:
            continue
        covered.add(pre)   # 失败也不再重试（后台补星机制会兜底）
        try:
            extra = st_json(ST_Q.format(cls="gp", pred="OBJECT_ID", val="~~" + pre,
                                        extra="/orderby/NORAD_CAT_ID"))
        except Exception as e:
            log("ST 批次补抓 %s 失败（由补星机制兜底）：%s" % (pre, e))
            continue
        for r in extra if isinstance(extra, list) else []:
            # ★ 收 PAYLOAD + UNKNOWN、拒 R/B/碎片：OBJECT_ID 前缀检索会带出同批火箭残骸
            #   （实测 2026-211 批混进 1 个 GRAVITY-1 R/B，CT 组里没有）；而 CT 组里的
            #   「QIANFAN 16 OBJECT A~K」这类未命名载荷在 ST 的 OBJECT_TYPE 恰是 UNKNOWN
            #   （只收 PAYLOAD 会把它们全滤掉——2026-10-09 实测踩过）。
            if (isinstance(r, dict) and r.get("NORAD_CAT_ID")
                    and r["NORAD_CAT_ID"] not in have
                    and _st_objtype(r.get("OBJECT_TYPE")) in ("PAY", "UNK")):
                arr.append(r)
                have.add(r["NORAD_CAT_ID"])
                n_extra += 1
    if n_extra:
        log("ST 组取数 %s：名字检索 %d + 批次补抓 +%d" % (group_key, len(arr) - n_extra, n_extra))
    return arr


def st_satcat_rows(pattern):
    """ST satcat 表按名字包含检索，字段归一到 CT satcat JSON 的键名
    （fetch_group_meta 第 2 步的合并循环按 CT 键名取值，两边共用一段代码）。"""
    arr = st_json(ST_Q.format(cls="satcat", pred="OBJECT_NAME", val=pattern, extra=""))
    out = []
    for r in arr if isinstance(arr, list) else []:
        if not isinstance(r, dict):
            continue
        out.append({
            "NORAD_CAT_ID": r.get("NORAD_CAT_ID"),
            "OBJECT_ID": (r.get("OBJECT_ID") or r.get("INTLDES") or "").strip(),
            "OBJECT_NAME": (r.get("OBJECT_NAME") or "").strip(),
            "OBJECT_TYPE": _st_objtype(r.get("OBJECT_TYPE")),
            "LAUNCH_DATE": r.get("LAUNCH") or "",
            "LAUNCH_SITE": r.get("SITE") or "",
            "DECAY_DATE": r.get("DECAY") or "",
            "OWNER": r.get("COUNTRY") or r.get("OWNER") or "",
        })
    return out


def st_hist_daily(norad):
    """ST gp_history → [(iso_date, 当日中位高度 km)]，输出形状与 _hist_parse_plotdata 一致
    （celesTrak graph-orbit-data 的 plotData 本就源自同一份 GP 历史，数值应逐点吻合，
    _hist_append_pts 的 50 km 离群护栏兜着）。"""
    import collections as _collections
    import statistics as _statistics
    rows = st_json(ST_Q.format(cls="gp_history", pred="NORAD_CAT_ID", val=str(norad),
                               extra="/orderby/EPOCH%20asc"), timeout=120)
    g = _collections.defaultdict(list)
    seen = set()
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        try:
            t = datetime.fromisoformat((r.get("EPOCH") or "")[:19])
            a = r.get("SEMIMAJOR_AXIS")
            a = float(a) if a else None
            if a is None:
                mm = float(r.get("MEAN_MOTION") or 0)
                if not mm:
                    continue
                n_rad = mm * 2 * math.pi / 86400.0
                a = (398600.4418 / (n_rad * n_rad)) ** (1.0 / 3.0)
        except Exception:
            continue
        # 同一历元偶有重复行（实测 59069 连续两条同值）→ 去重再进中位数
        key = (r.get("EPOCH"), round(a, 3))
        if key in seen:
            continue
        seen.add(key)
        g[t.date()].append(a - 6378.137)
    if not g:
        return []
    return [(d.isoformat(), round(_statistics.median(v), 1)) for d, v in sorted(g.items())]


# ------------------------------------------------------- COSPAR 扩展（用户指定数据源）
# 数据来源 = 卫星百科「星网」「千帆星座」词条业务星表的 COSPAR 批次前缀。
# 拿每个前缀到 CelesTrak SATCAT 实时编目库（satcat/records.php?INTDES=<前缀>）里
# 检索已编目载荷并并入 meta_map，之后由既有补抓机制（CATNR+OMM）自动取根数、算数据。
# 背景：2026 年下半年起新批星命名改为 GUOWANG NN OBJECT X（旧名 HULIANWANG DIGUI-nn），
# 名字检索（hulianwang digui）抓不到它们，只有按国际标识符检索才覆盖；
# 且 satcat.txt 下载文件比实时库滞后，不能作为判断依据。
_SATCAT_IDX_LOCK = threading.Lock()
_INTDES_CACHE = {}    # cos -> {"t": ts, "rows": [[norad, intdes, name], ...]}
_INTDES_TTL = 12 * 3600
_SATCAT_BLOCKED = {"until": 0.0}   # 403 限流退避：半小时内不再请求
LOCAL_LAUNCHES = {}


def intdes_lookup(cos, net):
    """按国际标识符前缀查 SATCAT 实时编目库，返回 [[norad, intdes, name], ...]（仅载荷）。
    结果缓存 12 小时；遇 403 退避 30 分钟。"""
    with _SATCAT_IDX_LOCK:
        c = _INTDES_CACHE.get(cos)
        if c and time.time() - c["t"] < _INTDES_TTL:
            return c["rows"]
    idx_file = os.path.join(CACHE_DIR, "intdes_idx.json")
    disk = read_json(idx_file) or {}
    ent = disk.get(cos)
    if ent and time.time() - ent.get("t", 0) < _INTDES_TTL:
        with _SATCAT_IDX_LOCK:
            _INTDES_CACHE[cos] = ent
        return ent["rows"]
    if DATA_SRC == "spacetrack":
        # ST 模式：satcat 表按国际标识符**包含**检索（1 次请求；无 CT 那套 403 限流，
        # 出站节奏由 st_source 全局限流统一管）。OBJECT_TYPE 是全称，归一后过滤。
        arr = st_json(ST_Q.format(cls="satcat", pred="INTLDES", val="~~" + cos, extra=""))
        rows = []
        for r in arr if isinstance(arr, list) else []:
            norad = str(r.get("NORAD_CAT_ID") or "")
            if not norad.isdigit() or _st_objtype(r.get("OBJECT_TYPE")) != "PAY":
                continue   # 只要载荷；R/B 与碎片不算业务星
            rows.append([int(norad), (r.get("OBJECT_ID") or r.get("INTLDES") or "").strip(),
                         (r.get("OBJECT_NAME") or "").strip()])
        ent = {"t": time.time(), "rows": rows}
        with _SATCAT_IDX_LOCK:
            _INTDES_CACHE[cos] = ent
        try:
            disk[cos] = ent
            write_json(idx_file, disk)
        except Exception:
            pass
        return rows
    if time.time() < _SATCAT_BLOCKED["until"]:
        # 退避中：有过期缓存就先凑合用（编目数据过时几小时无害），否则直接抛
        if ent and ent.get("rows"):
            return ent["rows"]
        raise RuntimeError("SATCAT 403 退避中，%.0f 秒后重试" % (_SATCAT_BLOCKED["until"] - time.time()))
    # ★ 逐候选出口试（同 wiki）：SATCAT 与 gp.php 的限流互相独立，
    #   直连被封时换个本地代理往往就通；命中后记 route_proxy.json 供下批复用。
    raw, last_err, got_403 = None, None, False
    for p in proxy_probe_candidates(net):
        try:
            raw = http_get("https://celestrak.org/satcat/records.php?INTDES=%s&FORMAT=json"
                           % urllib.parse.quote(cos),
                           proxy=p, timeout=15, retries=1)
            if p and (read_json(os.path.join(CACHE_DIR, "route_proxy.json")) or {}).get("proxy") != p:
                write_json(os.path.join(CACHE_DIR, "route_proxy.json"),
                           {"proxy": p, "checked_at": iso_local(now_local()), "for": "satcat"})
            break
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 403:
                got_403 = True
                continue          # 这个出口被限流，换下一个
            raise                 # 404 等确定性错误立刻抛出
        except Exception as e:
            last_err = e
            continue
    if raw is None:
        if got_403 and isinstance(last_err, urllib.error.HTTPError):
            _SATCAT_BLOCKED["until"] = time.time() + 1800
            log("SATCAT 403（所有候选出口均被限流），30 分钟内不再请求")
        else:
            # 网络/代理故障：5 分钟短退避。不退避的话，42 个批次 × 每批两次
            # 超时会把首个 /api/tle 请求拖住 20 分钟，页面一直白屏。
            _SATCAT_BLOCKED["until"] = time.time() + 300
        raise last_err or RuntimeError("SATCAT 无可用出口")
    arr = json.loads(raw.decode("utf-8", "replace"))
    rows = []
    for r in arr if isinstance(arr, list) else []:
        norad = str(r.get("NORAD_CAT_ID") or "")
        if not norad.isdigit() or (r.get("OBJECT_TYPE") or "PAY") != "PAY":
            continue   # 只要载荷；R/B 与碎片不算业务星
        rows.append([int(norad), (r.get("OBJECT_ID") or "").strip(),
                     (r.get("OBJECT_NAME") or "").strip()])
    ent = {"t": time.time(), "rows": rows}
    with _SATCAT_IDX_LOCK:
        _INTDES_CACHE[cos] = ent
    try:
        disk[cos] = ent
        write_json(idx_file, disk)
    except Exception:
        pass
    return rows


def _wiki_date(s):
    """「2026年9月23日 21:32」（卫星百科，北京时间）→「2026-09-23」。
    也兼容已是 ISO 的「2026-09-23」。解析失败返回 ""（调用方保留原值）。
    发射日期一律以卫星百科为准——CelesTrak 的 LAUNCH_DATE 是 UTC，
    凌晨发射（北京时间）会差一天。"""
    s = (s or "").strip()
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        return "%04d-%02d-%02d" % tuple(int(x) for x in m.groups())
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return "%04d-%02d-%02d" % tuple(int(x) for x in m.groups())
    return ""


def _days_since_launch(row):
    """发射表某行的发射时间距今天数（表里是北京时间 '2026-10-10 03:27'）。
    取不到时间返回 9999（当成老批次，走跳过分支，保持旧行为）。"""
    s = (row.get("time") or row.get("launch_time") or "").strip()
    d = None
    for fmt, n in (("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            d = datetime.strptime(s[:n], fmt).date()
            break
        except Exception:
            continue
    if d is None:
        return 9999
    return (datetime.now().date() - d).days


def cospar_expand(group_key, mapping, net):
    """把业务星批次表里、编目库已收录但名字检索没覆盖的星并进 meta_map。"""
    g = GROUPS[group_key]
    skip = set(g.get("cospar_skip") or ())
    biz_only = bool(g.get("business_only"))
    added = 0
    n_pre = 0
    n_skip = 0
    # ★ 已被现有映射覆盖的 COSPAR 前缀就不必再检索（2026-10-07 提速）：
    #   组接口 `GROUP=hulianwang` 一次拿回的 OMM 已带 GUOWANG 新名星的 OBJECT_ID，
    #   这些批次的国际标识符检索可整批跳过 —— 老链路冷启动的 42 次 SATCAT 请求
    #   正是全部来自这里。名字检索漏星时该前缀不在集合里，照旧会去检索，行为不变。
    covered = set()
    for _rec in mapping.values():
        _c8 = (_rec.get("cospar") or "")[:8]
        if _c8:
            covered.add(_c8)
    for lr in LAUNCH_ROWS:
        if group_key not in lr["groups"] or lr["cos"] in skip:
            continue
        if biz_only and "试验" in (lr["row"].get("mission") or lr["row"].get("group") or ""):
            continue   # 只录业务星
        # ★ 2026-10-15：这个「已被主数据源覆盖 → 整批跳过」的提速优化**不能用在刚发射的批次上**。
        #   实测 2026-230（星网低轨27组）：组接口只带回 7 颗（100973/74/77/78/79/80/81），
        #   前缀 `2026-230` 一被标记 covered，剩下 2 颗（100975 C / 100976 D）就再也补不进来，
        #   而日志只报「27 个批次已覆盖、+0 颗」，看起来一切正常 —— 典型的「不报异常的错」。
        #   编目是陆续发布的，所以近 45 天的批次一律照旧检索（老批次仍跳过，冷启动开销不变）。
        if lr["cos"] and lr["cos"] in covered and _days_since_launch(lr["row"]) > 45:
            n_skip += 1
            continue   # 该批次已由主数据源覆盖（有 COSPAR），无需国际标识符检索
        n_pre += 1
        try:
            hits = intdes_lookup(lr["cos"], net)
        except Exception as e:
            log("INTDES 检索 %s 失败（本批跳过）：%s" % (lr["cos"], e))
            continue
        for norad, intdes, name in hits:
            k = str(norad)
            if k in mapping:
                continue
            ltime = (lr["row"].get("time") or lr["row"].get("launch_time") or "").strip()
            mapping[k] = {
                "cospar": intdes,
                "obj_name": name,
                "launch_date": _wiki_date(ltime),   # 北京时间，源自卫星百科
                "launch_time": ltime,
                "launch_site": lr["row"].get("site") or "",
                "owner": "PRC",
            }
            covered.add((intdes or "")[:8])
            added += 1
    if n_skip:
        log("COSPAR 扩展 %s：%d 个批次已由主数据源覆盖、跳过检索；实际检索 %d 个批次，+%d 颗"
            % (group_key, n_skip, n_pre, added))
    elif added:
        log("COSPAR 扩展 %s：+%d 颗已编目星（业务星批次 %d 个）" % (group_key, added, n_pre))
    return added

LAUNCH_ROWS = []   # [{"cos", "row", "groups"}]：批次 ↔ 发射表 ↔ 分组归属
FILE_GROUPS = {    # 内置发射表 → 分组归属
    "launches.json": ("qianfan",),
    "gw_launches.json": ("gw",),
    "gw_geo.json": ("gaogui",),
}
AUTO_LAUNCH_FILE = "launches_auto.json"   # 卫星百科自动同步的任务表（在缓存目录，可写）


def _upsert_launch(cos, row, groups):
    """按 (分组, COSPAR) 去重写入任务表索引；同键后到者覆盖（自动同步的较新数据优先）。"""
    cos = (cos or "").strip()
    if not cos:
        return False
    prev = None
    for lr in LAUNCH_ROWS:
        if lr["cos"] == cos and set(lr["groups"]) & set(groups):
            prev = lr["row"]
            break
    lch = {
        "mission": row.get("group") or row.get("mission") or "",
        "rocket": row.get("rocket") or "",
        "launch_time": row.get("time") or row.get("launch_time") or "",
        "site": row.get("site") or "",
        "plane": row.get("plane") or "",
        "orbit": row.get("orbit") or "",
        # ★ 研制方（卫星百科「研发机构 / 研制单位」列，按组次给）：新数据没带就沿用
        #   同键旧值。否则自动同步表里那些还没抓到此列的历史行，会把静态表里
        #   已核实的研制方清成空（2026-10-06）。
        "dev": row.get("dev") or (prev or {}).get("dev") or "",
    }
    for i, lr in enumerate(LAUNCH_ROWS):
        if lr["cos"] == cos and set(lr["groups"]) & set(groups):
            LAUNCH_ROWS[i] = {"cos": cos, "row": row, "groups": groups}
            LOCAL_LAUNCHES[cos] = lch
            return True
    LAUNCH_ROWS.append({"cos": cos, "row": row, "groups": groups})
    LOCAL_LAUNCHES[cos] = lch
    return True


def load_launches():
    """载入发射任务表，按 COSPAR 前段建立索引，给每颗星补
    「组次 / 火箭型号与遥号 / 发射场 / 轨道面或轨道」。

    来源优先级（后者覆盖前者）：
    1) data/launches.json       千帆 16 次（与卫星百科「千帆星座」组网星表一致）
    2) data/gw_launches.json    星网试验星+业务星+高轨三星（源自卫星百科「星网」词条）
    3) cache/launches_auto.json 程序每日从卫星百科自动同步的任务表（含新增发射）
    """
    global LOCAL_LAUNCHES
    LOCAL_LAUNCHES = {}
    LAUNCH_ROWS.clear()
    n = 0
    for fn, groups in FILE_GROUPS.items():
        p = os.path.join(DATA_DIR, fn)
        d = read_json(p)
        if not isinstance(d, list):
            continue
        k = 0
        for row in d:
            if _upsert_launch(row.get("cospar"), row, groups):
                k += 1
        n += k
        log("发射任务表载入 %s：%d 条" % (fn, k))
    auto = read_json(os.path.join(CACHE_DIR, AUTO_LAUNCH_FILE))
    if isinstance(auto, dict):
        k = 0
        for gkey, rows in auto.items():
            if gkey not in GROUPS:
                continue
            for row in rows if isinstance(rows, list) else []:
                if _upsert_launch(row.get("cospar"), row, (gkey,)):
                    k += 1
        if k:
            log("发射任务表载入 %s（自动同步）：%d 条" % (AUTO_LAUNCH_FILE, k))
    if not n:
        for p in (r"D:\Work Buddy 工作区\qianfan_orbit\launches.json",):
            d = read_json(p)
            if isinstance(d, list):
                LOCAL_LAUNCHES = {r["cospar"]: r for r in d if r.get("cospar")}
                log("发射任务表载入（回退源）：%d 次" % len(LOCAL_LAUNCHES))


# ------------------------------------------------------- 卫星百科任务表自动同步
# 用户要求：录入新发射不再靠人动手。程序每日抓取卫星百科「星网」「千帆星座」词条，
# 解析发射记录表（试验星/业务星）里的 COSPAR / 火箭 / 时间 / 场站，
# 写入 cache/launches_auto.json 并热更新任务索引；后续 COSPAR 扩展自动接力。
WIKI_PAGES = (
    # (分组, 词条名, 允许收录的小节关键词)——经 api.php 取渲染后的正文
    ("gw", "%E6%98%9F%E7%BD%91", ("业务星", "试验星")),
    ("qianfan", "%E5%8D%83%E5%B8%86%E6%98%9F%E5%BA%A7", ("组网星", "业务星")),
)
WIKI_SYNC_TTL = 24 * 3600     # 卫星百科任务表同步间隔（启动时另有一次即时同步）。
                              # 新发射批次本就不频繁（月均几次），24 小时足够。
_wiki_sync_lock = threading.Lock()
_wiki_sync_state = {"t": 0.0}

_SITE_KEYS = ("文昌", "太原", "西昌", "酒泉", "海南商发", "海商", "东方航天港",
              "南海", "黄海", "海上", "烟台", "海阳")
_ROCKET_KEYS = ("长征", "朱雀", "引力", "捷龙", "快舟", "双曲线", "天龙", "力箭")


def _strip_tags(s):
    import re as _re
    s = _re.sub(r"<[^>]+>", "", s or "")
    s = _re.sub(r"\[\d+\]", "", s)          # 引用角标
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return s.strip()


_DEV_HDR = r"研发机构|研制单位|研发单位|研制方|制造方"


def parse_wiki_launches(html):
    """从词条 HTML 解析发射记录：返回 {分组: [row, ...]}。
    只收录 COSPAR 形如 YYYY-NNN 的行（失败任务 2025-F05 之类自动跳过）。

    ★ 研制方（千帆词条叫「研发机构」、星网词条叫「研制单位」）**按组次给，不按单星**，
      所以这里跟 cospar 一样是「批次级」字段，由 load_launches → LOCAL_LAUNCHES
      按 COSPAR 前缀一路带到每颗星（2026-10-06 新增）。
    """
    import re as _re
    out = {}
    # 按 h2/h3 小节切块并记录小节标题
    parts = _re.split(r"<h[23][^>]*>(.*?)</h[23]>", html, flags=_re.S)
    # parts: [前言, 标题1, 块1, 标题2, 块2, ...]
    for i in range(1, len(parts) - 1, 2):
        title = _strip_tags(parts[i])
        body = parts[i + 1]
        hit = None
        for gkey, _, sections in WIKI_PAGES:
            if any(s in title for s in sections):
                hit = gkey
                break
        if not hit:
            continue
        dev_idx = None      # 「研发机构 / 研制单位」列在本小节表里的下标
        for tr in _re.findall(r"<tr[^>]*>(.*?)</tr>", body, _re.S):
            raw = [_strip_tags(c) for c in
                   _re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, _re.S)]
            # 表头行 → 认出研制方列在哪一列，然后跳过。用**原始列位**（不先滤掉空
            # 单元格），否则某个空单元格会把下标整体错位、取到隔壁的运载火箭。
            # 同一小节里表头可能重复出现（长表分块），所以每次都重认一遍。
            if _re.search(_DEV_HDR, " ".join(raw)) and _re.search(
                    r"名称|中文名|序号|发射次数", " ".join(raw)):
                for j, c in enumerate(raw):
                    if _re.search(_DEV_HDR, c):
                        dev_idx = j
                        break
                continue
            cells = [c for c in raw if c]
            if len(cells) < 4:
                continue
            cos = None
            for c in cells:
                m = _re.search(r"\b(\d{4}-\d{3})[A-Z]?\b", c)
                if m:
                    cos = m.group(1)
                    break
            if not cos:
                continue
            row = {"cospar": cos, "mission": "", "rocket": "", "time": "", "site": "",
                   "orbit": "", "dev": ""}
            if dev_idx is not None and dev_idx < len(raw):
                row["dev"] = raw[dev_idx].strip()
            for c in cells:
                m = _re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}:\d{2})?", c)
                if m and not row["time"]:
                    t = "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
                    if m.group(4):
                        t += " " + m.group(4)
                    row["time"] = t
                    continue
                if any(k in c for k in _ROCKET_KEYS) and (not row["rocket"] or len(c) > len(row["rocket"])):
                    row["rocket"] = c
                    continue
                if any(k in c for k in _SITE_KEYS) and not row["site"] and "发射" not in c[:2]:
                    row["site"] = c
                    continue
            if not row["mission"]:
                for c in cells:
                    if _re.match(r"^\d+$", c):
                        continue
                    if len(c) >= 4 and not _re.search(r"\d{4}-\d{3}", c[:4]):
                        row["mission"] = c
                        break
            out.setdefault(hit, []).append(row)
    return out


_wiki_route = {"proxy": "<未定>", "t": 0.0}
_WIKI_BLOCKED = {"until": 0.0}     # 整轮全失败后的退避（秒）


def wiki_fetch_page(page, net=None, timeout=30):
    """取卫星百科某词条的渲染正文（HTML 字符串）；全候选都失败返回 None。

    ★ 卫星百科**直连经常 403**（Cloudflare，间歇性），走本地代理则稳通；而全局出口会因为
      「CelesTrak 直连更快」被判成 direct。所以这里**独立枚举出口候选**，绝不复用
      net['proxy']。命中后把可用出口记到 cache/route_proxy.json，下一轮优先用它。
      （2026-10-03 修复：此前该链路 25 次尝试 0 成功，等于功能不存在。）
    """
    if time.time() < _WIKI_BLOCKED["until"]:
        return None
    u = ("https://sat.huijiwiki.com/api.php?action=parse&page=%s"
         "&format=json&prop=text" % page)
    cands = proxy_probe_candidates(net)
    for p in cands:
        try:
            # 探测用短超时：可用代理 ~0.3 s 回，非代理端口立刻失败，不会拖时间
            raw = http_get(u, proxy=p, timeout=min(timeout, 10), retries=1)
            j = json.loads(raw.decode("utf-8", "replace"))
            html = (j.get("parse") or {}).get("text") or {}
            if isinstance(html, dict):
                html = html.get("*") or ""
            if html:
                if _wiki_route["proxy"] != p:
                    _wiki_route.update({"proxy": p, "t": time.time()})
                    log("卫星百科出口确定：%s" % (p or "直连"))
                write_json(os.path.join(CACHE_DIR, "route_proxy.json"),
                           {"proxy": p, "checked_at": iso_local(now_local()),
                            "for": "wiki"})
                return html
        except Exception:
            continue
    _WIKI_BLOCKED["until"] = time.time() + 1800
    log("卫星百科：%d 个候选出口全部失败（含本机 %d 个监听端口），30 分钟后退避"
        % (len(cands), len(localhost_ports())))
    return None


def wiki_launch_sync(net=None, force=False):
    """抓取卫星百科词条，把（新出现的）发射任务合并进 launches_auto.json。
    幂等：已存在且字段齐全的批次原样保留；网络失败静默跳过（沿用现有任务表）。
    灰机 wiki 对连续请求限流（偶发 403），所以用 API 端点 + 页间延迟 + 独立出口枚举。"""
    with _wiki_sync_lock:
        if not force and time.time() - _wiki_sync_state["t"] < WIKI_SYNC_TTL:
            return 0
        _wiki_sync_state["t"] = time.time()
    merged = 0
    filled = 0
    failed = []
    auto = read_json(os.path.join(CACHE_DIR, AUTO_LAUNCH_FILE)) or {}
    if not isinstance(auto, dict):
        auto = {}
    for gkey, page, _sec in WIKI_PAGES:
        html = wiki_fetch_page(page, net)
        if not html:
            failed.append(gkey)
            continue
        try:
            parsed = parse_wiki_launches(html)
            rows = parsed.get(gkey) or []
            cur = list(auto.get(gkey) or [])
            by_cos = {r.get("cospar"): r for r in cur}
            fresh = 0
            touched = False
            for r in rows:
                old = by_cos.get(r["cospar"])
                if old is not None:
                    # 已有批次：**只补空字段**（研制方是后加的列，历史行没有），
                    # 已核实过的内容一个字都不动。
                    for k, v in r.items():
                        if v and not old.get(k):
                            old[k] = v
                            touched = True
                            if k == "dev":
                                filled += 1
                    continue
                cur.append(r)
                by_cos[r["cospar"]] = r
                fresh += 1
            if fresh or touched:
                auto[gkey] = cur
                merged += fresh
            if fresh:
                log("卫星百科同步 %s：新增 %d 个批次（%s）"
                    % (gkey, fresh, "、".join(r["cospar"] for r in cur[-fresh:])))
        except Exception as e:
            log("卫星百科同步 %s 解析失败（不影响现有数据）：%s" % (gkey, e))
        time.sleep(8)   # 页间延迟，别惹灰机的限流器
    write_json(os.path.join(CACHE_DIR, AUTO_LAUNCH_FILE), auto)
    n_qf = len(auto.get("qianfan") or [])
    n_gw = len(auto.get("gw") or [])
    if merged or filled:
        load_launches()   # 热更新任务索引，COSPAR 扩展/研制方即刻生效
        log("卫星百科同步完成：新增 %d 个批次、回填研制方 %d 处，任务索引已热更新"
            "（千帆 %d / 星网 %d 批次）" % (merged, filled, n_qf, n_gw))
    elif failed and len(failed) == len(WIKI_PAGES):
        # ★ 措辞区分开：不能再说「任务表已是最新」——那是抓取失败的假象
        log("卫星百科同步失败：%s 抓取不到，**沿用现有任务表**（千帆 %d / 星网 %d 批次）"
            % ("、".join(failed), n_qf, n_gw))
    else:
        if failed:
            log("卫星百科同步部分失败：%s 未抓到，已抓到的分组无新增" % "、".join(failed))
        else:
            log("卫星百科同步：任务表已是最新（千帆 %d / 星网 %d 批次）" % (n_qf, n_gw))
    return merged


def _bg_wiki_sync():
    try:
        net = resolve_network()
        wiki_launch_sync(net)
    except Exception as e:
        log("卫星百科同步线程异常（不影响主体数据）：%s" % e)


def maybe_start_wiki_sync(delay=20.0):
    """启动后延迟跑一次，之后每 24 小时自动同步一次。"""
    def _loop():
        time.sleep(delay)
        while True:
            _bg_wiki_sync()
            time.sleep(WIKI_SYNC_TTL)
    threading.Thread(target=_loop, daemon=True, name="wiki-sync").start()
    log("卫星百科任务表自动同步已启动（启动后一次，之后每 24 小时一次）")


# ---------------------------------------------------------------- 交付物自动更新
# 把「两张爬升大图（HTML + PNG）」也纳入自动更新：查看器启动后台跑一次，之后每 12 小时一次。
# 重活交给项目里的 refresh_deliverables.py（追平历史末点 → 重生成大图 → Edge 截图）。
# ★ 为什么不用系统计划任务：本机 schtasks.exe 被安全策略禁用；
#   而用 JSON 配置传中文路径也不会有 .bat 的代码页乱码问题。
DELIV_CONFIG = os.path.join(CACHE_DIR, "deliverables_refresh.json")
_DELIV_TTL = 12 * 3600     # 大图重生成间隔（启动时另有一次即时重生成）。
                           # 曲线数据每天才长一个点，12 小时足够新。


def _deliv_run_once(force=False):
    """到点就跑一次 refresh_deliverables.py。未配置/脚本不存在 → 静默跳过。"""
    cfg = read_json(DELIV_CONFIG) or {}
    if not cfg.get("enabled", True):
        return 0
    script = cfg.get("script") or ""
    if not (script and os.path.exists(script)):
        return 0
    py = cfg.get("python") or ""
    if not (py and os.path.exists(py)):
        py = sys.executable
    if not (py and os.path.exists(py)):
        return 0
    if not force and time.time() - float(cfg.get("last_run_ts") or 0) < _DELIV_TTL:
        return 0
    import subprocess
    kw = {}
    if os.name == "nt":
        # 无窗口 + 脱离父进程：查看器关掉也不会把它带走
        kw["creationflags"] = 0x00000008 | 0x08000000
    try:
        subprocess.Popen([py, script], cwd=os.path.dirname(script),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    except Exception as e:
        log("交付物更新：启动失败 %s" % e)
        return 0
    cfg["last_run_ts"] = time.time()
    cfg["last_run_at"] = iso_local(now_local())
    write_json(DELIV_CONFIG, cfg)
    log("交付物更新：已在后台重生成两张爬升大图（%s）" % os.path.basename(script))
    return 1


def maybe_start_deliverables_refresh(delay=60.0):
    """启动后延迟一次，之后每 10 分钟醒来检查一次 TTL（默认 12 小时跑一轮）。"""
    def _loop():
        time.sleep(delay)
        while True:
            try:
                _deliv_run_once()
            except Exception as e:
                log("交付物更新线程异常（不影响主体数据）：%s" % e)
            time.sleep(600)
    threading.Thread(target=_loop, daemon=True, name="deliv-refresh").start()
    if read_json(DELIV_CONFIG):
        log("交付物自动更新已启动（启动后一次，之后每 12 小时一次，可删 %s 关闭）" % DELIV_CONFIG)


# ---------------------------------------------------------------- 缺星补抓
_noTLE_cache = {}   # group -> {"norads": [...], "t": ts}  近 24h 内确认过「尚无公开根数」
_NO_TLE_TTL = 24 * 3600
_BACKFILL_MAX = 48  # 单次刷新最多补抓多少颗，避免对 CelesTrak 连打请求


def backfill_missing(group_key, sats, meta_map, net):
    """兜底补星：主数据源已改为完整的 OMM(JSON)，正常情况下这里不会触发。
    只有 satcat 里编目、但 gp.php?NAME= 与 satcat 都没有给出根数的个别星才会走到。

    用 gp.php?CATNR=<n>&FORMAT=json 取 OMM 再本地转 TLE —— 注意不能用
    FORMAT=tle：6 位（Alpha-5）NORAD 号在该接口下会 404，而 FORMAT=json 正常。
    只有确认 404（真的没有根数）才写 24h 负缓存；502/超时等偶发错误不写，
    否则一次网络抖动会让这些星被「封口」一整天。
    """
    g = GROUPS[group_key]
    if not g.get("backfill"):
        return sats, 0
    have = {s["norad"] for s in sats}
    neg = _noTLE_cache.get(group_key) or {}
    if time.time() - neg.get("t", 0) > _NO_TLE_TTL:
        saved = read_json(os.path.join(CACHE_DIR, group_key + "_noTLE.json")) or {}
        if time.time() - saved.get("t", 0) < _NO_TLE_TTL:
            neg = saved
            _noTLE_cache[group_key] = saved
    neg_set = set(neg.get("norads") or [])

    todo = []
    for norad_str, rec in meta_map.items():
        n = _int_or_none(norad_str)
        if not n or n in have or n in neg_set or rec.get("obj_type") == "DEB":
            continue
        todo.append(n)
    todo.sort()
    todo = todo[:_BACKFILL_MAX]
    if not todo:
        return sats, 0

    extra, got, dead = [], 0, []
    for n in todo:
        try:
            if DATA_SRC == "spacetrack":
                # ST：按 NORAD 直查 GP（自带 TLE_LINE）；未编目号返回**空数组**（CT 是 404）
                arr = st_json(ST_Q.format(cls="gp", pred="NORAD_CAT_ID", val=str(n),
                                          extra=""), timeout=60)
                if isinstance(arr, list) and arr and arr[0].get("TLE_LINE1"):
                    r0 = arr[0]
                    extra.append(parse_tle("%s\n%s\n%s\n" % (
                        (r0.get("OBJECT_NAME") or "").strip(),
                        r0["TLE_LINE1"].rstrip(), r0["TLE_LINE2"].rstrip())))
                    got += 1
                elif isinstance(arr, list) and not arr:
                    dead.append(n)      # 确实还没有公开根数
                elif isinstance(arr, list) and arr and arr[0].get("MEAN_MOTION"):
                    extra.append(parse_tle("\n".join(omm_to_tle(arr[0]))))
                    got += 1
            else:
                raw = http_get("https://celestrak.org/NORAD/elements/gp.php?CATNR=%d&FORMAT=json" % n,
                               proxy=net.get("proxy") if net else None, timeout=25)
                arr = json.loads(raw.decode("utf-8", "replace"))
                if isinstance(arr, list) and arr and arr[0].get("MEAN_MOTION"):
                    extra.append(parse_tle("\n".join(omm_to_tle(arr[0]))))
                    got += 1
        except urllib.error.HTTPError as e:
            if e.code == 404:
                dead.append(n)      # 确实还没有公开根数
        except Exception:
            pass                    # 偶发失败：不写负缓存，下次再试
        time.sleep(0.35)            # 对数据源客气一点（ST 侧另有全局限流）
    if dead:
        neg_set.update(dead)
        rec = {"norads": sorted(neg_set), "t": time.time()}
        _noTLE_cache[group_key] = rec
        write_json(os.path.join(CACHE_DIR, group_key + "_noTLE.json"), rec)
    if got:
        log("补抓 %s：%d 颗通过 CATNR+OMM 找回" % (group_key, got))
    return sats + [s for part in extra for s in part], got


# ------------------------------------------------------- 异步补抓（不阻塞首屏）
# 首次联网时 NAME 检索会漏 9~19 颗已编目星，逐颗 CATNR 补抓要一分多钟。
# 所以改成：先立刻返回主体数据，补抓在后台线程跑，跑完写回 .tle 缓存，
# 前端看到 backfill.running=true 就隔几秒自动再拉一次。
_backfill_lock = threading.Lock()
_backfill_state = {}   # group -> {"running","added","last","note"}


def _bf_state(group_key):
    return _backfill_state.setdefault(
        group_key, {"running": False, "added": 0, "last": 0.0, "note": ""})


def backfill_info(group_key):
    st = _bf_state(group_key)
    return {"running": bool(st["running"]), "added": int(st["added"]), "note": st["note"]}


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def maybe_start_backfill(group_key, sats, meta_map, net, meta, force=False):
    """按需在后台补齐缺星；同一分组同一 TTL 内只跑一次。

    ★ force=True（用户手动「刷新 TLE」）会重置 2 小时门闩。
      否则强刷会先丢掉补回的星、又在 2 小时内拒绝补回（2026-10-03 实测：
      刷新后国网从 213 掉回 186，55 秒后仍是 186 且 note='无需补抓'）。
    """
    if not GROUPS[group_key].get("backfill"):
        return
    if (net or {}).get("mode") == "none":
        return   # 无网络出口：不补抓，等出口恢复后由下次刷新触发
    st = _bf_state(group_key)
    with _backfill_lock:
        if force:
            st["last"] = 0.0      # ★ 手动强刷：门闩清零，立刻重补
        if st["running"] or time.time() - st["last"] < GROUPS[group_key]["ttl"]:
            return
        have = {s["norad"] for s in sats}
        pending = [n for n in (_int_or_none(k) for k in meta_map) if n and n not in have]
        st["last"] = time.time()
        if not pending:
            st["note"] = "无需补抓"
            return
        st.update({"running": True, "added": 0, "note": "正在补齐缺星…"})
    threading.Thread(target=_bg_backfill, args=(group_key, sats, meta_map, net, meta),
                     daemon=True, name="backfill-" + group_key).start()
    log("补抓 %s：后台开始补齐 %d 颗缺星" % (group_key, len(pending)))


def _bg_backfill(group_key, sats, meta_map, net, meta):
    st = _bf_state(group_key)
    try:
        merged, got = backfill_missing(group_key, sats, meta_map, net)
        st["added"] = got
        st["note"] = ("已补齐 %d 颗缺星" % got) if got else "已确认缺星暂无公开根数"
        if got:
            tle_f, meta_f = cache_paths(group_key)
            text = "".join("%s\n%s\n%s\n" % (s["name"], s["l1"], s["l2"]) for s in merged)
            with open(tle_f, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            meta = dict(meta or {})
            meta["count"] = len(merged)
            meta["backfilled"] = got
            meta["fetched_ts"] = time.time()
            write_json(meta_f, meta)
            log("补抓 %s 完成：+%d 颗（缓存已更新，前端会自动刷新）" % (group_key, got))
    except Exception as e:
        st["note"] = "补抓失败"
        log("补抓 %s 失败（不影响主体数据）：%s" % (group_key, e))
    finally:
        st["running"] = False





# ---------------------------------------------------------------- TLE 缓存与抓取
_tle_lock = threading.Lock()


def cache_paths(group_key):
    return (os.path.join(CACHE_DIR, group_key + ".tle"),
            os.path.join(CACHE_DIR, group_key + ".json"))


# ------------------------------------------------- 后台静默刷新（stale-while-revalidate）
# 缓存过期时页面也**不等待**：先秒回旧缓存，由后台线程刷新，前端轮询拿新数据。
_refreshing_lock = threading.Lock()
_refreshing = {}   # group -> {"running": bool, "last": ts}


def _refresh_in_progress(group_key):
    with _refreshing_lock:
        st = _refreshing.get(group_key)
        return bool(st and st.get("running"))


def _start_bg_refresh(group_key):
    """同一分组同一时刻只跑一个后台刷新；60 秒内不重复触发。"""
    with _refreshing_lock:
        st = _refreshing.setdefault(group_key, {"running": False, "last": 0.0})
        if st["running"] or time.time() - st["last"] < 60:
            return False
        st["running"] = True
        st["last"] = time.time()
    threading.Thread(target=_bg_refresh, args=(group_key,),
                     daemon=True, name="refresh-" + group_key).start()
    return True


def _bg_refresh(group_key):
    try:
        load_tle(group_key, force=True)   # force 路径会写缓存，完成前端轮询即得新数据
        log("后台刷新 %s 完成" % group_key)
    except Exception as e:
        log("后台刷新 %s 失败（不影响缓存显示）：%s" % (group_key, e))
    finally:
        with _refreshing_lock:
            st = _refreshing.get(group_key)
            if st:
                st["running"] = False


def load_tle(group_key, force=False):
    g = GROUPS[group_key]
    tle_f, meta_f = cache_paths(group_key)
    meta = read_json(meta_f) or {}
    cached_text = None
    net = None   # ★ 必须先初始化：force 强刷路径（有旧缓存）不经过下面的联网分支，
                 #   曾因 net 未赋值直接 UnboundLocalError，三组后台刷新同时炸（2026-09-29 修复）
    try:
        with open(tle_f, "r", encoding="utf-8", errors="replace") as f:
            cached_text = f.read()
    except Exception:
        pass

    fresh = bool(cached_text and meta.get("fetched_ts") and
                 time.time() - meta["fetched_ts"] < g["ttl"])
    if fresh and not force:
        sats = parse_tle(cached_text)
        # 元信息走缓存（内存/磁盘），响应立即返回；联网补抓与补星全部后台化
        payload = build_payload(group_key, sats, meta, stale=False, error=None,
                                from_cache=True,
                                meta_map=fetch_group_meta(group_key, allow_network=False))

        def _bg_extra():
            try:
                maybe_start_backfill(group_key, sats, fetch_group_meta(group_key),
                                     resolve_network(), meta)
            except Exception:
                pass
        threading.Thread(target=_bg_extra, daemon=True, name="meta-" + group_key).start()
        return payload

    if not force and not cached_text:
        pass  # 必须联网

    if not force and cached_text:
        # ★ stale-while-revalidate：缓存过期也绝不让页面等——
        #   无条件先回旧缓存（refreshing=true），连网络探测都交给后台线程；
        #   前端 5 秒轮询只打本地接口，刷完自动换新。无网络时后台刷新会在
        #   冷却期后自动重新探测，网络一恢复数据就自动更新。
        sats = parse_tle(cached_text)
        _start_bg_refresh(group_key)
        return build_payload(group_key, sats, meta, stale=True,
                             error="后台正在刷新数据…（当前显示本地缓存，完成后自动更新）",
                             from_cache=True, refreshing=True,
                             meta_map=fetch_group_meta(group_key, allow_network=False))
    if not cached_text:
        net = resolve_network()
        if net.get("mode") == "none":
            return build_payload(group_key, [], meta, stale=True,
                                 error="无法访问 %s 且无本地缓存" % SRC_LABEL)

    # force 强刷（有旧缓存）走到这里时 net 还没解析 —— 统一在此兜底：
    if net is None:
        net = resolve_network()
        if net.get("mode") == "none":
            sats = parse_tle(cached_text) if cached_text else []
            log("刷新 %s：无可用网络出口，继续用本地缓存（%d 颗）" % (group_key, len(sats)))
            return build_payload(group_key, sats, meta, stale=True,
                                 error="无法访问 %s（当前显示本地缓存）" % SRC_LABEL,
                                 from_cache=True,
                                 meta_map=fetch_group_meta(group_key, allow_network=False))

    # —— 主数据源（双源，2026-10-09）：
    #   celestrak 模式：gp.php?FORMAT=json（OMM 根数，完整）。不用 FORMAT=tle 做主源：
    #     NAME 检索会漏掉所有 6 位（Alpha-5）NORAD 号的星，千帆少 19 颗、星网少 9 颗。
    #     OMM 拿全后本地按列位拼回 TLE，喂前端 satellite.js。
    #   spacetrack 模式：GP 名字查询 + 批次 OBJECT_ID 补抓 → 直接用官方 TLE_LINE1/2
    #     （见 st_group_rows / st_rows_to_tle_text 处注释）。
    url = group_omm_url(g)
    try:
        if DATA_SRC == "spacetrack":
            arr = st_group_rows(group_key)
            if not isinstance(arr, list) or not arr:
                raise ValueError("Space-Track GP 返回为空")
            text = st_rows_to_tle_text(arr)
        else:
            arr = json.loads(http_get(url, proxy=net.get("proxy"), timeout=40)
                             .decode("utf-8", "replace"))
            arr = filter_group_rows(g, arr)
            if not isinstance(arr, list) or not arr:
                raise ValueError("OMM 返回为空")
            triple = [omm_to_tle(r) for r in arr if r.get("MEAN_MOTION")]
            text = "".join("%s\n%s\n%s\n" % t for t in triple)
        _omm_last[group_key] = {"t": time.time(), "rows": arr}
        sats = parse_tle(text)
        if not sats:
            raise ValueError("没有解析到 TLE")
        with open(tle_f, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        meta = {
            "group": group_key,
            "fetched_at": iso_local(now_local()),
            "fetched_ts": time.time(),
            "source": SRC_LABEL,
            "proxy": (st_source.last_route_desc() if DATA_SRC == "spacetrack"
                      else (net.get("proxy") or "直连")),
            "count": len(sats),
        }
        write_json(meta_f, meta)
        log("TLE %s：%s 取回 %d 颗 → TLE %d 条（%s）"
            % (group_key, "GP" if DATA_SRC == "spacetrack" else "OMM",
               len(arr), len(sats), meta["proxy"]))
        payload = build_payload(group_key, sats, meta, stale=False, error=None)
        try:
            maybe_start_backfill(group_key, sats, fetch_group_meta(group_key), net, meta,
                                 force=force)
        except Exception as e:
            log("启动补抓 %s 失败（不影响主体数据）：%s" % (group_key, e))
        return payload
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
        log("主源 %s 抓取失败：%s%s" % (group_key, err,
            "" if DATA_SRC == "spacetrack" else "，退回 FORMAT=tle 老接口"))

    if DATA_SRC == "spacetrack":
        # ST 模式没有第二来源：与 CT 模式「回退也失败」同一套收尾——回本地缓存
        if cached_text:
            sats = parse_tle(cached_text)
            return build_payload(group_key, sats, meta, stale=True,
                                 error="联网失败，显示本地缓存（%s）" % err)
        return build_payload(group_key, [], meta, stale=True, error=err)

    # —— 回退：老接口 FORMAT=tle（会少几颗 6 位号的星，但聊胜于无）
    try:
        raw = http_get(group_tle_url(g),
                       proxy=net.get("proxy"), timeout=35)
        text = filter_group_tle_text(g, raw.decode("utf-8", "replace"))
        sats = parse_tle(text)
        if not sats:
            raise ValueError("返回内容中没有解析到 TLE")
        with open(tle_f, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        meta = {
            "group": group_key,
            "fetched_at": iso_local(now_local()),
            "fetched_ts": time.time(),
            "source": SRC_LABEL,
            "proxy": net.get("proxy") or "直连",
            "count": len(sats),
        }
        write_json(meta_f, meta)
        log("TLE %s：回退接口取回 %d 颗（%s）" % (group_key, len(sats), meta["proxy"]))
        payload = build_payload(group_key, sats, meta, stale=False, error=None)
        try:
            maybe_start_backfill(group_key, sats, fetch_group_meta(group_key), net, meta,
                                 force=force)
        except Exception:
            pass
        return payload
    except Exception as e:
        err2 = "%s: %s" % (type(e).__name__, e)
        log("TLE %s 回退接口也失败：%s" % (group_key, err2))
        if cached_text:
            sats = parse_tle(cached_text)
            return build_payload(group_key, sats, meta, stale=True,
                                 error="联网失败，显示本地缓存（%s）" % err2)
        return build_payload(group_key, [], meta, stale=True, error=err2)


def build_payload(group_key, sats, meta, stale=False, error=None, from_cache=False,
                  refreshing=False, meta_map=None):
    g = GROUPS[group_key]
    meta_local = local_meta(group_key)
    if meta_map is not None:
        satcat = meta_map   # 调用方已备好（秒开路径：只用缓存，不阻塞在网络抓取上）
    else:
        satcat = {}
        try:
            satcat = fetch_group_meta(group_key)   # 已按分组隔离（gp.json ∪ satcat）
        except Exception:
            satcat = {}

    epochs = [s["epoch_ms"] for s in sats if s.get("epoch_ms")]
    now_ms = int(time.time() * 1000)
    have = set()
    for s in sats:
        info = dict(meta_local.get(str(s["norad"])) or {})
        info.update({k: v for k, v in (satcat.get(str(s["norad"])) or {}).items() if v})
        # ★ 研制方兜底：卫星百科按「组次」给（千帆叫研发机构 / 星网叫研制单位），
        #   不在单星元信息里。万一 metamap 还没并进来（进程刚起来、缓存未过一遍），
        #   这里再按 COSPAR 前缀直接从任务表取一次，保证详情面板恒有值（2026-10-06）。
        _cos = info.get("cospar") or ""
        if len(_cos) >= 8 and not info.get("dev"):
            _dev = (LOCAL_LAUNCHES.get(_cos[:8]) or {}).get("dev")
            if _dev:
                info["dev"] = _dev
        if info:
            s["meta"] = info
        if s.get("epoch_ms"):
            s["epoch_age_days"] = round((now_ms - s["epoch_ms"]) / 86400000.0, 3)
        if s.get("norad"):
            have.add(s["norad"])

    # 已编目总数只统计本分组；no_tle = 已被 CelesTrak 编目、但尚未公布根数的载荷
    catalog_total = len(satcat) if satcat else len(sats)
    no_tle = 0
    for k, rec in satcat.items():
        n = _int_or_none(k)
        if n and n not in have and (rec.get("obj_type") or "PAY") != "DEB":
            no_tle += 1

    return {
        "group": group_key,
        "label": g["label"],
        "sub": g["sub"],
        "color": g["color"],
        "source": SRC_LABEL,   # 来源随 DATA_SRC（CelesTrak / Space-Track.org）；不读缓存里的旧标注
        "fetched_at": meta.get("fetched_at"),
        "from_cache": bool(from_cache),
        "stale": bool(stale),
        "refreshing": bool(refreshing) or _refresh_in_progress(group_key),
        "error": error,
        "count": len(sats),
        "catalog_total": catalog_total,
        "no_tle": no_tle,
        "backfill": backfill_info(group_key),
        "epoch_min": min([s["epoch"] for s in sats if s.get("epoch")] or [None]),
        "epoch_max": max([s["epoch"] for s in sats if s.get("epoch")] or [None]),
        "epoch_age_days": round((now_ms - max(epochs)) / 86400000.0, 3) if epochs else None,
        "server_time": iso_local(now_local()),
        "sats": sats,
    }


# ---------------------------------------------------------------- HTTP 服务
MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "ConstellationOrbitViewer/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # 静音访问日志

    # ---------- 响应工具
    def send_bytes(self, body, ctype, code=200, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # 基础安全头：不许按内容嗅探类型、不外泄来路、只允许同源页面嵌本服务
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def send_json(self, obj, code=200):
        self.send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                        MIME[".json"], code)

    def send_file(self, path):
        if not os.path.isfile(path):
            return self.send_json({"error": "not found", "path": path}, 404)
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            body = f.read()
        self.send_bytes(body, MIME.get(ext, "application/octet-stream"))

    # ---------- 来源校验
    def host_ok(self):
        """Host 头必须是本机回环名（挡 DNS 重绑定）；局域网模式下再放行私有网段 IP 字面量。"""
        h = header_hostname(self.headers.get("Host"))
        return h in _ALLOWED_HOSTS or (_LAN_MODE and is_lan_literal(h))

    def origin_ok(self):
        """状态变更请求必须是同源发起的（挡 CSRF）。

        现代浏览器都会带 Sec-Fetch-Site；缺省时不拦截（桌面版 WebView2 / 老浏览器），
        但只要带了 Origin 就必须是本机回环（局域网模式下再加私有网段 IP）。
        """
        sfs = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if sfs in ("cross-site", "same-site"):
            return False
        origin = self.headers.get("Origin")
        if origin:
            try:
                o = urllib.parse.urlparse(origin)
            except Exception:
                return False
            hn = (o.hostname or "").lower()
            if hn not in _LOOPBACK_NAMES and not (_LAN_MODE and is_lan_literal(hn)):
                return False
        return True

    # ---------- 路由
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        q = urllib.parse.parse_qs(u.query)

        try:
            if not self.host_ok():
                return self.send_json({"error": "bad host"}, 403)

            if path in ("/", "/index.html"):
                return self.send_file(os.path.join(WEB_DIR, "index.html"))

            if path == "/favicon.ico":
                return self.send_bytes(b"", "image/x-icon", 204)

            if path == "/api/groups":
                return self.send_json({
                    k: {"label": v["label"], "sub": v["sub"], "color": v["color"]}
                    for k, v in GROUPS.items()
                })

            if path == "/api/status":
                net = _proxy_state
                return self.send_json({
                    "boot": BOOT_ID,
                    "server_time": iso_local(now_local()),
                    "network": net,
                    "cache": {
                        k: (read_json(cache_paths(k)[1]) or {}) for k in GROUPS
                    },
                })

            if path == "/api/net":
                # 给页面上的「手机访问」卡片用：告诉它本机在局域网里的地址。
                # 只回地址与端口，不回任何凭据。
                return self.send_json({
                    "bind": _BIND_HOST,
                    "port": _BOUND_PORT,
                    "lan_enabled": _LAN_MODE,
                    "lan": lan_urls(_BOUND_PORT) if _LAN_MODE else [],
                })

            if path == "/quit":
                # 退出入口已改为 POST（见 do_POST）。GET 一律 405 —— 否则任意网页
                # 只要写一句 <img src="http://127.0.0.1:8770/quit"> 就能把程序关掉。
                return self.send_json({"error": "use POST /quit"}, 405)

            if path == "/api/tle":
                key = (q.get("group") or ["qianfan"])[0]
                if key not in GROUPS:
                    return self.send_json({"error": "未知分组 " + key}, 400)
                force = (q.get("force") or ["0"])[0] in ("1", "true", "yes")
                if force and not self.origin_ok():
                    # 强刷会真去联网并改写缓存 —— 不许被跨站页面当"免费计算资源"用
                    return self.send_json({"error": "cross-site force rejected"}, 403)
                t0 = time.time()
                with _tle_lock:
                    payload = load_tle(key, force=force)
                payload["elapsed_ms"] = int((time.time() - t0) * 1000)
                return self.send_json(payload)

            if path == "/api/ping":
                return self.send_json({"ok": True, "t": iso_local(now_local()), "boot": BOOT_ID})

            if path == "/api/sat_hist":
                norad = (q.get("norad") or [""])[0].strip()
                sat = SAT_HIST.get("sats", {}).get(norad)
                if not sat:
                    return self.send_json(
                        {"error": "no history", "norad": norad}, 404)
                return self.send_json({
                    "norad": norad,
                    "base": SAT_HIST.get("base", "2024-01-01"),
                    "now": SAT_HIST.get("now", ""),
                    **sat,
                })

            if path == "/api/batch_hist":
                # 组次聚合爬升历史：把同一 COSPAR 批次（batch 前 8 位）的全部
                # 卫星曲线打包返回，供 batch.html 叠加绘制。只查本地缓存，不联网。
                # 元信息来源三级兜底：内存 → 磁盘 metamap（不限龄，批次归属不会过期）→ 秒回路径
                grp = (q.get("group") or [""])[0].strip()
                bat = (q.get("batch") or [""])[0].strip()
                if grp not in GROUPS or not bat:
                    return self.send_json({"error": "bad group/batch"}, 400)
                with _satcat_lock:
                    c = _satcat_cache.get(grp)
                meta_map = c["map"] if c else None
                if not meta_map:
                    disk = read_json(os.path.join(CACHE_DIR, grp + "_metamap.json")) or {}
                    if isinstance(disk.get("map"), dict):
                        meta_map = disk["map"]
                if not meta_map:
                    meta_map = fetch_group_meta(grp, allow_network=False) or {}
                out, label = [], ""
                for norad, m in meta_map.items():
                    if (m.get("batch") or "") != bat:
                        continue
                    h = SAT_HIST.get("sats", {}).get(str(norad))
                    if not h:
                        continue
                    if not label:
                        label = (m.get("group") or m.get("mission")
                                 or ("第 %s 批" % m.get("batch_index", "?")))
                    out.append({
                        "norad": str(norad),
                        "name": h.get("name") or m.get("obj_name") or ("NORAD " + str(norad)),
                        "launch": h.get("launch") or m.get("launch_date") or "",
                        "target": h.get("target"),
                        "src": h.get("src") or grp,
                        "pts": h.get("pts") or [],
                    })
                out.sort(key=lambda x: (x.get("launch") or "", x["name"]))
                if not out:
                    return self.send_json(
                        {"error": "no history for batch", "group": grp, "batch": bat}, 404)
                return self.send_json({
                    "group": grp, "batch": bat,
                    "label": label or bat,
                    "base": SAT_HIST.get("base", "2024-01-01"),
                    "now": SAT_HIST.get("now", ""),
                    "count": len(out),
                    "sats": out,
                })

            # 静态资源
            if path.startswith("/web/"):
                rel = path[len("/web/"):]
                # 爬升大图优先用磁盘上的 live 覆盖层（见 live_raise_target 的说明）
                target = live_raise_target(rel) or static_target(WEB_DIR, rel)
                if target is None:
                    return self.send_json({"error": "forbidden"}, 403)
                return self.send_file(target)

            if path.startswith("/data/"):
                target = static_target(DATA_DIR, path[len("/data/"):])
                if target is None:
                    return self.send_json({"error": "forbidden"}, 403)
                return self.send_file(target)

            return self.send_json({"error": "not found", "path": path}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self.send_json({"error": "%s: %s" % (type(e).__name__, e)}, 500)
            except Exception:
                pass

    def do_POST(self):
        """只用于状态变更端点（目前只有 /quit）。
        三重门槛：Host 必须是本机 → 不能是跨站发起的 → 才是真退出。
        POST + Origin 校验能挡住 <img>/<script>/表单跨站提交这些 CSRF 手法。"""
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        try:
            if not self.host_ok():
                return self.send_json({"error": "bad host"}, 403)
            if path != "/quit":
                return self.send_json({"error": "not found", "path": path}, 404)
            if not self.origin_ok():
                log("已拒绝一个跨站退出请求（来源 %s / Sec-Fetch-Site=%s）"
                    % (self.headers.get("Origin") or "-",
                       self.headers.get("Sec-Fetch-Site") or "-"))
                return self.send_json({"error": "cross-site request rejected"}, 403)
            log("收到退出请求，程序即将结束。")
            try:
                self.send_json({"ok": True})
            except Exception:
                pass
            threading.Timer(0.4, os._exit, args=(0,)).start()
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self.send_json({"error": "%s: %s" % (type(e).__name__, e)}, 500)
            except Exception:
                pass


class Server(ThreadingHTTPServer):
    # Windows 下 SO_REUSEADDR 允许多进程绑同一端口，会让「端口占用自动换端口」失效
    allow_reuse_address = False
    daemon_threads = True


def bind_server(port, host="127.0.0.1", tries=15):
    """绑定端口并记下实际用的 host/port（/api/net 要靠它告诉页面手机该敲什么地址）。"""
    global _BIND_HOST, _BOUND_PORT
    _BIND_HOST = host or "127.0.0.1"
    if not port:
        srv = Server((host, 0), Handler)
        _BOUND_PORT = srv.server_address[1]
        return srv, _BOUND_PORT
    last = None
    for p in range(port, port + tries):
        try:
            srv = Server((host, p), Handler)
            _BOUND_PORT = p
            return srv, p
        except OSError as e:
            last = e
            continue
    raise last


def open_url(url):
    """尽量把页面在默认浏览器里打开；失败就返回 False（用户手动复制地址）。
    三级兜底：webbrowser → os.startfile → cmd start"""
    try:
        import webbrowser
        if webbrowser.open(url, new=2):
            return True
    except Exception:
        pass
    try:
        os.startfile(url)  # type: ignore[attr-defined]
        return True
    except Exception:
        pass
    try:
        import subprocess
        subprocess.Popen('start "" "%s"' % url, shell=True)
        return True
    except Exception:
        return False


def copy_to_clipboard(text):
    try:
        import subprocess
        p = subprocess.Popen("clip", stdin=subprocess.PIPE, shell=True)
        p.communicate(text.encode("utf-16-le"))
        return p.returncode == 0
    except Exception:
        return False


def banner(url, port, from_default_port):
    line = "=" * 62
    print(line, flush=True)
    print("  星座轨道实时查看器已启动", flush=True)
    print("", flush=True)
    print("  在浏览器里打开这个地址：", flush=True)
    print("", flush=True)
    print("      " + url, flush=True)
    print("", flush=True)
    if from_default_port:
        print("  （默认端口 8770 被占用，已自动换到 %d）" % port, flush=True)
    print("  这个窗口是服务本体，关闭窗口即停止程序。", flush=True)
    print(line, flush=True)


def main():
    ap = argparse.ArgumentParser(description="星座轨道实时查看器")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--host", default=None,
                    help="绑定地址；默认 127.0.0.1（只有本机能开）")
    ap.add_argument("--lan", action="store_true",
                    help="允许手机/平板在同一 Wi-Fi 下访问（等价于 --host 0.0.0.0）")
    ap.add_argument("--no-lan", action="store_true",
                    help="强制只监听本机（优先于 --lan）")
    ap.add_argument("--proxy", default=None, help="手动指定代理，如 http://127.0.0.1:7890")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--prefetch", action="store_true", help="启动时立刻抓一次 TLE")
    a = ap.parse_args()

    host = a.host
    if host is None:
        host = "0.0.0.0" if a.lan else "127.0.0.1"
    if a.no_lan:
        host = "127.0.0.1"

    global _cli_proxy
    _cli_proxy = a.proxy
    if a.proxy:
        log("使用指定代理：%s" % a.proxy)

    init_allowed_hosts(host)            # Host 头白名单：回环名 +（局域网模式下）私有网段 IP
    if DATA_SRC == "spacetrack":
        _ident, _pw = st_source.credentials()
        if not _ident:
            log("警告：未找到 spacetrack.txt（两行 identity=注册邮箱 / password=密码，"
                "放在 exe 同目录或用户主目录）——Space-Track 模式联网取数将失败，只能用本地缓存")
        else:
            log("数据来源：Space-Track.org（凭据已就绪；官方限额 30 次/分钟、300 次/小时，"
                "已内置会话复用 + 全局限流）")
    load_launches()
    # 大图覆盖层状态：有就说「用的是磁盘最新版」，没有就明说「用包内快照」——
    # 免得「exe 里大图停在打包日」这种事再靠用户发现（★ 恒空数据不报错是坑，见 README）。
    _live_n = 0
    try:
        _live_n = sum(1 for f in os.listdir(WEB_LIVE_RAISE)
                      if f.lower().endswith(".html"))
    except Exception:
        _live_n = 0
    if _live_n:
        log("爬升大图：使用磁盘覆盖层 web_live/raise/（%d 个文件，%s）" % (_live_n, WEB_LIVE_RAISE))
    elif FROZEN:
        log("爬升大图：包内快照（未找到 %s —— exe 里的大图会停在打包日；"
            "跑一次 refresh_deliverables.py 即可生成）" % WEB_LIVE_RAISE)
    maybe_start_wiki_sync(delay=20.0)   # 启动即同步卫星百科任务表，之后每 24 小时一次
    start_hist_backfill(delay=90.0)     # 并入本地补抓历史 + 每 3 小时自动补齐缺历史的新星
    maybe_start_deliverables_refresh(delay=60.0)   # 每 12 小时重生成两张爬升大图（HTML+PNG）
    srv, port = bind_server(a.port, host)
    # 浏览器地址永远写回环：绑 0.0.0.0 时 `http://0.0.0.0:8770/` 部分浏览器会直接拒绝
    url = "http://127.0.0.1:%d/" % port
    banner(url, port, port != a.port)
    if port != a.port:
        log("提示：端口 %d 被占用，已改用 %d" % (a.port, port))

    # 局域网地址：这是「手机能不能看」的唯一开关，启动时直接印在窗口里，
    # 免得用户还要自己去 ipconfig 里找 IPv4。
    if _LAN_MODE:
        _urls = lan_urls(port)
        if _urls:
            print("", flush=True)
            print("  手机 / 平板访问（与本机连同一个 Wi-Fi）：", flush=True)
            for _u in _urls:
                print("    " + _u, flush=True)
            print("", flush=True)
            log("局域网模式：%s" % ", ".join(_urls))
        else:
            print("  局域网模式已开启，但没探测到本机 IP（电脑未联网？）", flush=True)
            log("局域网模式已开启，但未探测到本机 IP")
    else:
        log("仅本机可访问；要让手机看，用 --lan 启动（start.bat 已默认开启）")

    # 预取（2026-09-29 提速）：启动瞬间自动开后台线程——出口判定 + 过期缓存刷新
    # 与「开浏览器」并行进行，不再等页面打开才触发。缓存新鲜时秒回、不发请求；
    # 过期时页面先显示缓存（refreshing=true），刷新完成后前端 5 秒轮询自动换新。
    # （旧 --prefetch 参数保留兼容，现在无论是否传参都会自动预取）
    def _prefetch_all():
        try:
            resolve_network()   # 先把出口判定做掉，后面各组直接复用结果
        except Exception:
            pass
        for k in GROUPS:
            try:
                load_tle(k)
            except Exception as e:
                log("预取 %s 失败：%s" % (k, e))
    threading.Thread(target=_prefetch_all, daemon=True, name="prefetch").start()

    if not a.no_browser:
        def do_open():
            # 等服务真正开始 accept 再开浏览器，避免刚打开就连接被拒
            for _ in range(40):
                if probe_tcp(a.host, port, 0.2):
                    break
                time.sleep(0.1)
            time.sleep(0.35)
            ok = open_url(url)
            if copy_to_clipboard(url):
                log("地址已复制到剪贴板（若浏览器没弹出，直接 Ctrl+V 粘贴）")
            if ok:
                log("已尝试用默认浏览器打开页面")
            else:
                log("未能自动打开浏览器，请手动复制上面的地址粘贴到浏览器")
        threading.Thread(target=do_open, daemon=True).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("已停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    sys.exit(main())
