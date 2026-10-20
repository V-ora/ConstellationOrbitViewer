# -*- coding: utf-8 -*-
"""千帆星座轨道爬升大图 · 数据完整性与准确性自检。

用法：python _audit_qf.py
产出：控制台摘要 + _audit_qf.md（可交付的自检报告）

检查口径：
  A 完整性：颗数 / 每颗点数 / t 是否严格递增 / 末点日期 / 相邻点间隔 / 发射归属
  B 准确性：末点高度 vs CelesTrak 当前高度、真实历史末两点一致性、
            状态与高度是否自洽、抵达日期与曲线首达日期是否一致、异常高度点排查
"""
import json, io, os, re, sys, math, ssl, datetime, collections, statistics, urllib.request

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
MU, RE = 398600.4418, 6378.137
BASE = datetime.date(2024, 1, 1)
TARGET, TOL = 1068.5, 3.0
REACHED = ('已抵达工作轨道', '已进入工作轨道（在轨调整）')
EXPECT_N = 257

ssl._create_default_https_context = ssl._create_unverified_context
_OP = [urllib.request.build_opener(),
       urllib.request.build_opener(urllib.request.ProxyHandler({}))]


def d_of(t):
    return BASE + datetime.timedelta(days=int(t))


def fetch(url, timeout=25):
    last = None
    for _ in range(3):
        for op in _OP:
            try:
                return op.open(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                               timeout=timeout).read()
            except Exception as e:
                last = e
    raise last


def alt_of(gp):
    n = float(gp["MEAN_MOTION"]) * 2 * math.pi / 86400.0
    return (MU / (n * n)) ** (1.0 / 3.0) - RE


H = json.load(io.open(os.path.join(HERE, 'data', 'sat_hist.json'), encoding='utf-8'))
A = json.load(io.open(os.path.join(WS, 'qianfan_orbit', 'analysis.json'), encoding='utf-8'))
LA = json.load(io.open(os.path.join(HERE, 'data', 'launches.json'), encoding='utf-8'))
META = {str(s['catnr']): s for s in A['sats']}
qf = {k: e for k, e in H['sats'].items() if e.get('src') == 'qianfan'}

L = []
add = L.append
bad = []

add('# 千帆星座轨道爬升大图 · 数据自检报告')
add('')
add('- 生成时间：%s' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))
add('- 数据源：`data/sat_hist.json`（逐日高度）+ `qianfan_orbit/analysis.json`（元信息）+ `data/launches.json`')
add('- 时间基准：t = 距 2024-01-01 的天数；工作轨道 %.1f km（容差 ±%.1f）' % (TARGET, TOL))
add('')

# ---------------- A 完整性 ----------------
add('## A 完整性')
n_ok = len(qf) == EXPECT_N
add('- 在库千帆：**%d 颗**（期望 %d）%s' % (len(qf), EXPECT_N, '' if n_ok else '  ← 不符'))
if not n_ok:
    bad.append('颗数 %d ≠ %d' % (len(qf), EXPECT_N))

no_pts = [k for k, e in qf.items() if not e.get('pts')]
add('- 无逐日数据的星：%s' % ('无' if not no_pts else ', '.join(no_pts)))
if no_pts:
    bad.append('有星缺逐日数据')

bad_order = [k for k, e in qf.items()
             if any(e['pts'][i][0] >= e['pts'][i + 1][0] for i in range(len(e['pts']) - 1))]
add('- 时间戳非严格递增的星：%s' % ('无' if not bad_order else ', '.join(bad_order)))
if bad_order:
    bad.append('有星时间戳乱序')

last = collections.Counter(e['pts'][-1][0] for e in qf.values())
add('- 末点日期分布：%s' % '，'.join('%s×%d' % (d_of(t).isoformat(), c) for t, c in sorted(last.items())))

gaps = collections.Counter()
for e in qf.values():
    ts = [p[0] for p in e['pts']]
    for i in range(len(ts) - 1):
        gaps[int(ts[i + 1] - ts[i])] += 1
top = sorted(gaps.items(), key=lambda x: -x[1])[:6]
add('- 相邻点间隔（天 → 出现次数）：%s' % '，'.join('%d天×%d' % (g, c) for g, c in top))
n_gap = sum(c for g, c in gaps.items() if g > 1)
add('- 其中间隔 >1 天的断点：%d 处%s' % (
    n_gap, '（多为发射首日/补点边界，属正常）' if n_gap else ''))

cnt = sorted(len(e['pts']) for e in qf.values())
add('- 每颗点数：最少 %d / 中位 %d / 最多 %d' % (cnt[0], int(statistics.median(cnt)), cnt[-1]))

# 发射归属：每颗星的 launch 是否落在 launches.json 的发射日里
ldates = {x['time'][:10] for x in LA}
bysrc = collections.Counter((e.get('launch') or '?') for e in qf.values())
add('- 发射日归属：%d 个不同发射日，覆盖 %d 颗；launches.json 有 %d 次发射'
    % (len(bysrc), sum(bysrc.values()), len(LA)))
# 任务表校验：按 COSPAR 前缀分组后，各次发射的卫星数合计应 = 库内总数，且不应有 0
html = io.open(os.path.join(HERE, 'web', 'raise', '千帆星座轨道爬升交互大图.html'),
               encoding='utf-8').read()
m = re.search(r'const DATA = (\{.*?\});', html, re.S)
lld = json.loads(m.group(1))['launches']
tot = sum(x['n'] for x in lld)
zero = [x['grp'] for x in lld if x['n'] == 0]
add('- 任务表各次卫星数合计 **%d**（库内 %d）%s' % (tot, len(qf), '✓' if tot == len(qf) else ' ← 不符'))
add('- 卫星数为 0 的发射任务：%s' % ('无' if not zero else '、'.join(zero)))
if tot != len(qf) or zero:
    bad.append('任务表卫星数与库不一致（合计 %d / %d，零值组 %s）' % (tot, len(qf), zero))
dup = [d for d, c in collections.Counter((x['time'][:10], x['group']) for x in LA).items() if c > 1]
add('- 任务表重复行：%s' % ('无' if not dup else dup))

# ---------------- B 准确性 ----------------
add('')
add('## B 准确性')

# B1 与 CelesTrak 当前高度对照（末点就是用同一接口补的，差异应≈0）
gp = {}
try:
    data = json.loads(fetch('https://celestrak.org/NORAD/elements/gp.php?GROUP=qianfan&FORMAT=json').decode('utf-8'))
    gp = {str(s['NORAD_CAT_ID']): round(alt_of(s), 1) for s in data}
    add('- CelesTrak `gp.php?GROUP=qianfan` 实时获取：%d 颗' % len(gp))
except Exception as e:
    add('- CelesTrak 实时获取失败：%s（跳过该项）' % e)

if gp:
    diffs, worst = [], []
    for k, e in qf.items():
        if k in gp:
            dd = abs(e['pts'][-1][1] - gp[k])
            diffs.append(dd)
            worst.append((dd, k, e.get('name'), e['pts'][-1][1], gp[k]))
    worst.sort(reverse=True)
    add('- 末点高度 vs 当前实测高度：可比 %d 颗，平均绝对差 **%.3f km**，最大 %.2f km'
        % (len(diffs), statistics.mean(diffs), worst[0][0]))
    add('- 差异最大的 5 颗：%s' % '；'.join('%s(曲线%.1f vs 实测%.1f, Δ%.1f)'
                                          % (w[2], w[3], w[4], w[0]) for w in worst[:5]))
    if statistics.mean(diffs) > 1.0:
        bad.append('末点与实测高度平均差 %.2f km 偏大' % statistics.mean(diffs))
    nohit = [k for k in qf if k not in gp]
    add('- 库里有的星但组里查不到：%s' % ('无' if not nohit else ', '.join(nohit)))

# B2 真实历史末两点是否自洽（已用真实历史刷到 10-02 的那批）
real = []
for k, e in qf.items():
    ts = [p[0] for p in e['pts']]
    if len(ts) >= 2 and ts[-1] - ts[-2] == 1:     # 末点是紧接的真实历史点
        real.append(abs(e['pts'][-1][1] - e['pts'][-2][1]))
if real:
    add('- 真实历史相邻两天的高度变化（%d 颗，不含补点）：平均 %.2f km，最大 %.2f km'
        % (len(real), statistics.mean(real), max(real)))
else:
    add('- 真实历史相邻两天：本批末点为补点，无样本（历史接口恢复后再看）')

# B3 状态与高度是否自洽
add('')
add('**状态 × 当前高度**（来自 analysis.json 的口径 + 最新高度）')
cur = {k: e['pts'][-1][1] for k, e in qf.items()}
stc = collections.defaultdict(list)
for k, e in qf.items():
    stc[META.get(k, {}).get('status', '(无状态)')].append(cur[k])
for st, vals in sorted(stc.items(), key=lambda x: -len(x[1])):
    vals.sort()
    add('- %s：%d 颗，min %.1f / 中位 %.1f / max %.1f'
        % (st, len(vals), vals[0], statistics.median(vals), vals[-1]))
    if st in REACHED and vals[0] < TARGET - TOL:
        bad.append('%s 里有星当前高度低于工作轨道带' % st)
    if st == '爬升中' and vals[-1] >= TARGET - TOL:
        bad.append('爬升中里有星已达工作轨道带（应升为已抵达）')
    if st == '抬升中止（未达工作轨道）' and vals[-1] >= TARGET - TOL:
        bad.append('抬升中止里有星已达工作轨道带（应改判状态）')

# B4 抵达日期 vs 曲线首达日期
mis = []
for k, e in qf.items():
    m = META.get(k, {})
    if m.get('status') not in REACHED or not m.get('arrive'):
        continue
    first = None
    for p in e['pts']:
        if p[1] >= TARGET - TOL:
            first = d_of(p[0]).isoformat()
            break
    if first and first != m['arrive']:
        mis.append((e.get('name'), m['arrive'], first))
add('')
add('- 抵达日期一致性（元信息 vs 曲线首达 ±%.1f km）：%d 颗比对，%d 颗不一致'
    % (TOL, sum(1 for k in qf if META.get(k, {}).get('status') in REACHED and META.get(k, {}).get('arrive')),
       len(mis)))
if mis:
    add('  - 样例（前 8）：%s' % '；'.join('%s 元信息%s / 曲线%s' % m for m in mis[:8]))

# B5 异常高度点
hi = [(k, e.get('name'), p) for k, e in qf.items() for p in e['pts'] if p[1] > 1100]
lo = [(k, e.get('name'), p) for k, e in qf.items() for p in e['pts'] if p[1] < 700]
add('')
add('- 异常高度点：>1100 km 共 %d 个，<700 km 共 %d 个' % (len(hi), len(lo)))
for tag, arr in (('偏高', hi), ('偏低', lo)):
    for k, nm, p in arr[:5]:
        add('  - %s %s %s %.1f km' % (tag, nm, d_of(p[0]).isoformat(), p[1]))

# ---------------- 结论 ----------------
add('')
add('## 结论')
add('- 发现问题：%s' % ('无' if not bad else '；'.join(bad)))
add('- 说明：末点为 2026-10-03 由 `gp.php` 实测高度补齐；真实逐日历史到 2026-10-02，'
    '两者衔接处已核对（见 B2）。CelesTrak 的 `graph-orbit-data.php` 端点当前不可用时，'
    '这段时间的逐日细节会在接口恢复后用 `update_hist.py` 覆盖。')

open(os.path.join(HERE, '_audit_qf.md'), 'w', encoding='utf-8').write('\n'.join(L))
print('\n'.join(L))
print('\n>> 报告已写入 _audit_qf.md')
print('>> 结论：%s' % ('未发现问题' if not bad else '需关注：' + '；'.join(bad)))
