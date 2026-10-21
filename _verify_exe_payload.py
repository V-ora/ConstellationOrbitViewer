# -*- coding: utf-8 -*-
"""拆包核对：把每个发布 exe 里**真正嵌进去**的 web/index.html 与 sat_hist.json 抽出来，
和磁盘上的源文件逐字节比对，并断言变体归属正确。

必须用带 PyInstaller 的解释器跑（默认 python 没装 PyInstaller）：
    C:/Users/t2007/.workbuddy/binaries/python/envs/default/Scripts/python.exe _verify_exe_payload.py

为什么要这一步：spec 用环境变量切 web 源目录，打错一次就会把「不带过境预报」的页面
装进正式版（或反过来）—— 文件级 MD5 一样能对上，因为三处副本确实一致。
只有拆包看内容才能证明**内容对不对**。
"""
import hashlib
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))

try:
    from PyInstaller.archive.readers import CArchiveReader
except Exception as e:                                     # pragma: no cover
    print("需要 PyInstaller（请用 venv 的 python 运行）：%s" % e)
    sys.exit(2)


def md5b(b):
    return hashlib.md5(b).hexdigest()


def norm(b):
    """文本资源比较前把 CRLF 归一成 LF。

    仓库里 .gitattributes 写的是 `* text=auto eol=lf`，但 disk 上的文件可能是
    CRLF（比如更早生成的 web_nopass/）—— 内容一样、字节不同。这里统一口径，
    避免把「换行符风格」误报成「内容不一致」。
    """
    return b.replace(b"\r\n", b"\n") if isinstance(b, (bytes, bytearray)) else b


def member(cr, name):
    """归档里的名字用反斜杠（web\\index.html）。"""
    key = name.replace("/", "\\")
    if key not in cr.toc:
        return None
    data = cr.extract(key)
    if data is None:
        dpos, dlen, _ulen, flag, _typ = cr.toc[key][:5]
        with open(cr._archive_file.name if hasattr(cr, "_archive_file") else None, "rb") as f:
            pass
    return data


CASES = [
    # exe 名, 期望含过境预报, 期望对应的磁盘页面
    ("星座轨道实时查看器.exe", True, os.path.join(HERE, "web", "index.html")),
    ("星座轨道实时查看器_桌面版.exe", True, os.path.join(HERE, "web", "index.html")),
    # SpaceTrack 版与 CelesTrak 版**共用同一份 web/**，差异只在 runtime hook 注入的数据源，
    # 所以页面/历史库必须与 web/ 完全一致（2026-10-15 补：原先漏核这两个）
    ("星座轨道实时查看器_SpaceTrack版.exe", True, os.path.join(HERE, "web", "index.html")),
    ("星座轨道实时查看器_SpaceTrack版_桌面版.exe", True, os.path.join(HERE, "web", "index.html")),
    ("星座轨道实时查看器_无过境预报.exe", False, os.path.join(HERE, "_features", "index_original.html")),
    ("星座轨道实时查看器_无过境预报_桌面版.exe", False, os.path.join(HERE, "_features", "index_original.html")),
]

# SpaceTrack 版归档里的 runtime hook 条目名（有=注入 spacetrack，无=CelesTrak）
ST_HOOK = "_st_data_src_hook"

# 两张爬升大图（包内必须是最新那份；磁盘覆盖层 `web_live\raise\` 也只覆盖这个目录）
BIG_HTMLS = ("千帆星座轨道爬升交互大图.html", "国网低轨卫星轨道爬升交互大图.html")

ok = bad = 0
for exe_name, want_pass, ref in CASES:
    exe = os.path.join(HERE, "dist", exe_name)
    print("=" * 70)
    print(exe_name)
    print("=" * 70)
    if not os.path.exists(exe):
        print("  FAIL 不存在：%s" % exe)
        bad += 1
        continue
    cr = CArchiveReader(exe)
    html = cr.extract("web\\index.html")
    if html is None:
        print("  FAIL 归档里没有 web/index.html")
        bad += 1
        continue
    txt = html.decode("utf-8", "replace")
    has = "passView" in txt
    ref_b = open(ref, "rb").read()
    same = norm(html) == norm(ref_b)
    print("  内嵌页面 %d B  md5=%s  含 passView=%s" % (len(html), md5b(html)[:16], has))
    print("  与磁盘 %s 一致=%s" % (os.path.basename(ref), same))
    if has == want_pass:
        print("  PASS 变体归属正确（%s过境预报）" % ("含" if want_pass else "不含"))
        ok += 1
    else:
        print("  FAIL 变体归属错误：期望 %s过境预报，实际 %s" % ("含" if want_pass else "不含",
                                                              "含" if has else "不含"))
        bad += 1
    if same:
        ok += 1
    else:
        print("  FAIL 内嵌页面与磁盘源文件不一致")
        bad += 1

    # ★ 2026-10-20：手机端适配的两个共享资源必须真的打进包。
    #   页面里靠 <link href="/web/mobile.css"> 与 <script src="/web/mobile.js"> 引用，
    #   包里缺一个 ⇒ 手机打开是「没有响应式样式」的桌面版，页脚没有 ☰/ℹ，侧栏挤成一团，
    #   而且**不报任何错**（404 只在浏览器控制台里，用户看不见）。
    for _f in ("mobile.css", "mobile.js"):
        _in = cr.extract("web\\" + _f)
        _disk = open(os.path.join(HERE, "web", _f), "rb").read()
        if _in is not None and norm(_in) == norm(_disk):
            print("  内嵌 %-11s %d B  与磁盘一致=True" % (_f, len(_in)))
            ok += 1
        else:
            print("  FAIL 内嵌 %s %s" % (_f, "缺失" if _in is None else "与磁盘不一致（%d B vs %d B）"
                                          % (len(_in), len(_disk))))
            bad += 1

    hist = cr.extract("data\\sat_hist.json")
    disk = open(os.path.join(HERE, "data", "sat_hist.json"), "rb").read()
    same_h = hist is not None and hashlib.md5(hist).hexdigest() == hashlib.md5(disk).hexdigest()
    if same_h:
        print("  内嵌历史库 %s B  与磁盘一致=True" % (len(hist) if hist else 0))
        ok += 1
    else:
        # ★ 2026-10-19：包内历史库落后**不再直接判错** —— exe 启动时用
        #   %LOCALAPPDATA% 的 sat_hist_extra.json 追平（local_backfill 每轮重建最近 30 天），
        #   服务端 _hist_load_extras() 会把它并进包内基线。
        #   但**必须真的存在那份 extras**，否则 exe 里就真停在打包日了。
        cands = [os.path.join(os.environ.get("LOCALAPPDATA") or "",
                              "ConstellationOrbitViewer", c, "sat_hist_extra.json")
                 for c in ("cache", "cache_st")]
        ex = next((p for p in cands if os.path.isfile(p) and os.path.getsize(p) > 200), None)
        print("  内嵌历史库 %s B  与磁盘一致=False（包内是打包日基线）" % (len(hist) if hist else 0))
        print("    exe 侧追平文件：%s" % (("%s（%d B）" % (ex, os.path.getsize(ex))) if ex else "★ 不存在"))
        if ex:
            ok += 1
        else:
            print("  FAIL 包内历史库落后、且 exe 侧没有 extras ⇒ exe 里会停在打包日")
            bad += 1

    # SpaceTrack 版的「唯一行为差异」= runtime hook 注入了 COV_DATA_SRC=spacetrack。
    # 归档里的 _st_data_src_hook 就是硬证据：ST 版必须有且含 spacetrack，其它变体必须没有。
    hk = cr.extract("_st_data_src_hook") if "_st_data_src_hook" in cr.toc else b""
    hk = hk or b""
    has_st = b"spacetrack" in hk
    want_st = "SpaceTrack版" in exe_name
    print("  数据源钩子 %s（含 spacetrack=%s）" % ("有" if hk else "无", has_st))
    if want_st == has_st:
        print("  PASS 数据源归属正确（%s）" % ("Space-Track" if want_st else "CelesTrak"))
        ok += 1
    else:
        print("  FAIL 数据源归属错误：期望 %s，实际 %s"
              % ("Space-Track" if want_st else "CelesTrak",
                 "Space-Track" if has_st else "CelesTrak"))
        bad += 1

    # ★ 包内两张爬升大图。
    #   ★ 2026-10-19 语义修正：大图现在有**磁盘覆盖层** `web_live\raise\`（server.py 优先读它），
    #     所以「包内 != 磁盘」**不再算错** —— 只要覆盖层那份是最新的，exe 显示的就是最新的。
    #     判定改成：① 包内大图必须存在且非空；② **没有覆盖层时**才要求与磁盘逐字节一致。
    has_live = all(os.path.isfile(os.path.join(HERE, "web_live", "raise", b)) for b in BIG_HTMLS)
    for big in BIG_HTMLS:
        data = cr.extract(("web/raise/" + big).replace("/", "\\"))
        ref = open(os.path.join(HERE, "web", "raise", big), "rb").read()
        same_big = bool(data) and norm(data) == norm(ref)
        if has_live:
            print("  内嵌大图 %-28s %s B  存在=%s  与磁盘同版=%s（有覆盖层，包内版本允许旧）"
                  % (big, len(data) if data else 0, bool(data), same_big))
            good = bool(data)
        else:
            print("  内嵌大图 %-28s %s B  与磁盘一致=%s（无覆盖层，必须一致）"
                  % (big, len(data) if data else 0, same_big))
            good = same_big
        if good:
            ok += 1
        else:
            print("  FAIL 内嵌大图缺失%s：%s" % ("" if has_live else "或与磁盘不一致", big))
            bad += 1

print()
print("=" * 70)
print("覆盖层安全性：web\\raise\\ 与 web_nopass\\raise\\ 必须逐字节一致")
print("=" * 70)
for big in BIG_HTMLS:
    a = open(os.path.join(HERE, "web", "raise", big), "rb").read()
    p2 = os.path.join(HERE, "web_nopass", "raise", big)
    b = open(p2, "rb").read() if os.path.exists(p2) else b""
    same = bool(b) and md5b(a) == md5b(b)
    print("  %-28s %s" % (big, "一致" if same else "不一致 / 缺失"))
    if same:
        ok += 1
    else:
        print("  FAIL web_nopass\\raise\\ 缺失或与 web\\raise\\ 不一致（覆盖层会换错变体页）")
        bad += 1

print()
print("=" * 70)
print("磁盘覆盖层：web_live\\raise\\ 必须与 web\\raise\\ 逐字节一致（否则线上大图会退回旧版）")
print("=" * 70)
for big in BIG_HTMLS:
    a = open(os.path.join(HERE, "web", "raise", big), "rb").read()
    p3 = os.path.join(HERE, "web_live", "raise", big)
    c = open(p3, "rb").read() if os.path.exists(p3) else b""
    same = bool(c) and md5b(a) == md5b(c)
    print("  %-28s %s" % (big, "一致" if same else "不一致 / 缺失（跑 refresh_deliverables.py 补）"))
    if same:
        ok += 1
    else:
        print("  FAIL web_live\\raise\\ 缺失或落后于 web\\raise\\")
        bad += 1

print()
print("== 拆包核对小结: %d 通过 / %d 失败 ==" % (ok, bad))
sys.exit(1 if bad else 0)
