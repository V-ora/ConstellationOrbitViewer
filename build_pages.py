# -*- coding: utf-8 -*-
"""把工程打成「能直接放 GitHub Pages」的纯静态站，输出到 docs\\。

为什么需要这一步
----------------
本项目是「网页 + 本地 Python 服务」两件套。GitHub Pages 只托管文件、不跑 Python，
所以页面里 `/api/groups`、`/api/tle?group=…` 这些请求必然 404，打开就是加载圈转不停。
本脚本做四件事：

  1. **烘快照**：起一个临时服务实例，把 /api/groups、/api/tle?group=…、以及
     逐日历史（供大图里的「组次 / 单星」子页用）全部落成 JSON；
  2. **改路径**：页面里写的是绝对路径 `/web/…`，放在 `user.github.io/<仓库名>/`
     这种子路径下会解析到域名根 → 404。统一改成相对路径；
  3. **装适配层**：pages_shim.js 接管 fetch，把 /api/* 映射到上面的 JSON；
  4. **比对**：把 shim 里复写的「组次筛选」逻辑与 server.py 的真接口**逐组比一遍**，
     不一致直接报错 —— 两份实现最怕悄悄漂移（见 制作记录/02 的踩坑清单）。

用法
----
    python build_pages.py             # 生成 docs/
    python build_pages.py --keep      # 保留 docs/ 里已有文件（默认先清空）

发布（GitHub 网页上点几下，不需要命令行）
------------------------------------------
    Settings → Pages → Source: Deploy from a branch
                      → Branch: main  /  Folder: /docs  → Save
    等 1~2 分钟：https://<你的用户名>.github.io/<仓库名>/
"""
import argparse
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs")

# 本地请求绝不能被本机代理劫持（会 502），所以显式禁掉 proxy
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def log(*a):
    print(*a, flush=True)


def free_port():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def http(url, timeout=120):
    with OPENER.open(url, timeout=timeout) as r:
        return r.read()


def http_json(url, timeout=120):
    return json.loads(http(url, timeout).decode("utf-8"))


def start_server(port):
    env = dict(os.environ)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(k, None)
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
    proc = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(port), "--no-browser", "--no-lan"],
        cwd=HERE, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    for _ in range(180):
        if proc.poll() is not None:
            raise SystemExit("服务进程启动即退出（端口被占？）")
        try:
            http(base + "/api/ping", 3)
            return proc, base
        except Exception:
            time.sleep(1)
    proc.kill()
    raise SystemExit("等了 180 秒服务仍未就绪")


def load_metamap(group):
    """批次归属只存在磁盘 metamap 里（sat_hist.json 没有 batch 字段）。"""
    p = os.path.join(HERE, "cache", group + "_metamap.json")
    try:
        d = json.load(io.open(p, encoding="utf-8"))
        m = d.get("map")
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def shim_batch_hist(hist, grp, bat):
    """**与 pages_shim.js 的 batchHist() 逐行对应**的 Python 版，只用于构建期比对。"""
    bm = hist.get("batch_map") or {}
    sats = hist.get("sats") or {}
    out, label = [], ""
    for norad, m in bm.items():
        if (m.get("batch") or "") != bat:
            continue
        h = sats.get(str(norad))
        if not h:
            continue
        if not label:
            label = m.get("group") or m.get("mission") or ("第 %s 批" % m.get("batch_index", "?"))
        out.append({
            "norad": str(norad),
            "name": h.get("name") or m.get("obj_name") or ("NORAD " + str(norad)),
            "launch": h.get("launch") or m.get("launch_date") or "",
            "target": h.get("target"),
            "src": h.get("src") or grp,
            "pts": h.get("pts") or [],
        })
    out.sort(key=lambda x: (x.get("launch") or "", x["name"]))
    return {"group": grp, "batch": bat, "label": label or bat,
            "base": hist.get("base"), "now": hist.get("now"),
            "count": len(out), "sats": out}


def cmp_json(o):
    """比对用的规范化序列化：**剔除 now / base**。
    ★ 2026-10-25：这两个是「历史库的时间戳」，服务端运行期会自己追平（可能比磁盘文件新几秒），
    与「组次筛选逻辑对不对」无关。原来连它们一起严格比，结果 46 个批次全被判"不一致"，
    构建直接失败 —— 真逻辑不一致才该失败，时间戳差几秒不该。"""
    d = dict(o or {})
    d.pop("now", None)
    d.pop("base", None)
    return json.dumps(d, sort_keys=True, ensure_ascii=False)


def wait_fresh(base, g, d, timeout=180):
    """★ 2026-10-25：服务端是 stale-while-revalidate —— 第一次请求只回旧缓存，后台才开始刷新。
    原样烘下去 = 把「正在刷新」这一帧永久冻进静态站：线上页面顶部常驻
    「正在后台刷新数据…」、状态卡永远「缓存兜底」，用户以为数据没更新（其实历元已经是最新）。
    所以烘快照之前必须等这批数据真的落地（refreshing=false 且 stale=false），最多等 timeout 秒；
    等不到就按缓存烘，但要把 error 文案换成静态站说得通的说法。"""
    t0 = time.time()
    while (d.get("refreshing") or d.get("stale")) and time.time() - t0 < timeout:
        time.sleep(1.5)
        d = http_json(base + "/api/tle?group=" + urllib.parse.quote(g))
    dt = time.time() - t0
    if d.get("refreshing") or d.get("stale"):
        log("  \u26a0 %s \u7b49\u4e86 %.0fs \u4ecd\u672a\u5237\u65b0\u5b8c\u6210\uff0c\u53ea\u80fd\u6309\u7f13\u5b58\u70d8" % (g, dt))
    elif dt >= 1.5:
        log("  \u00b7 %s \u7b49\u5230\u540e\u53f0\u5237\u65b0\u5b8c\u6210\uff0c\u7528\u65f6 %.1fs" % (g, dt))
    return d


def main():
    ap = argparse.ArgumentParser(description="生成 GitHub Pages 静态站（docs/）")
    ap.add_argument("--keep", action="store_true", help="保留 docs/ 已有文件")
    a = ap.parse_args()

    root_index = os.path.join(HERE, "web", "index.html")
    if not os.path.isfile(root_index):
        raise SystemExit("找不到 web/index.html")

    port = free_port()
    log("起临时服务：127.0.0.1:%d（烘快照用，跑完自动关）" % port)
    proc, base = start_server(port)
    try:
        # ------------------------------------------------ 1. 烘接口快照
        groups = http_json(base + "/api/groups")
        keys = list(groups.keys())
        log("分组：%s" % ", ".join(keys))

        tle, counts = {}, {}
        for g in keys:
            d = http_json(base + "/api/tle?group=" + urllib.parse.quote(g))
            d = wait_fresh(base, g, d)      # ★ 等后台刷新落地，别把「刷新中」烘进去
            # 静态站没有后台：把 SWR 的瞬时状态洗掉，否则页面会永远说自己正在刷新
            d["refreshing"] = False
            if d.get("stale"):
                d["error"] = "构建时未取到最新根数，本页为缓存快照（每 12 小时自动重烘）"
            tle[g] = d
            counts[g] = len(d.get("sats") or d.get("objects") or [])
            log("  /api/tle?group=%-8s  %d 颗  %.0f KB  历元 %s  数据龄 %.2f 天  stale=%s"
                % (g, counts[g], len(json.dumps(d, ensure_ascii=False)) / 1024.0,
                   d.get("epoch_max") or "-", d.get("epoch_age_days") or -1, d.get("stale")))

        # 逐日历史：一次装齐，供 iframe 子页现场切片
        hist_path = os.path.join(HERE, "data", "sat_hist.json")
        raw = json.load(io.open(hist_path, encoding="utf-8"))
        sats = {}
        for norad in (raw.get("sats") or {}):
            try:
                one = http_json(base + "/api/sat_hist?norad=" + urllib.parse.quote(str(norad)))
            except Exception:
                continue
            one.pop("norad", None)
            one.pop("base", None)
            one.pop("now", None)
            sats[str(norad)] = one
        batch_map = {}
        for g in keys:
            for norad, m in load_metamap(g).items():
                if not isinstance(m, dict):
                    continue
                batch_map[str(norad)] = {
                    k: m.get(k) for k in
                    ("batch", "group", "mission", "batch_index", "obj_name", "launch_date")
                    if m.get(k) is not None
                }
        hist = {"base": raw.get("base"), "now": raw.get("now"),
                "sats": sats, "batch_map": batch_map}
        log("  逐日历史：%d 颗 / 批次归属 %d 条" % (len(sats), len(batch_map)))

        # ------------------------------------------------ 2. 比对组次逻辑（关键）
        bats = sorted({m.get("batch") for m in batch_map.values() if m.get("batch")})
        log("比对组次筛选逻辑：%d 个组次组合" % (len(keys) * len(bats)))
        bad = 0
        for g in keys:
            for b in bats:
                try:
                    real = http_json("%s/api/batch_hist?group=%s&batch=%s"
                                     % (base, urllib.parse.quote(g), urllib.parse.quote(b)))
                except Exception:
                    continue                       # 该组没有这个批次，服务端 404，合法
                mine = shim_batch_hist(hist, g, b)
                if cmp_json(real) != cmp_json(mine):
                    bad += 1
                    log("  !! 不一致：group=%s batch=%s（真接口 %d 颗 / 快照 %d 颗）"
                        % (g, b, len(real.get("sats") or []), len(mine.get("sats") or [])))
                # ★ 2026-10-25：服务端的 hist 库在运行期会自己追平（`now` 比磁盘文件新几秒），
                #   快照用服务端那份，页面时间轴才不会比数据短一截。
                if real.get("now") and real["now"] != hist.get("now"):
                    hist["now"] = real["now"]
        if bad:
            raise SystemExit("组次逻辑与真接口有 %d 处不一致，先修 pages_shim.js 再发布" % bad)
        log("  ✓ 全部一致")

        # ------------------------------------------------ 3. 落盘
        if os.path.isdir(OUT) and not a.keep:
            shutil.rmtree(OUT)
        os.makedirs(os.path.join(OUT, "api"), exist_ok=True)

        def dump(name, obj, indent=None):
            p = os.path.join(OUT, "api", name)
            with io.open(p, "w", encoding="utf-8", newline="\n") as f:
                json.dump(obj, f, ensure_ascii=False,
                          separators=(",", ":") if indent is None else None, indent=indent)
            return p

        dump("groups.json", groups, 1)
        for g in keys:
            dump("tle-%s.json" % g, tle[g])
        dump("hist.json", hist)

        # 页面与资源
        shutil.copytree(os.path.join(HERE, "web"), os.path.join(OUT, "web"),
                        ignore=shutil.ignore_patterns("index.html"))
        shutil.copyfile(os.path.join(HERE, "pages_shim.js"), os.path.join(OUT, "pages_shim.js"))
        # 分享卡片预览图：用 README 里的 3D 地球截图，存成 ASCII 名的 og.png
        og_src = os.path.join(HERE, "预览_3D地球.png")
        if os.path.isfile(og_src):
            shutil.copyfile(og_src, os.path.join(OUT, "og.png"))

        # 4. 首页：绝对路径 → 相对路径，并注入静态元信息 + 适配层
        html = io.open(root_index, encoding="utf-8").read()
        n_web = html.count('"/web/')
        if n_web == 0:
            raise SystemExit("web/index.html 里没有找到 \"/web/ 形式的引用，路径规则变了？")
        html = html.replace('"/web/', '"web/')
        meta = {"date": time.strftime("%Y-%m-%d"),
                "counts": counts, "total": sum(counts.values())}
        # 图标：见 assets/logo.svg（这是源文件，可编辑）。这里内联成 data URI，
        # 好处是不额外发一次请求，也不会在子路径部署时因相对路径踩坑。：不加文件，也不让浏览器去请求「域名根」的 /favicon.ico
        # （Pages 是子路径部署，根路径那个 404 会一直在控制台里报，看着像出错）
        fav = ("<link rel=\"icon\" href=\"data:image/svg+xml,"
               "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
               "%3Crect width='32' height='32' rx='7' fill='%232f6fed'/%3E"
               "%3Cellipse cx='16' cy='16' rx='11' ry='5.2' fill='none' stroke='%23fff'"
               " stroke-width='2' transform='rotate(-30 16 16)'/%3E"
               "%3Ccircle cx='16' cy='16' r='4.2' fill='%23fff'/%3E%3C/svg%3E\">\n")
        # ★ 2026-10-25：分享卡片。静态站是纯前端，爬虫不会执行 JS，
        #   所以标题/描述/预览图必须写死在 HTML 里，否则发到群里只有一条裸链接。
        site = "https://v-ora.github.io/ConstellationOrbitViewer/"
        desc = ("千帆星座（QIANFAN）与中国星网（HULIANWANG DIGUI）的实时轨道查看器："
                "3D 地球 / 2D 地面轨迹 / 轨道爬升大图 / 过境预报，当前 %d 颗"
                "（千帆 %d · 星网低轨 %d · 中国星网高轨 %d）。免安装，浏览器直接看，手机也能开。"
                % (sum(counts.values()), counts.get("qianfan", 0),
                   counts.get("gw", 0), counts.get("gaogui", 0)))
        social = ('<meta name="description" content="%s">\n'
                  '<meta property="og:type" content="website">\n'
                  '<meta property="og:site_name" content="星座轨道实时查看器">\n'
                  '<meta property="og:title" content="星座轨道实时查看器 · 千帆 / 中国星网">\n'
                  '<meta property="og:description" content="%s">\n'
                  '<meta property="og:url" content="%s">\n'
                  '<meta property="og:image" content="%sog.png">\n'
                  '<meta property="og:image:width" content="1570">\n'
                  '<meta property="og:image:height" content="838">\n'
                  '<meta property="og:image:alt" content="星座轨道实时查看器 · 3D 地球视图">\n'
                  '<meta name="twitter:card" content="summary_large_image">\n'
                  % (desc, desc, site, site))
        inject = (fav + social +
                  '<script>window.__COV_STATIC__=%s;</script>\n'
                  '<script src="pages_shim.js"></script>\n'
                  % json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
        anchor = '<meta charset="utf-8">'
        if anchor not in html:
            raise SystemExit("web/index.html 没找到 <meta charset>，注入锚点失效")
        html = html.replace(anchor, anchor + "\n" + inject, 1)
        with io.open(os.path.join(OUT, "index.html"), "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
        log("首页：%d 处 \"/web/ 已改为相对路径" % n_web)

        # 5. 大图里的两个子页也要装适配层（父页的 shim 管不到 iframe）
        for name in ("batch.html", "sat.html"):
            p = os.path.join(OUT, "web", "raise", name)
            if not os.path.isfile(p):
                continue
            t = io.open(p, encoding="utf-8").read()
            if "pages_shim.js" in t:
                continue
            tag = '<script src="../../pages_shim.js"></script>\n'
            if '<meta charset="utf-8">' in t:
                t = t.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n' + tag, 1)
            else:
                t = tag + t
            with io.open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(t)
            log("已注入适配层：web/raise/%s" % name)

        # 6. 关掉 Jekyll（省掉它对下划线/特殊字符文件的过滤）
        with io.open(os.path.join(OUT, ".nojekyll"), "w") as f:
            f.write("")

        # ------------------------------------------------ 小结
        total = 0
        for r, _d, fs in os.walk(OUT):
            for fn in fs:
                total += os.path.getsize(os.path.join(r, fn))
        log("")
        log("=" * 62)
        log("已生成：%s" % OUT)
        log("体积：%.1f MB　文件：%d 个" % (total / 1048576.0,
                                       sum(len(fs) for _r, _d, fs in os.walk(OUT))))
        log("快照：%s　共 %d 颗（%s）" % (meta["date"], meta["total"],
                                      " / ".join("%s %d" % (k, counts[k]) for k in keys)))
        log("")
        log("发布：仓库 Settings → Pages → Deploy from a branch")
        log("      → Branch: main / Folder: /docs → Save")
        log("      1~2 分钟后访问 https://<用户名>.github.io/<仓库名>/")
        log("=" * 62)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


if __name__ == "__main__":
    main()
