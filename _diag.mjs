// 诊断根页为什么没就绪：console 错误 + 失败请求 + 关键全局量。
// 用法：node _diag.mjs [URL]
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const URL0 = process.argv[2] || 'http://127.0.0.1:8770/';
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9336;
const prof = mkdtempSync(path.join(tmpdir(), 'edge-prof-'));
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-sandbox',
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`, '--window-size=1200,900', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => { try { edge.kill(); } catch {} });
const sleep = ms => new Promise(r => setTimeout(r, ms));
let t = null;
for (let i = 0; i < 50 && !t; i++) {
  try { t = (await (await fetch(`http://127.0.0.1:${DBG}/json`)).json()).find(x => x.type === 'page'); } catch {}
  if (!t) await sleep(300);
}
const ws = new WebSocket(t.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let mid = 0; const pend = new Map(); const errs = []; const reqs = [];
ws.onmessage = ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); return; }
  if (m.method === 'Runtime.exceptionThrown')
    errs.push('EXC ' + (m.params.exceptionDetails?.exception?.description || m.params.exceptionDetails?.text || '').slice(0, 300));
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error')
    errs.push('ERR ' + (m.params.args || []).map(a => a.value ?? a.description ?? '').join(' ').slice(0, 300));
  if (m.method === 'Network.responseReceived' && m.params.response.status >= 400)
    reqs.push(m.params.response.status + ' ' + m.params.response.url.slice(0, 120));
  if (m.method === 'Network.loadingFailed')
    reqs.push('FAILED ' + (m.params.errorText || '') + ' ' + (m.params.requestId || ''));
};
const send = (method, params = {}) => new Promise(res => { const id = ++mid; pend.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
const evl = async e => (await send('Runtime.evaluate', { expression: e, returnByValue: true })).result?.result?.value;

await send('Runtime.enable');
await send('Network.enable');
await send('Page.enable');
await send('Page.navigate', { url: URL0 });
for (const s of [5, 10, 20]) {
  await sleep(s === 5 ? 5000 : 5000);
  const snap = await evl(`JSON.stringify({rs:document.readyState, S:typeof window.S,
    vis:(window.S&&S.visible)?S.visible.length:-1, sats:(window.SATS?SATS.length:-1),
    stage:!!document.querySelector('#stage'), gcards:document.querySelectorAll('#gcards button').length,
    txt:(document.body.innerText||'').replace(/\\s+/g,' ').slice(0,160)})`);
  console.log(`[${s}s]`, snap);
}
console.log('--- console 错误 ---');
console.log(errs.length ? errs.slice(0, 8).join('\n') : '(无)');
console.log('--- 失败请求 ---');
console.log(reqs.length ? [...new Set(reqs)].slice(0, 8).join('\n') : '(无)');
ws.close(); process.exit(0);
