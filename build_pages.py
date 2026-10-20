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
            tle[g] = d
            counts[g] = len(d.get("sats") or d.get("objects") or [])
            log("  /api/tle?group=%-8s  %d 颗  %.0f KB"
                % (g, counts[g], len(json.dumps(d, ensure_ascii=False)) / 1024.0))

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
                if json.dumps(real, sort_keys=True, ensure_ascii=False) != \
                   json.dumps(mine, sort_keys=True, ensure_ascii=False):
                    bad += 1
                    log("  !! 不一致：group=%s batch=%s（真接口 %d 颗 / 快照 %d 颗）"
                        % (g, b, len(real.get("sats") or []), len(mine.get("sats") or [])))
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

        # 4. 首页：绝对路径 → 相对路径，并注入静态元信息 + 适配层
        html = io.open(root_index, encoding="utf-8").read()
        n_web = html.count('"/web/')
        if n_web == 0:
            raise SystemExit("web/index.html 里没有找到 \"/web/ 形式的引用，路径规则变了？")
        html = html.replace('"/web/', '"web/')
        meta = {"date": time.strftime("%Y-%m-%d"),
                "counts": counts, "total": sum(counts.values())}
        # 图标内联成 SVG data URI：不加文件，也不让浏览器去请求「域名根」的 /favicon.ico
        # （Pages 是子路径部署，根路径那个 404 会一直在控制台里报，看着像出错）
        fav = ("<link rel=\"icon\" href=\"data:image/svg+xml,"
               "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
               "%3Crect width='32' height='32' rx='7' fill='%232f6fed'/%3E"
               "%3Cellipse cx='16' cy='16' rx='11' ry='5.2' fill='none' stroke='%23fff'"
               " stroke-width='2' transform='rotate(-30 16 16)'/%3E"
               "%3Ccircle cx='16' cy='16' r='4.2' fill='%23fff'/%3E%3C/svg%3E\">\n")
        inject = (fav +
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
