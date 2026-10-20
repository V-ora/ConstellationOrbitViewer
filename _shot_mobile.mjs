// 多视口布局验收截图：手机竖屏 / 小屏安卓 / 平板 / 桌面基准。
// 与 _shot.mjs 的区别：这里要 emul 触屏（mobile:true + touch），
// 因为 web/mobile.css 里有 @media (hover:none) 分支，不模拟触屏就测不到。
//
// 用法：node _shot_mobile.mjs <URL> <输出目录> [端口]
//   例：node _shot_mobile.mjs http://127.0.0.1:8799/ _shots 9351
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const URL0 = process.argv[2];
const OUTDIR = process.argv[3] || '_shots';
const DBG = parseInt(process.argv[4] || '9351', 10);
if (!URL0) { console.log('用法: node _shot_mobile.mjs <url> <outdir> [debugPort]'); process.exit(2); }
mkdirSync(OUTDIR, { recursive: true });

const EDGE = process.env.MSEDGE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const VIEWS = [
  // [标签, 宽, 高, DPR, 是否触屏]
  ['phone', 390, 844, 3, true],     // iPhone 14/13/12 逻辑分辨率
  ['small', 360, 780, 2, true],     // 常见小屏安卓
  ['phone-land', 844, 390, 3, true],// 手机横屏
  ['pad', 820, 1180, 2, true],      // 正好压在断点上：应仍是抽屉布局
  ['desk', 1440, 900, 1, false],    // 桌面基准：不应被移动样式污染
];

const T0 = Date.now();
const el = () => ((Date.now() - T0) / 1000).toFixed(1);
const sleep = ms => new Promise(r => setTimeout(r, ms));

const prof = mkdtempSync(path.join(tmpdir(), 'edge-mb-'));
const edge = spawn(EDGE, ['--headless=new', '--no-sandbox',
  '--enable-unsafe-swiftshader',            // 无 GPU 时用 SwiftShader 软件光栅，否则 WebGL 画不出
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`,
  '--window-size=1600,1000', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => { try { edge.kill(); } catch { } });

let target = null;
for (let i = 0; i < 60 && !target; i++) {
  try {
    const list = await (await fetch(`http://127.0.0.1:${DBG}/json`)).json();
    target = list.find(t => t.type === 'page');
  } catch { }
  if (!target) await sleep(150);
}
if (!target) { console.log('FATAL: CDP target 未就绪'); process.exit(1); }

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
async function evalJS(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  return r.result?.result?.value;
}
async function waitFor(expr, maxMs) {
  const t = Date.now();
  while (Date.now() - t < maxMs) {
    if (await evalJS(expr)) return true;
    await sleep(150);
  }
  return false;
}

await send('Runtime.enable');
await send('Page.enable');

let bad = 0;
for (const [label, w, h, dpr, touch] of VIEWS) {
  await send('Emulation.setDeviceMetricsOverride',
    { width: w, height: h, deviceScaleFactor: dpr, mobile: touch });
  await send('Emulation.setTouchEmulationEnabled',
    touch ? { enabled: true, maxTouchPoints: 5 } : { enabled: false });
  // 每次重新导航：device-width / hover 媒体查询要重新算，热改容易拿到上一轮的布局
  await send('Page.navigate', { url: URL0 });
  const ok = await waitFor(
    `document.readyState==='complete' && !!document.getElementById('list')
     && document.querySelectorAll('#list .item').length > 20
     && typeof S !== 'undefined' && S.sats && S.sats.length > 20`, 25000);
  if (!ok) console.log(`WARN[${label}] 数据未就绪，仍截图`);
  await sleep(1300);

  // 布局体检：横向溢出 / 关键元素可见性
  const diag = await evalJS(`(()=>{
    const de=document.documentElement, b=document.body;
    const q=s=>document.querySelector(s);
    const rect=s=>{const e=q(s); if(!e) return null; const r=e.getBoundingClientRect();
      return {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height),
              vis:getComputedStyle(e).display!=='none'};};
    const hdr=q('header'), ft=q('footer');
    const seg=q('.seg');
    return {
      vw: innerWidth, vh: innerHeight,
      overflowX: Math.max(de.scrollWidth, b.scrollWidth) - innerWidth,
      headerH: hdr?Math.round(hdr.getBoundingClientRect().height):-1,
      headerRows: hdr?Math.round(hdr.getBoundingClientRect().height):-1,
      segScroll: seg? (seg.scrollWidth - seg.clientWidth):-1,
      footerH: ft?Math.round(ft.getBoundingClientRect().height):-1,
      stage: rect('#stage'),
      left: rect('#left'), right: rect('#right'),
      mbLeft: rect('#mbLeft'), mbRight: rect('#mbRight'),
      hud: rect('#hud'), vlabel: rect('#vlabel'),
      touchCSS: matchMedia('(hover:none)').matches,
      listItems: document.querySelectorAll('#list .item').length
    };
  })()`);

  const png = path.join(OUTDIR, `mb-${label}-${w}x${h}.png`);
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  if (shot.result?.data) writeFileSync(png, Buffer.from(shot.result.data, 'base64'));
  else { console.log(`FATAL[${label}] 截图失败`); bad++; }

  const over = diag ? diag.overflowX : -999;
  let flag = 'OK';
  if (over > 1) { flag = 'OVERFLOW'; bad++; }
  if (diag && touch && w <= 900 && (!diag.mbLeft || !diag.mbLeft.vis)) { flag = 'NO-DRAWER-BTN'; bad++; }
  if (diag && !touch && diag.mbLeft && diag.mbLeft.vis) { flag = 'BTN-LEAK-ON-DESK'; bad++; }

  console.log(`[${label}] ${w}x${h} dpr${dpr} ${flag}`
    + ` | header ${diag?.headerH}px | footer ${diag?.footerH}px`
    + ` | stage ${diag?.stage?.w}x${diag?.stage?.h}`
    + ` | 横向溢出 ${over}px | seg可滑 ${diag?.segScroll}px`
    + (diag ? ` | hover:none=${diag.touchCSS} 列表${diag.listItems}条` : '')
    + ` | ${el()}s`);

  /* 手机竖屏再多拍几个交互场景：抽屉、2D、爬升图、过境预报。
     这些都是「手机上最容易塌」的地方，光看默认态看不出问题。 */
  if (label === 'phone') {
    const scenes = [
      ['drawer-left', `document.getElementById('mbLeft').click()`, 520],
      ['drawer-right', `document.getElementById('mbLeft').click();
                        document.getElementById('mbRight').click()`, 520],
      ['view-2d', `document.getElementById('mbRight').click();
                   document.querySelector('#viewSeg button[data-v="2d"]').click()`, 900],
      ['view-raise', `document.querySelector('#viewSeg button[data-v="raise"]').click()`, 3200],
      ['view-pass', `document.querySelector('#viewSeg button[data-v="pass"]').click()`, 1400],
    ];
    for (const [name, js, wait] of scenes) {
      await evalJS(`(()=>{try{${js}}catch(e){return 'ERR '+e.message};return 'ok'})()`);
      await sleep(wait);
      const d2 = await evalJS(`(()=>{const de=document.documentElement;
        return Math.max(de.scrollWidth, document.body.scrollWidth) - innerWidth;})()`);
      const s2 = await send('Page.captureScreenshot', { format: 'png' });
      const p2 = path.join(OUTDIR, `mb-${label}-${name}.png`);
      if (s2.result?.data) writeFileSync(p2, Buffer.from(s2.result.data, 'base64'));
      if (d2 > 1) { flag = 'OVERFLOW@' + name; bad++; }
      console.log(`    ↳ ${name} 横向溢出 ${d2}px ${d2 > 1 ? '!!' : ''}`);
    }
  }
}

console.log(bad === 0 ? 'ALL OK' : `有 ${bad} 项需要看`);
ws.close();
process.exit(bad === 0 ? 0 : 1);
