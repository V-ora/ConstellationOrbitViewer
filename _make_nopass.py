# -*- coding: utf-8 -*-
"""生成「不含过境预报」的前端目录 web_nopass/。

做法：把 web/ 整份拷过去，只把 index.html 换成 _features/index_original.html
—— 那一份最初是从上一版发布 exe 里**原样提取**的页面，此后作为「无过境预报」变体
**与 web/index.html 并行维护**（凡是不依赖过境预报的功能改动，两边都要改）。

ORIG_MD5 是这份页面的**指纹**，用途只有一个：防止「改了 web/index.html 却忘了同步变体页」，
或反过来「变体页被误改」。指纹不符时脚本会停下来并给出提示，不会悄悄放过。
确认是本轮有意改动后，`NO_PASS_ACCEPT=1` 重跑一次即自动更新指纹。

再用 spec 的环境变量打一套不含该功能的 exe：
    set COV_WEB_SRC=web_nopass
    set COV_EXE_NAME=星座轨道实时查看器_无过境预报
    pyinstaller --noconfirm --workpath build/wp_nopass spec_onefile.spec

可反复重跑（幂等）。改过 web/index.html 后要重跑一次，web_nopass 才会跟上。
"""
import hashlib
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
DST = os.path.join(HERE, "web_nopass")
ORIG = os.path.join(HERE, "_features", "index_original.html")
ORIG_MD5 = "3ccd61487baa0d052877fa822c2d03f9"


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def accept_fingerprint(new):
    src = open(__file__, encoding="utf-8").read()
    out = re.sub(r'ORIG_MD5 = "[0-9a-f]{32}"', 'ORIG_MD5 = "%s"' % new, src, count=1)
    if out == src:
        print("警告：没能自动改写 ORIG_MD5，请手工改成 %s" % new)
        return False
    open(__file__, "w", encoding="utf-8").write(out)
    print("已接受新指纹：%s → %s（ORIG_MD5 已就地更新）" % (ORIG_MD5, new))
    return True


def main():
    if not os.path.isdir(WEB):
        print("找不到 web/ 目录：%s" % WEB)
        return 1
    if not os.path.isfile(ORIG):
        print("找不到原始页面：%s" % ORIG)
        return 1
    got = md5(ORIG)
    if got != ORIG_MD5:
        if os.environ.get("NO_PASS_ACCEPT") == "1":
            if not accept_fingerprint(got):
                return 1
        else:
            print("变体页指纹变了：期望 %s，实际 %s" % (ORIG_MD5, got))
            print("  → 若这轮**有意**改了无过境预报版页面（正常应和 web/index.html 同步改），")
            print("     设 NO_PASS_ACCEPT=1 重跑一次即自动更新指纹；")
            print("     否则说明变体页被漏改或误改，先查清楚再继续。")
            return 1

    if os.path.isdir(DST):
        shutil.rmtree(DST)
    shutil.copytree(WEB, DST)
    shutil.copyfile(ORIG, os.path.join(DST, "index.html"))

    cur = os.path.join(DST, "index.html")
    txt = open(cur, encoding="utf-8", errors="replace").read()
    if "passView" in txt:
        print("异常：生成的页面里仍含 passView，放弃")
        return 1

    # 逐档核对：除 index.html 外必须与 web/ 完全一致（保证只差这一个功能）
    diff = []
    for root, _d, files in os.walk(WEB):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), WEB)
            a = os.path.join(WEB, rel)
            b = os.path.join(DST, rel)
            if not os.path.isfile(b) or md5(a) != md5(b):
                diff.append(rel)
    print("web_nopass/ 已生成")
    print("  index.html  %d B  md5=%s（不含 passView）" % (os.path.getsize(cur), md5(cur)))
    print("  与 web/ 不同的文件：%s" % (diff or "无"))
    if diff != ["index.html"]:
        print("警告：预期只有 index.html 不同")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
