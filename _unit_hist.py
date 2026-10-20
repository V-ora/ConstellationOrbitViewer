# -*- coding: utf-8 -*-
"""离线单测：sat_hist_extra.json 的「只存增量」改造。
不联网、不动真实数据（全部在临时目录里造基线 + extras）。"""
import importlib.util, io, json, os, sys, tempfile, copy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

spec = importlib.util.spec_from_file_location("srv", os.path.join(HERE, "server.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

ok = bad = 0


def chk(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1
        print("  PASS", name, extra)
    else:
        bad += 1
        print("  FAIL", name, extra)


TMP = tempfile.mkdtemp(prefix="histtest_")
EXTRA = os.path.join(TMP, "sat_hist_extra.json")
m._HIST_EXTRA_PATH = EXTRA


def base_state():
    return {
        "base": "2024-01-01", "now": "",
        "sats": {
            "1": {"name": "A", "src": "qianfan", "target": 1068.5,
                  "pts": [[1.0, 800.0], [2.0, 801.0]]},
            "2": {"name": "B", "src": "qianfan", "target": 1068.5,
                  "pts": [[1.0, 700.0]]},
            "3": {"name": "C", "src": "gw", "target": 1149.3,
                  "pts": [[1.0, 900.0]]},
        },
    }


print("=" * 66)
print("T1 首次启动：无 extras 文件")
print("=" * 66)
m.SAT_HIST = base_state()
m._hist_load_extras()
chk("T1.1 基线快照长度正确", m._HIST_BASE_LEN == {"1": 2, "2": 1, "3": 1}, m._HIST_BASE_LEN)
chk("T1.2 无 extras 时不崩溃、库不变", len(m.SAT_HIST["sats"]) == 3)

print()
print("=" * 66)
print("T2 追加一天 + 新增一颗星 → 落盘只存增量")
print("=" * 66)
for n, t in (("1", 3.0), ("2", 2.0), ("3", 2.0)):
    m._hist_append_pts(m.SAT_HIST["sats"][n], [[t, 810.0]])
m.SAT_HIST["sats"]["9"] = {"name": "NEW", "src": "gw", "target": 1149.3,
                           "pts": [[5.0, 950.0]]}
m._hist_save_extras({"attempts": {"9": "2026-10-06"}})
doc = json.load(open(EXTRA, encoding="utf-8"))
print("  落盘 keys =", sorted(doc))
chk("T2.1 sats 里只有基线没有的新星", set(doc.get("sats") or {}) == {"9"}, list(doc.get("sats") or {}))
chk("T2.2 appends 只含新增的 1 个点/星",
    {k: len(v) for k, v in (doc.get("appends") or {}).items()} == {"1": 1, "2": 1, "3": 1},
    doc.get("appends"))
chk("T2.3 attempts 保留", doc.get("attempts", {}).get("9") == "2026-10-06")
size = os.path.getsize(EXTRA)
chk("T2.4 文件很小（<1.5 KB，旧写法会是整库副本）", size < 1500, "%d B" % size)
chk("T2.5 不再出现「整条 3 点历史」被复制", len((doc.get("appends") or {}).get("1", [])) == 1)

print()
print("=" * 66)
print("T3 重启：从「基线 + 增量」还原")
print("=" * 66)
m.SAT_HIST = base_state()
m._hist_load_extras()
s = m.SAT_HIST["sats"]
chk("T3.1 基线里星星恢复成 3 点", [p[0] for p in s["1"]["pts"]] == [1.0, 2.0, 3.0], s["1"]["pts"])
chk("T3.2 新星 9 也在", "9" in s and len(s["9"]["pts"]) == 1)
chk("T3.3 各星点数 = 基线 + 增量",
    (len(s["1"]["pts"]), len(s["2"]["pts"]), len(s["3"]["pts"])) == (3, 2, 2))

print()
print("=" * 66)
print("T4 幂等：再存一次内容不变；重复点不再增长")
print("=" * 66)
m._hist_save_extras({"attempts": {"9": "2026-10-06"}})
doc2 = json.load(open(EXTRA, encoding="utf-8"))
chk("T4.1 再存一次文件内容不变", doc2 == doc)
before = len(m.SAT_HIST["sats"]["1"]["pts"])
added = m._hist_append_pts(m.SAT_HIST["sats"]["1"], [[3.0, 810.0]])
chk("T4.2 同一 (t, 高度) 重复追加被拒", added == 0 and len(m.SAT_HIST["sats"]["1"]["pts"]) == before)

print()
print("=" * 66)
print("T5 兼容旧格式（整库复制的那份 extras）")
print("=" * 66)
legacy = {"sats": {
    "1": {"name": "A", "src": "qianfan", "target": 1068.5,
          "pts": [[1.0, 800.0], [2.0, 801.0], [3.0, 810.0]]},   # 整条副本（含基线点）
    "2": {"name": "B", "src": "qianfan", "target": 1068.5,
          "pts": [[1.0, 700.0], [2.0, 810.0]]},
}, "now": "2026-10-06 14:28"}
json.dump(legacy, open(EXTRA, "w", encoding="utf-8"), ensure_ascii=False)
m.SAT_HIST = base_state()
m._hist_load_extras()
s = m.SAT_HIST["sats"]
chk("T5.1 旧格式的基线点没有被重复插入", [p[0] for p in s["1"]["pts"]] == [1.0, 2.0, 3.0], s["1"]["pts"])
chk("T5.2 旧格式只取多出来的点", len(s["2"]["pts"]) == 2, s["2"]["pts"])
# 迁移：存回去后应变成增量格式
m._hist_save_extras({"attempts": {}})
doc3 = json.load(open(EXTRA, encoding="utf-8"))
chk("T5.3 读旧格式后落盘自动缩成增量（无 sats 或只有新星）",
    set(doc3.get("sats") or {}) == set(), list(doc3.get("sats") or {}))
chk("T5.4 缩成增量后体积 < 1.5 KB", os.path.getsize(EXTRA) < 1500, "%d B" % os.path.getsize(EXTRA))
chk("T5.5 旧格式副本点高扩容为 810.0 已丢失（只保留 3.0→810.0 的那次）",
    all(len(v) == 1 for v in (doc3.get("appends") or {}).values()), doc3.get("appends"))

print()
print("=" * 66)
print("T7 脏点护栏：同一 t 相差 >50 km 的读数必须拒收（微差仍放行）")
print("=" * 66)
json.dump({"sats": {"1": {"name": "A", "pts": [[1.0, 800.0], [2.0, 801.0], [2.0, 400.0]]}}},
          open(EXTRA, "w", encoding="utf-8"), ensure_ascii=False)
m.SAT_HIST = base_state()
m._hist_load_extras()
p1 = m.SAT_HIST["sats"]["1"]["pts"]
chk("T7.1 同 t 的 400.0 脏点被拒收", all(a > 500 for _, a in p1), p1)
chk("T7.2 正常点仍然入库且有序", [x[0] for x in p1] == [1.0, 2.0], p1)

json.dump({"sats": {"1": {"name": "A", "pts": [[2.0, 801.4]]}}},
          open(EXTRA, "w", encoding="utf-8"), ensure_ascii=False)
m.SAT_HIST = base_state()
m._hist_load_extras()
p2 = m.SAT_HIST["sats"]["1"]["pts"]
chk("T7.3 同 t 微差（0.4 km，多源同日）仍允许并入", len(p2) == 3, p2)

print()
print("=" * 66)
print("T6 static_target 路径边界")
print("=" * 66)
f = m.static_target
root = os.path.join(TMP, "web")
chk("T6.1 正常文件放行", f(root, "index.html") is not None)
chk("T6.2 子目录放行", f(root, "raise/a.html") is not None)
chk("T6.3 ../ 越界拦截", f(root, "../data/x") is None)
chk("T6.4 ..\\ 越界拦截", f(root, "..\\data\\x") is None)
chk("T6.5 同级同前缀目录 web_evil 拦截", f(root, "../web_evil/a.html") is None)
chk("T6.6 绝对路径注入拦截", f(root, "C:/Windows/win.ini") is None)
chk("T6.7 空相对路径 = 根目录本身（交给 send_file 判 404）", f(root, "") == os.path.normpath(root))

print()
print("== 单测小结：%d 通过 / %d 失败 ==" % (ok, bad))
sys.exit(1 if bad else 0)
