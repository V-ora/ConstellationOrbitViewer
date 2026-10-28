# -*- coding: utf-8 -*-
"""Space-Track.org 数据传输层（星座查看器 SpaceTrack 版专用）。

★ 移植自星链查看器 st_data.py 的传输部分（2026-10-08 在星链版实测打磨过），
  只留「凭据 + 出口探测 + 登录会话复用 + 全局限流 + 带重试查询」，
  查询的组装与字段映射在 server.py 里做（两边关注点分离，避免互相牵动）。

为什么必须有会话复用 + 限流：Space-Track 的限额明文写死 **30 次/分钟、300 次/小时**，
登录本身也占配额。冷启动要发 3~6 个查询、缺星补抓一次可到几十个：
  - 复用会话：同一出口的 opener（带 cookie）留 20 分钟，登录从「每星一次」降到「20 分钟一次」；
  - 全局限流：任意两次出站之间至少隔 _ST_MIN_GAP 秒（≈28.5 次/分钟，留 5% 余量），
    再加一小时滑动窗口预算做硬保险。限流按进程全局生效，多前端并发也守得住。

凭据：spacetrack.txt（两行 `identity=…` / `password=…`），**不进源码、不打进 exe**。
查找顺序：环境变量 COV_ST_CRED → exe/工程目录 → 当前目录 → 用户主目录
（星链工程根有一份现成的，复制过来或放主目录皆可）。
"""
import os
import sys
import json
import time
import threading
import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar

ST_BASE = "https://www.space-track.org"
ST_LOGIN = ST_BASE + "/ajaxauth/login"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 "
       "ConstellationOrbitViewer-SpaceTrack/1.0")

_HERE = None
_CACHE = None
_log = print
_log_lock = threading.Lock()


def configure(here, cache_dir, log=None):
    global _HERE, _CACHE, _log
    _HERE, _CACHE = here, cache_dir
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except Exception:
        pass
    if log:
        _log = log


def _lp(msg):
    try:
        _log(msg)
    except Exception:
        pass


def _rp(*parts):
    return os.path.join(*parts)


# ---------------------------------------------------------------- 凭据
def _cred_files():
    out = []
    env = os.environ.get("COV_ST_CRED")
    if env:
        # 环境变量可直接指向凭据文件，也可指向所在目录
        out.append(env if os.path.isfile(env) else os.path.join(env, "spacetrack.txt"))
    for d in (_HERE, os.getcwd(), os.path.expanduser("~")):
        if d:
            p = os.path.join(d, "spacetrack.txt")
            if p not in out:
                out.append(p)
    return out


def credentials():
    """从 spacetrack.txt 读 identity/password；找不到返回 (None, None)。"""
    for p in _cred_files():
        if not os.path.isfile(p):
            continue
        identity = password = None
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip().lower(), v.strip()
                    if k in ("identity", "user", "username", "email"):
                        identity = v
                    elif k in ("password", "pass", "pwd"):
                        password = v
        except Exception as e:
            _lp("读取凭据失败 %s：%s" % (p, e))
            continue
        if identity and password:
            return identity, password
    return None, None


# ---------------------------------------------------------------- 出口探测
def _export_candidates():
    """直连优先，其次环境代理 / 常用本机端口（Space-Track 实测直连可通）。"""
    cands = [None]
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        v = os.environ.get(k)
        if v and v not in cands:
            cands.append(v)
    for p in (49834, 51558, 57186, 57674, 7897, 7890, 10809, 10808, 1080, 8888, 8080):
        v = "http://127.0.0.1:%d" % p
        if v not in cands:
            cands.append(v)
    # 上次成功的出口优先
    try:
        saved = json.load(open(_rp(_CACHE, "st_route.json"), encoding="utf-8"))
        px = saved.get("proxy")
        if px or px is None:
            if px in cands:
                cands.remove(px)
            cands.insert(0, px)
    except Exception:
        pass
    return cands


def _save_route(proxy):
    try:
        json.dump({"proxy": proxy, "t": time.time()},
                  open(_rp(_CACHE, "st_route.json"), "w", encoding="utf-8"))
    except Exception:
        pass


def last_route_desc():
    """给 meta.source/proxy 标注用：最近一次成功查询的出口说明。"""
    return _last_desc.get("v") or "直连"


_last_desc = {"v": None}


def _opener(proxy):
    cj = http.cookiejar.CookieJar()
    handlers = [urllib.request.HTTPCookieProcessor(cj)]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))   # 明确不走环境代理
    return urllib.request.build_opener(*handlers)


def _st_login(op, identity, password):
    body = urllib.parse.urlencode({"identity": identity, "password": password}).encode()
    req = urllib.request.Request(ST_LOGIN, data=body, method="POST", headers={
        "User-Agent": _UA, "Accept-Encoding": "identity",
        "Content-Type": "application/x-www-form-urlencoded"})
    with op.open(req, timeout=60) as r:
        txt = r.read().decode("utf-8", "replace")
    if "Failed" in txt:
        raise RuntimeError("Space-Track 登录失败（用户名或密码错误）")
    return True


def _st_get(op, url, timeout=180):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Encoding": "identity"})
    with op.open(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- 会话复用 + 全局限流
_ST_MIN_GAP = 2.1              # 秒。30/分钟 = 2.0 秒/次，取 2.1 留余量
_ST_MAX_PER_HOUR = 200        # 次。限额 300/小时；2026-10-26 从 280 降到 200（留 1/3 余量）
_ST_STATS = {"calls": 0, "throttled": 0, "logins": 0, "session_hits": 0}
_st_rl_lock = threading.Lock()
_st_rl = {"last": 0.0, "hour": []}


# ---------------------------------------------------------------- 跨进程共享限流（2026-10-26）
# 为什么有这一层：_ST_MAX_PER_HOUR / _ST_MIN_GAP 原来是**进程内**变量。
# 同时开两个程序（或同一程序开两份）就能叠加成 560 次/小时、57 次/分钟，
# 双双超过 Space-Track 的 300 次/小时、30 次/分钟 —— 会被封。
# 现在所有进程共用一份磁盘账本：谁要出站，先在同目录的锁里登记一个时间戳。
_SHARED_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "SpaceTrack")
_SHARED_RATE = "rate.json"
_SHARED_LOCK = "rate.lock"


def shared_dir():
    """跨程序共用的目录（限流账本、SATCAT 库、出站台账、日限标记都在这里）。"""
    try:
        os.makedirs(_SHARED_DIR, exist_ok=True)
    except Exception:
        pass
    return _SHARED_DIR


def shared_path(name):
    return os.path.join(shared_dir(), name)


class _SharedLock(object):
    """跨进程互斥：O_EXCL 建锁文件 + 30 秒陈旧清理（不依赖 msvcrt/fcntl）。"""

    def __init__(self, path, timeout=30.0):
        self.path = path
        self.timeout = timeout

    def __enter__(self):
        t0 = time.time()
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > 30:
                        os.remove(self.path)
                        continue
                except Exception:
                    pass
                if time.time() - t0 > self.timeout:
                    raise RuntimeError("等待 Space-Track 出站锁超时：%s" % self.path)
                time.sleep(0.05)

    def __exit__(self, *a):
        try:
            os.remove(self.path)
        except Exception:
            pass


def _shared_slot(min_gap, per_hour):
    """申请一个「整机唯一」的出站名额。返回 (还要等几秒, 是否拿到)。"""
    try:
        lp = shared_path(_SHARED_LOCK)
        rp = shared_path(_SHARED_RATE)
        now = time.time()
        with _SharedLock(lp):
            data = {}
            try:
                with open(rp, encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}
            # 只认「过去一小时内」的时间戳；顺手丢掉未来时间（系统时间回拨时不至于卡死）
            stamps = [t for t in (data.get("ts") or [])
                      if isinstance(t, (int, float)) and 0 <= now - t < 3600.0]
            if len(stamps) >= per_hour:
                return 3600.0 - (now - min(stamps)) + 0.5, False
            last = max(stamps) if stamps else 0.0
            if last and now - last < min_gap:
                return min_gap - (now - last), False
            stamps.append(now)
            data["ts"] = stamps[-400:]
            data["updated"] = now
            tmp = rp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, rp)
            return 0.0, True
    except Exception:
        # 目录不可写等极端情况：宁可放行也不要死等（本进程自己的限流仍在）
        return 0.0, True


def shared_satcat_store():
    """跨程序共用的 SATCAT 库文件（星座 ST 版与火箭末级从此只维护一份、每天只下一次）。"""
    return shared_path("satcat_all.json")


def _st_throttle():
    """出站前调用：保证距上次请求 ≥ _ST_MIN_GAP，且一小时窗口内不超过预算。"""
    while True:
        with _st_rl_lock:
            now = time.time()
            _st_rl["hour"] = [t for t in _st_rl["hour"] if now - t < 3600.0]
            wait = 0.0
            if len(_st_rl["hour"]) >= _ST_MAX_PER_HOUR:
                wait = 3600.0 - (now - _st_rl["hour"][0]) + 0.5
            else:
                gap = now - _st_rl["last"]
                if gap < _ST_MIN_GAP:
                    wait = _ST_MIN_GAP - gap
            if wait <= 0:
                _st_rl["last"] = now
                _st_rl["hour"].append(now)
                break
            _ST_STATS["throttled"] += 1
        time.sleep(min(wait, 20.0))
    # ★ 跨进程排队：2.1 秒间隔与 200 次/小时按「整机」算，多开程序也不会叠加
    while True:
        _w, _got = _shared_slot(_ST_MIN_GAP, _ST_MAX_PER_HOUR)
        if _got:
            _ST_STATS["calls"] += 1
            return
        _ST_STATS["throttled"] += 1
        time.sleep(min(max(_w, 0.2), 20.0))


_st_sess_lock = threading.Lock()
_st_sess = {}                  # proxy -> {"opener": op, "t": ts, "identity": identity}
_ST_SESS_TTL = 20 * 60         # 秒；Space-Track 会话实测可存活数小时，取 20 分钟保守值


def _st_open_session(proxy, identity, password):
    """取一个**已登录**的 opener：缓存命中直接用，否则登录一次再缓存。"""
    with _st_sess_lock:
        s = _st_sess.get(proxy)
        if s and s.get("identity") == identity and time.time() - s["t"] < _ST_SESS_TTL:
            _ST_STATS["session_hits"] += 1
            return s["opener"]
    op = _opener(proxy)
    _st_throttle()                      # 登录也占配额
    _st_login(op, identity, password)
    _ST_STATS["logins"] += 1
    with _st_sess_lock:
        _st_sess[proxy] = {"opener": op, "t": time.time(), "identity": identity}
    return op


def _st_drop_session(proxy):
    with _st_sess_lock:
        _st_sess.pop(proxy, None)


def rate_stats():
    """给自检/日志用：出站请求数、限流等待次数、登录次数、会话命中数。"""
    with _st_rl_lock:
        return dict(_ST_STATS, recent_calls=len(_st_rl["hour"]))


# ================================================================ 账号安全阀（2026-10-26）
# 背景：2026-10-26 账号被 Space-Track 暂停，理由「SATCAT 每天下载超过一次」
#       —— 我们曾对每一发火箭各查一次 class/satcat，一次全量刷新就 58 次。
# 三道闸全部装在**传输层**，上层逻辑绕不过去：
#   1) 急停：COV_ST_PAUSED=1，或在程序目录/缓存目录放 ST_PAUSED 空文件 →
#      所有出站请求立刻拒绝（不登录、不发网络）。恢复时删掉即可。
#   2) SATCAT 日限：非 /file/ 的 class/satcat 查询，每 UTC 日最多 1 次，超限抛错。
#      （政策：18 SDS 每天 17:00Z 后更新一次；日常增量应走 class/satcat/file/<号>。）
#   3) 台账：每次真正发出的请求追加一行「UTC 时间 + URL」到 <cache>/st_calls.log，
#      便于自查，也便于向 Space-Track 说明我们会在什么时间发什么 URL。
# 注意：日限在**发起前**记账（失败也算用过一次），宁可少查不可多查。
_ST_PAUSE_MARKERS = ("ST_PAUSED", "spacetrack.pause")
_ST_SATCAT_STATE = "st_satcat_day.json"
_ST_CALL_LOG = "st_calls.log"


def _st_policy_pause():
    v = (os.environ.get("COV_ST_PAUSED") or "").strip()
    if v and v.lower() not in ("0", "false", "no", "off"):
        raise RuntimeError("Space-Track 出站已暂停（COV_ST_PAUSED=%s）。账号恢复后再解除。" % v)
    for d in (_HERE, _CACHE):
        if not d:
            continue
        for n in _ST_PAUSE_MARKERS:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                raise RuntimeError("Space-Track 出站已暂停（存在 %s）。账号恢复后删掉该文件即可。" % p)


def _st_policy_satcat(url):
    """SATCAT 每 UTC 日最多一次（Space-Track 政策）。/file/<号> 增量不受限。"""
    low = (url or "").lower()
    if "class/satcat" not in low or "/file/" in low:
        return
    if not _CACHE:
        return
    today = time.strftime("%Y-%m-%d", time.gmtime())
    state = {}
    try:
        with open(shared_path(_ST_SATCAT_STATE), encoding="utf-8") as f:
            state = json.load(f) or {}
    except Exception:
        state = {}
    if state.get("day") == today and int(state.get("n") or 0) >= 1:
        raise RuntimeError(
            "SATCAT 今天（UTC %s）已经取过 1 次，按 Space-Track 政策不再重复查询；"
            "要更新请等 17:00Z 之后走 class/satcat/file/<文件号> 增量接口。" % today)
    state["day"] = today
    state["n"] = int(state.get("n") or 0) + 1
    try:
        os.makedirs(_CACHE, exist_ok=True)
        with open(shared_path(_ST_SATCAT_STATE), "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


def _st_policy_log(url):
    if not _CACHE:
        return
    try:
        os.makedirs(_CACHE, exist_ok=True)
        with open(shared_path(_ST_CALL_LOG), "a", encoding="utf-8") as f:
            f.write("%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), url))
    except Exception:
        pass
# ================================================================ 安全阀结束


def st_query(url, timeout=240, tries=3):
    """带「限流 + 会话复用 + 429/401 自动重试」的一次 Space-Track 查询，返回 (bytes, 出口说明)。"""
    _st_policy_pause()
    _st_policy_satcat(url)
    _st_policy_log(url)
    identity, password = credentials()
    if not identity:
        raise RuntimeError(
            "未找到 spacetrack.txt（exe 同目录，两行 identity=邮箱 / password=密码；"
            "没有它 Space-Track 模式无法联网取数）")
    last = None
    for proxy in _export_candidates():
        desc = proxy or "直连"
        for attempt in range(tries):
            try:
                op = _st_open_session(proxy, identity, password)
                _st_throttle()
                raw = _st_get(op, url, timeout=timeout)
                _save_route(proxy)
                _last_desc["v"] = desc
                return raw, desc
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 429:
                    # 被限流：丢掉会话、按 Retry-After 或 65 秒退避，再来一次
                    _st_drop_session(proxy)
                    try:
                        wait = float(e.headers.get("Retry-After") or 65)
                    except Exception:
                        wait = 65.0
                    wait = min(max(wait, 10.0), 180.0)
                    _lp("Space-Track 限流（429），退避 %.0f 秒后重试 %s" % (wait, desc))
                    time.sleep(wait)
                    continue
                if e.code in (401, 403, 500, 502, 503, 504):
                    _st_drop_session(proxy)      # 会话过期/服务端抖动 → 下一轮重登
                    _lp("Space-Track %s 返回 HTTP %d，重登后重试" % (desc, e.code))
                    continue
                break                            # 其它 4xx：重试没意义，换出口
            except Exception as e:
                last = e
                _st_drop_session(proxy)
                break
    raise RuntimeError("所有出口均失败：%s" % last)
