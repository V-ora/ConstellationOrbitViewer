// 「覆盖圆 · 最低仰角」专项自检（CDP，真实渲染 + 真实输入）。
// 用法：先起服务，然后
//   node _chk_cover.mjs [页面URL，默认 http://127.0.0.1:8770/]
//
// 覆盖的点：
//   下拉框存在 / 选项 5·10·15·20·30° / 默认 10° / 无上下文时隐藏
//   选中后出现；开关关掉时 2D 真的不画（2026-10-07 修的行为）
//   2D 实绘（像素差分）随仰角变小；东西半跨/纬度区间等于球面闭式解（不是把 β 当纬差的椭圆）
//   赤道处跨度 = 2β、高纬 >75° 接近整幅图宽（绕极极冠）
//   与「过境预报」最低仰角双向同步；改仰角后旧结果失效；跨刷新持久化
//   3D 实绘（WebGL readPixels 冻结帧）覆盖像素面积随仰角变小
//   无页面 JS 异常
//
// 同一份脚本可以跑「不含过境预报」的版本：页面里没有 #passView 时自动跳过同步类检查。
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const APP = process.argv[2] || 'http://127.0.0.1:8770/';
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9337;

const prof = mkdtempSync(path.join(tmpdir(), 'edge-prof-cover-'));
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
const click = async (sel, wait = 400) => {
  const p = await center(sel);
  if (!p) return false;
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: p.x, y: p.y, button: 'left', clickCount: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: p.x, y: p.y, button: 'left', clickCount: 1 });
  await sleep(wait);
  return true;
};
// headless 下第一次点击偶发丢失，点击后核对状态，没变就再点一次
const clickToggle = async (sel, stateExpr) => {
  for (let i = 0; i < 3; i++) {
    await click(sel);
    if (await evl(stateExpr)) return true;
  }
  return false;
};

// ---------- 数据就绪 ----------
let total = 0;
for (let i = 0; i < 75; i++) {
  const v = await evl('(typeof S !== "undefined" && S.sats) ? S.sats.length : 0');
  if (typeof v === 'number' && v > 100) { await sleep(1200); total = v; break; }
  await sleep(2000);
}
check('0 数据就绪', total > 100, `sats=${total}`);

// 页面里有没有「过境预报」（决定要不要跑同步类检查）
const hasPass = await evl('!!document.querySelector("#passView")');
console.log(`模式：${hasPass ? '含过境预报' : '不含过境预报（跳过同步类检查）'}`);

// ---------- 1 下拉框存在与默认值 ----------
const d1 = await evl(`(()=>{const s=document.querySelector('#coverEl');` +
  `if(!s) return JSON.stringify({has:false});` +
  `return JSON.stringify({has:true, opts:[...s.options].map(o=>o.value), val:s.value,` +
  `disp:getComputedStyle(s).display, tag:s.tagName});})()`);
const o1 = JSON.parse(d1);
check('1 覆盖圆仰角下拉框存在', o1.has && o1.tag === 'SELECT');
check('2 选项为 5/10/15/20/30°', JSON.stringify(o1.opts) === '["5","10","15","20","30"]', JSON.stringify(o1.opts));
check('3 默认 10°', o1.val === '10', `val=${o1.val}`);
const d3b = await evl(hasPass ? 'OBS.minEl' : 'coverMinEl');
check('3b 内部默认值同为 10', d3b === 10, `${hasPass ? 'OBS.minEl' : 'coverMinEl'}=${d3b}`);
check('4 无上下文时下拉框隐藏', o1.disp === 'none', `display=${o1.disp}`);

// ---------- 2 与「过境预报」双向同步 ----------
if (hasPass) {
  const syncA = await evl(`(()=>{const p=document.querySelector('#pvMinEl');` +
    `p.value='20';p.dispatchEvent(new Event('change',{bubbles:true}));` +
    `return JSON.stringify({cov:document.querySelector('#coverEl').value,OBS:OBS.minEl});})()`);
  const sa = JSON.parse(syncA);
  check('5 过境侧改 20° → 覆盖圆跟随', sa.cov === '20' && sa.OBS === 20, JSON.stringify(sa));

  await evl(`(()=>{const p=document.querySelector('#pvMinEl');p.value='10';p.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  const syncB = await evl(`(()=>{const s=document.querySelector('#coverEl');` +
    `s.value='30';s.dispatchEvent(new Event('change',{bubbles:true}));` +
    `return JSON.stringify({pv:document.querySelector('#pvMinEl').value,OBS:OBS.minEl});})()`);
  const sb = JSON.parse(syncB);
  check('6 覆盖圆侧改 30° → 过境跟随', sb.pv === '30' && sb.OBS === 30, JSON.stringify(sb));

  // 改仰角要让已经算出来的过境结果失效（否则列表与参数不一致）
  const stale = await evl(`(()=>{OBS.passes=[{norad:1}];const s=document.querySelector('#coverEl');` +
    `s.value='15';s.dispatchEvent(new Event('change',{bubbles:true}));` +
    `return JSON.stringify({n:OBS.passes.length,OBS:OBS.minEl});})()`);
  const st = JSON.parse(stale);
  check('7 改仰角后旧过境结果作废', st.n === 0 && st.OBS === 15, JSON.stringify(st));

  // 复原到 10°
  await evl(`(()=>{const s=document.querySelector('#coverEl');s.value='10';s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
}

// ---------- 3 选中后出现 ----------
check('8 点列表第一颗', await click('#list .item', 600));
const selInfo = await evl(`JSON.stringify({sel:!!S.selected,alt:S.selected?S.selected.alt:null,` +
  `disp:getComputedStyle(document.querySelector('#coverEl')).display})`);
const si = JSON.parse(selInfo);
check('9 选中后下拉框出现', si.sel && si.disp !== 'none', `disp=${si.disp} alt=${si.alt}`);

// ---------- 4 2D 实绘（像素差分；暂停时间轴后两帧只有覆盖圆不同） ----------
check('10 切 2D 视图', await click('#viewSeg button[data-v="2d"]', 500));

const geoInfo = JSON.parse(await evl(`(()=>{const cv=document.querySelector('#cv2d');` +
  `const L=mapLayout(cv.width,cv.height);window.__cvw=cv.width;` +
  `return JSON.stringify({w:cv.width,h:cv.height,mw:L.mw,mh:L.mh,oy:L.oy});})()`));
console.log(`   2D 画布 ${geoInfo.w}x${geoInfo.h}，地图 ${geoInfo.mw}x${geoInfo.mh}`);
// 版面会随右侧面板内容变化（选中不同的星 → 面板高度变 → 画布尺寸变），
// 凡是「把像素换算成经纬度」的断言都必须在测量那一刻重读，不能沿用 geoInfo。
const layoutNow = async () => JSON.parse(await evl(`(()=>{const cv=document.querySelector('#cv2d');` +
  `const L=mapLayout(cv.width,cv.height);window.__cvw=cv.width;` +
  `return JSON.stringify({w:cv.width,h:cv.height,mw:L.mw,mh:L.mh,oy:L.oy});})()`));

check('11 点覆盖圆开关（按钮真的切换状态）', await clickToggle('#btnCover', 'S.coverOn'));

// 冻结时间轴：暂停后帧与帧之间只有覆盖圆不同，像素差分才可比
const paused2d = await evl(`(()=>{if(S.playing){document.querySelector('#btnPlay').click();}return !S.playing;})()`);
await sleep(400);
check('11b 已暂停时间轴（2D 差分可比）', paused2d === true);

const cvSnap = async name => await evl(`(()=>{const cv=document.querySelector('#cv2d');` +
  `const g=cv.getContext('2d');window.__cvw=cv.width;` +
  `window[${JSON.stringify(name)}]=g.getImageData(0,0,cv.width,cv.height).data;return 1;})()`);
const cvDiff = async (a, b) => await evl(`(()=>{const x=window[${JSON.stringify(a)}],y=window[${JSON.stringify(b)}];` +
  `if(!x||!y||x.length!==y.length)return JSON.stringify({n:-1});const w=window.__cvw;` +
  `let n=0,minx=1e9,maxx=-1,miny=1e9,maxy=-1;` +
  `for(let i=0;i<x.length;i+=4){if(Math.abs(x[i]-y[i])+Math.abs(x[i+1]-y[i+1])+Math.abs(x[i+2]-y[i+2])>20){` +
  `n++;const q=i/4,px=q%w,py=(q-px)/w;` +
  `if(px<minx)minx=px;if(px>maxx)maxx=px;if(py<miny)miny=py;if(py>maxy)maxy=py;}}` +
  `return JSON.stringify({n,minx,maxx,miny,maxy,w:(maxx-minx+1),h:(maxy-miny+1)});})()`);

const setCover = async deg => {
  await evl(`(()=>{const s=document.querySelector('#coverEl');` +
    `s.value=${JSON.stringify(String(deg))};s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await sleep(180);
};
// 量「关 / 开」两帧的差分：像素数与东西跨度
const capBox = async deg => {
  // 两帧之间卫星必须在原位：只要时间轴还跑着，卫星点自己就会挪，差分里就混进点位移，
  // 纬度上界这类「按像素换经纬度」的断言会被带偏（曾出现 5px 级假失败）。
  await evl('(()=>{if(S.playing)document.querySelector("#btnPlay").click();})()');
  await sleep(120);
  await evl('S.coverOn=false'); await sleep(360); await cvSnap('__cBase');
  await setCover(deg);
  await evl('S.coverOn=true'); await sleep(420); await cvSnap('__cOn');
  const d = JSON.parse(await cvDiff('__cBase', '__cOn'));
  d.sel = await evl('S.selected ? S.selected.name : null');   // 诊断：选中没生效就会画全部 473 个圈
  d.vis = await evl('S.visible ? S.visible.length : -1');
  return d;
};

// 开关关掉时必须真的不画——旧版 2D 只要「选中了星」就无视开关照画（按钮形同虚设）
await evl('S.coverOn=false'); await sleep(380); await cvSnap('__cOff1');
await evl('S.coverOn=true'); await sleep(420);
await evl('S.coverOn=false'); await sleep(420); await cvSnap('__cOff2');
const dOff = JSON.parse(await cvDiff('__cOff1', '__cOff2'));
check('11c 选中卫星但开关关着 → 2D 不画覆盖圆', dOff.n === 0 && si.sel, `差异像素=${dOff.n}`);

const box = {};
for (const deg of [5, 10, 30]) {
  const d = await capBox(deg);
  box[deg] = d;
  console.log(`   仰角 ${deg}° → 2D 覆盖圆差异像素 ${d.n}，东西跨度 ${d.w}px（选中 ${d.sel}）`);
}
check('12 三种仰角都画出了覆盖圆', !!(box[5] && box[30]) && box[5].n > 0 && box[30].n > 0);
check('13 仰角越高覆盖圆越小（实绘像素数单调）',
  !!box[5] && !!box[30] && box[5].n > box[10].n && box[10].n > box[30].n,
  `像素: 5°=${box[5] && box[5].n} 10°=${box[10] && box[10].n} 30°=${box[30] && box[30].n}`);

// ---------- 4b 几何口径：2D 画的是球面真圆，不是把 β 当纬差的椭圆 ----------
await setCover(10);   // 上面量完 30° 会把取值留下，这里必须钉回 10°
const R_EARTH = 6378.137, D2R = Math.PI / 180;
// 仰角存在哪个变量里，两版页面不一样
const elVar = hasPass ? 'OBS.minEl' : 'coverMinEl';
const hk = (alt, el) => R_EARTH * (Math.acos(Math.max(-1, Math.min(1,
  R_EARTH / (R_EARTH + alt) * Math.cos(el * D2R)))) - el * D2R);
// 独立参考解（不复用页面代码）：最大东西半跨解 sin²φ0 + cos²φ0·cos²Δ = cos²β；含极则 180°
const refSpan = (lat0, betaDeg) => {
  const s = Math.sin(lat0 * D2R), c = Math.cos(lat0 * D2R), cb = Math.cos(betaDeg * D2R);
  const v = (cb * cb - s * s) / (c * c);
  return v <= 0 ? 180 : Math.acos(Math.min(1, Math.sqrt(v))) / D2R;
};
// β 跟着页面当前的仰角走，免得测试自己跟自己不一致
const elNow = Number(await evl(elVar));
const betaDeg = hk(1068.5, elNow) / R_EARTH / D2R;   // 千帆工作高度 → 10° 时 22.488°
const pageSpan = async l0 => Number(await evl(`(()=>{const cs=capColumns(${l0},` +
  `horizonKm(1068.5,${elVar})/${R_EARTH},3600).cols;let d=0;` +
  `for(const c of cs){if(c&&Math.abs(c[0])>d)d=Math.abs(c[0]);}return d;})()`));
const spanRows = []; let spanOk = true;
// 南北都要测：南半球星下点时 atan2 落到 ±180° 附近，曾把 |Δlon|>90° 的经线全丢掉
for (const l0 of [0, 45, 60, 80, -45, -60, -80]) {
  const got = await pageSpan(l0), want = refSpan(l0, betaDeg);
  spanRows.push(`${l0}°:${got.toFixed(1)}/${want.toFixed(1)}`);
  if (!(Math.abs(got - want) < 0.35)) spanOk = false;
}
check(`14 2D 东西半跨 = 球面闭式解（${elNow}° 仰角，β=${betaDeg.toFixed(1)}°）`, spanOk, spanRows.join('  '));
check('14b 高纬（80°）确实是绕极极冠：跨满 180°',
  Math.abs(await pageSpan(80) - 180) < 0.35);
const rangeRows = []; let rangeOk = true;
for (const l0 of [0, 60, 80, -60, -80]) {
  const raw = await evl(`(()=>{const cs=capColumns(${l0},` +
    `horizonKm(1068.5,${elVar})/${R_EARTH},3600).cols;` +
    `const c=cs.find(x=>x&&Math.abs(x[0])<1e-9);return JSON.stringify(c?[c[1],c[2]]:null);})()`);
  let r = null;
  try { r = typeof raw === 'string' ? JSON.parse(raw) : null; } catch { r = null; }
  const e0 = Math.max(-90, l0 - betaDeg), e1 = Math.min(90, l0 + betaDeg);  // 南北通用
  rangeRows.push(`${l0}°:[${r ? r[0].toFixed(1) : '?'},${r ? r[1].toFixed(1) : '?'}]/[${e0.toFixed(1)},${e1.toFixed(1)}]`);
  if (!r || Math.abs(r[0] - e0) > 0.35 || Math.abs(r[1] - e1) > 0.35) rangeOk = false;
}
check('14c 纬度区间 = 星下点±β（越过极点截到 90°）', rangeOk, rangeRows.join('  '));

// ---------- 4c 实绘跨度：赤道 = 2β（与旧椭圆同值），高纬 ≈ 整幅图宽（旧椭圆的 8 倍） ----------
const pickSat = async cond => JSON.parse(await evl(`(()=>{const s=S.sats.find(x=>${cond});` +
  `if(!s) return 'null';select(s);if(S.setView) S.setView('2d');` +
  `return JSON.stringify({lat:s.lat,alt:s.alt});})()`) || 'null');
const eqSel = await pickSat('Math.abs(x.lat)<6 && x.alt>800');
await sleep(400);
let eqD = eqSel ? await capBox(10) : null;
const wantW = eqSel ? 2 * (hk(eqSel.alt, 10) / R_EARTH / D2R) / 360 * geoInfo.mw : 0;
// 偶发：某次快照没落在稳定帧上（差分里混进别的变化），复量一次再判
if (eqD && wantW && Math.abs(eqD.w - wantW) / wantW >= 0.15) {
  await sleep(500);
  eqD = await capBox(10);
}
check('14d 赤道处实绘东西跨度 = 2β（没画过头）',
  !!eqD && Math.abs(eqD.w - wantW) / wantW < 0.15,
  eqD ? `实测 ${eqD.w}px / 期望 ${wantW.toFixed(0)}px（星下点 ${eqSel.lat.toFixed(1)}°，选中 ${eqD.sel}，地图宽 ${geoInfo.mw}px）` : '没找到近赤道的星');

const poSel = await pickSat('Math.abs(x.lat)>75 && x.alt>800');
await sleep(400);
let poD = poSel ? await capBox(10) : null;
if (poD && poD.w <= geoInfo.mw * 0.85) { await sleep(500); poD = await capBox(10); }
check('14e 高纬（>75°）实绘接近整幅图宽（旧椭圆只有约 1/8）',
  !!poD && poD.w > geoInfo.mw * 0.85,
  poD ? `实测 ${poD.w}px / 地图 ${geoInfo.mw}px（星下点 ${poSel.lat.toFixed(1)}°，选中 ${poD.sel}）` : '没找到高纬的星');

// ---------- 4d 实绘纬度上界：南北两极都要测 ----------
// ★ 为什么必须分半球：atan2 在 φ0<0 时落到 ±180° 附近，曾让南半球的高纬极冠少画一半；
//   只测北半球（或只测「碰到的第一颗」）会漏掉这条路径，结果还随模拟时刻漂移。
const topLatCase = async (cond, tag) => {
  const p = await pickSat(cond);
  if (!p) return { ok: false, msg: `${tag}：没找到这样的星` };
  await sleep(400);
  let d = await capBox(10);
  if (d && d.n > 0 && d.w <= geoInfo.mw * 0.85) { await sleep(500); d = await capBox(10); }
  if (!d || d.n <= 0) return { ok: false, msg: `${tag}：没量到覆盖圆（差异像素 ${d ? d.n : -1}）` };
  // 版面必须在「量这一帧的那一刻」重读：选中不同的星会改右侧面板高度、连带 2D 画布尺寸，
  // 沿用前面的 geoInfo 会把几十像素的版面差算成「几何画错了」。
  const L = await layoutNow();
  const latNow = Number(await evl('S.selected ? S.selected.lat : NaN'));
  // β 要按这颗星自己的高度算（用 1068.5 km 的基准会平白差 0.5°）
  const bNow = hk(p.alt, 10) / R_EARTH / D2R;
  const topLat = 90 - ((d.miny - L.oy) / L.mh) * 180;
  const wantTop = Math.min(90, latNow + bNow);
  return { ok: Math.abs(topLat - wantTop) < 1.2,
    msg: `${tag} 星下点 ${latNow.toFixed(1)}° β=${bNow.toFixed(1)}° → 实测顶边 ${topLat.toFixed(1)}° / 期望 ${wantTop.toFixed(1)}°` +
      `（miny=${d.miny} oy=${L.oy} mh=${L.mh}）` };
};
const tlN = await topLatCase('x.lat>75 && x.alt>800', '北');
const tlS = await topLatCase('x.lat<-75 && x.alt>800', '南');
check('14f 实绘纬度上界 = 星下点+β（渲染层；南北两极各测一次）',
  tlN.ok && tlS.ok, `${tlN.msg}；${tlS.msg}`);

// ---------- 5 几何口径（锁死 0/10/30° 三档数值） ----------
const hkV = JSON.parse(await evl(
  `JSON.stringify([horizonKm(1068.5,0),horizonKm(1068.5,10),horizonKm(1068.5,30)])`));
const want0 = hk(1068.5, 0), want10 = hk(1068.5, 10), want30 = hk(1068.5, 30);
check('15 horizonKm 三档与公式一致（1068.5km）',
  Math.abs(hkV[0] - want0) < 1 && Math.abs(hkV[1] - want10) < 1 && Math.abs(hkV[2] - want30) < 1,
  `0°=${hkV[0].toFixed(0)}km 10°=${hkV[1].toFixed(0)}km 30°=${hkV[2].toFixed(0)}km（期望 ${want0.toFixed(0)}/${want10.toFixed(0)}/${want30.toFixed(0)}）`);
check('15b 三档数值与旧版记录一致（3459/2503/1349）',
  Math.abs(hkV[0] - 3459) < 6 && Math.abs(hkV[1] - 2503) < 6 && Math.abs(hkV[2] - 1349) < 6,
  `${hkV[0].toFixed(0)}/${hkV[1].toFixed(0)}/${hkV[2].toFixed(0)}`);
check('16 0° 即旧几何地平线口径', Math.abs(hkV[0] - R_EARTH * Math.acos(R_EARTH / (R_EARTH + 1068.5))) < 1e-6);

const rad = await evl(`(()=>{const s=document.querySelector('#coverEl');s.value='20';` +
  `s.dispatchEvent(new Event('change',{bubbles:true}));` +
  `const r=coverElRad();s.value='10';s.dispatchEvent(new Event('change',{bubbles:true}));return r;})()`);
check('17 coverElRad() 与所选仰角一致', Math.abs(rad - 20 * D2R) < 1e-9, `rad=${rad}`);

// ---------- 5b 取消选中 → 下拉框收起 + 覆盖圆复位 ----------
await click('#btnDeselect', 500);
const de = JSON.parse(await evl(`JSON.stringify({disp:getComputedStyle(document.querySelector('#coverEl')).display,` +
  `on:S.coverOn,sel:!!S.selected})`));
check('17b 取消选中后下拉框隐藏且覆盖圆复位', de.disp === 'none' && !de.on && !de.sel, JSON.stringify(de));

// ---------- 6 3D 实绘面积（WebGL readPixels 冻结帧） ----------
check('18 切回 3D', await click('#viewSeg button[data-v="3d"]', 400));
// 冻结时间轴：暂停后帧与帧之间只有覆盖圆不同，像素差分才可比
const paused = await evl(`(()=>{if(S.playing){document.querySelector('#btnPlay').click();}return !S.playing;})()`);
await sleep(400);
check('19 已暂停时间轴（保证帧可比）', paused === true);

// 改聚焦一个多颗的组次：3D 里必然有若干颗在正面，覆盖圆才看得见
const bi = JSON.parse(await evl(`(()=>{const s=S.sats.find(x=>(x.meta||{}).batch&&x.group);` +
  `if(!s) return 'null';` +
  `return JSON.stringify({group:s.group,batch:s.meta.batch,` +
  `n:S.sats.filter(y=>y.group===s.group&&(y.meta||{}).batch===s.meta.batch).length});})()`));
let focal = null;
if (bi && bi.n >= 2) {
  await evl(`setBatchFocus(${JSON.stringify(bi.group)},${JSON.stringify(bi.batch)})`);
  await sleep(700);
  focal = bi;
}
check('20 已聚焦一个多颗组次', !!focal, focal ? `${focal.group} / ${focal.batch} (${focal.n} 颗)` : '未找到');

const glSnap = async name => await evl(`(()=>{const cv=document.querySelector('#cv3d');` +
  `const gl=cv.getContext('webgl')||cv.getContext('experimental-webgl');if(!gl) return -1;` +
  `const w=cv.width,h=cv.height;const b=new Uint8Array(w*h*4);` +
  `gl.readPixels(0,0,w,h,gl.RGBA,gl.UNSIGNED_BYTE,b);window[${JSON.stringify(name)}]=b;return w*h;})()`);
const glDiff = async (a, b) => await evl(`(()=>{const x=window[${JSON.stringify(a)}],y=window[${JSON.stringify(b)}];` +
  `if(!x||!y||x.length!==y.length) return -1;let n=0;` +
  `for(let i=0;i<x.length;i+=4){` +
  `if(Math.abs(x[i]-y[i])+Math.abs(x[i+1]-y[i+1])+Math.abs(x[i+2]-y[i+2])>20) n++;}return n;})()`);

if (await evl('S.coverOn')) await clickToggle('#btnCover', '!S.coverOn');
await sleep(500);
const px = await glSnap('__snapOff');
check('21 WebGL 帧可读（readPixels）', typeof px === 'number' && px > 0, `pixels=${px}`);

await clickToggle('#btnCover', 'S.coverOn');
await setCover(5); await sleep(500); await glSnap('__snap5');
await setCover(30); await sleep(500); await glSnap('__snap30');
const d5 = await glDiff('__snapOff', '__snap5');
const d30 = await glDiff('__snapOff', '__snap30');
check('22 3D 覆盖圆确实画出来了（有像素变化）', d5 > 50, `5°差异像素=${d5}`);
check('23 3D 覆盖面积随仰角变小', d30 >= 0 && d5 > d30, `5°=${d5} vs 30°=${d30}`);
check('23b 面积比明显大于 1', d30 > 0 ? d5 / d30 > 1.5 : false,
  d30 > 0 ? `比值=${(d5 / d30).toFixed(2)}` : '30° 无差异像素');

// ---------- 7 无上下文复位（退出组次聚焦后应整组收起并复位） ----------
await click('#viewSeg button[data-v="2d"]', 400);
await click('#batchExit', 700);
const reset = await evl(`JSON.stringify({bf:!!S.batchFilter,sel:!!S.selected,` +
  `disp:getComputedStyle(document.querySelector('#coverEl')).display,on:S.coverOn})`);
const rs = JSON.parse(reset);
check('24 退出聚焦后下拉框隐藏且覆盖圆复位', !rs.bf && !rs.sel && rs.disp === 'none' && !rs.on, JSON.stringify(rs));

// ---------- 8 跨刷新持久化 ----------
if (hasPass) {
  await setCover(20);
  await send('Page.navigate', { url: APP });
  await sleep(2500);
  let t2 = 0;
  for (let i = 0; i < 75; i++) {
    const v = await evl('(typeof S !== "undefined" && S.sats) ? S.sats.length : 0');
    if (typeof v === 'number' && v > 100) { await sleep(900); t2 = v; break; }
    await sleep(2000);
  }
  const persist = JSON.parse(await evl(
    `JSON.stringify({val:document.querySelector('#coverEl').value,OBS:OBS.minEl})`));
  check('25 刷新后记住 20°', t2 > 100 && persist.val === '20' && persist.OBS === 20,
    `sats=${t2} val=${persist.val} OBS.minEl=${persist.OBS}`);
}

// ---------- 9 无页面 JS 异常 ----------
check('26 无页面 JS 异常', pageErrors.length === 0, pageErrors.slice(0, 3).join(' | '));

console.log(`\n== 覆盖圆·最低仰角自检: ${ok} 通过 / ${fail} 失败 ==`);
process.exit(fail ? 1 : 0);
