// 「过境预报」视图专项自检（CDP，真实输入）。
// 用法：先起服务，然后
//   node _chk_pass.mjs [页面URL，默认 http://127.0.0.1:8770/]
// 覆盖：页签切换 / 经纬度解析（十进制·度分秒·方向字母·中文方位）/ 越界拒绝 /
//       粘贴拆分 / 此刻头顶 / 未来过境计算 / localStorage 持久化 / 地图选点 /
//       2D 观测点标记 / 3D 观测点标记 / 控制台异常（页面报错即判失败）。
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const APP = process.argv[2] || 'http://127.0.0.1:8770/';
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9335;

const prof = mkdtempSync(path.join(tmpdir(), 'edge-prof-pass-'));
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-sandbox',
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`,
  '--window-size=1280,940', 'about:blank'], { stdio: 'ignore' });
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
const pageErrors = [];
ws.onmessage = ev => {
  const m = JSON.parse(ev.data);
  if (m.method === 'Runtime.exceptionThrown') {
    const d = m.params && m.params.exceptionDetails;
    pageErrors.push(String((d && (d.text || (d.exception && d.exception.description))) || 'unknown'));
  }
  if (m.id && pend.has(m.id)) { pend.get(m.id).res(m.result); pend.delete(m.id); }
};
const send = (method, params = {}) => new Promise((res, rej) => {
  const id = ++mid; pend.set(id, { res, rej });
  ws.send(JSON.stringify({ id, method, params }));
});
const evl = async expr => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) return { __err: String(r.exceptionDetails.text) };
  return r.result.value;
};

await send('Page.enable');
await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 940, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: APP });
await sleep(2500);

let ok = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { ok++; console.log(`PASS ${name} ${extra}`); }
  else { fail++; console.log(`FAIL ${name} ${extra}`); }
};
const center = async sel => await evl(
  `(()=>{const el=document.querySelector(${JSON.stringify(sel)});if(!el)return null;` +
  `const r=el.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
const click = async (sel, wait = 500) => {
  const p = await center(sel);
  if (!p) return false;
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: p.x, y: p.y, button: 'left', clickCount: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: p.x, y: p.y, button: 'left', clickCount: 1 });
  await sleep(wait);
  return true;
};
// 真实键盘输入：先真实点击聚焦，再插字（不用直接改 .value）
const typeInto = async (sel, text) => {
  await click(sel, 120);
  await evl(`(()=>{const el=document.querySelector(${JSON.stringify(sel)});if(el){el.value='';}})()`);
  await send('Input.insertText', { text });
  await sleep(120);
};

// 数据就绪
let total = 0;
for (let i = 0; i < 75; i++) {
  const v = await evl('(typeof S !== "undefined" && S.sats) ? S.sats.length : 0');
  if (typeof v === 'number' && v > 100) { await sleep(1200); total = v; break; }
  await sleep(2000);
}
check('0 数据就绪', total > 100, `sats=${total}`);

// 1 页签
const nPills = await evl(`document.querySelectorAll('#viewSeg button[data-v]').length`);
check('1 顶部页签数量', nPills === 4, `n=${nPills}`);
check('2 点「过境预报」', await click('#viewSeg button[data-v="pass"]', 800));
const r2 = await evl(`JSON.stringify({v:S.view,` +
  `on:document.querySelector('#passView').classList.contains('on'),` +
  `disp:getComputedStyle(document.querySelector('#passView')).display,` +
  `cv3d:getComputedStyle(document.querySelector('#cv3d')).display,` +
  `hud:getComputedStyle(document.querySelector('#hud')).display})`);
const o2 = JSON.parse(r2);
check('2b 进入过境预报', o2.v === 'pass' && o2.on && o2.disp === 'block');
check('2c 画布与 HUD 隐藏', o2.cv3d === 'none' && o2.hud === 'none', `cv3d=${o2.cv3d}`);

// 3 空输入报错
await click('#viewSeg button[data-v="pass"]', 300);
await evl(`(()=>{const a=document.querySelector('#pvLat'),b=document.querySelector('#pvLon');a.value='';b.value='';})()`);
await click('#pvApply', 300);
const r3 = await evl(`(document.querySelector('#pvMsg')||{}).textContent || ''`);
check('3 空输入有提示', r3.length > 0 && /填/.test(r3), r3.slice(0, 30));

// 4 十进制
await typeInto('#pvLat', '39.9042');
await typeInto('#pvLon', '116.4074');
await click('#pvApply', 400);
const r4 = await evl(`JSON.stringify({lat:OBS.lat,lon:OBS.lon,` +
  `geom:!!OBS.geom,msg:(document.querySelector('#pvMsg')||{}).textContent||''})`);
const o4 = JSON.parse(r4);
check('4 十进制解析', Math.abs(o4.lat - 39.9042) < 1e-6 && Math.abs(o4.lon - 116.4074) < 1e-6 && o4.geom,
  `lat=${o4.lat} lon=${o4.lon}`);

// 5 度分秒 + 方向字母
await typeInto('#pvLat', "39°54′15″N");
await typeInto('#pvLon', "116°23′29″E");
await click('#pvApply', 400);
const r5 = await evl(`JSON.stringify({lat:OBS.lat,lon:OBS.lon})`);
const o5 = JSON.parse(r5);
check('5 度分秒解析', Math.abs(o5.lat - 39.90417) < 2e-4 && Math.abs(o5.lon - 116.39139) < 2e-4,
  `lat=${o5.lat} lon=${o5.lon}`);

// 6 中文方位 + 负号
await typeInto('#pvLat', '南纬33.87');
await typeInto('#pvLon', '西经118.24');
await click('#pvApply', 400);
const r6 = await evl(`JSON.stringify({lat:OBS.lat,lon:OBS.lon})`);
const o6 = JSON.parse(r6);
check('6 中文方位解析', o6.lat < 0 && o6.lon < 0 && Math.abs(o6.lat + 33.87) < 1e-6 && Math.abs(o6.lon + 118.24) < 1e-6,
  `lat=${o6.lat} lon=${o6.lon}`);

// 7 越界必须拒绝（不得悄悄截断）
await typeInto('#pvLat', '120');
await typeInto('#pvLon', '116.4');
await click('#pvApply', 400);
const r7 = await evl(`JSON.stringify({lat:OBS.lat,` +
  `msg:(document.querySelector('#pvMsg')||{}).textContent||'',` +
  `cls:(document.querySelector('#pvMsg')||{className:''}).className})`);
const o7 = JSON.parse(r7);
check('7 越界拒绝且不改动原值', o7.lat < 0 && Math.abs(o7.lat + 33.87) < 1e-6 && /超出/.test(o7.msg) && /err/.test(o7.cls),
  `lat=${o7.lat} msg=${o7.msg.slice(0, 24)}`);

// 8 乱码拒绝
await typeInto('#pvLat', 'abc');
await click('#pvApply', 300);
const r8 = await evl(`(document.querySelector('#pvMsg')||{}).textContent||''`);
check('8 乱码拒绝', /无法识别/.test(r8), r8.slice(0, 24));

// 9 粘贴拆分（直接调解析 + 分派 paste 事件，模拟剪贴板两段数字）
const r9 = await evl(`(()=>{const el=document.querySelector('#pvLat');` +
  `const dt=new DataTransfer();dt.setData('text','31.2304, 121.4737');` +
  `el.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));` +
  `return JSON.stringify({lat:document.querySelector('#pvLat').value,lon:document.querySelector('#pvLon').value});})()`);
const o9 = JSON.parse(r9);
check('9 粘贴按纬度,经度拆分', o9.lat === '31.2304' && o9.lon === '121.4737', `${o9.lat} / ${o9.lon}`);

// 10 设定北京 → 此刻头顶应非空
await evl(`(()=>{const a=document.querySelector('#pvLat'),b=document.querySelector('#pvLon');a.value='39.9042';b.value='116.4074';})()`);
await click('#pvApply', 500);
await sleep(400);
const r10 = await evl(`JSON.stringify({note:document.querySelector('#pvNowNote').textContent,` +
  `rows:document.querySelectorAll('#pvNow .pv-row').length})`);
const o10 = JSON.parse(r10);
check('10 此刻头顶有结果', o10.rows > 0 && /颗/.test(o10.note), `rows=${o10.rows} note=${o10.note}`);

// 11 过境计算
await evl(`(()=>{const s=document.querySelector('#pvHours');s.value='3';s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
const t11 = Date.now();
check('11 点「计算过境」', await click('#pvGo', 300));
let passes = 0, waited = 0;
for (let i = 0; i < 120; i++) {          // 最多等 60s
  await sleep(500); waited += 500;
  passes = (await evl('(typeof OBS!=="undefined"&&OBS.passes)?OBS.passes.length:0')) || 0;
  const busy = await evl('(typeof OBS!=="undefined")?!!OBS.computing:false');
  if (!busy && passes >= 0) { if (Date.now() - t11 > 1000) break; }
}
const r11 = await evl(`JSON.stringify({n:OBS.passes.length,rows:document.querySelectorAll('#pvUp .pv-row').length,` +
  `note:document.querySelector('#pvUpNote').textContent,` +
  `sorted:OBS.passes.every((p,i,a)=>i===0||a[i-1].start<=p.start),` +
  `sane:OBS.passes.every(p=>p.maxEl>=OBS.minEl-1e-6&&p.end>p.start)})`);
const o11 = JSON.parse(r11);
check('11b 过境结果合理', o11.n > 0 && o11.sorted && o11.sane, `n=${o11.n} ${waited}ms`);
check('11c 过境列表已渲染', o11.rows > 0 && /\d/.test(o11.note), `rows=${o11.rows} note=${o11.note}`);

// 12 最低仰角调高 → 过境次数应减少或持平
await evl(`(()=>{const s=document.querySelector('#pvMinEl');s.value='30';s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
await click('#pvGo', 300);
for (let i = 0; i < 120; i++) {
  await sleep(500);
  const busy = await evl('(typeof OBS!=="undefined")?!!OBS.computing:false');
  if (!busy) break;
}
await sleep(300);
const n30 = (await evl('OBS.passes.length')) || 0;
check('12 仰角阈值生效', n30 <= o11.n, `10°→${o11.n} 次, 30°→${n30} 次`);
await evl(`(()=>{const s=document.querySelector('#pvMinEl');s.value='10';s.dispatchEvent(new Event('change',{bubbles:true}));})()`);

// 12b 点结果行 → 右侧「选中卫星」应切到这颗星（data-norad 委托）
const r12b = await evl(`(()=>{const row=document.querySelector('#pvUp .pv-row[data-norad]')` +
  `||document.querySelector('#pvNow .pv-row[data-norad]');` +
  `if(!row)return JSON.stringify({ok:false});row.click();` +
  `return JSON.stringify({ok:true,norad:row.dataset.norad,` +
  `sel:S.selected?String(S.selected.norad):null,` +
  `detail:(document.querySelector('#detail')||{}).textContent||''});})()`);
const o12b = JSON.parse(r12b);
check('12b 点结果行选中该星', o12b.ok === true && o12b.sel === o12b.norad &&
  o12b.detail.indexOf('点选一颗卫星') < 0, r12b.slice(0, 90));

// 13 localStorage 持久化：整页重载后仍记得观测点
await send('Page.navigate', { url: APP });
await sleep(3000);
for (let i = 0; i < 60; i++) {
  const v = await evl('(typeof S !== "undefined" && S.sats) ? S.sats.length : 0');
  if (typeof v === 'number' && v > 100) break;
  await sleep(1500);
}
const r13 = await evl(`JSON.stringify({lat:OBS.lat,lon:OBS.lon,` +
  `fLat:document.querySelector('#pvLat').value,` +
  `minEl:document.querySelector('#pvMinEl').value,scope:document.querySelector('#pvScope').value})`);
const o13 = JSON.parse(r13);
check('13 重载后观测点持久化', Math.abs(o13.lat - 39.9042) < 1e-6 && Math.abs(o13.lon - 116.4074) < 1e-6,
  `lat=${o13.lat} lon=${o13.lon} 框=${o13.fLat}`);

// 14 2D 观测点标记：进入 2D 后画布应出现红色系像素
await click('#viewSeg button[data-v="2d"]', 900);
const r14 = await evl(`(()=>{const cv=document.querySelector('#cv2d');const g=cv.getContext('2d');` +
  `const d=g.getImageData(0,0,cv.width,cv.height).data;let red=0;` +
  `for(let i=0;i<d.length;i+=4){if(d[i]>170&&d[i+1]<90&&d[i+2]<90)red++;}return red;})()`);
check('14 2D 画布有观测点标记', typeof r14 === 'number' && r14 > 20, `红色像素=${r14}`);

// 15 3D 观测点标记：相机对准观测点后应显示 DOM 标记
await click('#viewSeg button[data-v="3d"]', 600);
await evl(`(()=>{S.cam.lon=OBS.lon;S.cam.lat=20;S.cam.dist=2.6;})()`);
await sleep(900);
const r15 = await evl(`JSON.stringify({d:getComputedStyle(document.querySelector('#obs3d')).display,` +
  `lb:document.querySelector('#obs3dLb').textContent})`);
const o15 = JSON.parse(r15);
check('15 3D 观测点标记显示', o15.d === 'block' && /观测点/.test(o15.lb), `${o15.d} ${o15.lb}`);

// 16 地图选点：切 2D → 点地图上一个已知位置 → 观测点应等于该位置的经纬度
await click('#viewSeg button[data-v="pass"]', 500);
check('16 点「地图选点」', await click('#pvPick', 900));
const r16a = await evl(`JSON.stringify({v:S.view,pick:OBS.pick})`);
const o16a = JSON.parse(r16a);
check('16b 已切 2D 且等待拾取', o16a.v === '2d' && o16a.pick === true, JSON.stringify(o16a));
// 用页面里同一套 mapLayout 反推「地图内 30%,70% 处」的屏幕坐标与应有经纬度。
// ★ 必须先在画布像素里挑点、再换算成 CSS 像素，否则画布像素比 ≠ 1 时换算不上，
//   点会落到地图外，obsPickAt 直接拒绝 → 误判成"取点不生效"（自检脚本自身踩过）。
const probe = await evl(`(()=>{const cv=document.querySelector('#cv2d');const r=cv.getBoundingClientRect();` +
  `const L=mapLayout(cv.width,cv.height);` +
  `const X=L.ox+L.mw*0.30, Y=L.oy+L.mh*0.70;` +
  `const cx=r.left+X/cv.width*r.width, cy=r.top+Y/cv.height*r.height;` +
  `return JSON.stringify({cx:cx,cy:cy,lat:90-(Y-L.oy)/L.mh*180,lon:(X-L.ox)/L.mw*360-180});})()`);
const P = JSON.parse(probe);
await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: P.cx, y: P.cy, button: 'left', clickCount: 1 });
await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: P.cx, y: P.cy, button: 'left', clickCount: 1 });
await sleep(900);
const r16 = await evl(`JSON.stringify({lat:OBS.lat,lon:OBS.lon,pick:OBS.pick,` +
  `msg:(document.querySelector('#pvMsg')||{}).textContent||''})`);
const o16 = JSON.parse(r16);
check('16c 地图取点位置正确', o16.pick === false &&
  Math.abs(o16.lat - P.lat) < 0.6 && Math.abs(o16.lon - P.lon) < 0.6 && /地图取点/.test(o16.msg),
  `期望 ${P.lat.toFixed(2)},${P.lon.toFixed(2)} → 实得 ${o16.lat},${o16.lon}`);

// 17 无控制台异常
check('17 无页面 JS 异常', pageErrors.length === 0, pageErrors.slice(0, 3).join(' | '));

// 18 XSS：把带标签的载荷输进经纬度框 —— 错误提示必须原样当文本，不得生成元素
//    （新增输入框是新增攻击面，写进自检基线，防以后有人改成 innerHTML）
await click('#viewSeg button[data-v="pass"]', 500);
await typeInto('#pvLat', '<img src=x onerror=window.__xss=1>');
await typeInto('#pvLon', '116.4');
await click('#pvApply', 500);
const r18 = await evl(`JSON.stringify({img:document.querySelectorAll('#pvMsg img').length,` +
  `html:(document.querySelector('#pvMsg')||{innerHTML:''}).innerHTML,` +
  `xss:!!window.__xss})`);
const o18 = JSON.parse(r18);
check('18 XSS 载荷不生成元素', o18.img === 0 && !o18.xss && /&lt;img/.test(o18.html),
  `img=${o18.img} xss=${o18.xss}`);

// 19 计算期间重复触发不应并发重入（防结果互相覆盖 / 重复起线程）
await evl(`(()=>{const a=document.querySelector('#pvLat'),b=document.querySelector('#pvLon');` +
  `a.value='39.9042';b.value='116.4074';})()`);
await click('#pvApply', 400);
await evl(`(()=>{const s=document.querySelector('#pvHours');s.value='12';s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
await click('#pvGo', 250);
const r19 = await evl(`(async()=>{const before=_passSeq;await computePasses();` +
  `return JSON.stringify({same:_passSeq===before,computing:!!OBS.computing});})()`);
const o19 = JSON.parse(r19);
check('19 计算期间重复触发不重入', o19.same === true && o19.computing === true, r19);

console.log(`\n== 过境预报自检: ${ok} 通过 / ${fail} 失败 ==`);
process.exit(fail ? 1 : 0);
