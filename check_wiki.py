# -*- coding: utf-8 -*-
"""卫星百科连通性自检（与生产代码同一套方法：cookie 会话 + 热身）。

结论依据（2026-11-09 实测）：
  · 直接请求 api.php：时好时坏，密一点就 403（0 字节）
  · GET / 会下发 __cf_bm（哪怕回 403 挑战页）
  · 带着 cookie 再请求：稳定 200
所以自检必须复现"同一个 opener + cookie"的方式，否则测出来的不是真实路径。

退出码：两个词条都失败 → 1；至少一个成功 → 0。
结果写入 cache/wiki_status.json（提交进仓库，CI 上到底通不通有据可查）。
"""
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATUS = os.path.join(HERE, "cache", "wiki_status.json")
PAGES = [("qianfan", "千帆星座"), ("gw", "星网")]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
BASE = "https://sat.huijiwiki.com"


def new_opener():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                       urllib.request.HTTPCookieProcessor(cj)), cj


def warm(op):
    """GET 首页领 cookie（403 也照收）。"""
    try:
        op.open(urllib.request.Request(BASE + "/", headers={
            "User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}), timeout=15).read()
        return True
    except Exception:
        return False


def probe(op, page):
    u = (BASE + "/api.php?action=parse&page=%s&format=json&prop=text" % urllib.parse.quote(page))
    t0 = time.time()
    try:
        req = urllib.request.Request(u, headers={
            "User-Agent": UA, "Accept": "application/json,text/html;q=0.9",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": BASE + "/wiki/" + urllib.parse.quote(page)})
        with op.open(req, timeout=30) as r:
            raw = r.read()
        dt = round(time.time() - t0, 1)
        try:
            d = json.loads(raw.decode("utf-8", "replace"))
            html = (d.get("parse") or {}).get("text") or {}
            html = html.get("*") if isinstance(html, dict) else html
            return {"ok": bool(html), "http": r.status, "bytes": len(raw),
                    "html_len": len(html or ""), "secs": dt}
        except Exception:
            return {"ok": False, "http": r.status, "bytes": len(raw), "secs": dt,
                    "note": "非 JSON（反爬页）"}
    except urllib.error.HTTPError as e:
        return {"ok": False, "http": e.code, "secs": round(time.time() - t0, 1)}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__, "secs": round(time.time() - t0, 1)}


def main():
    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "method": "cookie-session(warm-up GET / → api.php)",
           "host": "sat.huijiwiki.com", "pages": {}}
    op, cj = new_opener()
    warm_ok = warm(op)
    print("卫星百科自检  %s" % out["ts"])
    print("  热身 GET /：%s   cookie=%s"
          % ("完成" if warm_ok else "失败（403 也算）", [c.name for c in cj] or "无"))
    any_ok = False
    for gk, page in PAGES:
        r = probe(op, page)
        print("  %-6s %-5s ok=%s http=%s %sB %ss %s"
              % (gk, page, r.get("ok"), r.get("http"), r.get("bytes", 0),
                 r.get("secs", 0), r.get("note", "")))
        out["pages"][gk] = dict(r, page=page)
        any_ok = any_ok or r.get("ok")
        time.sleep(6)          # 页面之间留间隔，别把反爬惹毛
    out["ok"] = any_ok
    out["cookies"] = [c.name for c in cj]
    os.makedirs(os.path.dirname(STATUS), exist_ok=True)
    tmp = STATUS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATUS)
    print("  写入 cache/wiki_status.json（ok=%s）" % any_ok)
    return 0 if any_ok else 1


if __name__ == "__main__":
    sys.exit(main())
