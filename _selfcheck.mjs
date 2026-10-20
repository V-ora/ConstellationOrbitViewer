// CDP 全链路自检：真实鼠标输入（Input.dispatchMouseEvent）+ 用户视口仿真。
// 用法：先起服务（exe 或 server.py），然后
//   node _selfcheck.mjs [页面URL，默认 http://127.0.0.1:8770/]
// 检查项（2026-09-29 版）：
//   1 组次面板打开  2 点行聚焦 + 状态条瘦身（无 3D/2D/爬升图重复按钮）
//   3 顶部页签跳整组叠加爬升图  4 跳回 3D  5 跳 2D  6 退出组次恢复全部
//   7 选中卫星 → 详情面板「取消选中」取消选中
//   7b 详情面板显示「研制方」（卫星百科按组次给，2026-10-06 加）
//   10/10a/10b 第 4 个视图「过境预报」页签可切、面板开合、画布让位（2026-10-07 加）
//   「过境预报」的深度检查（经纬度解析 / 越界拒绝 / 过境计算 / 持久化 / 地图取点）在 _chk_pass.mjs
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const APP = process.argv[2] || 'http://127.0.0.1:8770/';
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9333;

const prof = mkdtempSync(path.join(tmpdir(), 'edge-prof-'));
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-sandbox',
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`,
  '--window-size=1052,571', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => { try { edge.kill(); } catch {} });

const sleep = ms => new Promise(r => setTimeout(r, ms));

let target = null;
for (let i = 0; i < 50 && !target; i++) {
  try {
    const list = await (await fetch(`http://127.0.0.1:${DBG}/json`)).json();
    target = list.find(t => t.type === 'page');
  } catch {}
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
const evl = async expr => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
  if (r.exceptionDetails) return { __err: String(r.exceptionDetails.text) };
  return r.result.value;
};

await send('Page.enable');
// 用户真实视口：不设的话 #stage 塌缩为宽 0，elementFromPoint 全错
await send('Emulation.setDeviceMetricsOverride', { width: 1052, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: APP });
await sleep(3000);    // 首帧；数据就绪改为下面的轮询等待

let ok = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { ok++; console.log(`PASS ${name} ${extra}`); }
  else { fail++; console.log(`FAIL ${name} ${extra}`); }
};
const center = async sel => await evl(
  `(()=>{const el=document.querySelector(${JSON.stringify(sel)});if(!el)return null;` +
  `const r=el.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
const click = async sel => {
  const p = await center(sel);
  if (!p) return false;
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: p.x, y: p.y, button: 'left', clickCount: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: p.x, y: p.y, button: 'left', clickCount: 1 });
  await sleep(600);
  return true;
};

// 等待数据就绪（冷启动要抓根数+元信息，最多 150s）——固定 sleep 会误判成页面崩了。
// 判据用 S.sats（数据加载完成即赋值），S.visible 要等首次筛选才填，早期恒为 0。
let total = 0;
for (let i = 0; i < 75; i++) {
  const v = await evl('(typeof S !== "undefined" && S.sats) ? S.sats.length : 0');
  if (typeof v === 'number' && v > 100) { await sleep(1500); total = (await evl('S.visible.length')) || v; break; }
  await sleep(2000);
}
if (!total) total = await evl('S.sats.length') || 0;
check('0 数据就绪', total > 100, `total=${total}`);

// 1 打开组次面板
check('1 组次按钮可点', await click("#gcards button[data-act='batch']"));
const rows = await evl(`document.querySelectorAll('#batchPanel .brow[data-b]').length`);
check('1b 面板有组次行', rows > 0, `rows=${rows}`);
const rowLabel = await evl(`(document.querySelector('#batchPanel .brow[data-b] .t')||{textContent:''}).textContent`);

// 2 点第一行 → 聚焦 + 瘦身状态条
check('2 点击组次行', await click('#batchPanel .brow[data-b]'));
const st = await evl(
  `JSON.stringify({bf:!!S.batchFilter,n:S.visible.length,` +
  `disp:(document.querySelector('#batchBar')||{style:{}}).style.display,` +
  `txt:(document.querySelector('#batchBar')||{textContent:''}).textContent,` +
  `dv:document.querySelectorAll("#batchBar button[data-v]").length,` +
  `ex:!!document.querySelector('#batchExit'),` +
  `fs:getComputedStyle(document.querySelector('#batchBar')||document.body).fontSize})`);
const o = JSON.parse(st);
check('2a 聚焦生效', o.bf && o.n < total, `n=${o.n}`);
check('2b 状态条显示', o.disp === 'flex', `disp=${o.disp}`);
check('2c 含"正在查看"', o.txt.includes('正在查看'), o.txt.slice(0, 40));
check('2d 无重复视图按钮', o.dv === 0, `data-v按钮=${o.dv}`);
check('2e 小号字体', parseFloat(o.fs) <= 12, `font=${o.fs}`);
check('2f 有退出按钮', o.ex);
const r2g = await evl(`JSON.stringify({bi:getComputedStyle(document.querySelector('#batchInfo')).display,` +
  `gc:getComputedStyle(document.querySelector('#gcards')).display,` +
  `txt:document.querySelector('#batchInfo').textContent})`);
const o2g = JSON.parse(r2g);
check('2g 左栏显示组次资料', o2g.bi !== 'none' && o2g.gc === 'none' &&
  o2g.txt.includes('组次资料') && o2g.txt.includes('所属星座'), o2g.txt.slice(0, 30));

// 3 顶部页签 → 轨道爬升图（组次叠加图）
check('3 跳爬升图', await click('#viewSeg button[data-v="raise"]'));
const r3 = await evl(`JSON.stringify({v:S.view,src:(document.querySelector('#raiseFrame')||{src:''}).src})`);
const o3 = JSON.parse(r3);
check('3b 进入爬升视图', o3.v === 'raise');
check('3c 整组叠加图', o3.src.includes('batch.html'), o3.src.split('/').pop().slice(0, 50));

// 4/5 顶部页签回 3D、去 2D
check('4 跳回 3D', await click('#viewSeg button[data-v="3d"]'));
check('4b 视图=3d', (await evl('S.view')) === '3d');
check('5 跳 2D', await click('#viewSeg button[data-v="2d"]'));
check('5b 视图=2d', (await evl('S.view')) === '2d');

// 6 退出组次
check('6 点退出', await click('#batchExit'));
const r6 = await evl(`JSON.stringify({bf:!!S.batchFilter,n:S.visible.length,` +
  `disp:(document.querySelector('#batchBar')||{style:{display:'none'}}).style.display,` +
  `bi:getComputedStyle(document.querySelector('#batchInfo')).display,` +
  `gc:getComputedStyle(document.querySelector('#gcards')).display,` +
  `bt:getComputedStyle(document.querySelector('#btnTrail')).display,on:S.trailOn})`);
const o6 = JSON.parse(r6);
check('6a 恢复全部', !o6.bf && o6.n === total, `n=${o6.n}/${total}`);
check('6b 状态条隐藏', o6.disp !== 'flex');
check('6c 左栏恢复数据源卡片', o6.bi === 'none' && o6.gc !== 'none');
check('6d 回全部视图隐藏轨迹开关', o6.bt === 'none' && !o6.on);

// 7 选中卫星：详情面板 + 取消选中 + 轨迹/覆盖圆开关出现（有上下文才显示）
check('7 点列表第一颗', await click('#list .item'));
const r7 = await evl(`JSON.stringify({sel:!!S.selected,` +
  `bd:getComputedStyle(document.querySelector('#btnDeselect')).display,` +
  `bt:getComputedStyle(document.querySelector('#btnTrail')).display})`);
const o7 = JSON.parse(r7);
check('7a 已选中且按钮出现', o7.sel && o7.bd !== 'none' && o7.bt !== 'none', `bd=${o7.bd},bt=${o7.bt}`);

// 7b 详情面板「研制方」：卫星百科按组次给，不该因为取数路径不同而丢字
const r7b = await evl(`JSON.stringify({dev:(S.selected.meta||{}).dev||'',` +
  `txt:document.querySelector('#detail').textContent})`);
const o7b = JSON.parse(r7b);
check('7b 详情面板显示研制方', !!o7b.dev && o7b.txt.includes('研制方') && o7b.txt.includes(o7b.dev),
  `dev=${o7b.dev || '(空)'}`);

// 8 轨迹/覆盖圆开关即时生效（选中上下文）
// headless 下第一次点击偶发丢失（时序抖动），所以点击后验证状态、没变就再点一次
const clickToggle = async (sel, stateExpr) => {
  for (let i = 0; i < 3; i++) {
    await click(sel);
    if (await evl(stateExpr)) return true;
  }
  return false;
};
check('8 点轨迹开关', await clickToggle('#btnTrail', 'S.trailOn'));
const r8 = await evl(`JSON.stringify({on:S.trailOn,` +
  `seeded:S.visible.filter(s=>s.trail&&s.trail.length>1).length,` +
  `txt:document.querySelector('#btnTrail').textContent})`);
const o8 = JSON.parse(r8);
// seedTrails 会给全部可见星回推 60 个历史点（几百颗），自然采样只会给个别星攒 1~2 个点
check('8a 轨迹开且立即有历史', o8.on && o8.seeded > 50 && o8.txt.includes('开'), `seeded=${o8.seeded}`);
check('8 点覆盖圆开关', await clickToggle('#btnCover', 'S.coverOn'));
const r8b = await evl(`JSON.stringify({on:S.coverOn,txt:document.querySelector('#btnCover').textContent})`);
const o8b = JSON.parse(r8b);
check('8b 覆盖圆开', o8b.on && o8b.txt.includes('开'));
check('8c 再点关闭复原', await clickToggle('#btnTrail', '!S.trailOn') &&
  await clickToggle('#btnCover', '!S.coverOn'));

// 9 取消选中：清空选中 + 开关隐藏 + 状态复位
check('9 点取消选中', await click('#btnDeselect'));
const r9 = await evl(`JSON.stringify({sel:!!S.selected,` +
  `hint:document.querySelector('#detail').textContent.includes('点选一颗卫星'),` +
  `bd:getComputedStyle(document.querySelector('#btnDeselect')).display,` +
  `bt:getComputedStyle(document.querySelector('#btnTrail')).display,on:S.trailOn,cover:S.coverOn})`);
const o9 = JSON.parse(r9);
check('9a 取消选中且开关隐藏复位', !o9.sel && o9.hint && o9.bd === 'none' &&
  o9.bt === 'none' && !o9.on && !o9.cover);

// 10 第 4 个视图「过境预报」：页签可切、面板开合、画布让位
check('10 切到过境预报', await click('#viewSeg button[data-v="pass"]'));
const r10 = await evl(`JSON.stringify({v:S.view,` +
  `on:document.querySelector('#passView').classList.contains('on'),` +
  `disp:getComputedStyle(document.querySelector('#passView')).display,` +
  `cv:getComputedStyle(document.querySelector('#cv3d')).display})`);
const o10 = JSON.parse(r10);
check('10a 视图=pass 且面板打开', o10.v === 'pass' && o10.on && o10.disp === 'block' && o10.cv === 'none',
  JSON.stringify(o10));
check('10b 切回 3D 面板关闭', await click('#viewSeg button[data-v="3d"]') &&
  (await evl('S.view')) === '3d' &&
  !(await evl(`document.querySelector('#passView').classList.contains('on')`)));

console.log(`\n== 自检结果: ${ok} 通过 / ${fail} 失败 ==`);
process.exit(fail ? 1 : 0);
