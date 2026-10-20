// 纯截图：打开大图 HTML，等画完，整页截图存盘。
// 不做任何断言、不改页面状态 —— 与 _chk_*.mjs（自检用，会拖滑块/切按钮）分开，
// 所以拿到的永远是「默认状态」的干净大图。
// 用法：node _shot.mjs <URL或file路径> <输出png> <页面高度>
//
// ★ 2026-10-19 提速：原来是固定 sleep(4000) + sleep(900)，实测单张 5.8~5.9 s。
//   大图是**同步一次性绘制**的 canvas（模板里没有入场动画，只有表格 hover 的 CSS 过渡），
//   所以不需要盲等，改成三个条件等待：
//     ① document.readyState==='complete' 且 #cv 已存在
//     ② canvas 已有非空像素（图真的画出来了）
//     ③ 改完视口高度后 scrollHeight 连续两次相同（布局稳定）
//   每步都有上限，超时只是「不等了、照截」，不会把流程卡死。
// 端口：默认 9341，可用环境变量 SHOT_DBG 覆盖 —— **两张图并行截图时必须给不同端口**。
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const URL0 = process.argv[2];
const OUT = process.argv[3];
const H = parseInt(process.argv[4] || '1900', 10);
if (!URL0 || !OUT) { console.log('用法: node _shot.mjs <url> <out.png> [height]'); process.exit(2); }

const EDGE = process.env.MSEDGE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = parseInt(process.env.SHOT_DBG || '9341', 10);
const T0 = Date.now();
const el = () => ((Date.now() - T0) / 1000).toFixed(1);

const prof = mkdtempSync(path.join(tmpdir(), 'edge-shot-'));
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-sandbox',
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`,
  '--window-size=1600,980', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => { try { edge.kill(); } catch { } });

const sleep = ms => new Promise(r => setTimeout(r, ms));

let target = null;
for (let i = 0; i < 50 && !target; i++) {
  try {
    const list = await (await fetch(`http://127.0.0.1:${DBG}/json`)).json();
    target = list.find(t => t.type === 'page');
  } catch { }
  if (!target) await sleep(150);
}
if (!target) { console.log('FATAL: CDP target 未就绪（Edge 没起来？）'); process.exit(1); }

const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

let mid = 0; const pend = new Map();
ws.onmessage = ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); }
};
function send(method, params = {}) {
  const id = ++mid;
  return new Promise(res => { pend.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
}

/** 反复求值，条件为真就返回 true；超时返回 false（只记一笔，不阻断）。 */
async function waitFor(expr, maxMs, stepMs = 120) {
  const t0 = Date.now();
  while (Date.now() - t0 < maxMs) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
    if (r.result?.result?.value) return true;
    await sleep(stepMs);
  }
  return false;
}

await send('Runtime.enable');
await send('Page.enable');
await send('Page.navigate', { url: URL0 });

// ① 文档加载完 + canvas 已插入
const tLoad = await waitFor(
  `document.readyState==='complete' && !!document.getElementById('cv')`, 15000);
if (!tLoad) console.log(`WARN: 加载条件等待超时（${el()}s），继续尝试截图`);

// ② canvas 真的画完了（非空像素 > 0 且**连续三次采样不再变化**）
//    ★ 别只判「> 0」：实测画布是先画背景/网格、再逐层画曲线，
//      只判 >0 会在 221000 像素时就放行（画完是 1161615），截出半张图。
let px = -1, pxPrev = -1, pxSame = 0;
const t0 = Date.now();
for (; Date.now() - t0 < 12000;) {
  const r = await send('Runtime.evaluate', {
    expression: `(()=>{const c=document.getElementById('cv'); if(!c) return -1;
      const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
      let n=0; for(let i=3;i<d.length;i+=4) if(d[i]) n++;
      return n;})()`, returnByValue: true,
  });
  px = r.result?.result?.value ?? -1;
  if (px > 0 && px === pxPrev) { if (++pxSame >= 5) break; } else pxSame = 0;
  pxPrev = px;
  await sleep(150);
}
if (px <= 0) console.log('WARN: canvas 似乎没画出来（非空像素=' + px + '）');

await send('Emulation.setDeviceMetricsOverride',
  { width: 1600, height: H, deviceScaleFactor: 1, mobile: false });

// ③ 布局稳定：scrollHeight 连续两次相同
let prev = -1;
for (let i = 0; i < 40; i++) {
  const r = await send('Runtime.evaluate',
    { expression: 'document.documentElement.scrollHeight', returnByValue: true });
  const h = r.result?.result?.value ?? 0;
  if (h > 0 && h === prev) break;
  prev = h; await sleep(100);
}
await sleep(120);                                  // 再给一帧合成时间

const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
if (!shot.result?.data) { console.log('FATAL: 截图失败'); process.exit(1); }
writeFileSync(OUT, Buffer.from(shot.result.data, 'base64'));
console.log(`screenshot: ${OUT}  非空像素=${px}  用时=${el()}s`);
ws.close();
process.exit(0);
