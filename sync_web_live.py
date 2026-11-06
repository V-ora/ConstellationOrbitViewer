# -*- coding: utf-8 -*-
"""把 web/raise/* 镜像到 web_live/raise/（exe 运行时的磁盘覆盖层）。

为什么需要这个脚本（2026-11-06 踩坑）：
  server.py 里 `/web/raise/` 的请求**优先读** exe 同目录的 `web_live\\raise\\`
  （为了让已打包的 exe 也能显示新生成的大图），没有才回落到包内 `web/`。
  我改了 `web/raise/batch.html`、`web/raise/sat.html`，但没同步到 `web_live/raise/`，
  结果页面被旧副本挡住 —— 用户看不到改动，还以为是没做。

以后凡是改 web/raise/ 下的页面，跑一次这个脚本（CI 里 build_pages 会自动调用）。
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "web", "raise")
DST = os.path.join(HERE, "web_live", "raise")


def main():
    if not os.path.isdir(SRC):
        print("!! 找不到 web/raise：%s" % SRC)
        return 1
    os.makedirs(DST, exist_ok=True)
    n = 0
    for fn in sorted(os.listdir(SRC)):
        if not fn.lower().endswith((".html", ".js", ".css")):
            continue
        s, d = os.path.join(SRC, fn), os.path.join(DST, fn)
        if os.path.isfile(d):
            try:
                if os.path.getsize(s) == os.path.getsize(d):
                    with open(s, "rb") as f1, open(d, "rb") as f2:
                        if f1.read() == f2.read():
                            continue          # 内容一致，跳过
            except Exception:
                pass
        shutil.copyfile(s, d)
        print("  同步 %s（%d B）" % (fn, os.path.getsize(d)))
        n += 1
    print("web_live/raise 同步完成：更新 %d 个文件" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
