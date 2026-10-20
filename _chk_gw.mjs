// 验证重建后的国网低轨轨道爬升大图：console 无错 + 卡片刻度 + 表格行数 + canvas 有内容 + 刻度不崩 + 配色 + 截图。
// 用法：node _chk_gw.mjs [URL或file路径]
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const DEF = 'file:///D:/Work Buddy %E5%B7%A5%E4%BD%9C%E5%8C%BA/%E6%98%9F%E5%BA%A7%E8%BD%A8%E9%81%93%E5%AE%9E%E6%97%B6%E6%9F%A5%E7%9C%8B%E5%99%A8/web/raise/%E5%9B%BD%E7%BD%91%E4%BD%8E%E8%BD%A8%E5%8D%AB%E6%98%9F%E8%BD%A8%E9%81%93%E7%88%AC%E5%8D%87%E4%BA%A4%E4%BA%92%E5%A4%A7%E5%9B%BE.html';
const URL0 = process.argv[2] || DEF;
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9336;

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
ok('状态卡(8张，含两个壳层卡)', Array.isArray(cards) && cards.length === 8, (cards || []).join(' ; '));

// 两张壳层卡：文案 + 计算色（与壳层按钮/曲线一致）
const shRaw = await evl(`(()=>{const cs=[...document.querySelectorAll('.card')];
  const f=k=>{const c=cs.find(x=>x.querySelector('.k').textContent===k); if(!c) return null;
    const v=c.querySelector('.v');
    return {t:v.textContent.trim(), col:getComputedStyle(v).color, cls:c.className};};
  return JSON.stringify({a:f('50° 倾角壳层'), b:f('86.5° 倾角壳层')});})()`);
const shc = JSON.parse(shRaw);
// ★ 2026-10-19：壳层颗数不再写死（220 颗时代是 160/60，补入 100975/100976 后是 162/60）。
//   断言改成「卡片文案 == DATA.n50/n86」「两卡之和 == 在轨总数」这种自洽关系，
//   数据自然增长时不会假红，但卡片和 DATA 对不上、或有卫星没归壳层时照样会红。
const n50 = await evl('DATA.n50'), n86 = await evl('DATA.n86');
ok(`新增「50° 倾角壳层」卡 = ${n50} 颗（与 DATA.n50 一致）· 绿(#22c55e)`,
  !!shc.a && parseInt(shc.a.t) === n50 && shc.a.col === 'rgb(34, 197, 94)', shRaw);
ok(`新增「86.5° 倾角壳层」卡 = ${n86} 颗（与 DATA.n86 一致）· 蓝(#257ddd)`,
  !!shc.b && parseInt(shc.b.t) === n86 && shc.b.col === 'rgb(37, 125, 221)', shRaw);

const nSats = await evl('DATA.sats.length');
ok('两壳层卡颗数之和 = 在轨卫星总数（每颗都归了壳层）',
  !!shc.a && !!shc.b && (parseInt(shc.a.t) + parseInt(shc.b.t)) === nSats,
  `${shc.a && shc.a.t} + ${shc.b && shc.b.t} vs n=${nSats}`);
ok('在轨卫星总数是个合理量级（≥200）', nSats >= 200, `n=${nSats}`);

const rows = await evl(`document.querySelectorAll('#lauts tbody tr').length`);
ok('发射列表行数=27', rows === 27, `rows=${rows}`);

const sumN = await evl('DATA.launches.reduce((a,x)=>a+x.n,0)');
ok('发射任务卫星数合计 = 在轨总数', sumN === nSats, `sum=${sumN} n=${nSats}`);

const zero = await evl(`JSON.stringify(DATA.launches.filter(x=>x.n===0).map(x=>x.grp))`);
ok('无零卫星发射组', zero === '[]', zero);

// ★ 2026-10-15 新增（起因：用户报「星网怎么没更新」）——
//   发射清单是人工维护的静态 JSON，27 组（2026-230）当时根本没补录，大图里就整批消失。
//   以后新发射漏录，这三条会立刻红，不用等用户发现。
const g27raw = await evl(`(()=>{const tr=[...document.querySelectorAll('#lauts tbody tr')]
  .find(r=>/2026-230/.test(r.textContent));
  return tr?JSON.stringify({n:tr.children[5].textContent.trim(),
    na:!!tr.children[5].querySelector('sup'),
    pill:tr.children[8].textContent.trim()}):null;})()`);
const g27 = g27raw ? JSON.parse(g27raw) : null;
ok('星网低轨27组（2026-230）已进大图发射表', !!g27, g27raw);
// ★ 2026-10-19：27 组 9 颗编目现在都发布了根数（100975/100976 = OBJECT C/D 补上），
//   所以 n=9、na=9，上标「/9」按设计自动消失。断言改成「颗数 == 编目数，或还有未发根数的上标」二者之一，
//   只要不是「颗数 < 编目数却又不标上标」这种静默少画就不算错。
ok('27 组颗数与编目数对得上（n=na 无上标 / n<na 必须有上标）',
  !!g27 && (g27.n.includes('/') ? true : !g27.na), g27 ? g27.n : '');
ok('27 组全部颗数已进图（n=9）',
  !!g27 && parseInt(g27.n) === 9, g27 ? g27.n : '');
ok('27 组状态不是「抬升中止」（数据点稀疏时不得误判；「爬升中/入轨停泊/已抵达」都算对）',
  !!g27 && !/抬升中止/.test(g27.pill) && /爬升中|入轨停泊|已抵达/.test(g27.pill), g27 ? g27.pill : '');

const g27d = await evl(`(()=>{const x=DATA.launches.find(y=>y.cospar==='2026-230');
  return x?JSON.stringify({n:x.n,na:x.na,grp:x.grp,shell:x.shell}):null;})()`);
ok('DATA 里 27 组 n=9 / na=9 / 50°壳层',
  g27d === '{"n":9,"na":9,"grp":"低轨27组","shell":0}', g27d);

const shellCnt = await evl(`JSON.stringify({n50:DATA.n50,n86:DATA.n86,t50:DATA.t50,t86:DATA.t86,sum:DATA.n50+DATA.n86,n:DATA.sats.length})`);
ok('壳层口径 50°→1149.3 / 86.5°→1167.9', /"t50":1149\.3/.test(shellCnt) && /"t86":1167\.9/.test(shellCnt), shellCnt);
ok('DATA.n50 + DATA.n86 == DATA.sats.length', /"sum":(\d+)/.test(shellCnt) &&
  (() => { const m = shellCnt.match(/"sum":(\d+).*?"n":(\d+)/); return m && m[1] === m[2]; })(), shellCnt);

const cvInfo = await evl(`(()=>{const c=document.getElementById('cv');
  if(!c) return null; const ctx=c.getContext('2d');
  const d=ctx.getImageData(0,0,c.width,c.height).data;
  let n=0; for(let i=3;i<d.length;i+=4){ if(d[i]>0) n++; }
  return {w:c.width,h:c.height,painted:n};})()`);
ok('canvas 存在且有内容', !!cvInfo && cvInfo.painted > 5000,
  cvInfo ? `${cvInfo.w}x${cvInfo.h} painted=${cvInfo.painted}` : 'no canvas');

console.log('title:', await evl('document.title'));

ok('console 无错误', errors.length === 0, errors.slice(0, 3).join(' || '));

// —— 抓 canvas 实际绘制文字（hook fillText），直接看刻度有没有崩 ——
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

// 时间基准：本页原点 2024-11-25（与 DATA.p 的 t 一致）
const ro = JSON.parse(await evl(`JSON.stringify({t0:DATA.t0,
  last: Math.max(...DATA.sats.map(s=>s.p[s.p.length-1][0])), tMin:tMin, tMax:tMax})`));
ok('时间基准 t0=2024-11-25', ro.t0 === '2024-11-25', `t0=${ro.t0}`);
ok('末点落在可视范围右界内', ro.last <= ro.tMax + 0.01 && ro.last >= ro.tMin, `last=${ro.last} tMin=${ro.tMin.toFixed(1)} tMax=${ro.tMax.toFixed(1)}`);
ok('左界贴合数据(不留 -20 空白)', ro.tMin > 0 && ro.tMin <= 21 + 8, `tMin=${ro.tMin.toFixed(2)}`);

const colJson = await evl('JSON.stringify(COL)');
ok('壳层配色 50°绿/86.5°蓝', colJson === '["#22c55e","#257ddd"]', colJson);

const btns = await evl(`JSON.stringify([...document.querySelectorAll('.bar button')].map(b=>b.id))`);
ok('按钮保留 b0~b4(双壳层过滤)', btns === '["b0","b1","b2","b3","b4"]', btns);

// 壳层过滤按钮真的生效
const filt = await evl(`(()=>{const before=SHOW.slice();
  b0.click(); const after=SHOW.slice(); b0.click(); return JSON.stringify({before,after});})()`);
ok('b0 壳层开关可切换', filt === '{"before":[true,true],"after":[false,true]}', filt);

// —— 默认即「适应全部高度范围」（2026-10-03 用户要求，与千帆图一致）——
const ydef = await evl(`JSON.stringify({fullY: fullY, yMin: yMin, yMax: yMax,
  b4on: document.getElementById('b4').classList.contains('on')})`);
ok('默认启用适应全部高度范围(770~1210)', ydef === '{"fullY":true,"yMin":770,"yMax":1210,"b4on":true}', ydef);

// 点一下 b4 应切到「工作轨道近景」二档窗口，再点回来恢复全高度
const b4tog = await evl(`(()=>{const b=document.getElementById('b4');
  b.click(); const z={fullY:fullY,yMin:yMin,yMax:yMax,on:b.classList.contains('on')};
  b.click(); const f={fullY:fullY,yMin:yMin,yMax:yMax,on:b.classList.contains('on')};
  return JSON.stringify({zoom:z,full:f});})()`);
ok('b4 二档切换：近景 1075~1202 ↔ 全高 770~1210',
  b4tog === '{"zoom":{"fullY":false,"yMin":1075,"yMax":1202,"on":false},"full":{"fullY":true,"yMin":770,"yMax":1210,"on":true}}',
  b4tog);

// —— 2026-10-03 用户提出：表左侧色条跟壳层配色一致（50°绿 / 86.5°蓝）——
const bar0 = await evl(`(()=>{const tr=document.querySelector('#lauts tbody tr.s0');
  return tr?getComputedStyle(tr.querySelector('td')).borderLeftColor:null;})()`);
ok('50°壳层行左侧色条=绿(#22c55e)', bar0 === 'rgb(34, 197, 94)', String(bar0));
const bar1 = await evl(`(()=>{const tr=document.querySelector('#lauts tbody tr.s1');
  return tr?getComputedStyle(tr.querySelector('td')).borderLeftColor:null;})()`);
ok('86.5°壳层行左侧色条=蓝(#257ddd)', bar1 === 'rgb(37, 125, 221)', String(bar1));

// —— 副标题补上「50° / 86.5° 各多少颗」——
const subTxt = await evl(`document.getElementById('sub').textContent`);
ok(`副标题含两壳层颗数(50°=${n50} / 86.5°=${n86}，取自 DATA)`,
  new RegExp('50° 倾角壳层 ' + n50 + ' 颗').test(subTxt || '') &&
  new RegExp('86.5° 倾角壳层 ' + n86 + ' 颗').test(subTxt || ''), subTxt);

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

await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1950, deviceScaleFactor: 1, mobile: false });
await sleep(700);
const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
if (shot.result?.data) {
  writeFileSync('_chk_gw.png', Buffer.from(shot.result.data, 'base64'));
  console.log('screenshot: _chk_gw.png');
}

ws.close();
console.log(fail ? `RESULT: ${fail} FAIL` : 'RESULT: ALL PASS');
process.exit(fail ? 1 : 0);
