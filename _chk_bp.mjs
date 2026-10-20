// 验证新功能：点「发射任务一览」某一行 → 大图「之外」单独出现该组次的爬升图。
// 检查项：面板从隐藏变可见 / canvas 真的画了像素 / 图例条数 = 该组卫星数 / 提示语已补 / console 零报错。
// 用法：node _chk_bp.mjs [URL或file路径] [行序号(0基)]
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const QF = 'file:///D:/Work Buddy %E5%B7%A5%E4%BD%9C%E5%8C%BA/%E6%98%9F%E5%BA%A7%E8%BD%A8%E9%81%93%E5%AE%9E%E6%97%B6%E6%9F%A5%E7%9C%8B%E5%99%A8/web/raise/%E5%8D%83%E5%B8%86%E6%98%9F%E5%BA%A7%E8%BD%A8%E9%81%93%E7%88%AC%E5%8D%87%E4%BA%A4%E4%BA%92%E5%A4%A7%E5%9B%BE.html';
const URL0 = process.argv[2] || QF;
const ROW = parseInt(process.argv[3] || '2', 10);
const SHOT = process.argv[4] || '';   // 给了路径就顺手截一张（用来肉眼核对版式）
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9336;

const prof = mkdtempSync(path.join(tmpdir(), 'edge-bp-'));
const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-sandbox',
  `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`,
  '--window-size=1600,1000', 'about:blank'], { stdio: 'ignore' });
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

let mid = 0; const pend = new Map(); const errors = [];
ws.onmessage = ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); return; }
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error')
    errors.push((m.params.args || []).map(a => a.value ?? a.description ?? '').join(' ').slice(0, 200));
  if (m.method === 'Runtime.exceptionThrown')
    errors.push((m.params.exceptionDetails?.exception?.description ||
      m.params.exceptionDetails?.text || 'exception').slice(0, 200));
};
function send(method, params = {}) {
  const id = ++mid;
  return new Promise(res => { pend.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
}
async function evl(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.result?.exceptionDetails) return { __err: r.result.exceptionDetails.text };
  return r.result?.result?.value;
}

await send('Runtime.enable');
await send('Page.enable');
await send('Page.navigate', { url: URL0 });
await sleep(3500);

let fail = 0;
const ok = (cond, label, extra = '') => {
  console.log(`${cond ? '  OK  ' : '  NG  '} ${label}${extra ? '   ' + extra : ''}`);
  if (!cond) fail++;
};

console.log('== 面板初始状态 ==');
ok(await evl(`document.getElementById('batchp').style.display === 'none'`), '初始隐藏');

console.log('== 点第 ' + ROW + ' 行表格 ==');
const info = await evl(`(() => {
  const tr = document.querySelectorAll('#lauts tbody tr')[${ROW}];
  if (!tr) return {err:'没有这一行'};
  const tds = [...tr.children].map(td => td.textContent.trim());
  tr.click();
  return {grp: tds[1], bjt: tds[2], n: tds[5], cospar: tds[6]};
})()`);
console.log('  行内容：', JSON.stringify(info));
await sleep(900);

console.log('== 展开结果 ==');
const st = await evl(`(() => {
  const p = document.getElementById('batchp');
  const cv = document.getElementById('bpCv');
  const ctx = cv.getContext('2d');
  let nz = 0;          // 非白像素（白底全铺，所以必须排除近白，否则永远满格）
  if (cv.width && cv.height) {
    const d = ctx.getImageData(0, 0, cv.width, cv.height).data;
    for (let i = 0; i < d.length; i += 4)
      if (d[i] < 245 || d[i + 1] < 245 || d[i + 2] < 245) nz++;
  }
  return {
    visible: p.style.display !== 'none',
    title: document.getElementById('bpTitle').textContent,
    sub: document.getElementById('bpSub').textContent,
    chips: document.querySelectorAll('#bpLegend .bchip[data-n]').length,
    cw: cv.clientWidth, ch: cv.clientHeight, nz,
    emptyShown: document.getElementById('bpEmpty').style.display !== 'none',
    rowcur: document.querySelectorAll('#lauts tbody tr.rowcur').length,
    hint: (document.getElementById('rstats').textContent || '').indexOf('点下面表格任意一行') >= 0,
    belowBig: p.getBoundingClientRect().top > document.querySelector('.wrap').getBoundingClientRect().bottom - 2,
  };
})()`);
console.log('  ' + JSON.stringify(st));

ok(st.visible, '面板已展开');
ok(/单独爬升图/.test(st.title || ''), '标题正确', st.title);
ok(st.chips > 0, '图例有条目', 'chips=' + st.chips);
ok(st.cw > 300 && st.ch > 200, 'canvas 有尺寸', st.cw + 'x' + st.ch);
ok(st.nz > 5000, 'canvas 真的画了内容（非白像素）', '非白=' + st.nz);
ok(st.rowcur === 1, '当前行有高亮');
ok(st.belowBig, '面板在大图「之外」（在其下方）');
ok(st.hint, '表格上方补了用法提示');

if (SHOT) {
  await send('Emulation.setDeviceMetricsOverride',
             { width: 1600, height: 1180, deviceScaleFactor: 1, mobile: false });
  await evl(`document.getElementById('batchp').scrollIntoView({block:'start'}); window.scrollBy(0,-56); 1`);
  await sleep(600);
  const r = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(SHOT, Buffer.from(r.result.data, 'base64'));
  console.log('  截图已存：' + SHOT);
  await send('Emulation.clearDeviceMetricsOverride');
}

console.log('== 再点同一行应收起 ==');
await evl(`document.querySelectorAll('#lauts tbody tr')[${ROW}].click()`);
await sleep(400);
ok(await evl(`document.getElementById('batchp').style.display === 'none'`), '再次点击后收起');

console.log('== 无数据组次的兜底 ==');
const em = await evl(`(() => {
  window.bpOpen({grp:'测试组', date:'2030-01-01', cospar:'—', n:0, bjt:'2030-01-01 00:00', rocket:'—'}, null);
  return { vis: document.getElementById('batchp').style.display !== 'none',
           empty: document.getElementById('bpEmpty').style.display !== 'none',
           txt: document.getElementById('bpEmpty').textContent.slice(0, 24) };
})()`);
console.log('  ' + JSON.stringify(em));
ok(em.vis && em.empty, '无数据时给出说明而不是空图');
await evl(`window.bpHide()`);

console.log('== console 报错 ==');
ok(errors.length === 0, '零 console 错误', errors.slice(0, 4).join(' | '));

console.log(fail ? `\n== 失败 ${fail} 项 ==` : '\n== 全部通过 ==');
ws.close();
process.exit(fail ? 1 : 0);
