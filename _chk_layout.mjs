// 快速检查：① 时间轴绝对居中 ② 组次聚焦时 #hud/#banner 下移 46px、不与组次条叠压
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const APP = process.argv[2] || 'http://127.0.0.1:8771/';
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9335;
const prof = mkdtempSync(path.join(tmpdir(), 'edge-prof-'));
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-sandbox',
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`,
  '--window-size=1052,900', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => { try { edge.kill(); } catch {} });
const sleep = ms => new Promise(r => setTimeout(r, ms));
let target = null;
for (let i = 0; i < 50 && !target; i++) {
  try { target = (await (await fetch(`http://127.0.0.1:${DBG}/json`)).json()).find(t => t.type === 'page'); } catch {}
  if (!target) await sleep(300);
}
if (!target) { console.log('FATAL'); process.exit(1); }
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let mid = 0; const pend = new Map();
ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && pend.has(m.id)) { pend.get(m.id).res(m.result); pend.delete(m.id); } };
const send = (m, p = {}) => new Promise((res, rej) => { const id = ++mid; pend.set(id, { res, rej }); ws.send(JSON.stringify({ id, method: m, params: p })); });
const evl = async (e, awaitPromise = false) => {
  const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise });
  if (r.exceptionDetails) return { __err: (r.exceptionDetails.exception?.description || r.exceptionDetails.text).slice(0, 200) };
  return r.result?.value;
};

await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: APP });
await sleep(9000);

let fail = 0;
const check = (n, c, x = '') => { console.log((c ? 'PASS ' : 'FAIL ') + n + (x ? '  ' + x : '')); if (!c) fail++; };

const t1 = await evl(`(function(){
  const slider = document.querySelector('#timeSlider');
  if (!slider) return { err: 'no #timeSlider' };
  const grp = slider.parentElement;   // 绝对居中的是整个滑块组（滑块+刻度文字）
  const r = grp.getBoundingClientRect();
  return { grpMid: Math.round(r.left + r.width / 2), winMid: Math.round(window.innerWidth / 2) };
})()`);
check('时间轴绝对居中', t1 && !t1.__err && t1.grpMid === t1.winMid, JSON.stringify(t1));

const t2 = await evl(`(async function(){
  const hudTop0 = document.querySelector('#hud') ? document.querySelector('#hud').style.top : '?';
  setBatchFocus('qianfan', '2024-140');
  await new Promise(r => setTimeout(r, 800));
  const hud = document.querySelector('#hud'), bnr = document.querySelector('#banner');
  const bar = document.querySelector('#batchBar');
  const hudTop = hud ? hud.style.top : '?', bnrTop = bnr ? bnr.style.top : '?';
  const barBottom = bar ? Math.round(bar.getBoundingClientRect().bottom) : -1;
  const hudTopPx = hud ? Math.round(hud.getBoundingClientRect().top) : -1;
  setBatchFocus(null);
  return { hudTop0, hudTop, bnrTop, barBottom, hudTopPx };
})()`, true);check('聚焦时浮层下移 46px', t2 && t2.hudTop === '46px' && t2.bnrTop === '46px', JSON.stringify(t2));
check('组次条与浮层不叠压', t2 && t2.barBottom <= 100, 'barBottom=' + (t2 && t2.barBottom));

console.log(fail === 0 ? '== 浮层/时间轴检查通过 ==' : '== 失败 ' + fail + ' 项 ==');
process.exit(fail ? 1 : 0);
