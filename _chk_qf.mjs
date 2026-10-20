// 验证重做的千帆大图：console 无错 + 关键元素 + canvas 有内容 + 截图。
// 用法：node _chk_qf.mjs [URL或file路径]
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const DEF = 'file:///D:/Work Buddy %E5%B7%A5%E4%BD%9C%E5%8C%BA/%E6%98%9F%E5%BA%A7%E8%BD%A8%E9%81%93%E5%AE%9E%E6%97%B6%E6%9F%A5%E7%9C%8B%E5%99%A8/web/raise/%E5%8D%83%E5%B8%86%E6%98%9F%E5%BA%A7%E8%BD%A8%E9%81%93%E7%88%AC%E5%8D%87%E4%BA%A4%E4%BA%92%E5%A4%A7%E5%9B%BE.html';
const URL0 = process.argv[2] || DEF;
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9334;

const prof = mkdtempSync(path.join(tmpdir(), 'edge-prof-'));
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-sandbox',
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`,
  '--window-size=1600,980', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => { try { edge.kill(); } catch {} });

const sleep = ms => new Promise(r => setTimeout(r, ms));
let target = null;
for (let i = 0; i < 50 && !target; i++) {
  try {
    const list = await (await fetch(`http://127.0.0.1:${DBG}/json`)).json();
    target = list.find(t => t.type === 'page');
  } catch {}
  if (!target) await sleep(300);
}
if (!target) { console.log('FATAL: CDP target 未就绪'); process.exit(1); }

const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

let mid = 0; const pend = new Map();
const errors = [];
ws.onmessage = ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); return; }
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
    errors.push((m.params.args || []).map(a => a.value ?? a.description ?? '').join(' ').slice(0, 200));
  }
  if (m.method === 'Runtime.exceptionThrown') {
    errors.push((m.params.exceptionDetails?.exception?.description ||
      m.params.exceptionDetails?.text || 'exception').slice(0, 200));
  }
};
function send(method, params = {}) {
  const id = ++mid;
  return new Promise(res => { pend.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
}
async function evl(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
  return r.result?.result?.value;
}

await send('Runtime.enable');
await send('Page.enable');
await send('Page.navigate', { url: URL0 });
await sleep(3500);

let fail = 0;
const ok = (name, cond, extra = '') => {
  console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${extra ? '  | ' + extra : ''}`);
  if (!cond) fail++;
};

const cards = await evl(`[...document.querySelectorAll('.card')].map(c=>c.querySelector('.k').textContent+':'+c.querySelector('.v').textContent)`);
ok('状态卡(7张)', Array.isArray(cards) && cards.length === 7, (cards || []).join(' ; '));

const rows = await evl(`document.querySelectorAll('#lauts tbody tr').length`);
ok('发射列表行数=16', rows === 16, `rows=${rows}`);

const cvInfo = await evl(`(()=>{const c=document.getElementById('cv');
  if(!c) return null; const ctx=c.getContext('2d');
  const d=ctx.getImageData(0,0,c.width,c.height).data;
  let n=0; for(let i=3;i<d.length;i+=4){ if(d[i]>0) n++; }
  return {w:c.width,h:c.height,painted:n};})()`);
ok('canvas 存在且有内容', !!cvInfo && cvInfo.painted > 5000,
  cvInfo ? `${cvInfo.w}x${cvInfo.h} painted=${cvInfo.painted}` : 'no canvas');

const title = await evl('document.title');
console.log('title:', title);

// 曲线末点日期（t 以天为单位，起点 2024-01-01）
const lastT = await evl(`Math.max(...DATA.sats.map(s=>s.p[s.p.length-1][0]))`);
const firstT = await evl(`Math.min(...DATA.sats.map(s=>s.p[s.p.length-1][0]))`);
const toDate = t => new Date(Date.UTC(2024, 0, 1) + t * 86400000).toISOString().slice(0, 10);
console.log(`曲线末点: 最新 ${toDate(lastT)} / 最早 ${toDate(firstT)}  (共 ${await evl('DATA.sats.length')} 颗)`);

ok('console 无错误', errors.length === 0, errors.slice(0, 3).join(' || '));

// —— 抓取 canvas 实际绘制的文字（hook fillText），直接看刻度有没有崩 ——
const grab = async (setup) => JSON.parse(await evl(`(() => {
  const out = [];
  const orig = CanvasRenderingContext2D.prototype.fillText;
  CanvasRenderingContext2D.prototype.fillText = function (t) { out.push(String(t)); return orig.apply(this, arguments); };
  ${setup}
  return JSON.stringify(out);
})()`));

const txts = await grab('draw();');
const badX = txts.filter(t => /undefined|NaN/.test(t));
ok('刻度文字无 undefined/NaN', badX.length === 0, badX.slice(0, 3).join(' , '));
const monLbl = txts.filter(t => /^\d{4}-\d{2}$/.test(t));
ok('默认视图为月份刻度', monLbl.length >= 3, monLbl.slice(0, 6).join(' '));

const zTxts = await grab(`const c=(tMin+tMax)/2,o1=tMin,o2=tMax;
  tMin=c-30; tMax=c+30; draw(); tMin=o1; tMax=o2;`);
const badZ = zTxts.filter(t => /undefined|NaN/.test(t));
ok('缩放到 60 天后刻度无 undefined', badZ.length === 0, badZ.slice(0, 3).join(' , '));
const dayLbl = zTxts.filter(t => /^\d{1,2}\/\d{1,2}$/.test(t));
ok('缩放后切换为日刻度', dayLbl.length >= 2, dayLbl.slice(0, 6).join(' '));

const rangeOk = await evl(`JSON.stringify({t0:DATA.t0,
  last: Math.max(...DATA.sats.map(s=>s.p[s.p.length-1][0])), tMax: tMax})`);
const ro = JSON.parse(rangeOk);
ok('时间基准与数据一致(2024-01-01)', ro.t0 === '2024-01-01', `t0=${ro.t0}`);
ok('末点落在可视范围右界内', ro.last <= ro.tMax, `last=${ro.last} tMax=${ro.tMax}`);

// 配色统一为千帆主题色 + 已移除「已就位/未就位」过滤按钮
const colJson = await evl('JSON.stringify(COL)');
ok('曲线统一千帆主题色 #f5a524', colJson === '["#f5a524","#f5a524"]', colJson);
const btns = await evl(`JSON.stringify([...document.querySelectorAll('.bar button')].map(b=>b.id))`);
ok('按钮仅剩 b2/b3/b4', btns === '["b2","b3","b4"]', btns);

// —— 2026-10-03 用户提出：表左侧色块统一橙色（与千帆主题色 #f5a524 一致）——
const barCols = await evl(`JSON.stringify([...new Set([...document.querySelectorAll('#lauts tbody tr')]
  .map(tr=>getComputedStyle(tr.querySelector('td')).borderLeftColor))])`);
ok('表格左侧色条统一橙色(#f5a524)', barCols === '["rgb(245, 165, 36)"]', barCols);

// —— 默认即「适应全部高度范围」——
const ydef = await evl(`JSON.stringify({fullY: fullY, yMin: yMin, yMax: yMax,
  b4on: document.getElementById('b4').classList.contains('on')})`);
ok('默认启用适应全部高度范围(700~1120)', ydef === '{"fullY":true,"yMin":700,"yMax":1120,"b4on":true}', ydef);

const b4tog = await evl(`(()=>{const b=document.getElementById('b4');
  b.click(); const z={fullY:fullY,yMin:yMin,yMax:yMax,on:b.classList.contains('on')};
  b.click(); const f={fullY:fullY,yMin:yMin,yMax:yMax,on:b.classList.contains('on')};
  return JSON.stringify({zoom:z,full:f});})()`);
ok('b4 二档切换：近景 1040~1090 ↔ 全高 700~1120',
  b4tog === '{"zoom":{"fullY":false,"yMin":1040,"yMax":1090,"on":false},"full":{"fullY":true,"yMin":700,"yMax":1120,"on":true}}',
  b4tog);

// —— 时间范围滑块：存在 + 把手随窗口同步 + 拖动真的能改窗口 ——
const sbRaw = await evl(`(()=>{const tb=document.getElementById('tbar');
  if(!tb) return '{"has":false}';
  const o1=tMin,o2=tMax,w0=tMax-tMin; tMin=o1+w0*0.25; tMax=o2-w0*0.25; draw();
  const L=parseFloat(document.getElementById('thL').style.left);
  const R=parseFloat(document.getElementById('thR').style.left);
  tMin=o1; tMax=o2; draw();
  return JSON.stringify({has:true,w:tb.clientWidth,L:L,R:R});})()`);
const sbo = JSON.parse(sbRaw);
ok('时间滑块存在且把手随窗口位置同步',
  sbo.has && Math.abs(sbo.L - sbo.w * 0.25) < 3 && Math.abs(sbo.R - sbo.w * 0.75) < 3, sbRaw);

const dgRaw = await evl(`(()=>{const tb=document.getElementById('tbar'),r=tb.getBoundingClientRect();
  const b0v=tMin; const thL=document.getElementById('thL');
  thL.dispatchEvent(new PointerEvent('pointerdown',{clientX:r.left+tb.clientWidth*0.25,bubbles:true}));
  window.dispatchEvent(new PointerEvent('pointermove',{clientX:r.left+tb.clientWidth*0.45,bubbles:true}));
  window.dispatchEvent(new PointerEvent('pointerup',{bubbles:true}));
  const a=tMin; tMin=SLIM[0]; tMax=SLIM[1]; draw();
  return JSON.stringify({before:b0v,after:a});})()`);
const dgo = JSON.parse(dgRaw);
ok('拖动左把手可改时间窗起点', dgo.after > dgo.before + 1, dgRaw);

const zc = await evl(`(()=>{const o1=tMin,o2=tMax;
  tMin=-1e5; tMax=1e5; clampT();
  const w=tMax-tMin, sp=SLIM[1]-SLIM[0];
  tMin=o1; tMax=o2; draw();
  return JSON.stringify({w:w,sp:sp});})()`);
const zco = JSON.parse(zc);
ok('窗口越界会被夹回整段量程', Math.abs(zco.w - zco.sp) < 0.01, zc);

await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1850, deviceScaleFactor: 1, mobile: false });
await sleep(700);
const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
if (shot.result?.data) {
  writeFileSync('_chk_qf.png', Buffer.from(shot.result.data, 'base64'));
  console.log('screenshot: _chk_qf.png');
}

ws.close();
console.log(fail ? `RESULT: ${fail} FAIL` : 'RESULT: ALL PASS');
process.exit(fail ? 1 : 0);
