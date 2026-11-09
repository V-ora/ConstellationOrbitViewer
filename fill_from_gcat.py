# -*- coding: utf-8 -*-
"""用 GCAT（Jonathan McDowell, planet4589.org）自动补全发射口径。

为什么用它（2026-11-09 实测）：
  GitHub runner 访问卫星百科恒 403，但 GCAT 在机房 0.9 秒就下完 13.8 MB，
  且 launch.tsv 里 **Launch_Tag 就是 COSPAR 年份序号**，直接能对上我们的批次：
     2026-248 → LV=Lijian-2  site=JQ  pad=LC140  date=2026 Nov 3 00:15 UTC
                mission="Qianfan Jigui 21"（= 千帆极轨21组）
     2026-253 → LV=Chang Zheng 12  site=HCSLS  pad=LC2  mission="WHDW 29"
所以「组次 / 火箭 / 场站 / 发射时刻」都能全自动填，不再依赖本机访问卫星百科。

规则：
  · 只填**空字段**，绝不覆盖人工表（data/launches.json）与卫星百科已有的值
  · 写入 cache/launches_auto.json（自动层），人工表优先级仍最高
  · UTC → 北京时间（+8）后写 "YYYY-MM-DD HH:MM"
  · 型号/场站按字典翻译，认不出就保留 GCAT 原值（不猜）
"""
import io
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE = os.path.join(HERE, "cache")
AUTO = os.path.join(CACHE, "launches_auto.json")
TSV = os.path.join(CACHE, "gcat_launch.tsv")
URL = "https://planet4589.org/space/gcat/tsv/launch/launch.tsv"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MAX_AGE = 20 * 3600          # 本地缓存 20 小时（CI 每次都会重下，机器快）

# 型号：GCAT 英文 → 我们表里的中文写法
LV_MAP = {
    "chang zheng 2c": "长征二号丙", "chang zheng 2d": "长征二号丁",
    "chang zheng 2f": "长征二号F", "chang zheng 3b": "长征三号乙",
    "chang zheng 4b": "长征四号乙", "chang zheng 4c": "长征四号丙",
    "chang zheng 5": "长征五号", "chang zheng 5b": "长征五号B",
    "chang zheng 6": "长征六号", "chang zheng 6a": "长征六号甲",
    "chang zheng 6c": "长征六号丙",
    "chang zheng 7": "长征七号", "chang zheng 7a": "长征七号甲",
    "chang zheng 8": "长征八号", "chang zheng 8a": "长征八号甲",
    "chang zheng 11": "长征十一号", "chang zheng 12": "长征十二号",
    "chang zheng 12b": "长征十二号乙",
    "lijian-2": "力箭二号", "lijian-1": "力箭一号",
    "zhuque-2e": "朱雀二号E", "zhuque-2": "朱雀二号",
    "gravity-1": "引力一号", "gravity-2": "引力二号",
    "kuaizhou-1a": "快舟一号甲", "ceres-1": "谷神星一号",
    "shuang quxian-1": "双曲线一号", "tianlong-2": "天龙二号",
    "falcon 9": "猎鹰9号", "electron": "电子号",
}
# 场站：GCAT 代码 → 中文
SITE_MAP = {
    "JQ": "酒泉", "JSLC": "酒泉", "XSC": "西昌", "TSC": "太原", "TSLC": "太原",
    "WSC": "文昌", "HCSLS": "海南商发", "HCS": "海南商发",
    "JSC": "酒泉", "HTP": "海阳", "SEA": "海上",
}
# 任务名：GCAT 写法 → 我们的组次口径
MISSION_MAP = [
    (re.compile(r"^Qianfan Jigui (\d+)$", re.I), lambda m: "千帆极轨%s组" % m.group(1)),
    (re.compile(r"^WHDW (\d+)$", re.I), lambda m: "卫星互联网低轨%s组" % m.group(1)),
    (re.compile(r"^Guowang (\d+)$", re.I), lambda m: "卫星互联网低轨%s组" % m.group(1)),
]


def log(m):
    print("  " + m, flush=True)


def read_json(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def download():
    """下 launch.tsv（带本地缓存）；失败时用旧缓存。"""
    if os.path.isfile(TSV) and time.time() - os.path.getmtime(TSV) < MAX_AGE:
        log("使用本地缓存 %s（%.1f MB）" % (os.path.basename(TSV), os.path.getsize(TSV) / 1048576))
        return TSV
    try:
        t0 = time.time()
        req = urllib.request.Request(URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=300) as r:
            raw = r.read()
        with open(TSV, "wb") as f:
            f.write(raw)
        log("下载 launch.tsv：%.1f MB / %.1f 秒" % (len(raw) / 1048576, time.time() - t0))
        return TSV
    except Exception as e:
        log("下载失败（%s）%s" % (type(e).__name__, e))
        if os.path.isfile(TSV):
            log("改用本地旧缓存（%.1f MB）" % (os.path.getsize(TSV) / 1048576))
            return TSV
        return None


def parse_gcat(path):
    """→ {tag: {...}}，只保留 2024 年及以后（我们关心的批次）。"""
    out = {}
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            if not ln or ln.startswith("#"):
                continue
            f2 = ln.rstrip("\n").split("\t")
            if len(f2) < 13:
                continue
            tag = f2[0].strip()
            if not re.match(r"^(19|20)\d{2}-\d{3}$", tag):
                continue
            if int(tag[:4]) < 2024:
                continue
            out[tag] = {
                "date_utc": f2[2].strip(),
                "lv": f2[3].strip(),
                "variant": f2[4].strip(),
                "flight": f2[6].strip(),   # GCAT 第 7 列是 Flight（如 Y11），第 8 列是名称
                "mission": f2[8].strip(),
                "site": f2[11].strip(),
                "pad": f2[12].strip(),
                "inc": f2[18].strip() if len(f2) > 18 else "",
            }
    return out


MON = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def utc_to_bjt(s):
    """'2026 Nov  3 0015:18' → '2026-11-03 08:15'（北京时间）。"""
    m = re.match(r"^(\d{4})\s+([A-Za-z]{3})\s+(\d{1,2})\s+(\d{2})(\d{2})", (s or "").strip())
    if not m:
        return ""
    y, mon, d, hh, mm = m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5))
    if mon not in MON:
        return ""
    import datetime
    try:
        dt = datetime.datetime(int(y), MON[mon], d, hh, mm) + datetime.timedelta(hours=8)
    except Exception:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def cn_lv(g):
    key = (g.get("lv") or "").strip().lower()
    base = LV_MAP.get(key) or (g.get("lv") or "")
    var = (g.get("variant") or "").strip()
    flight = (g.get("flight") or "").strip()
    txt = base
    if var and var != "-" and var.lower() not in base.lower():
        txt = "%s %s" % (base, var)
    if re.match(r"^Y(\d+)$", flight):
        txt += " 遥%s" % cn_num(int(flight[1:]))
    return txt.strip()


def cn_num(n):
    digits = "零一二三四五六七八九"
    if n < 10:
        return digits[n]
    if n < 20:
        return "十" + (digits[n % 10] if n % 10 else "")
    if n < 100:
        return digits[n // 10] + "十" + (digits[n % 10] if n % 10 else "")
    return str(n)


def cn_site(g):
    code = (g.get("site") or "").strip().upper()
    pad = (g.get("pad") or "").strip()
    name = SITE_MAP.get(code, code)
    if pad and pad != "-":
        pad = pad.replace("LC", "LC-").replace("--", "-")
        return "%s %s" % (name, pad)
    return name


def cn_mission(g):
    m = (g.get("mission") or "").strip()
    for pat, fn in MISSION_MAP:
        mm = pat.match(m)
        if mm:
            return fn(mm)
    return ""


def main():
    path = download()
    if not path:
        log("没有可用的 GCAT 数据，跳过（不影响出图）")
        return 0
    gcat = parse_gcat(path)
    log("解析 GCAT：%d 个 2024 年以后的批次" % len(gcat))

    auto = read_json(AUTO, {})
    if not isinstance(auto, dict):
        auto = {}
    hand = {}
    for fn in ("launches.json", "gw_launches.json"):
        for r in read_json(os.path.join(DATA, fn), []) or []:
            cs = str(r.get("cospar") or "")[:8]
            if cs:
                hand[cs] = r

    filled = {"rocket": 0, "site": 0, "time": 0, "mission": 0}
    for gk, rows in (auto or {}).items():
        for r in rows or []:
            cs = str(r.get("cospar") or "")[:8]
            g = gcat.get(cs)
            if not g:
                continue
            h = hand.get(cs) or {}
            # 人工表已有非空值 → 不再改（人工优先）
            def blank(k):
                return not (r.get(k) or "").strip() and not (h.get(k) or "").strip()

            if blank("mission"):
                v = cn_mission(g)
                if v:
                    r["mission"] = v
                    filled["mission"] += 1
            if blank("rocket"):
                v = cn_lv(g)
                if v and v != "-":
                    r["rocket"] = v
                    filled["rocket"] += 1
            if blank("site"):
                v = cn_site(g)
                if v and v != "-":
                    r["site"] = v
                    filled["site"] += 1
            if blank("time"):
                v = utc_to_bjt(g.get("date_utc"))
                if v:
                    r["time"] = v
                    filled["time"] += 1
            if not (r.get("gcat") or ""):
                r["gcat"] = g.get("date_utc") or ""
            if not (r.get("plane") or "") and g.get("inc") not in ("", "-"):
                r["plane"] = "%s°" % g["inc"]

    write_json(AUTO, auto)
    log("补全：型号 %d / 场站 %d / 时刻 %d / 组次 %d"
        % (filled["rocket"], filled["site"], filled["time"], filled["mission"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
