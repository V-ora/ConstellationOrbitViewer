# -*- coding: utf-8 -*-
"""星座轨道实时查看器 —— 桌面版入口（不依赖浏览器，2026-10-01）

与 server.py（浏览器版）共用全部后端逻辑：本文件只做两件事——
  1. 在后台线程里把 HTTP 服务跑起来（复用 server.py 的 bind_server / load_launches /
     预取线程，跳过「开浏览器 + 复制剪贴板」那套）；
  2. 用 pywebview(WebView2) 开一个独立应用窗口加载页面。窗口关掉 = 程序退出；
     页面右下「退出程序」按钮（/quit）同样直接结束进程。

注意：webview.start() 必须在主线程；服务线程为 daemon，窗口关闭随之结束。
"""
import os
import sys
import threading

# 打包成无控制台 exe 后，PyInstaller 的 sys.stdout 是 NullWriter（不是 None），
# server.py 里的 None 判断接不住 → 日志全被丢弃。这里在 import server 之前
# 主动重定向到 run.log，与浏览器版日志同文件，方便排查问题。
if getattr(sys, "frozen", False):
    _log_dir = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.dirname(sys.executable),
                            "ConstellationOrbitViewer")
    try:
        os.makedirs(_log_dir, exist_ok=True)
        _f = open(os.path.join(_log_dir, "run.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = _f
        sys.stderr = _f
    except Exception:
        pass

import server   # 同目录，复用后端（模块级无副作用，main() 仅在直接运行时触发）

import webview


def start_backend(port=8770):
    """把 HTTP 服务跑起来（等价于 server.main() 去掉浏览器/剪贴板部分）。返回 url。"""
    # ★ 2026-10-20：默认绑 0.0.0.0，手机 / 平板连同一个 Wi-Fi 就能打开
    #   （地址由页面的「手机访问」卡片显示，也写进 run.log）。
    #   只开本机：启动前设环境变量 COV_LAN=0。
    lan = (os.environ.get("COV_LAN", "1") or "1").strip().lower() not in ("0", "false", "no", "off")
    host = "0.0.0.0" if lan else "127.0.0.1"
    server.init_allowed_hosts(host)      # 同步 Host 白名单，否则手机访问会被 403
    srv, real_port = server.bind_server(port, host)
    url = "http://127.0.0.1:%d/" % real_port
    server.load_launches()
    server.maybe_start_wiki_sync(delay=20.0)
    server.start_hist_backfill(delay=90.0)   # 并入本地补抓历史 + 每 3 小时自动补齐缺历史的新星
    server.maybe_start_deliverables_refresh(delay=60.0)   # 每 12 小时重生成两张爬升大图
    if real_port != port:
        server.log("提示：端口 %d 被占用，已改用 %d" % (port, real_port))
    if lan:
        _urls = server.lan_urls(real_port)
        server.log("局域网模式：%s" % (", ".join(_urls) if _urls else "未探测到本机 IP"))
    else:
        server.log("仅本机模式（COV_LAN=0）：手机无法访问")

    # 预取线程：与 server.main() 相同（出口判定 + 过期缓存刷新）
    def _prefetch_all():
        try:
            server.resolve_network()
        except Exception:
            pass
        for k in server.GROUPS:
            try:
                server.load_tle(k)
            except Exception as e:
                server.log("预取 %s 失败：%s" % (k, e))

    threading.Thread(target=_prefetch_all, daemon=True, name="prefetch").start()
    threading.Thread(target=srv.serve_forever, daemon=True, name="http").start()
    return url


def main():
    url = start_backend(8770)
    server.log("桌面版窗口启动：%s（关闭窗口即退出程序）" % url)
    webview.create_window(
        "星座轨道实时查看器",
        url,
        width=1280, height=840,
        min_size=(1000, 620),
        background_color="#0b1020",
    )
    webview.start()   # 阻塞到窗口关闭
    server.log("窗口已关闭，程序退出。")


if __name__ == "__main__":
    main()
