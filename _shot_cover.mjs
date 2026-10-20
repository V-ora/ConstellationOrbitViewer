// 「覆盖圆 · 最低仰角」预览截图：打开页面 → 2D 视图 → 选中一颗千帆星 → 开覆盖圆 → 选仰角 → 截图。
// 用法：node _shot_cover.mjs [baseURL] [输出png] [仰角 5/10/15/20/30] [高度px] [纬度筛选 high|low|任一纬度数字]
//   high = 星下点 |lat|>70°（看绕极极冠，2026-10-07 的球面真圆改动就靠这个看）；
//   low  = 星下点 |lat|<6°（赤道，形状最接近旧椭圆）。
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const BASE = process.argv[2] || 'http://127.0.0.1:8770/';
const OUT = process.argv[3] || '预览_覆盖圆_最低仰角.png';
const EL = parseInt(process.argv[4] || '10', 10);
const H = parseInt(process.argv[5] || '1240', 10);
const LATMODE = (process.argv[6] || '').toLowerCase();
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9342;

const prof = mkdtempSync(path.join(tmpdir(), 'edge-shotcover-'));
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

// 选星条件：默认（不传）仍是「千帆 + alt>1000」；传 high/low 时按星下点纬度挑，
// 传数字则按「纬度最接近该值」挑——高纬看绕极极冠，赤道看最接近旧椭圆的形状。
const findExpr = LATMODE === 'high' ? 'x.group==="qianfan"&&x.alt>1000&&Math.abs(x.lat)>70'
  : LATMODE === 'south' ? 'x.group==="qianfan"&&x.alt>1000&&x.lat<-72'
  : LATMODE === 'low' ? 'x.group==="qianfan"&&x.alt>1000&&Math.abs(x.lat)<6'
  : (LATMODE !== '' && !isNaN(Number(LATMODE)))
    ? `x.group==="qianfan"&&x.alt>1000&&Math.abs(x.lat-(${Number(LATMODE)}))<12`
  : 'x.group==="qianfan"&&x.alt>1000';
// 切 2D → 选一颗千帆星 → 开覆盖圆 → 设仰角（全部走页面自己的入口，不依赖元素坐标）
await evl(`S.setView("2d")`);
await sleep(400);
let picked = await evl(`(()=>{const s=S.sats.find(x=>${findExpr});
  if(!s) return null; select(s); return JSON.stringify({name:s.name,alt:s.alt,lat:s.lat,lon:s.lon});})()`);
if (!picked) {
  picked = await evl(`(()=>{const s=S.sats.find(x=>x.group==="qianfan"&&x.alt>1000);
    if(!s) return null; select(s); return JSON.stringify({name:s.name,alt:s.alt,lat:s.lat,lon:s.lon});})()`);
}
await sleep(700);
await evl(`(()=>{const b=document.querySelector('#btnCover'); if(!S.coverOn) b.click();})()`);
await sleep(300);
await evl(`(()=>{const s=document.querySelector('#coverEl');
  s.value=${JSON.stringify(String(EL))}; s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
await sleep(1200);

const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
writeFileSync(OUT, Buffer.from(shot.data, 'base64'));
// 可选：再单独截一张「只含 2D 画布」的放大图，方便人眼核几何（整页图里覆盖圆太淡、太小）
const OUT2 = process.argv[7] || '';
if (OUT2) {
  const rect = JSON.parse(await evl(`(()=>{const r=document.querySelector('#cv2d').getBoundingClientRect();` +
    `return JSON.stringify({x:r.x,y:r.y,w:r.width,h:r.height});})()`));
  const s2 = await send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: false,
    clip: { x: rect.x, y: rect.y, width: rect.w, height: rect.h, scale: 2 } });
  writeFileSync(OUT2, Buffer.from(s2.data, 'base64'));
  console.log('canvas crop:', OUT2, JSON.stringify(rect));
}
const stats = await evl(`JSON.stringify({box:document.querySelector('#coverEl').value,
  on:S.coverOn, want:S.view, lat:S.selected.lat, lon:S.selected.lon,
  r:Math.round(horizonKm(S.selected.alt,Number(document.querySelector('#coverEl').value)))})`);
console.log('screenshot:', OUT, '选中=', picked, stats);
process.exit(0);
