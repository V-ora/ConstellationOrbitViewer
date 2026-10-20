// 星下点专项自检（千帆/国网版，2026-10-08 用户要求，与星链版同做法）
//
// 用户要求：「把之前关于星网和千帆的那个程序的星下点相关的显示也删掉，就和星链一样」。
// 即：3D 地球上不再画每颗星在地面的星下点小图标（473 颗点糊在地球上），
//     但星下点经纬度数据一个不少（详情面板 / 悬停 / 覆盖圆 / 2D / 过境预报照旧）。
//
// 本脚本从星链版 _chk_subpt.mjs 移植，不靠肉眼看截图，而是**钩住 WebGL 的 gl.drawArrays**，
// 直接读每一帧点图元（POINTS）实际画了多少个顶点：
//     修好 = 每颗可见星 1 个点  ⇒ count === S.visible.length
//     没修 = 星本体 + 星下点小点 ⇒ count === 2 × S.visible.length
// 为了证明「这条检查真的能抓到问题」，脚本会**自己生成一个把星下点塞回去的
// A/B 页面**（web/_selfcheck_ab_ground.html），对它跑同一套测量；A/B 量出约 2 倍
// 才能证明测量方法有效。跑完会把 A/B 临时文件删掉。
//
// 用法：node _chk_subpt.mjs [URL] [源文件] [--no-ab]
//   URL     默认 http://127.0.0.1:8770/（源码版服务）
//   源文件  默认 web/index.html；测「不含过境预报」版时传 web_nopass/index.html
//   --no-ab 打包版没有可写的 web 目录（A/B 页 404）时跳过 A/B 项——
//           测量方法的有效性已由源码版那次 A/B 证明，不算失败。
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, unlinkSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE = (process.argv[2] || 'http://127.0.0.1:8770/').replace(/\/+$/, '');
const NOAB = process.argv.includes('--no-ab');
const SRC_REL = process.argv[3] || 'web/index.html';
const SRC_FILE = path.join(HERE, SRC_REL);
const AB_FILE = path.join(HERE, 'web', '_selfcheck_ab_ground.html');
const AB_URL = BASE + '/web/_selfcheck_ab_ground.html';
const EDGE = process.env.MSEDGE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9339;

let pass = 0, fail = 0;
const ok = (m) => { pass++; console.log('  PASS  ' + m); };
const no = (m) => { fail++; console.log('  FAIL  ' + m); };
const info = (m) => console.log('        ' + m);

/* ---------- [0] 源文件文本断言（不开浏览器） ---------- */
console.log('星下点专项自检（千帆/国网版）  URL=' + BASE + '  源=' + SRC_REL);
console.log('');
console.log('[0] 源文件：星下点绘制已删、详情字段保留');
const src = existsSync(SRC_FILE) ? readFileSync(SRC_FILE, 'utf8') : null;
if (!src) {
  no('找不到源文件 ' + SRC_REL);
} else {
  const gone = src.indexOf('星下点小点') < 0 && src.indexOf('rr = 1.002') < 0;
  if (gone) ok(SRC_REL + ' 里已没有「星下点小点」绘制代码');
  else no(SRC_REL + ' 里仍有星下点绘制代码（rr = 1.002）');
  if (src.indexOf('星下点纬度') >= 0 && src.indexOf('星下点经度') >= 0)
    ok(SRC_REL + ' 详情面板仍含「星下点纬度 / 星下点经度」字段');
  else no(SRC_REL + ' 详情面板字段丢失');
}

/* ---------- 生成 A/B 页面：把原版的「星下点小点」塞回 3D 卫星点循环 ---------- */
function buildAB() {
  const anchor = '    vs.push(isSel ? 11 : (s === S.hover ? 8 : 4.4));';
  if (src.indexOf(anchor) < 0) return 'anchor-not-found';
  const inject =
    anchor + '\n' +
    '    { /* A/B 用：原版星下点小点（半径 1.002 R_EARTH 的地面点） */\n' +
    '      const la = s.lat * D2R, lo = s.lon * D2R, rr = 1.002;\n' +
    '      vp.push(rr*Math.cos(la)*Math.cos(lo), rr*Math.cos(la)*Math.sin(lo), rr*Math.sin(la));\n' +
    '      vc.push(c[0], c[1], c[2]); vs.push(isSel ? 6 : 2.3); }';
  writeFileSync(AB_FILE, src.replace(anchor, inject), 'utf8');
  return null;
}
if (existsSync(AB_FILE)) { try { unlinkSync(AB_FILE); } catch { } }
const abErr = src ? buildAB() : 'no-src';

/* ---------- 起无头 Edge ---------- */
const prof = mkdtempSync(path.join(tmpdir(), 'edge-subpt-qf-'));
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
  '--enable-unsafe-swiftshader', '--disable-dev-shm-usage',
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`,
  '--window-size=1680,1000', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => { try { edge.kill(); } catch { } });

const sleep = ms => new Promise(r => setTimeout(r, ms));
let target = null;
for (let i = 0; i < 60 && !target; i++) {
  try { const list = await (await fetch(`http://127.0.0.1:${DBG}/json`)).json(); target = list.find(t => t.type === 'page'); } catch { }
  if (!target) await sleep(300);
}
if (!target) { console.log('FATAL: CDP target 未就绪（Edge 没起来？）'); process.exit(1); }

const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let mid = 0; const pend = new Map();
ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); } };
const send = (method, params = {}) => new Promise(res => { const id = ++mid; pend.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
const js = async (expr) => (await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;

await send('Runtime.enable');
await send('Page.enable');

/* 钩子：顶点数必须和**同一帧**的 S.visible.length 配对比（可见数每帧都在抖动）。 */
const HOOK = `(() => {
  window.__pt = { frames: 0, last: null, ratios: [], modes: {} };
  const t = setInterval(() => {
    const cv = document.getElementById('cv3d');
    if (!cv) return;
    let gl = null;
    try { gl = cv.getContext('webgl') || cv.getContext('experimental-webgl'); } catch (e) { }
    if (!gl || gl.__hooked) return;
    gl.__hooked = true;
    clearInterval(t);
    const POINTS = gl.POINTS, LINES = gl.LINES, LINE_STRIP = gl.LINE_STRIP;
    const orig = gl.drawArrays.bind(gl);
    gl.drawArrays = function (mode, first, count) {
      try {
        const k = mode === POINTS ? 'POINTS' : mode === LINES ? 'LINES' : mode === LINE_STRIP ? 'LINE_STRIP' : ('m' + mode);
        window.__pt.modes[k] = (window.__pt.modes[k] || 0) + 1;
        if (mode === POINTS) {
          let vis = -1;
          try { vis = S.visible.length; } catch (e) { }
          window.__pt.frames++;
          window.__pt.last = { count: count, visible: vis, ratio: (vis > 0 ? count / vis : null) };
          if (vis > 0) {
            window.__pt.ratios.push(count / vis);
            if (window.__pt.ratios.length > 4000) window.__pt.ratios.shift();
          }
        }
      } catch (e) { }
      return orig(mode, first, count);
    };
  }, 4);
})();`;

async function measure(url, waitMs) {
  await send('Page.navigate', { url: 'about:blank' });
  await sleep(300);
  const r = await send('Page.addScriptToEvaluateOnNewDocument', { source: HOOK });
  const hookId = r.result?.identifier;
  await send('Page.navigate', { url });
  await sleep(waitMs);
  const st = await js(`(()=>{
    const out = { pt: window.__pt || null };
    try { out.visible = (typeof S!=='undefined' && Array.isArray(S.visible)) ? S.visible.length : -1; } catch(e){ out.visible = -2; }
    try { out.satCount = (typeof S!=='undefined' && Array.isArray(S.sats)) ? S.sats.length : -2; } catch(e){ out.satCount = -2; }
    try { out.glOk = (typeof GL!=='undefined') ? !!GL.ok : null; } catch(e){ out.glOk = null; }
    try {
      let bad = 0, n = 0;
      for (const s of S.visible) { n++; if (!isFinite(s.lat) || !isFinite(s.lon)) bad++; }
      out.latlon = { n: n, bad: bad };
    } catch(e){ out.latlon = null; }
    try { out.hasDetailFields = document.documentElement.innerHTML.indexOf('星下点纬度') >= 0
                                        && document.documentElement.innerHTML.indexOf('星下点经度') >= 0; }
    catch(e){ out.hasDetailFields = null; }
    return out;
  })()`);
  if (hookId) await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: hookId });
  return st;
}

console.log('');
console.log('[1] 主页面：每颗可见星应该只有 1 个点图元顶点');
const A = await measure(BASE + '/', 22000);
if (!A || !A.pt || !A.pt.last) {
  no('没拿到 WebGL 绘制计数（页面没起来？）');
} else {
  const rr = A.pt.ratios || [];
  const rmin = rr.length ? Math.min(...rr) : null, rmax = rr.length ? Math.max(...rr) : null;
  info('可见卫星 S.visible  = ' + A.visible + '　（在册 ' + A.satCount + '，GL.ok=' + A.glOk + '）');
  info('POINTS 帧数          = ' + A.pt.frames + '　各图元模式 ' + JSON.stringify(A.pt.modes));
  info('末帧 POINTS 顶点数   = ' + A.pt.last.count + '，同帧 visible = ' + A.pt.last.visible
     + '，比值 ' + (A.pt.last.ratio != null ? A.pt.last.ratio.toFixed(4) : 'n/a'));
  if (rr.length) info('全帧比值范围         = ' + rmin.toFixed(4) + ' ~ ' + rmax.toFixed(4) + '（每帧都应与末帧一致）');
  const r1 = A.pt.last.ratio;
  if (A.visible <= 0 || r1 == null) {
    no('S.visible 为空，无法判定（数据没加载？）');
  } else if (Math.abs(r1 - 1) < 0.001) {
    ok('POINTS 顶点数 === 同帧可见星数 ⇒ 每颗星只画 1 个点，没有额外的星下点图标');
  } else if (Math.abs(r1 - 2) < 0.01) {
    no('POINTS 顶点数 === 2× 同帧可见星数 ⇒ 星下点小点还在');
  } else {
    no('POINTS 顶点数 / 可见星数 = ' + r1.toFixed(4) + '（期望 1.0000；2.0 说明没修好）');
  }
}

console.log('');
console.log('[2] 星下点经纬度数据必须保留');
if (!A) { no('无数据'); } else {
  if (A.latlon && A.latlon.n > 0 && A.latlon.bad === 0)
    ok('全部 ' + A.latlon.n + ' 颗可见星的 lat / lon 都是有限数值（星下点照常推算）');
  else no('有 ' + (A.latlon ? A.latlon.bad + '/' + A.latlon.n : '?') + ' 颗星的 lat/lon 不是有限数值');
  if (A.hasDetailFields) ok('详情面板仍含「星下点纬度」「星下点经度」两项');
  else no('详情面板找不到「星下点纬度 / 星下点经度」');
}

console.log('');
console.log('[3] 真选一颗星，看详情面板能否出数字');
const sel = await js(`(()=>{
  try {
    const s = S.visible.find(x => isFinite(x.lat) && isFinite(x.lon));
    if (!s) return { err: 'no finite latlon sat' };
    select(s);
    renderDetail();
    const el = document.querySelector('#detail');
    const txt = el ? el.textContent : '';
    const m1 = /星下点纬度[^\\d-]{0,6}([\\d.-]+)/.exec(txt);
    const m2 = /星下点经度[^\\d-]{0,6}([\\d.-]+)/.exec(txt);
    return { name: s.name, lat: s.lat, lon: s.lon,
             dLat: m1 ? +m1[1] : null, dLon: m2 ? +m2[1] : null,
             panelHas: txt.indexOf('星下点') >= 0 };
  } catch(e){ return { err: String(e) }; }
})()`);
if (!sel || sel.err) no('选星/取详情失败：' + (sel && sel.err));
else {
  info('选中 ' + sel.name + '　真值 lat=' + sel.lat.toFixed(4) + ' lon=' + sel.lon.toFixed(4));
  if (sel.panelHas && sel.dLat !== null && sel.dLon !== null) {
    const dLat = Math.abs(sel.dLat - sel.lat), dLon = Math.abs(sel.dLon - sel.lon);
    if (dLat < 0.05 && dLon < 0.05) ok('面板显示 纬度 ' + sel.dLat + ' / 经度 ' + sel.dLon + '，与推算值一致（误差 < 0.05°）');
    else no('面板数值与推算值对不上：Δlat=' + dLat.toFixed(4) + ' Δlon=' + dLon.toFixed(4));
  } else no('面板里「星下点纬度 / 经度」没出数值：' + JSON.stringify(sel));
}

console.log('');
console.log('[4] 自检自身的有效性：A/B 页面（把星下点塞回去）必须量出 2 倍');
if (NOAB) {
  info('--no-ab：打包版没有可写的 web 目录，A/B 项跳过（测量方法有效性已由源码版 A/B 证明，不计失败）');
} else if (abErr) {
  no('A/B 页面生成失败（' + abErr + '）⇒ 无法证明本检查有效，主页面结论不可信');
} else {
  const B = await measure(AB_URL, 22000);
  if (!B || !B.pt || !B.pt.last) no('A/B 页面没量到绘制计数（AB_URL=' + AB_URL + ' 可达？）');
  else {
    const r2 = B.pt.last.ratio;
    info('A/B 末帧 POINTS 顶点数 ' + B.pt.last.count + '，同帧可见星 ' + B.pt.last.visible
       + '　比值 ' + (r2 != null ? r2.toFixed(4) : 'n/a'));
    if (r2 != null && Math.abs(r2 - 2) < 0.01)
      ok('A/B 页面量出 2.0000 ⇒ 本检查确实能抓到「星下点没删干净」，main 页的 PASS 成立');
    else
      no('A/B 页面比值不是 2（量到 ' + (r2 != null ? r2.toFixed(4) : 'n/a') + '）⇒ 测量方法可能失效');
  }
}

try { if (existsSync(AB_FILE)) unlinkSync(AB_FILE); } catch { }

console.log('');
console.log('==============================================================');
console.log('  星下点专项自检：PASS ' + pass + ' / FAIL ' + fail);
console.log('==============================================================');
try { ws.close(); } catch { }
process.exit(fail ? 1 : 0);
