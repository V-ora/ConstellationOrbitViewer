// 单星爬升图检查：选中高轨/新星卫星 → 爬升图 iframe sat.html 出图
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
const APP = process.argv[2] || 'http://127.0.0.1:8771/';
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9336;
const prof = mkdtempSync(path.join(tmpdir(), 'edge-prof-'));
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-sandbox',
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`, '--window-size=1052,900', 'about:blank'], { stdio: 'ignore' });
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
const errs = [];
ws.onmessage = ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id).res(m.result); pend.delete(m.id); return; }
  if (m.method === 'Runtime.exceptionThrown') errs.push('EXC ' + JSON.stringify(m.params.exceptionDetails).slice(0, 200));
  if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') errs.push('LOG ' + m.params.entry.text.slice(0, 200));
};
const send = (m, p = {}) => new Promise((res, rej) => { const id = ++mid; pend.set(id, { res, rej }); ws.send(JSON.stringify({ id, method: m, params: p })); });
const evl = async (e, awaitPromise = false) => {
  const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise });
  if (r.exceptionDetails) return { __err: (r.exceptionDetails.exception?.description || r.exceptionDetails.text).slice(0, 200) };
  return r.result?.value;
};
await send('Runtime.enable'); await send('Log.enable'); await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1052, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: APP });
await sleep(9000);
let fail = 0;
const check = (n, c, x = '') => { console.log((c ? 'PASS ' : 'FAIL ') + n + (x ? '  ' + x : '')); if (!c) fail++; };

for (const norad of ['59069', '60327', '61503']) {
  const r = await evl(`(async function(){
    try {
      const sat = S.sats.find(s => String(s.norad) === '${norad}');
      if (!sat) return { err: 'sat not found' };
      select(sat);             // 选中
      S.setView('raise');      // 切爬升图 → loadRaiseSel
      await new Promise(r => setTimeout(r, 3000));
      const fr = document.querySelector('#raiseFrame');
      let drawn = false;
      try { drawn = fr.contentDocument && fr.contentDocument.querySelectorAll('canvas').length > 0; } catch (e) { drawn = 'noaccess'; }
      const r2 = { src: (fr.src || '').split('/').pop(), drawn, name: sat.name };
      S.setView('3d');
      document.querySelector('#btnDeselect').click();
      return r2;
    } catch (e) { return { err: String(e) }; }
  })()`, true);
  const okc = r && !r.err && String(r.src).indexOf('sat.html') >= 0 && r.drawn === true;
  check('单星爬升图 NORAD ' + norad, !!okc, JSON.stringify(r).slice(0, 160));
}
const realErrs = errs.filter(e => !/favicon|Autofill|DevTools/i.test(e));
check('无 JS 控制台错误', realErrs.length === 0, realErrs.slice(0, 3).join(' | '));
console.log(fail === 0 ? '== 单星爬升图检查通过 ==' : '== 失败 ' + fail + ' 项 ==');
process.exit(fail ? 1 : 0);
