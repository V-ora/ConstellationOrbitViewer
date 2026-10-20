// 专项自检：① JS 控制台错误/异常扫描 ② 逐组次聚焦 → 爬升图叠加实渲染（含高轨三星）
// 用法：node _chk_gaogui.mjs http://127.0.0.1:8771/
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const APP = process.argv[2] || 'http://127.0.0.1:8771/';
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9334;

const prof = mkdtempSync(path.join(tmpdir(), 'edge-prof-'));
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-sandbox',
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`,
  '--window-size=1052,900', 'about:blank'], { stdio: 'ignore' });
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
const consoleErrs = [];
ws.onmessage = ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id).res(m.result); pend.delete(m.id); return; }
  if (m.method === 'Runtime.exceptionThrown')
    consoleErrs.push('EXC: ' + JSON.stringify(m.params.exceptionDetails).slice(0, 300));
  if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error')
    consoleErrs.push('LOG: ' + m.params.entry.text.slice(0, 300));
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error')
    consoleErrs.push('CON: ' + (m.params.args || []).map(a => a.value ?? a.description).join(' ').slice(0, 300));
};
const send = (method, params = {}) => new Promise((res, rej) => {
  const id = ++mid; pend.set(id, { res, rej });
  ws.send(JSON.stringify({ id, method, params }));
});
const evl = async (expr, awaitPromise = false) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise });
  if (r.exceptionDetails) return { __err: String(r.exceptionDetails.text) };
  return r.result.value;
};

await send('Runtime.enable');
await send('Log.enable');
await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1052, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: APP });
await sleep(10000);

let fail = 0;
const check = (name, cond, extra = '') => {
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (extra ? '  ' + extra : ''));
  if (!cond) fail++;
};

// ---- ① 逐组次聚焦 → 爬升图 ----
const batchList = await evl(`fetch('/api/tle?group=gaogui').then(r=>r.json()).then(d=>[...new Set(d.sats.map(s=>s.meta&&s.meta.batch).filter(Boolean))])`, true);
check('gaogui 批次列表可取', Array.isArray(batchList) && batchList.length === 3, JSON.stringify(batchList));

// 用前端机制：打开组次面板 → 点高轨组次行 → 等叠加图/单星图出图
const rows = await evl(`(function(){
  const btn = document.querySelector('#btnBatches, [data-act="batches"], .btn-batches');
  if (btn) btn.click();
  return !!document.querySelector('#batchPanel, .batch-panel');
})()`);
check('组次面板可打开', !!rows);
await sleep(1500);

// 直接走 JS 状态机聚焦（比真点行更稳，聚焦逻辑同一份代码）
for (const b of (batchList || [])) {
  const r = await evl(`(async function(){
    try {
      setBatchFocus('gaogui', '${b}');
      S.setView('raise');
      await new Promise(r => setTimeout(r, 3500));
      const fr = document.querySelector('#raiseFrame');
      const src = fr ? fr.src : '';
      // iframe 内部是否画出了图（batch.html 用 canvas 画叠加图）
      let drawn = false, ipts = -1;
      try {
        const idoc = fr.contentDocument;
        const canvases = idoc ? idoc.querySelectorAll('canvas') : [];
        drawn = canvases.length > 0;
        ipts = idoc && idoc.body ? (idoc.body.textContent.match(/\\d+颗/)||[''])[0] : '';
      } catch (e) { drawn = 'noaccess:' + e; }
      const banner = document.querySelector('#banner');
      return { src: src.split('/').pop(), drawn, ipts,
               bannerText: banner ? banner.textContent.trim().slice(0, 60) : '' };
    } catch (e) { return { err: String(e) }; }
  })()`, true);
  const okc = r && !r.err && String(r.src).indexOf('batch=') >= 0 && r.drawn === true;
  check('聚焦 gaogui/' + b + ' → 爬升图', !!okc, JSON.stringify(r).slice(0, 200));
}

// 退出组次回 3D
const exitOk = await evl(`(function(){
  setBatchFocus(null); S.setView('3d');
  return { filtered: !!S.batchFilter, n: S.visible.length };
})()`);
check('退出组次恢复全部', exitOk && !exitOk.filtered && exitOk.n > 400, JSON.stringify(exitOk));

// ---- ② 控制台错误 ----
const errs = consoleErrs.filter(e => !/favicon|Autofill|DevTools/i.test(e));
check('无 JS 控制台错误/异常', errs.length === 0, errs.slice(0, 5).join(' | '));

console.log(fail === 0 ? '== 专项自检全部通过 ==' : '== 失败 ' + fail + ' 项 ==');
process.exit(fail === 0 ? 0 : 1);
