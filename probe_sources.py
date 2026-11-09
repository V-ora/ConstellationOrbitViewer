# -*- coding: utf-8 -*-
"""数据源可达性探测（在 CI runner 上跑，结果写 cache/sources_status.json）。

目的：找到"不依赖本机"的官方口径来源。
候选：
  1. sat.huijiwiki.com      卫星百科（本机偶尔能通，CI 恒 403）
  2. zh.wikipedia.org       中文维基（千帆星座条目，机房一般可访问）
  3. planet4589.org         Jonathan McDowell 的 GCAT 发射数据库（纯文本 TSV）
  4. r.jina.ai              第三方读取代理（服务端抓取，绕过反爬）
"""
import json
import os
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "cache", "sources_status.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

TARGETS = [
    ("huijiwiki", "https://sat.huijiwiki.com/api.php?action=query&meta=siteinfo&format=json",
     "application/json"),
    ("zh_wikipedia", "https://zh.wikipedia.org/w/api.php?action=query&list=search&srsearch=%E5%8D%83%E5%B8%86&format=json",
     "application/json"),
    ("gcat_launch", "https://planet4589.org/space/gcat/tsv/launch/launch.tsv", "text/plain"),
    ("jina_wiki", "https://r.jina.ai/https://sat.huijiwiki.com/wiki/%E5%8D%83%E5%B8%86%E6%98%9F%E5%BA%A7",
     "text/plain"),
]


def probe(url, accept, timeout=40):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": accept, "Accept-Language": "zh-CN,zh;q=0.9"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        return {"ok": True, "http": r.status, "bytes": len(raw),
                "secs": round(time.time() - t0, 1),
                "sample": raw[:120].decode("utf-8", "replace").replace("\n", " ")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "http": e.code, "secs": round(time.time() - t0, 1)}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__, "secs": round(time.time() - t0, 1)}


def main():
    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sources": {}}
    print("数据源可达性探测 %s" % out["ts"])
    for name, url, accept in TARGETS:
        r = probe(url, accept)
        out["sources"][name] = dict(r, url=url)
        print("  %-14s ok=%-5s http=%-4s %8sB %5ss %s"
              % (name, r.get("ok"), r.get("http", "-"), r.get("bytes", 0),
                 r.get("secs", 0), r.get("note", "") or r.get("error", "")))
        time.sleep(2)
    ok = [k for k, v in out["sources"].items() if v.get("ok")]
    out["reachable"] = ok
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print("  可达：%s" % (", ".join(ok) or "无"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
