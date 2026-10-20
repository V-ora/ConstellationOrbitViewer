// 「过境预报」视图预览截图：打开页面 → 切到过境预报 → 填观测点 → 算过境 → 整页截图。
// 与 _shot.mjs（纯截图大图）分开，因为这一屏需要先跑一次计算才有内容。
// 用法：node _shot_pass.mjs <baseURL> <输出png> [观测点 lat,lon] [高度px]
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const BASE = process.argv[2] || 'http://127.0.0.1:8770/';
const OUT = process.argv[3] || '预览_过境预报.png';
const OBSV = process.argv[4] || '39.9042,116.4074';
const H = parseInt(process.argv[5] || '1260', 10);
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9341;

const prof = mkdtempSync(path.join(tmpdir(), 'edge-shotpass-'));
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-sandbox',
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`,
  `--window-size=1320,${H}`, 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => { try { edge.kill(); } catch { } });
const sleep = ms => new Promise(r => setTimeout(r, ms));

let target = null;
for (let i = 0; i < 50 && !target; i++) {
  try {
    const list = await (await fetch(`http://127.0.0.1:${DBG}/json`)).json();
    target = list.find(t => t.type === 'page');
  } catch { }
  if (!target) await sleep(300);
}
if (!target) { console.log('FATAL: CDP target 未就绪'); process.exit(1); }
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let mid = 0; const pend = new Map();
ws.onmessage = ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id).res(m.result); pend.delete(m.id); }
};
const send = (method, params = {}) => new Promise((res, rej) => {
  const id = ++mid; pend.set(id, { res, rej });
  ws.send(JSON.stringify({ id, method, params }));
});
const evl = async e => {
  const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
  return r && r.result ? r.result.value : undefined;
};

await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1320, height: H, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: BASE });
await sleep(2500);
for (let i = 0; i < 80; i++) {
  const v = await evl('(typeof S!=="undefined"&&S.sats)?S.sats.length:0');
  if (typeof v === 'number' && v > 100) break;
  await sleep(1500);
}
await sleep(1500);
// 直接切视图 + 设观测点（截图脚本不点按钮，避免依赖元素坐标）
await evl(`S.setView("pass")`);
const [lat, lon] = OBSV.split(',');
await evl(`(()=>{const a=document.querySelector('#pvLat'),b=document.querySelector('#pvLon');
  a.value=${JSON.stringify(lat)};b.value=${JSON.stringify(lon)};
  document.querySelector('#pvApply').click();})()`);
await sleep(600);
await evl(`document.querySelector('#pvGo').click()`);
for (let i = 0; i < 120; i++) {
  const busy = await evl('(typeof OBS!=="undefined")?!!OBS.computing:false');
  if (!busy) break;
  await sleep(500);
}
await sleep(1200);
const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
writeFileSync(OUT, Buffer.from(shot.data, 'base64'));
const stats = await evl(`JSON.stringify({now:document.querySelectorAll('#pvNow .pv-row').length,
  up:document.querySelectorAll('#pvUp .pv-row').length,passes:(typeof OBS!=="undefined")?OBS.passes.length:0})`);
console.log('screenshot:', OUT, stats);
process.exit(0);
