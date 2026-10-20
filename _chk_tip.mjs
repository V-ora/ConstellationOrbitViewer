// 悬停提示「自适应定位」专项自检（2026-10-10）
//
// 背景：用户截图 —— 鼠标停在组次爬升图右侧时，提示框被窗口右边缘裁掉，数值只剩「· 10」。
//       根因是定位写死成 Math.min(clientX + 14, innerWidth - 200) 这类「估一个最大宽度」的钳制，
//       而组次图提示框最多 14 行、比估的宽。
// 检查项：
//   T1 全部源码/副本里再没有旧的写死钳制，且都定义了 placeTip；
//   T2 用真实尺寸（组次图最多 14 行）在 5 个鼠标位置 × 3 种窗口尺寸下摆放，
//      提示框必须完整落在视口内（不裁切）；
//   T3 贴右边缘时确实「翻到鼠标左侧」、贴下边缘时确实「翻到上方」（不是硬夹）；
//   T4 端到端：真的打开组次面板、真的把鼠标移到画布最右侧，提示框仍完整可见；
//   T5 端到端：大图主图悬停同样不越界；
//   T6 框比窗口还高（极端输入）时至少不跑出视口左上。
// 用法：node _chk_tip.mjs
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const ROOT = 'D:/Work Buddy 工作区/星座轨道实时查看器';
const EDGE = process.env.MSEDGE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9339;

const PAGES = [
  ['千帆大图', path.join(ROOT, 'web/raise/千帆星座轨道爬升交互大图.html'), true],
  ['国网大图', path.join(ROOT, 'web/raise/国网低轨卫星轨道爬升交互大图.html'), true],
  ['组次页 batch.html', path.join(ROOT, 'web/raise/batch.html'), false],
  ['单星页 sat.html', path.join(ROOT, 'web/raise/sat.html'), false],
];
// 可选：用 TIP_EXTRA="标签|URL;标签|URL" 追加要测的**线上**页面
// 例：TIP_EXTRA="组次页(线上)|http://127.0.0.1:8770/web/raise/batch.html?group=qianfan&batch=2026-211"
for (const s of (process.env.TIP_EXTRA || '').split(';')) {
  const i = s.indexOf('|');
  if (i > 0) PAGES.push([s.slice(0, i), s.slice(i + 1), false]);
}
// 源码里必须不再出现的旧钳制（200/170/215/250/320 是各页面当初写死的「估计宽度」）
const BAD_SNIPPETS = [
  'window.innerWidth - 200', 'window.innerWidth - 170', 'window.innerWidth - 215',
  'window.innerWidth - 250', 'window.innerWidth - 320',
];
const SCAN_FILES = [
  ...PAGES.map(p => p[1]).filter(f => !/^https?:/i.test(f)),
  path.join(ROOT, 'raise_batch_panel.html'),
  path.join(ROOT, 'gw_template.html'),
  path.join(ROOT, 'make_qf_html.py'),
  path.join(ROOT, 'web_nopass/raise/batch.html'),
  path.join(ROOT, 'web_nopass/raise/sat.html'),
  path.join(ROOT, 'web_nopass/raise/千帆星座轨道爬升交互大图.html'),
  path.join(ROOT, 'web_nopass/raise/国网低轨卫星轨道爬升交互大图.html'),
  path.join(ROOT, '..', '交付物/01_卫星轨道核查/千帆星座轨道爬升交互大图.html'),
  path.join(ROOT, '..', '交付物/01_卫星轨道核查/国网低轨卫星轨道爬升交互大图.html'),
];

let fail = 0;
const ok = (name, cond, extra = '') => {
  console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${extra ? '  | ' + extra : ''}`);
  if (!cond) fail++;
};

// ---------- T1 源码静态扫描（不需要浏览器） ----------
const dirty = [], missing = [];
for (const f of SCAN_FILES) {
  let txt;
  try { txt = readFileSync(f, 'utf8'); } catch { dirty.push(`${path.basename(f)}(读不到)`); continue; }
  for (const s of BAD_SNIPPETS) if (txt.includes(s)) dirty.push(`${path.basename(f)} → ${s}`);
  if (!txt.includes('function placeTip')) missing.push(path.basename(f));
}
ok('T1a 全部副本已无写死的提示框宽度钳制', dirty.length === 0, dirty.slice(0, 4).join(' ; '));
ok('T1b 全部副本都带 placeTip 自适应定位', missing.length === 0, missing.slice(0, 4).join(' ; '));

// ---------- 起浏览器 ----------
const prof = mkdtempSync(path.join(tmpdir(), 'edge-tip-'));
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-sandbox',
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`,
  '--window-size=1400,900', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => { try { edge.kill(); } catch { } });

const sleep = ms => new Promise(r => setTimeout(r, ms));
let target = null;
for (let i = 0; i < 60 && !target; i++) {
  try {
    const list = await (await fetch(`http://127.0.0.1:${DBG}/json`)).json();
    target = list.find(t => t.type === 'page');
  } catch { }
  if (!target) await sleep(300);
}
if (!target) { console.log('FATAL: CDP target 未就绪'); process.exit(1); }

const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

let mid = 0; const pend = new Map(); let errors = [];
ws.onmessage = ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); return; }
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error')
    errors.push((m.params.args || []).map(a => a.value ?? a.description ?? '').join(' ').slice(0, 200));
  if (m.method === 'Runtime.exceptionThrown')
    errors.push((m.params.exceptionDetails?.exception?.description ||
      m.params.exceptionDetails?.text || 'exception').slice(0, 200));
};
const send = (method, params = {}) => {
  const id = ++mid;
  return new Promise(res => { pend.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
};
const evl = async expr => (await send('Runtime.evaluate', { expression: expr, returnByValue: true }))
  .result?.result?.value;

await send('Runtime.enable');
await send('Page.enable');

// 真实内容：组次叠加图提示框最多 14 行，每行「名字 · 9999.9 km」
const TALL_HTML = '<b>2026-10-07</b><br>' + Array.from({ length: 14 }, (_, i) =>
  "<span style='display:inline-block;width:8px;height:8px;border-radius:50%;background:#e8890c;" +
  "margin-right:5px'></span>GUOWANG 24 OBJECT " + String.fromCharCode(65 + i) +
  ' · <b>1029.9</b> km<br>').join('');
// 极端输入：40 行（实际代码会切成 14 行，这里只为压一压边界）
const HUGE_HTML = '<b>2026-10-07</b><br>' + Array.from({ length: 40 }, (_, i) =>
  "<span style='display:inline-block;width:8px;height:8px;border-radius:50%;background:#2f7ddd;" +
  "margin-right:5px'></span>QIANFAN-" + (100 + i) + ' · <b>1068.5</b> km<br>').join('');

const VIEWPORTS = [[1400, 900], [1000, 520], [900, 650]];
const M = 8;   // 与 placeTip 里一致的视口边距

for (const [label, file, isBig] of PAGES) {
  errors = [];
  const pageUrl = /^https?:/i.test(file) ? file : pathToFileURL(file).href;
  await send('Page.navigate', { url: pageUrl });
  await sleep(1800);

  const has = await evl('typeof placeTip');
  ok(`${label}：定义了 placeTip`, has === 'function', `typeof=${has}`);
  if (has !== 'function') continue;

  for (const [vw, vh] of VIEWPORTS) {
    await send('Emulation.setDeviceMetricsOverride', {
      width: vw, height: vh, deviceScaleFactor: 1, mobile: false,
    });
    await sleep(300);

    const cases = [
      ['右侧贴边', 'vw - 4', 'vh / 2'],
      ['下方贴边', 'vw / 2', 'vh - 4'],
      ['右下角', 'vw - 4', 'vh - 4'],
      ['左上角', '2', '2'],
      ['正中', 'vw / 2', 'vh / 2'],
    ];
    for (const [name, cxE, cyE] of cases) {
      const raw = await evl(`(() => {
        const tip = document.getElementById('tip');
        tip.innerHTML = ${JSON.stringify(TALL_HTML)};
        tip.style.display = 'block';
        const vw = window.innerWidth, vh = window.innerHeight;
        const cx = ${cxE}, cy = ${cyE};
        placeTip(tip, cx, cy, 14, 14);
        const r = tip.getBoundingClientRect();
        return JSON.stringify({ vw, vh, cx, cy,
          left: r.left, top: r.top, right: r.right, bottom: r.bottom, w: r.width, h: r.height });
      })()`);
      const r = raw ? JSON.parse(raw) : null;
      const inside = !!r && r.left >= -0.5 && r.top >= -0.5 &&
        r.right <= r.vw + 0.5 && r.bottom <= r.vh + 0.5;
      ok(`${label} @${vw}x${vh} ${name}：框完整在视口内`, inside,
        r ? `rect=[${Math.round(r.left)},${Math.round(r.top)}~${Math.round(r.right)},${Math.round(r.bottom)}] ` +
            `vw/vh=${r.vw}/${r.vh} 框=${Math.round(r.w)}x${Math.round(r.h)}` : 'no result');
      if (!r) continue;

      // T3 贴边必须是「翻转」，而不是被硬夹到边缘
      if (name === '右侧贴边') {
        ok(`${label} @${vw}x${vh} 右侧贴边：翻到鼠标左侧`,
          r.left < r.cx - r.w * 0.5, `cx=${Math.round(r.cx)} left=${Math.round(r.left)} w=${Math.round(r.w)}`);
      }
      if (name === '下方贴边') {
        ok(`${label} @${vw}x${vh} 下方贴边：翻到鼠标上方`,
          r.top < r.cy - r.h * 0.5, `cy=${Math.round(r.cy)} top=${Math.round(r.top)} h=${Math.round(r.h)}`);
      }
    }

    // T6 极端高框：不可能整框放进窗口，只要求不跑出视口左上
    const rawH = await evl(`(() => {
      const tip = document.getElementById('tip');
      tip.innerHTML = ${JSON.stringify(HUGE_HTML)};
      tip.style.display = 'block';
      const vw = window.innerWidth, vh = window.innerHeight;
      placeTip(tip, vw / 2, vh / 2, 14, 14);
      const r = tip.getBoundingClientRect();
      return JSON.stringify({ vw, vh, left: r.left, top: r.top, right: r.right, bottom: r.bottom, h: r.height });
    })()`);
    const rh = rawH ? JSON.parse(rawH) : null;
    ok(`${label} @${vw}x${vh}：超高框不跑出视口左上`, !!rh &&
      rh.left >= -0.5 && rh.top >= -0.5 &&
      (rh.h <= rh.vh - 2 * M ? rh.bottom <= rh.vh + 0.5 : true),
      rh ? `rect=[${Math.round(rh.left)},${Math.round(rh.top)}~${Math.round(rh.right)},${Math.round(rh.bottom)}] ` +
           `框高=${Math.round(rh.h)} vh=${rh.vh}` : 'no result');
  }

  // ---------- 端到端：真开面板 / 真移鼠标 ----------
  if (isBig) {
    await send('Emulation.setDeviceMetricsOverride', {
      width: 1400, height: 900, deviceScaleFactor: 1, mobile: false,
    });
    await sleep(300);

    // T4 打开组次面板，把鼠标移到画布绘图区最右端
    const e2e = await evl(`(() => {
      const tr = document.querySelector('#lauts tbody tr:nth-child(3)');
      if (!tr) return JSON.stringify({ err: '没有第 3 行' });
      tr.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      const panel = document.getElementById('batchp');
      if (!panel || panel.style.display === 'none') return JSON.stringify({ err: '面板没打开' });
      panel.scrollIntoView({ block: 'start' });
      const cv = document.getElementById('bpCv');
      const wrap = document.getElementById('bpWrap');
      if (!cv || !wrap || wrap.style.display === 'none') return JSON.stringify({ err: 'bpWrap 隐藏（该行无数据？）' });
      const r = cv.getBoundingClientRect();
      if (r.width < 200) return JSON.stringify({ err: 'canvas 太窄 ' + Math.round(r.width) });
      const clientX = r.right - 25;            // 落在绘图区右端（L=66 … W-20 之内）
      const clientY = r.top + r.height / 2;
      cv.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX, clientY }));
      const tip = document.getElementById('tip');
      const t = tip.getBoundingClientRect();
      return JSON.stringify({ vw: window.innerWidth, vh: window.innerHeight,
        shown: tip.style.display === 'block', clientX, cvRight: r.right,
        left: t.left, top: t.top, right: t.right, bottom: t.bottom, w: t.width, h: t.height });
    })()`);
    const e = e2e ? JSON.parse(e2e) : null;
    if (!e || e.err) ok(`${label} T4 组次面板贴右边缘悬停：框完整可见`, false, e ? e.err : 'no result');
    else ok(`${label} T4 组次面板贴右边缘悬停：框完整可见`,
      e.shown && e.left >= -0.5 && e.top >= -0.5 && e.right <= e.vw + 0.5 && e.bottom <= e.vh + 0.5,
      `shown=${e.shown} clientX=${Math.round(e.clientX)} cvRight=${Math.round(e.cvRight)} ` +
      `rect=[${Math.round(e.left)},${Math.round(e.top)}~${Math.round(e.right)},${Math.round(e.bottom)}] ` +
      `框=${Math.round(e.w)}x${Math.round(e.h)} vw/vh=${e.vw}/${e.vh}`);

    // 截图①：组次面板贴右边缘悬停的真实画面（最贴近用户报的场景）
    await sleep(250);
    let shot = await send('Page.captureScreenshot', { format: 'png' });
    if (shot.result?.data) {
      const out = `_shots/tip_${label === '千帆大图' ? 'qf' : 'gw'}_panel_edge.png`;
      try { writeFileSync(out, Buffer.from(shot.result.data, 'base64')); console.log('screenshot: ' + out); } catch { }
    }

    // T5 主图：先滚回页面顶部（T4 把页面滚下去了），再从右往左扫，
    //    找到第一个能触发提示框的位置（自然就是靠右的位置）
    await evl('window.scrollTo(0, 0);');
    await sleep(400);
    const e2e2 = await evl(`(() => {
      const cv = document.getElementById('cv');
      const r = cv.getBoundingClientRect();
      const tip = document.getElementById('tip');
      let hit = null;
      for (let fx = 0.985; fx >= 0.15 && !hit; fx -= 0.035) {
        for (let fy = 0.10; fy <= 0.92; fy += 0.05) {
          const clientX = r.left + r.width * fx, clientY = r.top + r.height * fy;
          cv.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX, clientY }));
          if (tip.style.display === 'block') { hit = { clientX, clientY }; break; }
        }
      }
      if (!hit) return JSON.stringify({ err: '整幅图都没扫到提示框' });
      const t = tip.getBoundingClientRect();
      return JSON.stringify({ vw: window.innerWidth, vh: window.innerHeight,
        clientX: hit.clientX, left: t.left, top: t.top, right: t.right, bottom: t.bottom,
        w: t.width, h: t.height });
    })()`);
    const e2 = e2e2 ? JSON.parse(e2e2) : null;
    if (!e2 || e2.err) ok(`${label} T5 主图靠右悬停：框完整可见`, false, e2 ? e2.err : 'no result');
    else ok(`${label} T5 主图靠右悬停：框完整可见`,
      e2.left >= -0.5 && e2.top >= -0.5 && e2.right <= e2.vw + 0.5 && e2.bottom <= e2.vh + 0.5,
      `clientX=${Math.round(e2.clientX)} vw=${e2.vw} ` +
      `rect=[${Math.round(e2.left)},${Math.round(e2.top)}~${Math.round(e2.right)},${Math.round(e2.bottom)}] ` +
      `框=${Math.round(e2.w)}x${Math.round(e2.h)}`);

    // 截图②：主图靠右悬停
    await sleep(250);
    shot = await send('Page.captureScreenshot', { format: 'png' });
    if (shot.result?.data) {
      const out = `_shots/tip_${label === '千帆大图' ? 'qf' : 'gw'}_main_edge.png`;
      try { writeFileSync(out, Buffer.from(shot.result.data, 'base64')); console.log('screenshot: ' + out); } catch { }
    }

    ok(`${label}：console 无错误`, errors.length === 0, errors.slice(0, 3).join(' || '));
  }
}

ws.close();
console.log(fail ? `RESULT: ${fail} FAIL` : 'RESULT: ALL PASS');
process.exit(fail ? 1 : 0);
