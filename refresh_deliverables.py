# -*- coding: utf-8 -*-
"""一键把轨道交付物更新到最新。

通常不需要手动跑 —— 查看器（exe 或 start.bat）启动 60 秒后、之后每 12 小时
会自己调用本脚本一次（见 server.py 的 maybe_start_deliverables_refresh）。
本文件也支持手动双击 refresh_deliverables.bat 立即更新一次。

做六件事（任一步失败不影响后面，最后统一汇报）：
  1. local_backfill.py   —— 用批量 GP 根数把逐日历史末点追平到今天
  2. make_qf_html.py     —— 重生成《千帆星座轨道爬升交互大图》（双写 web/raise + 交付物/01）
  3. make_gw_html.py     —— 重生成《国网低轨卫星轨道爬升交互大图》（同上）
  4. _sync_live.py       —— ★ 2026-10-15 补：把两张新大图同步到 web_live\raise\，
                            让**打包好的 exe**（web/ 打进包、打包即冻结）也能读到最新大图，
                            不用重打包（见 server.py 的 live_first()）
  5. _make_nopass.py     —— 把 web/ 拷成 web_nopass/（★ 2026-10-10 补：此前漏了这步，
                            于是自动更新只刷新 web/raise，变体 web_nopass/raise 里的大图会
                            静默停留在旧数据/旧代码，而 _audit_consistency 不查它，没人会发现）
  6. _shot.mjs ×2        —— 用 Edge 无头模式给两张图各截一张整页 PNG 进 交付物/01
                            ★ 2026-10-19：两张**并行**（各自独立 CDP 端口 9341/9342）

用法：
  python refresh_deliverables.py            # 全流程（优化后约 14 秒，原 28 秒）
  python refresh_deliverables.py --no-png   # 跳过截图（约 12 秒）
  python refresh_deliverables.py --dry      # 只打印将要做什么
日志写到同目录 refresh.log（每次追加）。
"""
import os
import subprocess
import sys
import datetime
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
# 截图 PNG 的去处：开发机上写「交付物\01_卫星轨道核查」（与两张 HTML 大图放一起）；
# ★ 2026-10-20：独立 clone / 别人机器上这个目录不存在，就写工程内的 _shots\，
#   别再往外造目录（原来无条件 makedirs，跨盘时还会直接抛异常）。
OUT01 = os.path.join(WS, "交付物", "01_卫星轨道核查")
if not os.path.isdir(OUT01):
    OUT01 = os.path.join(HERE, "_shots")
LOG = os.path.join(HERE, "refresh.log")

# exe 的「大图覆盖层」：server.py 的 live_first() 会让 /web/raise/ 优先读这里。
# 只在 raise/ 下有文件时才生效；exe 与它同目录（工程根）时就能吃到最新大图。
LIVE_DIR = os.path.join(HERE, "web_live")
LIVE_FILES = ("千帆星座轨道爬升交互大图.html", "国网低轨卫星轨道爬升交互大图.html")

PY_FALLBACKS = [
    r"C:\Users\t2007\.workbuddy\binaries\python\versions\3.13.12\python.exe",
    r"C:\Users\t2007\.workbuddy\binaries\python\envs\default\Scripts\python.exe",
]
NODE_FALLBACKS = [
    # ★ 2026-10-15 修：原来只写 22.22.2-3，本机实际目录是 22.22.2-6
    #   ⇒ 每次都在第 5 步报「node 缺失」静默跳过截图，交付物\01 的两张 PNG 一直停在 10-07。
    #   现在把「版本号目录」与「current 软链目录」都列上，换 node 版本也不会再断。
    r"C:\Users\t2007\.workbuddy\binaries\node\versions\22.22.2-6\node.exe",
    r"C:\Users\t2007\.workbuddy\binaries\node\versions\current\node.exe",
    r"C:\Users\t2007\.workbuddy\binaries\node\versions\22.22.2-3\node.exe",
]
EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]


def pick(exe_env, fallbacks, which):
    """按「环境变量 → 当前解释器 → 本机绝对路径 → PATH」的顺序找可执行文件。

    ★ 2026-10-20 补最后一级 PATH 查找：前两级都是**开发机专用**的写法，
      别人 clone 下来跑时绝对路径一条都不命中，node 那步会直接静默跳过截图。
    """
    if os.environ.get(exe_env) and os.path.exists(os.environ[exe_env]):
        return os.environ[exe_env]
    if os.path.exists(sys.executable):
        if which != "node":
            return sys.executable
    for p in fallbacks:
        if os.path.exists(p):
            return p
    p = shutil.which(which)          # PATH 里找（跨机器可用）
    if p:
        return p
    return None


def say(*a):
    msg = " ".join(str(x) for x in a)
    # pythonw.exe（无控制台）下 sys.stdout 是 None，print 会抛异常 —— 静默跳过
    try:
        if sys.stdout is not None:
            print(msg, flush=True)
    except Exception:
        pass
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def run(title, cmd, timeout=1800):
    say("── %s" % title)
    if callable(cmd):                      # 进程内小步骤（如 live 同步）
        t0 = datetime.datetime.now()
        try:
            ok = bool(cmd())
        except Exception as e:
            say("   !! 异常 %r" % (e,))
            return False
        say("   %s（%.1fs）" % ("ok" if ok else "!! 返回 False",
                               (datetime.datetime.now() - t0).total_seconds()))
        return ok
    say("   $ %s" % " ".join('"%s"' % c if " " in c else c for c in cmd))
    t0 = datetime.datetime.now()
    try:
        p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        say("   !! 超时（%ds）" % timeout)
        return False
    tail = (p.stdout or "").strip().splitlines()[-6:]
    for ln in tail:
        say("   | " + ln)
    if p.returncode != 0:
        say("   !! 退出码 %d" % p.returncode)
        for ln in (p.stderr or "").strip().splitlines()[-4:]:
            say("   ! " + ln)
        return False
    say("   ok（%.1fs）" % (datetime.datetime.now() - t0).total_seconds())
    return True


def sync_live():
    """把两张刚重生成的大图同步到 web_live\\raise\\（= exe 的大图覆盖层）。

    ★ 为什么要它（2026-10-15，用户报「星网大图怎么没更新」）：exe 把 web/ 打进包
      （_MEIPASS），**打包那一刻就冻结** —— 之后这里把 web/raise 刷多少次，包内那份都不会变，
      用户看到的爬升大图永远停在打包日。server.py 的 live_first() 让 /web/raise/ 优先读
      web_live\\raise\\，于是「打了包没重打包」也能显示最新大图。
    ★ 只覆盖 raise\\ 两个文件：web_nopass 与 web 只差一个 index.html、raise\\ 内容完全一致，
      所以这份覆盖对「含 / 不含过境预报」四个变体都安全，不会把变体的页面换掉。
    """
    dst = os.path.join(LIVE_DIR, "raise")
    os.makedirs(dst, exist_ok=True)
    n = 0
    for fn in LIVE_FILES:
        src = os.path.join(HERE, "web", "raise", fn)
        if not os.path.isfile(src):
            say("   !! 源文件缺失：%s" % src)
            continue
        shutil.copyfile(src, os.path.join(dst, fn))
        say("   %s -> web_live/raise/（%.2f MB）" % (fn, os.path.getsize(src) / 1048576.0))
        n += 1
    say("   已同步 %d/%d 个文件到 %s" % (n, len(LIVE_FILES), dst))
    return n == len(LIVE_FILES)


def shoot_all(node):
    """两张爬升大图**并行**截图（各用独立 CDP 端口）。

    ★ 2026-10-19 提速：原来串行两步，单张 5.8~5.9 s、合计 11.7 s。慢在两处 ——
      ① `_shot.mjs` 里固定 sleep(4000)+sleep(900)；② 两张图排队，Edge 各起一次。
      现在 `_shot.mjs` 改成条件等待（加载完 + canvas 像素稳定 + 布局稳定），单张 1.4 s；
      再加并行，两张合计约 1.5 s。产物经**逐字节 MD5 比对与原来完全一致**（无损）。
    ★ 端口必须错开：`_shot.mjs` 的 CDP 端口由环境变量 SHOT_DBG 给，默认 9341；
      若两个进程同端口，第二个会连到第一个的浏览器实例上，截图内容会串。
    """
    os.makedirs(OUT01, exist_ok=True)
    jobs = [
        ("千帆", os.path.join(HERE, "web", "raise", "千帆星座轨道爬升交互大图.html"),
         os.path.join(OUT01, "千帆星座轨道爬升大图.png"), "1850"),
        ("国网", os.path.join(HERE, "web", "raise", "国网低轨卫星轨道爬升交互大图.html"),
         os.path.join(OUT01, "国网低轨卫星轨道爬升大图.png"), "1950"),
    ]
    t0 = datetime.datetime.now()
    procs = []
    for i, (name, html_f, png_f, h) in enumerate(jobs):
        url = "file:///" + html_f.replace("\\", "/").replace(" ", "%20")
        env = dict(os.environ)
        env["SHOT_DBG"] = str(9341 + i)
        say("── 6/6 截图 %s（并行，CDP %d）" % (name, 9341 + i))
        procs.append((name, subprocess.Popen(
            [node, "_shot.mjs", url, png_f, h], cwd=HERE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=env)))
    ok = True
    for name, p in procs:
        try:
            out, err = p.communicate(timeout=300)
        except subprocess.TimeoutExpired:
            p.kill()
            say("   !! 截图 %s 超时（300s）" % name)
            ok = False
            continue
        for ln in (out or "").strip().splitlines()[-2:]:
            say("   | " + ln)
        if p.returncode != 0:
            say("   !! %s 退出码 %d" % (name, p.returncode))
            for ln in (err or "").strip().splitlines()[-3:]:
                say("   ! " + ln)
            ok = False
    say("   两张并行截图合计 %.1fs" % (datetime.datetime.now() - t0).total_seconds())
    return ok


def main():
    dry = "--dry" in sys.argv
    no_png = "--no-png" in sys.argv
    say("=" * 62)
    say("开始更新交付物%s" % ("（dry-run）" if dry else ""))

    if dry:
        say("将执行：local_backfill.py → make_qf_html.py → make_gw_html.py → 同步 web_live → _make_nopass.py" +
            ("" if no_png else " → _shot.mjs ×2"))
        return 0

    py = pick("REFRESH_PY", PY_FALLBACKS, "python")
    if not py:
        say("!! 找不到 python 解释器，终止")
        return 1

    okall = True
    okall &= run("1/6 追平逐日历史末点", [py, "local_backfill.py"])
    okall &= run("2/6 重生成千帆爬升大图", [py, "make_qf_html.py"])
    okall &= run("3/6 重生成国网爬升大图", [py, "make_gw_html.py"])
    okall &= run("4/6 同步到 web_live（让 exe 也吃到新大图）", sync_live)
    # ★ 2026-10-10 补：web_nopass/ 是 web/ 的整份拷贝（只换 index.html），
    #   前四步改了 web/raise/*.html 之后必须同步，否则「无过境预报」变体里的大图会停在旧版本。
    okall &= run("5/6 同步 web_nopass 变体", [py, "_make_nopass.py"])

    if no_png:
        say("6/6 已跳过截图（--no-png）")
    else:
        node = pick("REFRESH_NODE", NODE_FALLBACKS, "node")
        edge = next((p for p in EDGE_PATHS if os.path.exists(p)), None)
        if not node or not edge:
            say("6/6 跳过截图：%s 缺失" % ("node" if not node else "Edge/Chrome"))
        else:
            okall &= shoot_all(node)

    say("汇总：%s" % ("全部成功" if okall else "★ 有步骤失败，请看上面 !! 行"))
    return 0 if okall else 1


if __name__ == "__main__":
    sys.exit(main())
