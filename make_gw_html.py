# -*- coding: utf-8 -*-
"""重建国网低轨（HULIANWANG DIGUI）轨道爬升交互大图 —— 只换数据载荷 + 修 JS。

背景（2026-10-03）：
  旧版国网大图由 `sat_orbit_check/analyze.py` + `make_html.py` 生成，数据来自
  `sat_orbit_check/cache/<catnr>.html`（CelesTrak graph-orbit-data 逐日页）。
  该端点 2026-10-03 起对所有出口都无响应，旧链路再也跑不动，
  结果国网大图一直停在 2026-09-26 / 186 颗 / 23 次发射（缺 GUOWANG 24/25/26 三批 27 颗）。

本脚本改为以「查看器自己的逐日库」为唯一数据源，与千帆大图同源，可随时重跑：
  数据源：
    - 查看器 data/sat_hist.json      （213 颗星网低轨逐日高度，服务端自动保持最新）
    - 查看器 cache/gw_metamap.json   （NORAD → COSPAR / 发射日期 / 任务组；缺失则回退用 launch 日期分组）
    - 查看器 data/gw_launches.json   （26 次低轨发射任务表，源自卫星百科）
  模板：沿用 9-27 版页面（双壳层配色 50°绿 #22c55e / 86.5°蓝 #257ddd、b0/b1 壳层按钮），
        只把 <div class="cards"> 与 const DATA 两处替换掉。

同时把千帆图上已修掉的 4 个前端缺陷同步到国网模板（两边同源同毛病）：
  ① 时间窗写死 tMin=-20 → 左边空一大片；改为贴合真实数据首/末点 ±6 天
  ② 月刻度步长取 0.5 个月 → MON[0.5] 变 undefined、Date.UTC(yy,0.5,1) 全落到同一天、
     刻度重叠成一团；改为「整月/整日分档 + 像素间距去重」
  ③ 曲线跳过视口外点时复用旧画笔 → 缩放后出现跨星假直线；加 pen 标志
  ④ 图内「数据截至…／气泡说明」文字无底衬，被曲线穿过；加白色底衬
"""
import os, re, json, collections, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)

T0 = dt.date(2024, 11, 25)      # ★ 本页时间轴原点（旧版沿用，勿改，否则整条时间轴错位）
BASE0 = dt.date(2024, 1, 1)     # data/sat_hist.json 里 t 的原点
SHIFT = (T0 - BASE0).days       # 329：库里 t → 本页 t'
MAXPTS = 520
T50, T86 = 1149.3, 1167.9       # 50°壳层 / 86.5°壳层 工作轨道高度（由稳定星中位数实测）
TOL = 2.0

# 干净模板：9-27 版页面（未打任何补丁）。固定从这里读，保证脚本可重复运行；
# 不要改成读 web/raise/ 的输出，否则第二次跑会因为补丁已存在而断言失败。
TEMPLATE = os.path.join(HERE, 'gw_template.html')
OUTS = [os.path.join(HERE, 'web', 'raise', '国网低轨卫星轨道爬升交互大图.html')]
# ★ 2026-10-20：同上（见 make_qf_html.py）—— 交付物目录只在开发机上已存在时才写，
#   否则独立 clone 的工程会凭空在外面建目录。
_DELIV01 = os.path.join(WS, '交付物', '01_卫星轨道核查')
if os.path.isdir(_DELIV01):
    OUTS.append(os.path.join(_DELIV01, '国网低轨卫星轨道爬升交互大图.html'))

H = json.load(open(os.path.join(HERE, 'data', 'sat_hist.json'), encoding='utf-8'))
# ★ 2026-10-28：发射任务表改成「卫星百科自动同步 + 人工表覆盖」合并，
#   新发射当天就能进表，不必再手补 gw_launches.json。
import launch_table
LA = launch_table.load_merged('gw', os.path.join(HERE, 'data', 'gw_launches.json'))

# ---------------------------------------------------------------- 元信息（COSPAR）
META = {}
for p in (os.path.join(HERE, 'cache', 'gw_metamap.json'),
          os.path.join(os.environ.get('LOCALAPPDATA', ''), 'ConstellationOrbitViewer',
                       'cache', 'gw_metamap.json')):
    try:
        META = json.load(open(p, encoding='utf-8')).get('map') or {}
    except Exception:
        META = {}
    if META:
        print('元信息：%s（%d 条）' % (p, len(META)))
        break
if not META:
    print('!! 未取到 metamap，COSPAR/批次将退化为按发射日期分组')

# ---------------------------------------------------------------- 真实倾角（本地 TLE，Alpha-5 安全）
_A5 = '0123456789ABCDEFGHJKLMNPQRSTUVWXYZ'


def alpha5(tok):
    tok = (tok or '').strip()
    if not tok:
        return None
    if tok[0].isdigit():
        return int(tok)
    v = _A5.find(tok[0])
    return None if v < 0 else v * 10000 + int(tok[1:5])


def load_incl():
    out = {}
    p = os.path.join(HERE, 'cache', 'gw.tle')
    if not os.path.exists(p):
        return out
    lines = open(p, encoding='utf-8').read().splitlines()
    for i in range(0, len(lines) - 2, 3):
        try:
            n = alpha5(lines[i + 2][2:7])
            if n:
                out[str(n)] = float(lines[i + 2][8:16])
        except Exception:
            pass
    return out


INCL = load_incl()
print('倾角：本地 TLE 解析出 %d 颗' % len(INCL))

# 现在时刻。★ sat_hist.json 的 now 由 datetime.now() 写成，是**本地时间**（北京），
# 而本页时间轴与「数据截至」标注都是 UTC，所以这里必须先换算成 UTC 再写进 DATA，
# 否则副标题会出现「17:19 UTC」这种差 8 小时的假标注。
NOW_LOCAL = dt.datetime.strptime(H['now'], '%Y-%m-%d %H:%M')
_off = dt.datetime.now().astimezone().utcoffset() or dt.timedelta(hours=8)
NOW = (NOW_LOCAL - _off).replace(microsecond=0)
NOWT = (NOW_LOCAL.date() - BASE0).days


def alt_at(pts, t):
    prev = None
    for tt, a in pts:
        if tt >= t:
            if prev is None:
                return a
            f = (t - prev[0]) / (tt - prev[0])
            return prev[1] + f * (a - prev[1])
        prev = (tt, a)
    return pts[-1][1]


# ---------------------------------------------------------------- 逐星
S_OK, S_CLIMB, S_PARK, S_STOP = ('已抵达工作轨道', '爬升中', '入轨停泊（待抬升）',
                                 '抬升中止（未达工作轨道）')
STATUS_ORDER = [S_OK, S_CLIMB, S_PARK, S_STOP]
STATUS_CLASS = {S_OK: 'green', S_CLIMB: 'red', S_PARK: 'amber', S_STOP: 'grey'}
REACHED = (S_OK,)

sats = []
for norad, e in H['sats'].items():
    if e.get('src') != 'gw' or not e.get('pts'):
        continue
    pts = sorted(e['pts'])
    if len(pts) > MAXPTS:                      # 降抽样，保尾点
        step = len(pts) / float(MAXPTS)
        pts = [pts[int(i * step)] for i in range(MAXPTS)] + [pts[-1]]
    cur = pts[-1][1]
    tgt = float(e.get('target') or 0)
    shell = 0 if '50' in (e.get('shell') or '') else 1
    if not tgt:
        tgt = T50 if shell == 0 else T86
    gap = tgt - cur

    # 稳定性：近 14 天高度基本不动（CelesTrak 高度分辨率 0.1 km）
    seg = [a for tt, a in pts if tt >= NOWT - 14]
    span14 = (max(seg) - min(seg)) if len(seg) > 1 else 0.0
    a14, a30 = alt_at(pts, NOWT - 14), alt_at(pts, NOWT - 30)
    # ★ 2026-10-15：**点数不够就不能判「稳定」**。刚发射的批次可能只写进来一个当前点
    #   （星网低轨27组：官方自 10-10 起没再发布根数）——单点必然「跨度 0、与 14 天前相等」，
    #   于是被误判成 S_STOP「抬升中止（未达工作轨道）」，而它其实才刚入轨、等着抬升。
    #   要求「≥5 个点且跨度 ≥7 天」才允许下「稳定」这个结论，否则落到 S_PARK（入轨停泊）。
    enough = len(pts) >= 5 and (pts[-1][0] - pts[0][0]) >= 7
    stable = enough and span14 < 0.6 and abs(cur - a14) < 0.4
    rate30 = (cur - a30) / 30.0

    if stable and abs(gap) <= TOL:
        st = S_OK
    elif stable and gap > TOL:
        st = S_STOP
    elif abs(gap) <= 8.0 or rate30 > 0.005:
        st = S_CLIMB
    else:
        st = S_PARK

    # 抵达日期：尾部连续落在工作轨道带内的起点
    d, k = '-', ''
    if st in REACHED:
        i = len(pts) - 1
        while i > 0 and pts[i - 1][1] >= tgt - TOL:
            i -= 1
        rd = BASE0 + dt.timedelta(days=int(pts[i][0]))
        d = rd.isoformat()
        try:
            k = (rd - dt.date.fromisoformat(e.get('launch') or '')).days
        except Exception:
            k = ''

    m = META.get(norad) or {}
    inc = INCL.get(norad)
    if inc is None:                            # 兜底：按壳层给标称倾角
        inc = 50.0 if shell == 0 else 86.5
    sats.append(dict(
        n=e.get('name') or ('DIGUI-' + norad), c=norad, s=shell,
        l=e.get('launch') or '', t=tgt, f=round(pts[0][1], 1), a=round(cur, 1),
        d=d, k=k, inc=round(inc, 2),
        ides=m.get('cospar') or '', st=st,
        cs=((m.get('cospar') or '')[:8]) or (e.get('launch') or ''),
        p=[[round(tt - SHIFT, 6), a2] for tt, a2 in pts]))

n_total = len(sats)
print('星网低轨 %d 颗（50°壳层 %d / 86.5°壳层 %d）'
      % (n_total, sum(1 for x in sats if x['s'] == 0), sum(1 for x in sats if x['s'] == 1)))

# ---------------------------------------------------------------- 发射任务（26 次低轨）
by_cs = collections.defaultdict(list)
for x in sats:
    if x['cs']:
        by_cs[x['cs']].append(x)
by_day = collections.defaultdict(list)
for x in sats:
    if x['l']:
        by_day[x['l']].append(x)

def cn_num(n):
    """1→一 … 10→十 … 11→十一 … 20→二十 … 95→九十五（遥号惯用中文数字）"""
    d = '零一二三四五六七八九'
    if n < 10:
        return d[n]
    if n == 10:
        return '十'
    if n < 20:
        return '十' + d[n % 10]
    return d[n // 10] + '十' + (d[n % 10] if n % 10 else '')


low = [m for m in LA if '低轨' in (m.get('mission') or '')]
launch_list = []
_used = set()
for m in sorted(low, key=lambda x: x.get('time') or ''):
    cs = m.get('cospar') or ''
    grp_sats = by_cs.get(cs, [])
    utc_dt = dt.datetime.strptime(m['time'], '%Y-%m-%d %H:%M') - dt.timedelta(hours=8)
    uday = utc_dt.strftime('%Y-%m-%d')
    if not grp_sats:                            # 回退：按 UTC 发射日（含 ±1 天跨午夜容差）
        grp_sats = by_day.get(uday, [])
        if not grp_sats:
            for cand in by_day:
                if cand in _used:
                    continue
                if abs((dt.date.fromisoformat(cand) - utc_dt.date()).days) <= 1:
                    grp_sats = by_day[cand]
                    uday = cand
                    break
    _used.add(uday)
    rk = (m.get('rocket') or '').strip()

    def _split(s):
        """'长征八号甲 Y3' → ('长征八号甲', '遥三')"""
        mm = re.match(r'^(.*?)\s+([Yy]\d+)$', s.strip())
        if mm:
            return mm.group(1).strip(), '遥' + cn_num(int(mm.group(2)[1:]))
        return s.strip(), ''

    parts = [p.strip() for p in rk.split('/')]
    lv, sn = _split(parts[0])
    up = ''
    if len(parts) > 1:                                  # 上面级 "远征二号 Y2" → "远征二号遥二"
        ulv, usn = _split(parts[1])
        up = (ulv + usn).strip()
    cnts = collections.Counter(x['st'] for x in grp_sats)
    # ★ 2026-10-15：**官方编目数 ≠ 可画曲线数**，两者都要如实给出来。
    #   星网低轨27组是一箭九星，CelesTrak 的 records.php 也把这 9 个都列为 PAYLOAD，
    #   但 100975(C) / 100976(D) 至今没有任何 GP 根数（records.php 有、gp.php 回
    #   「No GP data found」，Space-Track 侧一样没有）⇒ 只画得出 7 条。
    #   表里写成「7/9」，既不把没根数的两颗算进来，也不假装这发只有 7 颗。
    n_all = 0
    if cs:
        n_all = sum(1 for _m in META.values() if ((_m.get('cospar') or '')[:8] == cs))
    launch_list.append(dict(
        grp=re.sub(r'^星网', '', m['mission']).strip(),
        lv=lv, sn=sn, up=up, site=(m.get('site') or '').strip(), pad='',
        utc=utc_dt.strftime('%H:%M'), bjt=m['time'],
        date=uday, n=len(grp_sats), na=max(n_all, len(grp_sats)),
        shell=grp_sats[0]['s'] if grp_sats else 0,
        t=(dt.date.fromisoformat(uday) - T0).days,
        cospar=cs or '—', cnts=dict(cnts),
        rocket=(lv + ' ' + sn + ('（' + up + '）' if up else '')).strip(),
        site_s=(m.get('site') or '').strip()))

_miss = [x for x in launch_list if x['n'] == 0]
if _miss:
    print('!! 有 %d 次发射没匹配到卫星：%s' % (len(_miss), [x['grp'] for x in _miss]))
print('发射任务 %d 次，卫星合计 %d' % (len(launch_list), sum(x['n'] for x in launch_list)))

rocket_stat = collections.Counter(x['lv'] for x in launch_list)

# ---------------------------------------------------------------- 卡片
status_cnt = collections.Counter(x['st'] for x in sats)


def _card(key, val, unit, cls=''):
    return ('<div class="card %s"><div class="k">%s</div><div class="v">%s '
            '<span class="u">%s</span></div></div>' % (cls, key, val, unit))


n50 = sum(1 for x in sats if x['s'] == 0)
n86 = sum(1 for x in sats if x['s'] == 1)

cards = [_card('在轨卫星总数', n_total, '颗')]
# ★ 2026-10-03 用户追加：两个倾角壳层各自多少颗，紧跟在「总数」之后
#   （与副标题的排列顺序一致：全部 N 颗 → 50° N 颗 → 86.5° N 颗 → 各状态）
cards.append(_card('50° 倾角壳层', n50, '颗', 'sh50'))
cards.append(_card('86.5° 倾角壳层', n86, '颗', 'sh86'))
for kk in STATUS_ORDER:
    cards.append(_card(kk, status_cnt.get(kk, 0), '颗', STATUS_CLASS[kk]))
cards.append(_card('发射任务', len(launch_list), '次'))
CARDS = '\n'.join(cards)

data = dict(t0=T0.isoformat(), now=NOW.strftime('%Y-%m-%d %H:%M'), t50=T50, t86=T86,
            n50=n50, n86=n86, sats=sats, launches=launch_list, rockets=dict(rocket_stat))

# ---------------------------------------------------------------- 模板补丁
html = open(TEMPLATE, encoding='utf-8').read()
orig = html

# ① 时间窗贴合数据
old = ("let tMin = -20, tMax = (new Date(DATA.now.replace(' ', 'T')) "
       "- new Date(DATA.t0)) / 86400000 + 10;")
new = ("// 初始时间窗贴合真实数据（首点/末点各留 6 天），别写死 -20 让左边空一大片\n"
       "const _T_FIRST = Math.min(...DATA.sats.map(s => s.p[0][0]));\n"
       "const _T_LAST = Math.max(...DATA.sats.map(s => s.p[s.p.length - 1][0]));\n"
       "let tMin = _T_FIRST - 6, tMax = _T_LAST + 6;")
assert html.count(old) == 1, '① 时间窗锚点未命中'
html = html.replace(old, new)

# ② 刻度：整月/整日分档 + 像素间距去重
old = """  const span = tMax - tMin, stepM = span > 500 ? 2 : (span > 240 ? 1 : 0.5);
  const d0 = new Date(DATA.t0);
  let m = d0.getUTCFullYear() * 12 + d0.getUTCMonth();
  while (true) {
    const yy = Math.floor(m / 12), mm = m % 12;
    const t = (Date.UTC(yy, mm, 1) - new Date(DATA.t0).getTime()) / 86400000;
    if (t > tMax) break;
    if (t >= tMin) {
      const x = X(t);
      ctx.strokeStyle = '#e9eef5'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, T); ctx.lineTo(x, B); ctx.stroke();
      ctx.fillStyle = '#6b7686';
      ctx.fillText(yy + '-' + MON[mm], x, B + 9);
    }
    m += stepM;
  }"""
new = """  // 时间轴：跨度大用「月」刻度、小用「日」刻度。步长必须落在整月/整天上，
  // 并按像素间距去重——早前步长取 0.5「月」→ 月份索引变小数 → MON[0.5]=undefined，
  // 且 Date.UTC(yy, 0.5, 1) 全落到同一天，刻度重叠成一团。
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
  }"""
assert html.count(old) == 1, '② 刻度锚点未命中'
html = html.replace(old, new)

# ③ 曲线跳过视口外点时重置画笔
old = """    for (let k = 0; k < s.p.length; k++) {
      const q = s.p[k];
      if (q[0] < tMin - 5 || q[0] > tMax + 5) continue;
      const x = X(q[0]), y = Y(q[1]);
      k === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }"""
new = """    let pen = false;                        // ★ 跳过的点必须重新 moveTo，
    for (let k = 0; k < s.p.length; k++) {  //   否则缩放后视口外的点会让线段跨星连线
      const q = s.p[k];
      if (q[0] < tMin - 5 || q[0] > tMax + 5) { pen = false; continue; }
      const x = X(q[0]), y = Y(q[1]);
      if (pen) { ctx.lineTo(x, y); } else { ctx.moveTo(x, y); pen = true; }
    }"""
assert html.count(old) == 1, '③ 曲线锚点未命中'
html = html.replace(old, new)

# ④「数据截至」白底衬
old = """    ctx.fillStyle = '#8b96a6'; ctx.font = '11.5px "Microsoft YaHei",sans-serif';
    ctx.textAlign = 'right'; ctx.textBaseline = 'top';
    ctx.fillText('数据截至 ' + DATA.now.slice(0, 10), X(tnow) - 6, T + 2);"""
new = """    const _lbl = '数据截至 ' + DATA.now.slice(0, 10);
    ctx.font = '11.5px "Microsoft YaHei",sans-serif';
    ctx.textAlign = 'right'; ctx.textBaseline = 'top';
    const _lw = ctx.measureText(_lbl).width;
    ctx.fillStyle = 'rgba(255,255,255,.88)';                 // 白底衬：避免被曲线穿过
    ctx.fillRect(X(tnow) - 6 - _lw - 5, T + 1, _lw + 9, 16);
    ctx.fillStyle = '#8b96a6';
    ctx.fillText(_lbl, X(tnow) - 6, T + 2);"""
assert html.count(old) == 1, '④ 数据截至锚点未命中'
html = html.replace(old, new)

# ⑤ 轴上气泡说明白底衬
old = """    ctx.font = '11.5px "Microsoft YaHei",sans-serif'; ctx.fillStyle = '#8e9aa9';
    ctx.fillText('轴上气泡数字 = 发射组号（低轨 XX 组）；悬停刻度或表格行查看运载火箭与遥号', L + 4, T + 4);"""
new = """    ctx.font = '11.5px "Microsoft YaHei",sans-serif';
    const _hint = '轴上气泡数字 = 发射组号（低轨 XX 组）；悬停刻度或表格行查看运载火箭与遥号';
    ctx.fillStyle = 'rgba(255,255,255,.88)';                  // 白底衬：曲线不穿字
    ctx.fillRect(L + 2, T + 2, ctx.measureText(_hint).width + 8, 17);
    ctx.fillStyle = '#8e9aa9';
    ctx.fillText(_hint, L + 4, T + 4);"""
assert html.count(old) == 1, '⑤ 气泡说明锚点未命中'
html = html.replace(old, new)

# ⑥ 换 DATA
html, n = re.subn(r'const DATA = \{.*?\};\n', 'const DATA = __DATA__;\n', html,
                  count=1, flags=re.S)
assert n == 1, '⑥ DATA 段未命中'
html = html.replace('__DATA__', json.dumps(data, ensure_ascii=False, separators=(',', ':')))

# ⑦ 换卡片
i = html.index('<div class="cards">')
j = html.index('<div class="panel">', i)
html = html[:i] + '<div class="cards">\n' + CARDS + '\n</div>\n\n' + html[j:]

# ⑧ 发射表「卫星数」列：官方编目数 > 可画曲线数时补一个「/N」上标（见 n_all 的说明）。
#    ★ 2026-10-15：星网低轨27组一箭九星，但官方只发布了 7 颗的根数 ⇒ 显示「7/9」。
old = ("    '<td class=\"c\">' + x.n + '</td><td class=\"mono\">' + (x.cospar || '—') + '</td>' +")
new = ("    '<td class=\"c\">' + x.n + (x.na && x.na > x.n\n"
       "      ? '<sup style=\"color:#c9701a;font-weight:700;margin-left:1px;cursor:help\" title=\"'\n"
       "        + '官方编目 ' + x.na + ' 颗；其中 ' + x.n + ' 颗已发布根数（能画曲线），'\n"
       "        + (x.na - x.n) + ' 颗至今没有任何 GP 根数（官方尚未发布）\">/' + x.na + '</sup>'\n"
       "      : '') +\n"
       "    '</td><td class=\"mono\">' + (x.cospar || '—') + '</td>' +")
assert html.count(old) == 1, '⑧b 卫星数列锚点未命中'
html = html.replace(old, new)

# ⑨ 「组次单独爬升图」面板：与千帆版注入同一份共用片段（保证两版行为完全一致）。
#    放在所有正则补丁之后，免得片段里的内容被前面的锚点误命中。
with open(os.path.join(HERE, 'raise_batch_panel.html'), encoding='utf-8') as _f:
    _BPANEL = _f.read()
assert html.count('<div class="foot">') == 1, '⑧ 面板注入锚点不唯一'
html = html.replace('<div class="foot">', _BPANEL + '<div class="foot">', 1)

for out in OUTS:
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)
    print('saved', out, round(os.path.getsize(out) / 1024 / 1024, 2), 'MB')

print('sats =', n_total, ' launches =', len(launch_list))
print('status =', {kk: status_cnt.get(kk, 0) for kk in STATUS_ORDER})
print('rockets =', dict(rocket_stat))
