# -*- coding: utf-8 -*-
"""安全专项自检：自己起一个临时实例，逐个复现「本机服务被别的网页利用」的几种手法。
只读代码、只打自己的临时实例，不动用户的查看器。
用法：python _audit_security.py
"""
import io, json, os, subprocess, sys, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("SEC_PORT", "8881"))
BASE = "http://127.0.0.1:%d" % PORT
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ok = bad = 0


def chk(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1
        print("  PASS", name, extra)
    else:
        bad += 1
        print("  FAIL", name, extra)


def req(path, method="GET", headers=None, timeout=20):
    """返回 (状态码, 文本)。连不上返回 (0, '')。"""
    r = urllib.request.Request(BASE + path, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read(400).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(400).decode("utf-8", "replace")
    except Exception:
        return 0, ""


def alive():
    return req("/api/ping", timeout=5)[0] == 200


print("启动临时实例 :%d …" % PORT)
srv = subprocess.Popen(
    [sys.executable, os.path.join(HERE, "server.py"), "--port", str(PORT), "--no-browser"],
    cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(60):
    if alive():
        break
    time.sleep(0.5)
if not alive():
    print("临时实例没起来，退出")
    srv.kill()
    sys.exit(2)
print("就绪。\n")

try:
    print("=" * 70)
    print("A. DNS 重绑定：篡改 Host 头应被拒")
    print("=" * 70)
    code, _ = req("/api/ping", headers={"Host": "attacker.example.com"})
    chk("A1 Host: attacker.example.com 被拒(403)", code == 403, "-> %s" % code)
    code, _ = req("/api/ping", headers={"Host": "127.0.0.1:%d" % PORT})
    chk("A2 正常 Host 放行(200)", code == 200, "-> %s" % code)
    code, _ = req("/api/ping", headers={"Host": "localhost:%d" % PORT})
    chk("A3 Host: localhost 放行(200)", code == 200, "-> %s" % code)
    code, _ = req("/api/tle?group=qianfan", headers={"Host": "evil.test"})
    chk("A4 业务接口也挡（Host: evil.test → 403）", code == 403, "-> %s" % code)

    print()
    print("=" * 70)
    print("B. CSRF：任意网页的 <img src=\"…/quit\"> 关不掉程序")
    print("=" * 70)
    code, _ = req("/quit", headers={"Sec-Fetch-Site": "cross-site",
                                    "Sec-Fetch-Dest": "image",
                                    "Referer": "http://evil.example.com/x.html"})
    chk("B1 跨站 GET /quit 被拒(405)", code == 405, "-> %s" % code)
    chk("B2 程序仍然活着", alive())

    print()
    print("=" * 70)
    print("C. CSRF：跨站 POST /quit 也关不掉")
    print("=" * 70)
    code, _ = req("/quit", method="POST",
                  headers={"Origin": "http://evil.example.com", "Content-Length": "0"})
    chk("C1 带外部 Origin 的 POST 被拒(403)", code == 403, "-> %s" % code)
    code, _ = req("/quit", method="POST",
                  headers={"Sec-Fetch-Site": "cross-site", "Content-Length": "0"})
    chk("C2 声明 cross-site 的 POST 被拒(403)", code == 403, "-> %s" % code)
    code, _ = req("/quit", method="POST",
                  headers={"Origin": "http://127.0.0.1:9999", "Content-Length": "0"})
    chk("C3 带本机 Origin 的 POST 放行(200)", code == 200, "-> %s" % code)
    time.sleep(1.5)
    chk("C4 合法退出后程序确实已停", not alive())

    print()
    print("=" * 70)
    print("D. 跨站触发强刷（force）应被拒")
    print("=" * 70)
    srv2 = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "server.py"), "--port", str(PORT + 1), "--no-browser"],
        cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    BASE = "http://127.0.0.1:%d" % (PORT + 1)
    for _ in range(60):
        if alive():
            break
        time.sleep(0.5)
    code, _ = req("/api/tle?group=qianfan&force=1",
                  headers={"Sec-Fetch-Site": "cross-site",
                           "Origin": "http://evil.example.com"})
    chk("D1 跨站 force=1 被拒(403)", code == 403, "-> %s" % code)
    code, _ = req("/api/tle?group=qianfan")
    chk("D2 不带 force 的正常读取仍 200", code == 200, "-> %s" % code)

    print()
    print("=" * 70)
    print("E. 基础安全响应头")
    print("=" * 70)
    r = urllib.request.Request(BASE + "/api/ping")
    with urllib.request.urlopen(r, timeout=10) as resp:
        h = {k.lower(): v for k, v in resp.headers.items()}
    for name, key in (("X-Content-Type-Options", "x-content-type-options"),
                      ("Referrer-Policy", "referrer-policy"),
                      ("X-Frame-Options", "x-frame-options")):
        chk("E %s 存在" % name, key in h, h.get(key, "(缺)"))
    chk("E 不返回 CORS 通配（不给跨站读数据的机会）",
        h.get("access-control-allow-origin") not in ("*", "null"),
        h.get("access-control-allow-origin", "(无)"))

    print()
    print("=" * 70)
    print("F. 目录穿越复核（路径边界）")
    print("=" * 70)
    import http.client
    for p in ("/web/../data/sat_hist.json", "/web/..%2fdata/sat_hist.json",
              "/web/%2e%2e/%2e%2e/data/sat_hist.json", "/data/../../server.py",
              # 同级目录：曾经 startswith(WEB_DIR) 的写法会让 web_evil/ 这类同级目录被当成
              # web/ 的子路径放行；现用 commonpath 判边界。web_nopass/（不含过境预报的
              # 那份前端，2026-10-07 加）与 web/ 同级，正好当场验证这条边界。
              "/web_nopass/index.html", "/web_nopass/../web/index.html"):
        c = http.client.HTTPConnection("127.0.0.1", PORT + 1, timeout=10)
        try:
            c.request("GET", p)
            rp = c.getresponse()
            code = rp.status
            rp.read()
        except Exception:
            code = 0
        finally:
            c.close()
        # 403 = 命中了静态前缀但边界校验拒绝；404 = 压根没匹配到静态前缀。
        # 两者都不给内容，都可接受；绝不能出现 200。
        chk("F %s 被拒(403/404)" % p, code in (403, 404), "-> %s" % code)

    srv2.kill()
finally:
    try:
        srv.kill()
    except Exception:
        pass

print()
print("== 安全自检小结：%d 通过 / %d 失败 ==" % (ok, bad))
sys.exit(1 if bad else 0)
