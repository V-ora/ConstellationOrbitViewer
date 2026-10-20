// 一次性：重拍 3D 预览图（星下点小图标已删除的新版）。
// 用法：node _shot_3dviews.mjs [URL，默认 http://127.0.0.1:8770/]
// 产出：_shots/3d_default.png / 3d_north.png / 3d_south.png
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const BASE = (process.argv[2] || 'http://127.0.0.1:8770/').replace(/\/+$/, '');
const EDGE = process.env.MSEDGE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9343;
const prof = mkdtempSync(path.join(tmpdir(), 'edge-shot3d-'));
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
  '--enable-unsafe-swiftshader', '--disable-dev-shm-usage',
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`,
  '--window-size=1600,980', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => { try { edge.kill(); } catch { } });

const sleep = ms => new Promise(r => setTimeout(r, ms));
let target = null;
for (let i = 0; i < 60 && !target; i++) {
  try { const list = await (await fetch(`http://127.0.0.1:${DBG}/json`)).json(); target = list.find(t => t.type === 'page'); } catch { }
  if (!target) await sleep(300);
}
if (!target) { console.log('FATAL: CDP target 未就绪'); process.exit(1); }
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let mid = 0; const pend = new Map();
ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); } };
const send = (method, params = {}) => new Promise(res => { const id = ++mid; pend.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
const js = async (expr) => (await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;
await send('Page.enable');

const SHOTS = [
  ['3d_default', BASE + '/#lat=25&lon=105'],
  ['3d_north', BASE + '/#lat=89&lon=90'],
  ['3d_south', BASE + '/#lat=-89&lon=-90'],
];
for (const [name, url] of SHOTS) {
  await send('Page.navigate', { url: 'about:blank' });
  await sleep(400);
  await send('Page.navigate', { url });
  // 等数据就绪（S.visible 稳定为 473）再留渲染时间
  let vis = -1;
  for (let i = 0; i < 60; i++) {
    await sleep(1000);
    vis = await js(`(typeof S!=='undefined'&&Array.isArray(S.visible))?S.visible.length:-1`) || -1;
    if (vis >= 470) break;
  }
  await sleep(4000);
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  const out = path.join(process.cwd(), '_shots', name + '.png');
  writeFileSync(out, Buffer.from(shot.result.data, 'base64'));
  console.log('saved', out, 'visible=' + vis);
}
try { ws.close(); } catch { }
process.exit(0);
