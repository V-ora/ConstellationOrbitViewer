// 静态站（GitHub Pages 产物）验收。
// 关键不是「页面能打开」，而是**放在子路径下**（user.github.io/仓库名/）时
// 每一个资源与接口映射都还能命中 —— 这类部署最典型的翻车就是绝对路径 404，
// 而 404 只会报在控制台里，页面看上去只是"转圈"。
//
// 用法：node _chk_pages.mjs http://127.0.0.1:8891/orbit-viewer/ [debugPort]
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const URL0 = process.argv[2] || 'http://127.0.0.1:8891/orbit-viewer/';
const DBG = parseInt(process.argv[3] || '9380', 10);
const EDGE = process.env.MSEDGE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const OUTDIR = process.env.SHOT_DIR || '_shots';
mkdirSync(OUTDIR, { recursive: true });

let pass = 0, fail = 0;
const ok = (n, c, d = '') => {
  if (c) { pass++; console.log(`  ok   ${n}${d ? '  (' + d + ')' : ''}`); }
  else { fail++; console.log(`  FAIL ${n}${d ? '  (' + d + ')' : ''}`); }
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const T0 = Date.now();
const el = () => ((Date.now() - T0) / 1000).toFixed(1);

const prof = mkdtempSync(path.join(tmpdir(), 'edge-pg-'));
const edge = spawn(EDGE, ['--headless=new', '--no-sandbox', '--enable-unsafe-swiftshader',
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`,
  '--window-size=1500,950', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => { try { edge.kill(); } catch { } });

let target = null;
for (let i = 0; i < 60 && !target; i++) {
  try { target = (await (await fetch(`http://127.0.0.1:${DBG}/json`)).json()).find(t => t.type === 'page'); } catch { }
  if (!target) await sleep(150);
}
if (!target) { console.log('FATAL: CDP target 未就绪'); process.exit(1); }

const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let mid = 0; const pend = new Map();
const badReqs = [], failedReqs = [];
ws.onmessage = ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); return; }
  if (m.method === 'Network.responseReceived') {
    const r = m.params.response;
    if (r.status >= 400) badReqs.push(`${r.status} ${r.url}`);
  }
  if (m.method === 'Network.loadingFailed') {
    failedReqs.push(`${m.params.errorText} ${m.params.type}`);
  }
};
const send = (method, params = {}) => new Promise(res => {
  const id = ++mid; pend.set(id, res); ws.send(JSON.stringify({ id, method, params }));
});
async function js(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  const ex = r.result?.exceptionDetails;
  if (ex) { console.log('    !! 页面内异常：' + String(ex.exception?.description || ex.text).split('\n')[0]); return undefined; }
  return r.result?.result?.value;
}
async function waitFor(expr, maxMs) {
  const t = Date.now();
  while (Date.now() - t < maxMs) { if (await js(expr)) return true; await sleep(200); }
  return false;
}

await send('Runtime.enable');
await send('Page.enable');
await send('Network.enable');
await send('Network.setCacheDisabled', { cacheDisabled: true });

/* ---------------------------------------------------------------- 桌面视口 */
console.log(`\n── 桌面 1440×900　${URL0}`);
await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: URL0 });
const loaded = await waitFor(
  `document.readyState==='complete' && typeof S!=='undefined' && S.sats && S.sats.length > 300`, 30000);
ok('页面把快照数据装进来了（S.sats > 300）', loaded,
  await js(`(typeof S!=='undefined'&&S.sats)?('sats='+S.sats.length):'S 未就绪'`));

ok('没有任何 4xx/5xx 请求（favicon 已内联，不该再出现）', badReqs.length === 0,
  badReqs.slice(0, 4).join(' | ') || '0 个');
ok('没有加载失败的请求', failedReqs.length === 0, failedReqs.slice(0, 4).join(' | ') || '0 个');

const st = await js(`(()=>{
  const rf=document.getElementById('btnRefresh');
  const q=document.getElementById('btnQuit');
  const items=document.querySelectorAll('#list .item').length;
  const fp=(S.sats||[]).reduce((a,s)=>a + (s.norad||0) + (s.alt0||0), 0);
  return {rf:rf?rf.textContent.trim():'', quitVis:q?getComputedStyle(q).display!=='none':false,
          items, view:S.view, epoch:fp};
})()`);
ok('刷新按钮已降级为「快照」提示', /快照/.test(st.rf), st.rf);
ok('「退出程序」在静态站上已隐藏', st.quitVis === false);
ok('卫星列表已渲染（> 20 行）', st.items > 20, st.items + ' 行');

// 静态站会把顶栏副标题改写成「千帆 · 中国星网　数据快照 日期」——
// 分组改名（2026-10-20 国网/星网 → 中国星网）以后，这里也要跟着对上
const brand = await js(`(()=>{const s=document.querySelector('header .brand span'); return s?s.textContent.trim():'';})()`);
ok('顶栏副标题用「中国星网」且标明快照', /中国星网/.test(brand) && /快照/.test(brand), brand);
const cardTxt = await js(`(()=>{const g=document.getElementById('gcards'); return g?g.textContent.replace(/\\s+/g,' ').trim():'';})()`);
ok('左侧分组卡片显示「中国星网」', /中国星网/.test(cardTxt) && !/国网 \/ 星网 GW 星座/.test(cardTxt),
  cardTxt.slice(0, 60));

// 点一下「快照」按钮：应弹提示、且数据不被改动
const epochBefore = st.epoch;
await js(`document.getElementById('btnRefresh').click()`);
await sleep(700);
const afterClick = await js(`(()=>{
  const b=document.getElementById('banner');
  const rf=document.getElementById('btnRefresh');
  return {banner: b?b.textContent.trim():'', shown: b?getComputedStyle(b).display!=='none':false,
          epoch:(S.sats||[]).reduce((a,s)=>a + (s.norad||0) + (s.alt0||0), 0)};
})()`);
ok('点「快照」给出说明而不是假装刷新', afterClick.shown && /快照|静态/.test(afterClick.banner),
  afterClick.banner.slice(0, 30));
ok('点完数据确实没变（norad+高度指纹一致）', afterClick.epoch === epochBefore && epochBefore > 0,
  `${epochBefore} → ${afterClick.epoch}`);

// 「数据状态」里应有静态版说明
const note = await js(`(()=>{const p=document.getElementById('status'); if(!p) return '';
  const t=(p.parentNode.textContent||''); return t.slice(-260);})()`);
ok('「数据状态」里写明这是静态演示版', /静态演示版/.test(note), note.replace(/\s+/g, ' ').slice(-46));

/* ------------------------------------------------- 爬升图视图（iframe + 大图） */
await js(`document.querySelector('#viewSeg button[data-v="raise"]').click()`);
await sleep(3500);
const raise = await js(`(()=>{
  const f=document.getElementById('raiseFrame');
  if(!f) return {ok:false};
  const d=f.contentDocument;
  const c=d&&d.querySelector('canvas');
  return {src:(f.getAttribute('src')||'').slice(-28), hasDoc:!!d, hasCanvas:!!c,
          cw:c?c.width:0, bodyLen:d&&d.body?d.body.textContent.length:0};
})()`);
ok('爬升大图 iframe 已载入且画布存在', raise.hasDoc && raise.hasCanvas && raise.cw > 100,
  `src=…${raise.src} canvas=${raise.cw}px`);

/* ---------------------------------------- 大图里的两个子页（走 hist.json 切片） */
for (const [name, q] of [['sat.html', '?norad=60379'], ['batch.html', '?group=qianfan&batch=2024-140']]) {
  const u = new URL('web/raise/' + name + q, URL0).href;
  const r = await js(`fetch(${JSON.stringify(u)}).then(r=>r.status)`);
  ok(`子页可访问 ${name}`, r === 200, 'HTTP ' + r);
}
// 直接在页面里验 shim 的切片结果（不靠打开新标签）
const slice = await js(`fetch('/api/sat_hist?norad=60379').then(r=>r.json()).then(j=>({
  norad:j.norad, base:j.base, pts:(j.pts||[]).length, name:j.name
})).catch(e=>({err:String(e)}))`);
ok('/api/sat_hist 被 shim 接到了 hist.json（有曲线点）', slice && slice.pts > 100,
  slice && `${slice.name} ${slice.pts} 点 base=${slice.base}`);

const bh = await js(`fetch('/api/batch_hist?group=qianfan&batch=2024-140').then(r=>r.json())
  .then(j=>({count:j.count, label:j.label, first:(j.sats&&j.sats[0])?j.sats[0].name:''}))
  .catch(e=>({err:String(e)}))`);
ok('/api/batch_hist 被 shim 接到 hist.json（组次有星）', bh && bh.count > 0,
  bh && `${bh.label} · ${bh.count} 颗 · ${bh.first}`);

const badBefore = badReqs.length;          // 下面这条是**故意**打一个不存在的分组，会造 404
const bad = await js(`fetch('/api/tle?group=nosuch').then(r=>r.status).catch(e=>'ERR')`);
ok('未知分组不会崩（返回非 0 状态）', bad === 404 || bad === 200 || bad === 'ERR', String(bad));

/* ---------------------------------------------------------------- 手机视口 */
console.log(`\n── 手机 390×844`);
await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 3, mobile: true });
await send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 });
await send('Page.navigate', { url: URL0 });
const m1 = await waitFor(`document.readyState==='complete' && typeof S!=='undefined' && S.sats && S.sats.length>300`, 30000);
ok('手机视口下同样加载成功', m1);
const m2 = await js(`(()=>{
  const cols=getComputedStyle(document.getElementById('app')).gridTemplateColumns.split(' ').length;
  const de=document.documentElement,b=document.body;
  const vis=s=>{const e=document.querySelector(s);return !!e&&getComputedStyle(e).display!=='none';};
  return {cols, over:Math.max(de.scrollWidth,b.scrollWidth)-innerWidth,
          btn:vis('#mbLeft'), quit:vis('#btnQuit')};
})()`);
ok('窄屏单列布局', m2.cols === 1, 'cols=' + m2.cols);
ok('无横向溢出', m2.over <= 1, m2.over + 'px');
ok('抽屉按钮存在', m2.btn);
ok('手机上也不出现「退出程序」', m2.quit === false);

const shot = await send('Page.captureScreenshot', { format: 'png' });
if (shot.result?.data) writeFileSync(path.join(OUTDIR, 'pages-phone.png'), Buffer.from(shot.result.data, 'base64'));

const stray = badReqs.filter(u => !/nosuch/.test(u));
console.log(`\n累计请求异常：4xx/5xx ${stray.length} 个（已排除故意探测的 nosuch），加载失败 ${failedReqs.length} 个`);
if (badReqs.length) console.log('  4xx/5xx 明细：\n   ' + badReqs.slice(0, 12).join('\n   '));
console.log(`\n${pass} 项通过 / ${fail} 项失败　（用时 ${el()}s）`);
ws.close();
process.exit(fail === 0 ? 0 : 1);
