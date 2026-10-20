// 移动端 / 响应式自检。断言都是「关系式」，不写死像素与颗数——
// 星座会持续发射、视口也会变，写死的数字迟早假红（火箭末级那轮的教训）。
//
// 用法：node _chk_mobile.mjs [URL] [debugPort]
//   默认 http://127.0.0.1:8799/ 9354
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const URL0 = process.argv[2] || 'http://127.0.0.1:8799/';
const DBG = parseInt(process.argv[3] || '9354', 10);
const EDGE = process.env.MSEDGE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';

let pass = 0, fail = 0;
function ok(name, cond, detail = '') {
  if (cond) { pass++; console.log(`  ok   ${name}${detail ? '  (' + detail + ')' : ''}`); }
  else { fail++; console.log(`  FAIL ${name}${detail ? '  (' + detail + ')' : ''}`); }
}

const T0 = Date.now();
const el = () => ((Date.now() - T0) / 1000).toFixed(1);
const sleep = ms => new Promise(r => setTimeout(r, ms));

const prof = mkdtempSync(path.join(tmpdir(), 'edge-chkmb-'));
const edge = spawn(EDGE, ['--headless=new', '--no-sandbox', '--enable-unsafe-swiftshader',
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`,
  '--window-size=1500,950', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => { try { edge.kill(); } catch { } });

let target = null;
for (let i = 0; i < 60 && !target; i++) {
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
const send = (method, params = {}) => new Promise(res => {
  const id = ++mid; pend.set(id, res); ws.send(JSON.stringify({ id, method, params }));
});
async function js(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  const ex = r.result?.exceptionDetails;
  if (ex) {
    const msg = ex.exception?.description || ex.text || 'unknown';
    console.log('    !! 页面内异常：' + String(msg).split('\n')[0]);
    return { __err: msg };
  }
  return r.result?.result?.value;
}
async function waitFor(expr, maxMs) {
  const t = Date.now();
  while (Date.now() - t < maxMs) { if (await js(expr)) return true; await sleep(150); }
  return false;
}

await send('Runtime.enable');
await send('Page.enable');

async function goto(w, h, dpr, touch) {
  await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: dpr, mobile: touch });
  await send('Emulation.setTouchEmulationEnabled', touch ? { enabled: true, maxTouchPoints: 5 } : { enabled: false });
  await send('Page.navigate', { url: URL0 });
  const ready = await waitFor(
    `document.readyState==='complete'
     && document.querySelectorAll('#list .item').length > 20
     && typeof S !== 'undefined' && S.sats && S.sats.length > 20`, 25000);
  await sleep(900);
  return ready;
}

/* ============================================================ 手机竖屏 390×844 */
console.log(`\n── 手机竖屏 390×844（触屏模拟）`);
let ready = await goto(390, 844, 3, true);
ok('页面数据加载完成（列表 > 20 行且有 S.sats）', ready);

let d = await js(`(()=>{
  const app=getComputedStyle(document.getElementById('app'));
  const de=document.documentElement, b=document.body;
  const vis=s=>{const e=document.querySelector(s); return !!e && getComputedStyle(e).display!=='none';};
  const r=s=>{const e=document.querySelector(s); const q=e.getBoundingClientRect();
              return {l:Math.round(q.left),r:Math.round(q.right),w:Math.round(q.width),h:Math.round(q.height)};};
  const footer=document.querySelector('footer');
  const ml=document.getElementById('mbLeft').getBoundingClientRect();
  const sl=footer.querySelector('input[type=range]').getBoundingClientRect();
  return {
    cols: app.gridTemplateColumns.split(' ').length,
    overflowX: Math.max(de.scrollWidth,b.scrollWidth)-innerWidth,
    btnL: vis('#mbLeft'), btnR: vis('#mbRight'), btnF: vis('#mbFull'),
    btnOrderOK: ml.right <= sl.left + 1,
    left: r('#left'), stage: r('#stage'),
    maskPE: getComputedStyle(document.getElementById('mbMask')).pointerEvents,
    vp: (document.querySelector('meta[name=viewport]')||{}).content || ''
  };
})()`);
ok('单列布局（grid 只有 1 列）', d.cols === 1, 'cols=' + d.cols);
ok('无横向溢出（≤1px）', d.overflowX <= 1, d.overflowX + 'px');
ok('页脚出现 ☰ / ℹ / ⤢ 三个按钮', d.btnL && d.btnR && d.btnF);
ok('☰ 排在时间轴左边（拇指区顺序合理）', d.btnOrderOK);
ok('#left 默认滑出屏幕外（右侧 ≤ 0）', d.left.r <= 0, 'right=' + d.left.r);
ok('舞台占满宽度', Math.abs(d.stage.w - 390) <= 1, 'stage.w=' + d.stage.w);
ok('遮罩默认不可点（pointer-events:none）', d.maskPE === 'none', d.maskPE);
ok('viewport 带 viewport-fit=cover（刘海屏）', /viewport-fit=cover/.test(d.vp), d.vp);

// 打开左抽屉
await js(`document.getElementById('mbLeft').click()`);
await sleep(420);
d = await js(`(()=>{
  const b=getComputedStyle(document.body);
  const l=document.getElementById('left').getBoundingClientRect();
  const m=document.getElementById('mbMask');
  return {cls:document.body.className, left:Math.round(l.left), w:Math.round(l.width),
          maskPE:getComputedStyle(m).pointerEvents, maskOp:getComputedStyle(m).opacity,
          closeBtn:!!document.querySelector('#left .mb-x')};
})()`);
ok('点 ☰ 后 body 带 mb-l', /mb-l/.test(d.cls), d.cls);
ok('左抽屉滑入到 left=0', d.left === 0, 'left=' + d.left);
ok('抽屉宽度不超过视口', d.w <= 390 && d.w >= 260, 'w=' + d.w);
ok('遮罩变为可点（能点空白关掉）', d.maskPE === 'auto' && Number(d.maskOp) > 0.5, d.maskPE + ' op=' + d.maskOp);
ok('抽屉内有 ✕ 关闭按钮', d.closeBtn);

// 点遮罩关闭
await js(`document.getElementById('mbMask').click()`);
await sleep(420);
d = await js(`(()=>{const l=document.getElementById('left').getBoundingClientRect();
  return {cls:document.body.className, right:Math.round(l.right)};})()`);
ok('点遮罩后抽屉收起', !/mb-l/.test(d.cls) && d.right <= 0, d.cls + ' right=' + d.right);

// 右抽屉
await js(`document.getElementById('mbRight').click()`);
await sleep(420);
d = await js(`(()=>{const r=document.getElementById('right').getBoundingClientRect();
  return {cls:document.body.className, r:Math.round(r.right), w:Math.round(r.width)};})()`);
ok('点 ℹ 后右抽屉贴右边（right == 视口宽）', /mb-r/.test(d.cls) && Math.abs(d.r - 390) <= 1,
  `cls=${d.cls} right=${d.r} w=${d.w}`);
await js(`document.getElementById('mbMask').click()`);
await sleep(350);

/* ------------------------------------------------------ 双指捏合（核心交互） */
/* ★ 每一步之间要真的让出一次事件循环（await tick）：
   真实的两次触摸是两个独立任务，中间微任务/宏任务都会跑；
   全塞在一个同步 IIFE 里等于「零间隔狂按」，测不出真实行为。 */
d = await js(`(async()=>{
  const stage=document.getElementById('stage');
  const tick=()=>new Promise(r=>setTimeout(r,30));
  const fire=(t,id,x,y)=>stage.dispatchEvent(new PointerEvent(t,{
    pointerId:id, pointerType:'touch', clientX:x, clientY:y, bubbles:true, cancelable:true}));
  const snap=()=>({dist:S.cam.dist, lon:S.cam.lon, lat:S.cam.lat,
                   dragging:stage.classList.contains('dragging')});
  const before=snap();
  fire('pointerdown',11,180,420); await tick();
  fire('pointerdown',12,300,420); await tick();
  const afterDown=snap();                 // 两指落下后应立即取消旋转
  fire('pointermove',11,120,420); await tick();
  fire('pointermove',12,360,420); await tick();
  const mid=snap();                       // 张开到 240px ⇒ 应放大
  fire('pointermove',11,160,420); await tick();
  fire('pointermove',12,320,420); await tick();
  const back=snap();                      // 收拢回 160px ⇒ 应推远
  fire('pointerup',11,160,420); fire('pointerup',12,320,420); await tick();
  return {before, afterDown, mid, back, afterUp:snap()};
})()`);
ok('两指落下后立即解除拖拽（dragging 摘掉）', d && d.afterDown.dragging === false,
  d && 'dragging=' + d.afterDown.dragging);
ok('捏合（张开）使镜头拉近：dist 变小', d && d.mid.dist < d.before.dist - 1e-6,
  d && `${d.before.dist.toFixed(3)} → ${d.mid.dist.toFixed(3)}`);
ok('捏合（收拢）使镜头推远：dist 变大', d && d.back.dist > d.mid.dist + 1e-6,
  d && `${d.mid.dist.toFixed(3)} → ${d.back.dist.toFixed(3)}`);
ok('捏合过程中地球没有被转走（lon/lat 不变）',
  d && Math.abs(d.mid.lon - d.before.lon) < 1e-9 && Math.abs(d.mid.lat - d.before.lat) < 1e-9,
  d && `Δlon=${(d.mid.lon - d.before.lon).toFixed(6)} Δlat=${(d.mid.lat - d.before.lat).toFixed(6)}`);
ok('捏合后 dist 仍落在合法区间 [1.14, 45]', d && d.back.dist >= 1.14 && d.back.dist <= 45,
  d && d.back.dist.toFixed(3));

/* ------------------------------------------------------ 手机访问卡片 /api/net */
d = await js(`fetch('/api/net').then(r=>r.json()).then(j=>j)`);
ok('/api/net 可用且带 lan_enabled', d && typeof d.lan_enabled === 'boolean', JSON.stringify(d));
if (d && d.lan_enabled) {
  ok('局域网模式给出至少一个 http 地址', Array.isArray(d.lan) && d.lan.length > 0 && /^http:\/\/[\d.]+:\d+\/$/.test(d.lan[0]),
    (d.lan || []).join(','));
}
const card = await waitFor(`document.querySelector('#mbNet .mb-url, #mbNet code') !== null`, 6000);
ok('右侧「数据状态」里渲染出手机访问地址', card);

/* ============================================================ 桌面 1440×900 */
console.log(`\n── 桌面 1440×900（无触屏）`);
ready = await goto(1440, 900, 1, false);
ok('页面数据加载完成', ready);
d = await js(`(()=>{
  const app=getComputedStyle(document.getElementById('app'));
  const de=document.documentElement,b=document.body;
  const el=s=>document.querySelector(s);
  const vis=s=>{const e=el(s); return !!e && getComputedStyle(e).display!=='none';};
  const l=el('#left').getBoundingClientRect(), r=el('#right').getBoundingClientRect();
  return {
    cols: app.gridTemplateColumns.split(' ').length,
    overflowX: Math.max(de.scrollWidth,b.scrollWidth)-innerWidth,
    btnL: vis('#mbLeft'), btnR: vis('#mbRight'), x: vis('#left .mb-x'),
    leftL: Math.round(l.left), leftW: Math.round(l.width),
    rightR: Math.round(innerWidth - r.right),
    stageGrow: getComputedStyle(el('#stage')).position
  };
})()`);
ok('仍是三栏布局', d.cols === 3, 'cols=' + d.cols);
ok('无横向溢出', d.overflowX <= 1, d.overflowX + 'px');
ok('桌面不出现手机专用按钮（☰ / ℹ / ✕ 全隐藏）', !d.btnL && !d.btnR && !d.x);
ok('#left 留在左栏（left=0 且宽度 > 200）', d.leftL === 0 && d.leftW > 200, `left=${d.leftL} w=${d.leftW}`);
ok('#right 贴右栏', d.rightR <= 1, 'gap=' + d.rightR);

/* ============================================================ 平板 820×1180 */
console.log(`\n── 平板 820×1180（触屏）`);
ready = await goto(820, 1180, 2, true);
ok('页面数据加载完成', ready);
d = await js(`(()=>{
  const app=getComputedStyle(document.getElementById('app'));
  const de=document.documentElement,b=document.body;
  const vis=s=>{const e=document.querySelector(s); return !!e && getComputedStyle(e).display!=='none';};
  const st=document.getElementById('stage').getBoundingClientRect();
  return {cols:app.gridTemplateColumns.split(' ').length,
          overflowX:Math.max(de.scrollWidth,b.scrollWidth)-innerWidth,
          btnL:vis('#mbLeft'), stageW:Math.round(st.width), stageH:Math.round(st.height)};
})()`);
ok('单列布局', d.cols === 1, 'cols=' + d.cols);
ok('无横向溢出', d.overflowX <= 1, d.overflowX + 'px');
ok('抽屉按钮可见', d.btnL);
// 关系断言：舞台高度应占视口大头（不被顶栏/页脚吃掉）
ok('舞台高度占比合理（> 75% 视口高）', d.stageH / 1180 > 0.75,
  `stage.h=${d.stageH} / 1180 = ${(d.stageH / 1180 * 100).toFixed(1)}%`);

console.log(`\n${pass} 项通过 / ${fail} 项失败  （用时 ${el()}s）`);
ws.close();
process.exit(fail === 0 ? 0 : 1);
