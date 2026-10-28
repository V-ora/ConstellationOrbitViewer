# -*- coding: utf-8 -*-
"""生成千帆星座轨道爬升交互大图（对齐国网星网版 UI，单图版式）2026-10-03。

数据源：
  - 查看器 data/sat_hist.json（逐日高度，由 update_hist.py / 服务端自动补齐保持最新）
  - qianfan_orbit/analysis.json（每颗星的元信息：倾角/就位日期/状态口径等）
  - 查看器 data/launches.json（16 次发射任务表，源自卫星百科）
状态口径沿用千帆版 5 种（已就位/在轨调整/爬升中/入轨停泊/抬升中止），
并用最新逐日数据刷新：「未就位」的星若当前高度已进入工作轨道带(±tol) → 升为「已抵达工作轨道」。
输出：星座轨道实时查看器/web/raise/千帆星座轨道爬升交互大图.html
"""
import os, re, json, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)   # 工作区根（只在开发机上存在，工程独立 clone 后没有）
T0 = dt.date(2024, 8, 6)     # 千帆首次发射（仅用于文案/语义）
BASE0 = dt.date(2024, 1, 1)  # ★ 时间轴原点：必须与 data/sat_hist.json 的 t 一致
                             #   （库里 t = 距 2024-01-01 的天数；早前误用 T0 当原点，
                             #     导致整条时间轴错位 218 天、曲线末段被裁掉）
MAXPTS = 520
TARGET, TOL = 1068.5, 3.0

# 千帆元信息：优先用工程内的 data/qianfan_analysis.json（随仓库发布，开箱即用）；
# 找不到才退回开发机上的 qianfan_orbit/analysis.json。
# ★ 2026-10-20：原来写死指向 ../qianfan_orbit/analysis.json（工程目录之外），
#   单独 clone 这个工程就必然 FileNotFoundError。
_ANA_LOCAL = os.path.join(HERE, 'data', 'qianfan_analysis.json')
_ANA_WS = os.path.join(WS, 'qianfan_orbit', 'analysis.json')
ANA_PATH = _ANA_LOCAL if os.path.isfile(_ANA_LOCAL) else _ANA_WS
if not os.path.isfile(ANA_PATH):
    raise SystemExit('缺少千帆元信息：%s\n（仓库里应自带 data/qianfan_analysis.json）' % ANA_PATH)
A = json.load(open(ANA_PATH, encoding='utf-8'))
print('元信息：%s（%d 颗）' % (ANA_PATH, len(A.get('sats') or [])))
H = json.load(open(os.path.join(HERE, 'data', 'sat_hist.json'), encoding='utf-8'))
# ★ 2026-10-28：同上，走「自动同步 + 人工表覆盖」的合并表
import launch_table
LA = launch_table.load_merged('qianfan', os.path.join(HERE, 'data', 'launches.json'))
META = {str(s['catnr']): s for s in A['sats']}

S_OK, S_ADJ, S_CLIMB, S_PARK, S_STOP = ('已抵达工作轨道', '已进入工作轨道（在轨调整）',
                                        '爬升中', '入轨停泊（待抬升）', '抬升中止（未达工作轨道）')
STATUS_ORDER = [S_OK, S_ADJ, S_CLIMB, S_PARK, S_STOP]
STATUS_COLOR = {S_OK: '#199a63', S_ADJ: '#0f9fb5', S_CLIMB: '#d0342c',
                S_PARK: '#c9701a', S_STOP: '#7a52c7'}
STATUS_CLASS = {S_OK: 'green', S_ADJ: 'blue', S_CLIMB: 'red', S_PARK: 'amber', S_STOP: 'grey'}
STATUS_SHORT = {S_OK: '已就位', S_ADJ: '在轨调整', S_CLIMB: '爬升中', S_PARK: '入轨停泊', S_STOP: '抬升中止'}
REACHED = (S_OK, S_ADJ)

sats = []
for catnr, e in H['sats'].items():
    if e.get('src') != 'qianfan' or not e.get('pts'):
        continue
    m = META.get(catnr, {})
    pts = sorted(e['pts'])
    if len(pts) > MAXPTS:   # 降抽样，保尾点
        step = len(pts) / float(MAXPTS)
        pts = [pts[int(i * step)] for i in range(MAXPTS)] + [pts[-1]]
    cur = pts[-1][1]
    st = m.get('status', S_CLIMB)
    arrive = m.get('arrive') or ''
    reach_days = m.get('reach_days', '')
    if st not in REACHED and cur >= TARGET - TOL:
        # 用最新数据升为「已抵达」：尾部连续在带内的起点即就位日
        i = len(pts) - 1
        while i > 0 and pts[i - 1][1] >= TARGET - TOL:
            i -= 1
        arrive = (BASE0 + dt.timedelta(days=int(pts[i][0]))).isoformat()
        st = S_OK
        reach_days = (dt.date.fromisoformat(arrive) - dt.date.fromisoformat(e.get('launch') or '1970-01-01')).days
    sats.append(dict(
        n=e['name'], c=catnr, l=e.get('launch') or '',
        cs=(re.match(r'(\d{4}-\d+)', (m.get('intl') or '')) or [None, ''])[1],
        t=TARGET, f=round(pts[0][1], 1), a=round(cur, 1),
        d=arrive or '-', k=reach_days, inc=m.get('inc'),
        ides=m.get('intl', ''), st=st, p=pts))

# ---------------- 发射任务（16 次，launches.json 卫星百科口径） ----------------
by_launch = {}
for x in sats:
    by_launch.setdefault(x['l'], []).append(x)
by_cospar = {}
for x in sats:
    if x['cs']:
        by_cospar.setdefault(x['cs'], []).append(x)

launch_list = []
_used_days = set()
for m in sorted(LA, key=lambda x: x['time']):
    d = m['time'][:10]
    # ★ 首选 COSPAR 前缀精确匹配：同一天可能有两次发射（例：2026-09-15 的
    #   极轨19组 2026-210 / 极轨26组 2026-211，UTC 里是同一日），只按日期会串组。
    grp_sats = by_cospar.get(m.get('cospar') or '', [])
    pick = None
    if not grp_sats:
        # 回退：任务表用北京时间、历史库用 CelesTrak satcat 的 UTC 日期，跨午夜的发射会差一天，
        # 所以先精确匹配、再在 ±1 天内挑最近的未被占用的发射日。
        pick = d if (d in by_launch and d not in _used_days) else None
        if pick is None:
            dd = dt.date.fromisoformat(d)
            best = None
            for cand in by_launch:
                if cand in _used_days:
                    continue
                delta = abs((dt.date.fromisoformat(cand) - dd).days)
                if delta <= 1 and (best is None or delta < best[1]):
                    best = (cand, delta)
            pick = best[0] if best else None
        if pick:
            _used_days.add(pick)
        grp_sats = by_launch.get(pick, [])
    cnts = {}
    for x in grp_sats:
        cnts[x['st']] = cnts.get(x['st'], 0) + 1
    launch_list.append(dict(
        grp=m['group'], lv=m['rocket'].split(' 遥')[0], sn='遥' + m['rocket'].split(' 遥')[-1] if ' 遥' in m['rocket'] else '',
        up='', site=m['site'], pad='', date=d, bjt=m['time'],
        t=(dt.date.fromisoformat(d) - BASE0).days, n=len(grp_sats),
        plane=m.get('plane', '—'), cospar=m.get('cospar', '—'),
        cnts=cnts, rocket=m['rocket']))

_miss = [x for x in launch_list if x['n'] == 0]
if _miss:
    print('!! 有 %d 次发射没匹配到卫星: %s' % (len(_miss), [x['grp'] for x in _miss]))

for x in launch_list:
    def _site_short(site):
        s = (site.replace('商业航天发射场', '商发')
                 .replace('航天发射场', '')
                 .replace('卫星发射中心', ''))
        return s
    x['site_s'] = _site_short(x['site'])

rocket_stat = {}
for x in launch_list:
    lv = launch_table.model_of(x['rocket'])
    rocket_stat[lv] = rocket_stat.get(lv, 0) + 1

status_cnt = {}
for x in sats:
    status_cnt[x['st']] = status_cnt.get(x['st'], 0) + 1
n_total = len(sats)


def _card(k, v, u, cls=''):
    return ('<div class="card %s"><div class="k">%s</div><div class="v">%s '
            '<span class="u">%s</span></div></div>' % (cls, k, v, u))


cards = [_card('在轨卫星总数', n_total, '颗')]
for k in STATUS_ORDER:
    cards.append(_card(k, status_cnt.get(k, 0), '颗', STATUS_CLASS[k]))
cards.append(_card('发射任务', len(launch_list), '次'))
CARDS = '\n'.join(cards)

data = dict(t0=BASE0.isoformat(),
            # ★ sat_hist.json 的 now 由 datetime.now() 写成（本地时间），本页时间轴是 UTC，
            #   统一换算成 UTC 后再显示，副标题文案也标了 UTC。
            now=(dt.datetime.strptime(H.get('now') or '1970-01-01 00:00', '%Y-%m-%d %H:%M')
                 - (dt.datetime.now().astimezone().utcoffset() or dt.timedelta(hours=8))
                 ).strftime('%Y-%m-%d %H:%M'),
            target=TARGET, sats=sats, launches=launch_list, rockets=rocket_stat)

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>千帆星座轨道爬升交互大图</title>
<style>
  *{box-sizing:border-box}
  body{margin:0;font-family:"Microsoft YaHei","PingFang SC","Segoe UI",sans-serif;background:#f2f5f9;color:#1b2430}
  header{padding:20px 28px 12px}
  h1{margin:0;font-size:24px;letter-spacing:.5px}
  .sub{margin-top:6px;font-size:13px;color:#5b6675}
  .bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;padding:0 28px 12px}
  button{border:1px solid #c9d3df;background:#fff;color:#1b2430;border-radius:20px;padding:6px 14px;font-size:13px;cursor:pointer;font-family:inherit}
  button.on{background:#2f5597;border-color:#2f5597;color:#fff}
  button:hover{filter:brightness(.97)}
  .wrap{margin:0 20px 18px;background:#fff;border-radius:12px;box-shadow:0 2px 12px rgba(20,40,80,.08);padding:10px;position:relative}
  canvas{width:100%;display:block;border-radius:8px;cursor:crosshair}
  #tip{position:fixed;pointer-events:none;background:rgba(20,30,45,.95);color:#fff;font-size:12.5px;line-height:1.65;
       padding:10px 13px;border-radius:8px;display:none;z-index:50;white-space:nowrap;box-shadow:0 4px 16px rgba(0,0,0,.25)}
  #tip b{font-size:13.5px}
  #tip .lv{color:#ffd479;font-weight:bold}
  .cards{display:flex;gap:12px;flex-wrap:wrap;margin:0 28px 16px}
  .card{background:#fff;border:1px solid #e2e8f2;border-radius:10px;padding:11px 16px;min-width:150px;
        box-shadow:0 2px 12px rgba(20,40,80,.06)}
  .card .k{font-size:12px;color:#6b7a94;letter-spacing:.4px}
  .card .v{font-size:22px;font-weight:700;margin-top:3px}
  .card .u{font-size:13px;color:#8494ad;font-weight:400}
  .card.green .v{color:#199a63}.card.red .v{color:#d0342c}.card.blue .v{color:#0f9fb5}
  .card.amber .v{color:#c9701a}.card.grey .v{color:#7a52c7}
  body.emb header{padding-top:86px}
  .panel{background:#fff;border:1px solid #e2e8f2;border-radius:12px;padding:14px 16px 10px;margin:0 28px 18px;
         box-shadow:0 2px 12px rgba(20,40,80,.08)}
  .panel h2{font-size:16px;margin:2px 0 3px;font-weight:700}
  .panel p.note{font-size:12.5px;color:#6b7a94;margin:2px 0 10px;line-height:1.7}
  .panel p.note em{font-style:normal;font-weight:bold;color:#2f5597}
  .tbs{overflow:auto;max-height:520px}
  table.tb{border-collapse:collapse;width:100%;font-size:12.5px;margin-top:2px}
  table.tb th{background:#f0f4fa;color:#41506b;font-weight:700;text-align:left;padding:8px 9px;
    border-bottom:1px solid #e2e8f2;white-space:nowrap}
  table.tb td{padding:7px 9px;border-bottom:1px solid #eef2f8;vertical-align:middle}
  table.tb tbody tr:hover td{background:#eaf2fb;cursor:pointer}
  table.tb tbody tr.s0 td:first-child{border-left:4px solid #f5a524}
  table.tb tbody tr.s1 td:first-child{border-left:4px solid #f5a524}
  table.tb td.c{text-align:center;color:#6b7a94}
  table.tb td.num{white-space:nowrap;font-variant-numeric:tabular-nums;font-weight:600}
  table.tb td.mono{font-family:Consolas,Menlo,monospace;color:#5d6b85;font-size:11.5px}
  .pill{display:inline-block;background:#fff;color:var(--c);border:1px solid var(--c);
    border-radius:20px;padding:1px 8px;font-size:11.5px;margin:1px 3px 1px 0;white-space:nowrap}
  table.lau{width:calc(100% - 40px);margin:0 20px 26px;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;
            box-shadow:0 2px 12px rgba(20,40,80,.08);font-size:13px}
  table.lau th{background:#2f5597;color:#fff;font-weight:600;padding:10px 8px;text-align:center;font-size:12.5px;white-space:nowrap}
  table.lau td{padding:8px;text-align:center;border-bottom:1px solid #eef2f7;white-space:nowrap}
  table.lau tr{transition:background .12s}
  table.lau tbody tr:hover{background:#eaf2fb;cursor:pointer}
  table.lau tbody tr.s0 td:first-child{border-left:4px solid #f5a524}
  table.lau tbody tr.s1 td:first-child{border-left:4px solid #f5a524}
  .grp{font-weight:bold}
  .rk{font-weight:bold;color:#1b2430}
  .sn{display:inline-block;background:#eef3fa;border:1px solid #cfdcec;border-radius:4px;padding:0 6px;color:#2f5597;font-weight:bold}
  .foot{padding:0 28px 26px;font-size:11.5px;color:#7a8595;line-height:1.7}
  .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
  /* ---------- 时间范围滑块（拖动两端改窗口 / 拖中段平移） ---------- */
  .tbar{position:relative;height:30px;margin:10px 0 0;user-select:none;touch-action:none;cursor:pointer}
  .tk{position:absolute;left:0;right:0;top:12px;height:6px;background:#e2e8f2;border-radius:3px}
  .tsel{position:absolute;top:12px;height:6px;background:#2f5597;opacity:.5;border-radius:3px}
  .th{position:absolute;top:4px;width:13px;height:22px;margin-left:-6.5px;background:#fff;border:2px solid #2f5597;
      border-radius:5px;cursor:ew-resize;box-shadow:0 1px 4px rgba(20,40,80,.28);z-index:2}
  .th:hover{background:#eaf2fb}
</style>
</head>
<body>
<header>
  <h1>千帆星座（QIANFAN）轨道爬升交互大图</h1>
  <div class="sub" id="sub"></div>
</header>

<div class="cards">
__CARDS__
</div>

<div class="panel"><h2>发射任务一览（官方组号 · 发射时间 · 运载火箭型号与遥号）</h2>
<p class="note" id="rstats"></p>
<div class="tbs"><table class="tb" id="lauts">
  <thead><tr>
    <th>#</th><th>官方组号</th><th>发射时间（北京时间）</th><th>运载火箭</th><th>发射场</th>
    <th>卫星数</th><th>COSPAR 编号（非日期）</th><th>轨道面</th><th>当前状态分布</th>
  </tr></thead>
  <tbody></tbody>
</table></div></div>

<div class="bar">
  <button id="b2">只显示最新观测点</button>
  <button id="b3" class="on">显示发射事件</button>
  <button id="b4" class="on">适应全部高度范围</button>
</div>
<div class="wrap"><canvas id="cv"></canvas>
  <div class="tbar" id="tbar"><div class="tk"></div><div class="tsel" id="tsel"></div><div class="th" id="thL"></div><div class="th" id="thR"></div></div>
</div>
<div id="tip"></div>

<div class="foot">
  数据源：CelesTrak / NORAD 通用摄动(GP)历史轨道数据 · 纵轴为平均轨道高度（半长轴 − 地球半径 6378.137 km），分辨率 0.1 km ·
  发射任务信息据卫星百科「千帆星座」词条发射表核定 ·
  鼠标悬停可查看任一卫星对应时刻的高度；悬停表格行或图中发射刻度可高亮该次发射的全部卫星；滚轮缩放时间轴，Shift+滚轮缩放高度轴。
</div>
<script>
const DATA = __DATA__;
// 曲线统一用「千帆」主题色（与查看器左侧分组的千帆色 #f5a524 一致）
// —— 早前按「是否就位」二分着色（绿/橙），与查看器的配色体系不一致，已取消。
const COL = ['#f5a524', '#f5a524'];
const SHOW = [true, true];
// 默认即「适应全部高度范围」（y 取 YLIM_FULL），b4 按钮初始带 on 类：
// 早前默认纵轴只取 1040~1090（50 km 高），密集爬升段被放大成一根根竖线，观感很乱。
let onlyLast = false, showLaunch = true, fullY = true;
// 初始时间窗贴合真实数据（首点/末点各留 6 天），别写死 -20 让左边空一大片
const _T_FIRST = Math.min(...DATA.sats.map(s => s.p[0][0]));
const _T_LAST = Math.max(...DATA.sats.map(s => s.p[s.p.length - 1][0]));
let tMin = _T_FIRST - 6, tMax = _T_LAST + 6;
if (window.parent !== window) document.body.classList.add('emb');
let yMin = 700, yMax = 1120;    // = YLIM_FULL，与「适应全部高度范围」按钮初始态一致
let hoverLaunch = null, hover = null;

const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const tip = document.getElementById('tip');

const ST_COLOR = {'已抵达工作轨道': '#199a63', '已进入工作轨道（在轨调整）': '#0f9fb5', '爬升中': '#d0342c',
                  '入轨停泊（待抬升）': '#c9701a', '抬升中止（未达工作轨道）': '#7a52c7'};
const ST_SHORT = {'已抵达工作轨道': '已就位', '已进入工作轨道（在轨调整）': '在轨调整', '爬升中': '爬升中',
                  '入轨停泊（待抬升）': '入轨停泊', '抬升中止（未达工作轨道）': '抬升中止'};
const STATUS_ORDER = ['已抵达工作轨道', '已进入工作轨道（在轨调整）', '爬升中', '入轨停泊（待抬升）', '抬升中止（未达工作轨道）'];
const _stc = {};
DATA.sats.forEach(s => _stc[s.st] = (_stc[s.st] || 0) + 1);
document.getElementById('sub').textContent =
  '全部 ' + DATA.sats.length + ' 颗 · 数据截至 ' + DATA.now + ' UTC · ' +
  STATUS_ORDER.map(k => (ST_SHORT[k] || k) + ' ' + (_stc[k] || 0) + ' 颗').join(' · ');

document.getElementById('rstats').innerHTML =
  '运载火箭构成：' + Object.entries(DATA.rockets).sort((a, b) => b[1] - a[1])
    .map(([k, v]) => '<em>' + k + '</em> × ' + v).join('　·　') +
  '　|　发射场构成：' + (() => {
    const m = {}; DATA.launches.forEach(x => m[x.site] = (m[x.site] || 0) + 1);
    return Object.entries(m).map(([k, v]) => '<em>' + k + '</em> × ' + v).join('　·　');
  })() +
  '<br>每条发射对应的卫星组号即官方公布的「极轨XX组」。';

// ---------- 发射任务表格 ----------
const tbody = document.querySelector('#lauts tbody');
let _seq = 0;
DATA.launches.forEach(x => {
  _seq++;
  const pills = Object.entries(x.cnts || {}).filter(([k, v]) => v).map(([k, v]) =>
    '<span class="pill" style="--c:' + (ST_COLOR[k] || '#c9701a') + '" title="' + k + '">' +
    (ST_SHORT[k] || k) + ' ' + v + '</span>').join('');
  const reached = x.grp && (x._reached !== undefined);
  const tr = document.createElement('tr');
  tr.className = 's' + (DATA.sats.some(s => s.l === x.date && s.st.indexOf('已') !== 0) ? 1 : 0);
  tr.innerHTML = '<td class="c">' + String(_seq).padStart(2, '0') + '</td>' +
    '<td class="grp">' + x.grp + '</td><td class="num">' + x.bjt + '</td>' +
    '<td class="rk">' + x.rocket + '</td>' +
    '<td>' + x.site_s + '</td>' +
    '<td class="c">' + x.n + '</td><td class="mono">' + (x.cospar || '—') + '</td>' +
    '<td class="c">' + (x.plane || '—') + '</td>' +
    '<td>' + pills + '</td>';
  tr.addEventListener('mouseenter', () => { hoverLaunch = x.t; draw(); });
  tr.addEventListener('mouseleave', () => { hoverLaunch = null; draw(); });
  tr.addEventListener('click', () => { focusLaunch(x.t); bpOpen(x, tr); });  // 同时在大图下方单独出该组次的爬升图
  tbody.appendChild(tr);
});

const allT = DATA.sats.flatMap(s => s.p.map(q => q[0]));
const TLIM = [Math.min(...allT), Math.max(...allT) + 6];
const YLIM_FULL = [700, 1120];
const YLIM_ZOOM = [1040, 1090];

// ---------- 时间范围滑块（拖两端改窗口 / 拖中段平移） ----------
// 量程 = 全部数据范围（首末点各留 6 天），与「双击复位」的取值完全一致；
// 滚轮缩放、双击复位、点表格行聚焦发射都会把窗口变化反向同步成把手位置。
const tbar = document.getElementById('tbar');
const tsel = document.getElementById('tsel');
const thL = document.getElementById('thL'), thR = document.getElementById('thR');
const SLIM = [TLIM[0] - 6, TLIM[1] + 6];
const SL_MIN = Math.max(3, (SLIM[1] - SLIM[0]) * 0.02);   // 最小时间窗（天），保证把手可抓
tMin = SLIM[0]; tMax = SLIM[1];
let _drag = null, _x0 = 0, _a0 = 0, _b0 = 0;
const _sbw = () => tbar.clientWidth || 1;
function syncTbar() {
  const w = _sbw(), sp = SLIM[1] - SLIM[0];
  const a = Math.max(0, Math.min(1, (tMin - SLIM[0]) / sp));
  const b = Math.max(0, Math.min(1, (tMax - SLIM[0]) / sp));
  thL.style.left = (a * w) + 'px'; thR.style.left = (b * w) + 'px';
  tsel.style.left = (a * w) + 'px'; tsel.style.width = Math.max(0, (b - a) * w) + 'px';
}
function clampT() {                        // 把窗口夹回量程内（滚轮向外缩到底 = 整段）
  const sp = SLIM[1] - SLIM[0], w = tMax - tMin;
  if (w >= sp) { tMin = SLIM[0]; tMax = SLIM[1]; return; }
  if (tMin < SLIM[0]) { tMin = SLIM[0]; tMax = tMin + w; }
  if (tMax > SLIM[1]) { tMax = SLIM[1]; tMin = tMax - w; }
}
function _sbDown(which, e) {
  _drag = which; _x0 = e.clientX; _a0 = tMin; _b0 = tMax;
  e.preventDefault(); e.stopPropagation(); document.body.style.cursor = 'ew-resize';
}
thL.addEventListener('pointerdown', e => _sbDown('L', e));
thR.addEventListener('pointerdown', e => _sbDown('R', e));
tsel.addEventListener('pointerdown', e => _sbDown('M', e));
tbar.addEventListener('pointerdown', e => { if (_drag === null) _sbDown('M', e); });
window.addEventListener('pointermove', e => {
  if (!_drag) return;
  const sp = SLIM[1] - SLIM[0], dt = (e.clientX - _x0) / _sbw() * sp;
  if (_drag === 'M') { tMin = _a0 + dt; tMax = _b0 + dt; clampT(); }
  else if (_drag === 'L') { tMin = Math.min(_a0 + dt, tMax - SL_MIN); if (tMin < SLIM[0]) tMin = SLIM[0]; }
  else { tMax = Math.max(_b0 + dt, tMin + SL_MIN); if (tMax > SLIM[1]) tMax = SLIM[1]; }
  draw();
});
window.addEventListener('pointerup', () => { _drag = null; document.body.style.cursor = ''; });
window.addEventListener('pointercancel', () => { _drag = null; document.body.style.cursor = ''; });

const css = {padL: 74, padR: 168, padT: 18, padB: 52};
let W = 0, H = 0, dpr = window.devicePixelRatio || 1;

function resize() {
  const w = cv.parentElement.clientWidth - 20;
  const h = Math.max(520, Math.min(980, w * 0.52));
  cv.style.height = h + 'px';
  cv.width = w * dpr; cv.height = h * dpr;
  W = w; H = h;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}
window.addEventListener('resize', resize);

const X = t => css.padL + (t - tMin) / (tMax - tMin) * (W - css.padL - css.padR);
const Y = a => H - css.padB - (a - yMin) / (yMax - yMin) * (H - css.padT - css.padB);
const invX = x => tMin + (x - css.padL) / (W - css.padL - css.padR) * (tMax - tMin);
const invY = y => yMin + (H - css.padB - y) / (H - css.padT - css.padB) * (yMax - yMin);

const MON = ['01','02','03','04','05','06','07','08','09','10','11','12'];
function fmtDate(t) {
  const d = new Date(new Date(DATA.t0).getTime() + t * 86400000);
  return d.getUTCFullYear() + '-' + MON[d.getUTCMonth()] + '-' + String(d.getUTCDate()).padStart(2, '0');
}

function draw() {
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, W, H);
  const L = css.padL, R = W - css.padR, T = css.padT, B = H - css.padB;

  // 网格 + Y 轴
  ctx.font = '12px "Microsoft YaHei",sans-serif'; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  const yTicks = 8;
  for (let i = 0; i <= yTicks; i++) {
    const a = yMin + (yMax - yMin) * i / yTicks, y = Y(a);
    ctx.strokeStyle = '#e4eaf2'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(R, y); ctx.stroke();
    ctx.fillStyle = '#6b7686'; ctx.fillText(a.toFixed(0), L - 10, y);
  }
  ctx.save(); ctx.translate(18, (T + B) / 2); ctx.rotate(-Math.PI / 2);
  ctx.textAlign = 'center'; ctx.fillStyle = '#39424f'; ctx.font = '13px "Microsoft YaHei",sans-serif';
  ctx.fillText('平均轨道高度（km）', 0, 0); ctx.restore();

  // 工作轨道带
  [[DATA.target, COL[0]]].forEach(([tg, c]) => {
    if (tg < yMin - 25 || tg > yMax + 25) return;
    const y = Y(tg);
    ctx.fillStyle = c + '22'; ctx.fillRect(L, y - 1.5, R - L, 3);
    ctx.strokeStyle = c; ctx.lineWidth = 1.8; ctx.setLineDash([7, 5]);
    ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(R, y); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = c; ctx.font = 'bold 13px "Microsoft YaHei",sans-serif'; ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
    ctx.fillText('工作轨道 ' + tg.toFixed(1) + ' km', R + 8, y - 3);
  });

  // 时间轴：跨度大用「月」刻度、小用「日」刻度。步长必须落在整月/整天上，
  // 并按像素间距去重——早前步长取 0.5「月」→ 月份索引变小数 → MON[0.5]=undefined，
  // 且 Date.UTC(yy, 0.5, 1) 全落到同一天，刻度重叠成一团。
  ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.font = '12px "Microsoft YaHei",sans-serif';
  const span = tMax - tMin, t00 = new Date(DATA.t0).getTime();
  let lastTickX = -1e9;
  const tick = (t, label) => {
    const x = X(t);
    if (x - lastTickX < 62) return;       // 间距不足就跳过，防重叠
    ctx.strokeStyle = '#e9eef5'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, T); ctx.lineTo(x, B); ctx.stroke();
    ctx.fillStyle = '#6b7686';
    ctx.fillText(label, x, B + 9);
    lastTickX = x;
  };
  if (span > 420) {                       // 月刻度
    const stepM = span > 900 ? 2 : 1;
    const d0 = new Date(DATA.t0);
    let m = d0.getUTCFullYear() * 12 + d0.getUTCMonth();
    while (true) {
      const yy = Math.floor(m / 12), mm = m % 12;
      const t = (Date.UTC(yy, mm, 1) - t00) / 86400000;
      if (t > tMax) break;
      if (t >= tMin) tick(t, yy + '-' + MON[mm]);
      m += stepM;
    }
  } else {                                // 日刻度
    const stepD = span > 150 ? 14 : span > 60 ? 7 : 2;
    let t = Math.ceil(tMin / stepD) * stepD;
    while (t <= tMax) {
      if (t >= tMin) {
        const d = new Date(t00 + t * 86400000);
        tick(t, (d.getUTCMonth() + 1) + '/' + d.getUTCDate());
      }
      t += stepD;
    }
  }
  ctx.strokeStyle = '#c8d2df'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(L, B); ctx.lineTo(R, B); ctx.stroke();
  ctx.fillStyle = '#39424f'; ctx.font = '13px "Microsoft YaHei",sans-serif';
  ctx.fillText('时间（UTC）', (L + R) / 2, B + 30);

  // 悬停发射列的竖向高亮带（置于曲线之下）
  if (hoverLaunch !== null) {
    const px = X(hoverLaunch);
    ctx.fillStyle = 'rgba(47,85,151,.09)';
    ctx.fillRect(px - 9, T, 18, B - T);
  }

  // 曲线（裁剪在绘图区内）
  ctx.save();
  ctx.beginPath(); ctx.rect(L, T, R - L, B - T); ctx.clip();

  const order = DATA.sats.map((s, i) => i).filter(i => SHOW[DATA.sats[i].s]);
  order.sort((a, b) => DATA.sats[a].l < DATA.sats[b].l ? -1 : 1);
  order.forEach(i => {
    const s = DATA.sats[i], col = COL[s.s];
    ctx.strokeStyle = col + '66'; ctx.lineWidth = 1.05;
    ctx.beginPath();
    let pen = false;                        // ★ 跳过的点必须重新 moveTo，
    for (let k = 0; k < s.p.length; k++) {  //   否则缩放后视口外的点会让线段跨星连线
      const q = s.p[k];
      if (q[0] < tMin - 5 || q[0] > tMax + 5) { pen = false; continue; }
      const x = X(q[0]), y = Y(q[1]);
      if (pen) { ctx.lineTo(x, y); } else { ctx.moveTo(x, y); pen = true; }
    }
    ctx.stroke();
  });
  if (hoverLaunch !== null) {
    order.forEach(i => {
      const s = DATA.sats[i];
      const lt = (new Date(s.l + 'T00:00:00Z').getTime() - new Date(DATA.t0).getTime()) / 86400000;
      if (Math.abs(lt - hoverLaunch) > 0.5) return;
      ctx.strokeStyle = COL[s.s]; ctx.lineWidth = 2.6;
      ctx.beginPath();
      s.p.forEach((q, k) => { const x = X(q[0]), y = Y(q[1]); k === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); });
      ctx.stroke();
    });
  }
  order.forEach(i => {
    const s = DATA.sats[i];
    ctx.fillStyle = COL[s.s];                 // 末点圆点同用主题色
    ctx.beginPath(); ctx.arc(X(s.p[s.p.length - 1][0]), Y(s.a), onlyLast ? 3.1 : 2.4, 0, 6.284); ctx.fill();
  });

  const tnow = (new Date(DATA.now.replace(' ', 'T')) - new Date(DATA.t0)) / 86400000;
  if (tnow >= tMin && tnow <= tMax) {
    ctx.strokeStyle = '#b9c4d2'; ctx.lineWidth = 1.2; ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(X(tnow), T); ctx.lineTo(X(tnow), B); ctx.stroke(); ctx.setLineDash([]);
    const _lbl = '数据截至 ' + DATA.now.slice(0, 10);
    ctx.font = '11.5px "Microsoft YaHei",sans-serif';
    ctx.textAlign = 'right'; ctx.textBaseline = 'top';
    const _lw = ctx.measureText(_lbl).width;
    ctx.fillStyle = 'rgba(255,255,255,.88)';                 // 白底衬：避免被曲线穿过
    ctx.fillRect(X(tnow) - 6 - _lw - 5, T + 1, _lw + 9, 16);
    ctx.fillStyle = '#8b96a6';
    ctx.fillText(_lbl, X(tnow) - 6, T + 2);
  }
  ctx.restore();   // 结束曲线裁剪

  // 发射事件刻度 + 组号气泡
  if (showLaunch) {
    const rowsX = [-1e9, -1e9, -1e9, -1e9];
    DATA.launches.forEach(x => {
      if (x.t < tMin || x.t > tMax) return;
      const px = X(x.t), c = '#2f5597', hl = (hoverLaunch === x.t);
      ctx.strokeStyle = c; ctx.globalAlpha = hl ? 1 : 0.8; ctx.lineWidth = hl ? 4 : 2.4;
      ctx.beginPath(); ctx.moveTo(px, B - 1); ctx.lineTo(px, B - (hl ? 15 : 11)); ctx.stroke();
      ctx.globalAlpha = 1;
      const num = (x.grp.replace(/[^0-9]/g, '').slice(-2)) || x.grp;
      let row = 0, slack = -1e9;
      for (let i = 0; i < rowsX.length; i++) {
        const g = px - rowsX[i];
        if (g > 17) { row = i; break; }
        if (g > slack) { slack = g; row = i; }
      }
      rowsX[row] = px;
      const by = B - 23 - row * 15.5;
      ctx.beginPath(); ctx.arc(px, by, 7.2, 0, Math.PI * 2);
      ctx.fillStyle = hl ? c : '#fff'; ctx.fill();
      ctx.lineWidth = hl ? 2 : 1.2; ctx.strokeStyle = c; ctx.stroke();
      ctx.fillStyle = hl ? '#fff' : c;
      ctx.font = 'bold 9px "Microsoft YaHei",sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(num, px, by + 0.6);
    });
    ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    ctx.font = '11.5px "Microsoft YaHei",sans-serif';
    const _hint = '轴上气泡数字 = 发射组号（极轨 XX 组）；悬停刻度或表格行查看运载火箭与遥号';
    ctx.fillStyle = 'rgba(255,255,255,.88)';                  // 白底衬：曲线不穿字
    ctx.fillRect(L + 2, T + 2, ctx.measureText(_hint).width + 8, 17);
    ctx.fillStyle = '#8e9aa9';
    ctx.fillText(_hint, L + 4, T + 4);
  }
  syncTbar();                              // 重绘后同步滑块把手（缩放/复位/聚焦共用这条路径）
}

function focusLaunch(t) {
  const w = 110;
  tMin = t - w * 0.35; tMax = t + w * 0.65;
  yMin = YLIM_FULL[0]; yMax = YLIM_FULL[1];
  fullY = true;
  document.getElementById('b4').classList.add('on');
  draw();
}

function pickLaunch(mx) {
  let best = null;
  DATA.launches.forEach(x => {
    if (x.t < tMin || x.t > tMax) return;
    if (Math.abs(X(x.t) - mx) < 9) best = x;
  });
  return best;
}
function launchTip(x) {
  tip.innerHTML = '<b>' + x.grp + '（' + x.n + ' 颗卫星）</b>' +
    '<br>运载火箭：<span class="lv">' + x.rocket + '</span>' +
    '<br>发射场：' + x.site +
    '<br>发射时刻（北京时间）：' + x.bjt +
    '<br>目标轨道：1068.5 km（89° 极轨）';
}

function pick(mx, my) {
  let best = null, bd = 16;
  DATA.sats.forEach((s) => {
    if (!SHOW[s.s]) return;
    const p = s.p;
    let lo = 0, hi = p.length - 1;
    const t = invX(mx);
    if (t < p[0][0] || t > p[hi][0]) return;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; (p[mid][0] < t) ? lo = mid : hi = mid; }
    for (const q of [p[lo], p[hi]]) {
      const dx = X(q[0]) - mx, dy = Y(q[1]) - my, d = Math.hypot(dx, dy);
      if (d < bd) { bd = d; best = { s, q }; }
    }
  });
  return best;
}

/* ---- 悬停提示自适应定位（2026-10-10）-----------------------------------
   早前这里是 Math.min(clientX + 16, innerWidth - 320) 这类「估一个最大宽度」的钳制，
   大字号的提示框比估的宽、鼠标又常停在图右侧/下方，整框会被窗口边缘裁掉。
   现在按提示框实测尺寸摆放：默认贴鼠标右下；右侧装不下整框就翻到鼠标左侧，
   下方装不下就翻到上方；仍超出（框比视口还大）时最后夹住，保证整框始终可见。 */
function placeTip(tip, cx, cy, dx, dy){
  if (dx == null) dx = 16;
  if (dy == null) dy = 16;
  const m = 8;                                   // 与视口边缘的最小间距
  const w = tip.offsetWidth, h = tip.offsetHeight;
  const vw = window.innerWidth, vh = window.innerHeight;
  let left = cx + dx;                            // 默认：鼠标右侧
  if (left + w > vw - m) left = cx - w - dx;     // 右侧装不下 → 翻到左侧
  if (left < m) left = m;                        // 左侧也越界（鼠标在视口外）→ 贴左
  if (left + w > vw - m) left = Math.max(m, vw - w - m);   // 仍旧超出（框很宽）→ 贴右
  let top = cy + dy;                             // 默认：鼠标下方
  if (top + h > vh - m) {                        // 下方装不下
    const up = cy - h - dy;
    top = (up >= m) ? up : Math.max(m, (vh - h) / 2);      // 放得下就翻上去，否则尽量垂直居中
  }
  if (top < m) top = m;                          // 上方越界（鼠标在视口外）→ 贴顶
  if (top + h > vh - m) top = Math.max(m, vh - h - m);     // 仍旧超出（框很高）→ 贴底
  tip.style.left = left + 'px';
  tip.style.top = top + 'px';
}

cv.addEventListener('mousemove', e => {
  const r = cv.getBoundingClientRect();
  const mx = e.clientX - r.left, my = e.clientY - r.top;

  const lx = pickLaunch(mx);
  if (lx) {
    if (hoverLaunch !== lx.t) { hoverLaunch = lx.t; draw(); }
    launchTip(lx);
    tip.style.display = 'block';
    placeTip(tip, e.clientX, e.clientY);
    hover = null;
    return;
  }
  if (hoverLaunch !== null) { hoverLaunch = null; draw(); }

  const h = pick(mx, my);
  if (h !== hover) { hover = h; draw(); if (h) highlight(h); }
  if (h) {
    tip.style.display = 'block';
    tip.innerHTML = '<b>' + h.s.n + '</b><br>NORAD ' + h.s.c + '　倾角 ' + (h.s.inc != null ? h.s.inc.toFixed(2) : '—') + '°' +
      '<br>发射 ' + h.s.l + '　目标轨道 ' + h.s.t.toFixed(1) + ' km' +
      '<br>' + fmtDate(h.q[0]) + '　高度 <b style="color:' + (ST_COLOR[h.s.st] || COL[h.s.s]) + '">' + h.q[1].toFixed(1) + ' km</b>' +
      '<br>入轨 ' + h.s.f.toFixed(1) + ' km → 当前 ' + h.s.a.toFixed(1) + ' km' +
      '<br>抵达工作轨道：' + h.s.d + (typeof h.s.k === 'number' ? '（' + h.s.k + ' 天）' : '') +
      '<br><span style="color:#8fd6a0">' + h.s.st + '</span>';
    placeTip(tip, e.clientX, e.clientY);
  } else tip.style.display = 'none';
});
function highlight(h) {
  const s = h.s;
  ctx.strokeStyle = COL[s.s]; ctx.lineWidth = 2.6;
  ctx.beginPath();
  s.p.forEach((q, k) => { const x = X(q[0]), y = Y(q[1]); k === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); });
  ctx.stroke();
  ctx.fillStyle = '#fff'; ctx.strokeStyle = COL[s.s]; ctx.lineWidth = 2.2;
  ctx.beginPath(); ctx.arc(X(h.q[0]), Y(h.q[1]), 5.2, 0, 6.284); ctx.fill(); ctx.stroke();
}
cv.addEventListener('mouseleave', () => { hover = null; hoverLaunch = null; tip.style.display = 'none'; draw(); });
cv.addEventListener('wheel', e => {
  e.preventDefault();
  const r = cv.getBoundingClientRect();
  const mx = e.clientX - r.left, my = e.clientY - r.top;
  const f = e.deltaY > 0 ? 1.18 : 1 / 1.18;
  if (e.shiftKey) {
    const c = invY(my); yMin = c + (yMin - c) * f; yMax = c + (yMax - c) * f;
  } else {
    const c = invX(mx); tMin = c + (tMin - c) * f; tMax = c + (tMax - c) * f;
    clampT();                              // 时间轴向外缩到底即整段，滑块与图保持一致
  }
  draw();
}, { passive: false });

const b2 = document.getElementById('b2');
b2.onclick = () => { onlyLast = !onlyLast; b2.classList.toggle('on', onlyLast); draw(); };
const b3 = document.getElementById('b3');
b3.onclick = () => { showLaunch = !showLaunch; b3.classList.toggle('on', showLaunch); draw(); };
const b4 = document.getElementById('b4');
b4.onclick = () => {
  fullY = !fullY; b4.classList.toggle('on', fullY);
  yMin = fullY ? YLIM_FULL[0] : YLIM_ZOOM[0];
  yMax = fullY ? YLIM_FULL[1] : YLIM_ZOOM[1];
  draw();
};
window.addEventListener('dblclick', () => { tMin = TLIM[0] - 6; tMax = TLIM[1] + 6; draw(); });

resize();
</script>
</body>
</html>
"""

# 每颗星的分组标记：已就位(含在轨调整)=0 / 未就位=1
for x in sats:
    x['s'] = 0 if x['st'] in REACHED else 1

# 一次写两处：查看器静态目录（打包进 exe）+ 交付物目录，避免重跑后还要手动搬
HTML_OUT = (HTML.replace('__DATA__', json.dumps(data, ensure_ascii=False, separators=(',', ':')))
                .replace('__CARDS__', CARDS))
# 「组次单独爬升图」面板：共用片段，插在大图之后、页脚之前（千帆 / 国网两版注入同一份文件，
# 保证两边行为完全一致；片段只用到本页已有的 DATA，所以离线打开也照常可用）
with open(os.path.join(HERE, 'raise_batch_panel.html'), encoding='utf-8') as _f:
    _BPANEL = _f.read()
assert HTML_OUT.count('<div class="foot">') == 1, '面板注入锚点不唯一'
HTML_OUT = HTML_OUT.replace('<div class="foot">', _BPANEL + '<div class="foot">', 1)
OUTS = [os.path.join(HERE, 'web', 'raise', '千帆星座轨道爬升交互大图.html')]
# ★ 2026-10-20：第二份输出（工作区里的「交付物\01_卫星轨道核查」）只在它**已经存在**时才写。
#   原来是无条件 makedirs(WS/交付物/01_...)，把工程单独 clone 到别处时会凭空在外面建目录、
#   甚至因为盘符不同直接抛异常。开源发布后这个路径对别人没有意义。
_DELIV01 = os.path.join(WS, '交付物', '01_卫星轨道核查')
if os.path.isdir(_DELIV01):
    OUTS.append(os.path.join(_DELIV01, '千帆星座轨道爬升交互大图.html'))
for out in OUTS:
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(HTML_OUT)
    print('saved', out, round(os.path.getsize(out) / 1024 / 1024, 2), 'MB')
print('sats', len(sats), 'launches', len(launch_list), 'rockets', rocket_stat)
print('status', {k: status_cnt.get(k, 0) for k in STATUS_ORDER})
