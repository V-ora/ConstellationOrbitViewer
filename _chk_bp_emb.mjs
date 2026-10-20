// 集成自检：在「查看器里」点开轨道爬升视图，再点大图表格里的某一行，
// 确认 iframe 内部也能弹出该组次的单独爬升图。
// 用法：node _chk_bp_emb.mjs [http://127.0.0.1:8809/] [行序号] [截图路径]
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const BASE = process.argv[2] || 'http://127.0.0.1:8809/';
const ROW = parseInt(process.argv[3] || '2', 10);
const SHOT = process.argv[4] || '';
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DBG = 9338;

const prof = mkdtempSync(path.join(tmpdir(), 'edge-bpe-'));
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
    errors.push((m.params.args || []).map(a => a.value ?? a.description ?? '').join(' ').slice(0, 160));
  if (m.method === 'Runtime.exceptionThrown')
    errors.push((m.params.exceptionDetails?.exception?.description || 'exception').slice(0, 160));
};
const send = (method, params = {}) => new Promise(res => {
  const id = ++mid; pend.set(id, res); ws.send(JSON.stringify({ id, method, params }));
});
const evl = async expr => (await send('Runtime.evaluate',
  { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;

await send('Runtime.enable');
await send('Page.enable');
await send('Page.navigate', { url: BASE + '#raise' });
await sleep(9000);   // 等首屏 TLE 抓完 + iframe 加载大图

let fail = 0;
const ok = (c, l, e = '') => { console.log(`${c ? '  OK  ' : '  NG  '} ${l}${e ? '   ' + e : ''}`); if (!c) fail++; };

const pre = await evl(`(() => {
  const f = document.getElementById('raiseFrame');
  const d = f && f.contentDocument;
  return { hasFrame: !!f, src: f ? (f.getAttribute('src')||'') : '',
           hasTbl: !!(d && d.querySelector('#lauts tbody tr')),
           hasPane: !!(d && d.getElementById('batchp')),
           hidden: !!(d && d.getElementById('batchp') && d.getElementById('batchp').style.display === 'none') };
})()`);
console.log('  ' + JSON.stringify(pre));
ok(pre.hasTbl, '大图表格已加载进 iframe');
ok(pre.hasPane && pre.hidden, '面板已注入 iframe 且初始隐藏');

const st = await evl(`(() => {
  const d = document.getElementById('raiseFrame').contentDocument;
  const tr = d.querySelectorAll('#lauts tbody tr')[${ROW}];
  if (!tr) return { err: '没有这一行' };
  const tds = [...tr.children].map(t => t.textContent.trim());
  tr.click();
  return { grp: tds[1], cospar: tds[6] };
})()`);
console.log('  点了：', JSON.stringify(st));
await sleep(1200);

const r2 = await evl(`(() => {
  const d = document.getElementById('raiseFrame').contentDocument;
  const p = d.getElementById('batchp'), cv = d.getElementById('bpCv');
  const ctx = cv.getContext('2d');
  let nz = 0;
  if (cv.width && cv.height) {
    const px = ctx.getImageData(0, 0, cv.width, cv.height).data;
    for (let i = 0; i < px.length; i += 4) if (px[i] < 245 || px[i+1] < 245 || px[i+2] < 245) nz++;
  }
  const big = d.querySelector('.wrap').getBoundingClientRect();
  const me  = p.getBoundingClientRect();
  return { visible: p.style.display !== 'none', title: d.getElementById('bpTitle').textContent,
           chips: d.querySelectorAll('#bpLegend .bchip[data-n]').length,
           nz, belowBig: me.top > big.bottom - 2,
           shotTop: Math.round(me.top) };
})()`);
console.log('  ' + JSON.stringify(r2));
ok(r2.visible, '嵌入时点行也能展开');
ok(r2.chips > 0, '图例有条目', 'chips=' + r2.chips);
ok(r2.nz > 5000, 'canvas 画了内容（非白像素）', '非白=' + r2.nz);
ok(r2.belowBig, '面板在大图之外（下方）');

if (SHOT) {
  await evl(`document.getElementById('raiseFrame').contentWindow.scrollTo(0, document.getElementById('raiseFrame').contentWindow.scrollY + ${Math.round((r2.shotTop || 0) - 90)}); 1`);
  await sleep(500);
  const r = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(SHOT, Buffer.from(r.result.data, 'base64'));
  console.log('  截图已存：' + SHOT);
}

ok(errors.length === 0, '零 console 错误', errors.slice(0, 3).join(' | '));
console.log(fail ? `\n== 失败 ${fail} 项 ==` : '\n== 全部通过 ==');
ws.close();
process.exit(fail ? 1 : 0);
