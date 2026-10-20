import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9336;
const prof = mkdtempSync(path.join(tmpdir(), 'edge-prof-'));
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-sandbox',
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`, '--window-size=1600,900',
  'http://127.0.0.1:8770/'], { stdio: 'ignore' });
process.on('exit', () => { try { edge.kill(); } catch {} });
const sleep = ms => new Promise(r => setTimeout(r, ms));
let target = null;
for (let i = 0; i < 50 && !target; i++) {
  try { const l = await (await fetch(`http://127.0.0.1:${DBG}/json`)).json(); target = l.find(t => t.type === 'page'); } catch {}
  if (!target) await sleep(300);
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let mid = 0; const pend = new Map();
ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); } };
const send = (m2, p = {}) => new Promise(res => { const id = ++mid; pend.set(id, res); ws.send(JSON.stringify({ id, method: m2, params: p })); });
const evl = async e => (await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true })).result?.result?.value;
await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
await sleep(7000);
// ① 时间轴居中：滑块组中心 vs footer 可视区中心
const r1 = await evl(`(() => {
  const f = document.querySelector('footer').getBoundingClientRect();
  const s = document.getElementById('timeSlider').getBoundingClientRect();
  return JSON.stringify({ footerMid: (f.left + f.right) / 2, sliderMid: (s.left + s.right) / 2 });
})()`);
console.log('① 时间轴:', r1);
// ② 高轨组次面板
const r2 = await evl(`(async () => {
  showBatchPanel('gaogui'); await new Promise(r=>setTimeout(r,500));
  const rows = [...document.querySelectorAll('#batchPanel .brow')].map(x => x.textContent.trim().slice(0, 60));
  return JSON.stringify({ n: rows.length, rows });
})()`);
console.log('② 高轨组次:', r2);
await evl(`document.getElementById('batchPanel').style.display='none'; 'ok'`);
// ③ 轨迹：默认视角 → 旋转到背面 → 每个视角下正面轨迹卫星数
const r3 = await evl(`(async () => {
  S.trailOn = true; seedTrails();
  const count = () => { const { eye } = cameraMatrix(1200, 700);
    let n = 0; for (const s of S.visible) { if (s.trail.length < 4 || s.lat === undefined) continue;
      const la = s.lat * D2R, lo = s.lon * D2R, rr = 1 + (s.alt || 0) / R_EARTH;
      if (rr * (Math.cos(la)*Math.cos(lo)*eye[0] + Math.cos(la)*Math.sin(lo)*eye[1] + Math.sin(la)*eye[2]) > rr*rr) n++; }
    return n; };
  const before = count();
  S.cam.lon += 180; S.cam.lat = 20;   // 旋转到对面
  const after = count();
  await new Promise(r => setTimeout(r, 800));
  return JSON.stringify({ frontBefore: before, frontAfterRotate: after });
})()`);
console.log('③ 轨迹可见性:', r3);
await send('Page.captureScreenshot', { format: 'png' }).then(async r => {
  const fs = await import('node:fs');
  fs.writeFileSync('_v9_check.png', Buffer.from(r.result.data, 'base64'));
});
process.exit(0);
