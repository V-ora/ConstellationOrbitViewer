/* ============================================================================
   移动端 / 触屏配套脚本（2026-10-20）
   ----------------------------------------------------------------------------
   只做四件 index.html 内联脚本里没有的事，不碰原有业务逻辑：
     ① 窄屏抽屉：给左右面板造开关按钮 + 遮罩（桌面宽度下按钮由 CSS 隐藏）
     ② 双指捏合缩放：内联脚本只监听了 wheel（鼠标滚轮），手机上没有滚轮
     ③ 全屏按钮：浏览器在手机上隐藏地址栏，能多出 ~90px 给地球
     ④ 「手机访问」卡片：把局域网地址显示出来，省得去翻命令行

   加载位置：index.html 的 </body> 前，必须在内联主脚本**之后** ——
   这样才能读到大脚本里的顶层 const S（全局词法作用域，后续 classic script 可见）。
   ========================================================================== */
(function () {
  "use strict";

  var S_ = (typeof S !== "undefined") ? S : null;   // 主脚本的状态对象
  var stage = document.getElementById("stage");
  var footer = document.querySelector("footer");
  if (!stage || !footer) return;

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  // ★ 必须与 mobile.css 的单栏断点一致（≤900px），否则抽屉状态与布局会各说各话
  function narrow() { return window.innerWidth <= 900; }

  /* ------------------------------------------------------------------ ① 抽屉 */
  var btnL = document.createElement("button");
  var btnR = document.createElement("button");
  var btnF = document.createElement("button");
  btnL.id = "mbLeft"; btnR.id = "mbRight"; btnF.id = "mbFull";
  btnL.type = btnR.type = btnF.type = "button";
  btnL.textContent = "☰"; btnL.title = "星座 / 筛选 / 卫星列表";
  btnR.textContent = "ℹ"; btnR.title = "选中卫星 / 图例 / 数据状态";
  btnF.textContent = "⤢"; btnF.title = "全屏（手机上可多出一截视野）";
  btnL.style.order = "-1"; btnR.style.order = "9"; btnF.style.order = "10";
  footer.appendChild(btnL); footer.appendChild(btnR); footer.appendChild(btnF);

  var mask = document.createElement("div");
  mask.id = "mbMask";
  document.body.appendChild(mask);

  function closeDrawers() {
    document.body.classList.remove("mb-l", "mb-r");
    btnL.classList.remove("on"); btnR.classList.remove("on");
  }
  btnL.addEventListener("click", function () {
    var on = document.body.classList.contains("mb-l");
    closeDrawers();
    if (!on) { document.body.classList.add("mb-l"); btnL.classList.add("on"); }
  });
  btnR.addEventListener("click", function () {
    var on = document.body.classList.contains("mb-r");
    closeDrawers();
    if (!on) { document.body.classList.add("mb-r"); btnR.classList.add("on"); }
  });
  mask.addEventListener("click", closeDrawers);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeDrawers();
  });

  // 抽屉右上角的 ✕：抽屉打开时会把左下的 ☰ / 右下的 ℹ 盖住，没有明确的关闭入口
  ["left", "right"].forEach(function (id) {
    var a = document.getElementById(id);
    if (!a) return;
    var x = document.createElement("button");
    x.type = "button"; x.className = "mb-x"; x.textContent = "✕"; x.title = "收起面板";
    x.addEventListener("click", closeDrawers);
    a.appendChild(x);
  });

  // 转回宽屏时一定要把抽屉状态清掉，否则 aside 会带着 transform 留在原位挡住布局
  window.addEventListener("resize", function () { if (!narrow()) closeDrawers(); });

  // 在列表里点一颗星 → 收起抽屉，让人立刻看到地球上的位置
  var list = document.getElementById("list");
  if (list) {
    list.addEventListener("click", function () {
      if (document.body.classList.contains("mb-l")) setTimeout(closeDrawers, 40);
    });
  }

  /* ------------------------------------------------ ③ 全屏（不支持的设备就藏按钮） */
  var de = document.documentElement;
  var fsOK = !!(de.requestFullscreen || de.webkitRequestFullscreen || de.mozRequestFullScreen);
  if (!fsOK) { btnF.style.display = "none"; }
  btnF.addEventListener("click", function () {
    try {
      var cur = document.fullscreenElement || document.webkitFullscreenElement;
      if (cur) { (document.exitFullscreen || document.webkitExitFullscreen).call(document); }
      else { (de.requestFullscreen || de.webkitRequestFullscreen || de.mozRequestFullScreen).call(de); }
    } catch (e) { /* 被浏览器策略挡了就算了，不报错 */ }
  });

  /* ------------------------------------------------------ ② 双指捏合缩放 */
  /* 难点：主脚本用 pointerdown 设了 drag、pointermove 里就拿它转地球，两指同时按下
     会「一边缩放一边乱转」。这里不去改主脚本，改用事件层面把它按住 ——
     第二根手指落下时补发一个 pointercancel，主脚本的 pointercancel 监听器会把
     drag 置空并摘掉 dragging 样式，于是捏合过程中不再旋转。

     ★ 为什么要「延到微任务」里再发，而不是当场发：
       主脚本的画布监听器是在 DOMContentLoaded 的 boot() 里注册的，
       比本文件（普通 script，解析完立刻执行）**更晚**；按注册顺序本文件反而更早。
       当场发 pointercancel 的话，主脚本的 pointerdown 监听器随后才跑，
       又把 drag 重新设上 —— 白取消一场（实测捏合时地球仍被转了 16.8°）。
       放微任务里 ⇒ 本轮 pointerdown 的所有监听器都跑完了才取消，与注册顺序无关。 */
  var live = new Map();
  var pinch = null;

  function fireCancelSoon() {
    var run = function () {
      try { stage.dispatchEvent(new Event("pointercancel")); } catch (e) { }
    };
    if (typeof queueMicrotask === "function") queueMicrotask(run);
    else Promise.resolve().then(run);
  }

  stage.addEventListener("pointerdown", function (e) {
    if (e.pointerType !== "touch") return;
    live.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (live.size === 2) {
      var p = Array.from(live.values());
      var d = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y);
      var dist0 = (S_ && S_.cam && S_.cam.dist) || 3;
      pinch = { d0: d, dist0: dist0, lastD: 0 };
      fireCancelSoon();                   // 让主脚本停止旋转（见上面的说明）
    }
  });

  stage.addEventListener("pointermove", function (e) {
    if (!live.has(e.pointerId)) return;
    live.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (!pinch || live.size < 2 || !S_ || !S_.cam) return;
    if (S_.view !== "3d" && S_.view !== "2d") return;
    var p = Array.from(live.values());
    var d = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y);
    // d0 / d 太小的时候比值会疯掉（两指几乎重合），加个下限
    if (pinch.d0 < 24 || d < 24) return;
    // 2D：捏合 = 缩放地图（2026-10-25）；3D：捏合 = 拉近拉远
    if (S_.view === "2d" && S_.zoom2D) {
      var cv2 = document.getElementById("cv2d"), r2 = cv2.getBoundingClientRect();
      var k2 = r2.width ? cv2.width / r2.width : 1;
      var mid = { x: (p[0].x + p[1].x) / 2, y: (p[0].y + p[1].y) / 2 };
      var f = pinch.lastD ? d / pinch.lastD : 1;
      pinch.lastD = d;
      S_.zoom2D(f, (mid.x - r2.left) * k2, (mid.y - r2.top) * k2);
      return;
    }
    S_.cam.dist = clamp(pinch.dist0 * pinch.d0 / d, 1.14, 45);
  });

  function endPointer(e) {
    if (!live.has(e.pointerId)) return;
    live.delete(e.pointerId);
    if (live.size < 2) pinch = null;
  }
  stage.addEventListener("pointerup", endPointer);
  stage.addEventListener("pointercancel", endPointer);

  // 双指按在画布上时别让浏览器弹「长按选择 / 放大镜」
  stage.addEventListener("contextmenu", function (e) {
    if (e.target && e.target.tagName === "CANVAS") e.preventDefault();
  });

  /* --------------------------------------------------- ④ 「手机访问」卡片 */
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function renderNet(d) {
    var host = document.getElementById("mbNet");
    if (!host) return;
    // 静态站（GitHub Pages）：没有本地服务，也就没有局域网地址这回事
    if (d && d.static) {
      host.innerHTML = '<div class="mb-hint">本页是<b>静态演示版</b>（托管在 GitHub Pages），'
        + '数据为发布时的快照，没有后端服务，所以既不需要、也没有「局域网访问」。</div>';
      return;
    }
    if (!d || !d.lan_enabled) {
      host.innerHTML = '<div class="mb-hint">局域网访问未开启 —— 手机连不上。'
        + '用 <code>--lan</code> 启动（<code>start.bat</code> 已默认开启）即可。</div>';
      return;
    }
    var urls = (d.lan || []).filter(Boolean);
    if (!urls.length) {
      host.innerHTML = '<div class="mb-hint">已开启局域网模式，但没探测到本机 IP。'
        + '请确认电脑已连上 Wi‑Fi / 网线。</div>';
      return;
    }
    host.innerHTML = '<div class="mb-hint">手机与电脑连同一个 Wi‑Fi，用手机浏览器打开：</div>'
      + urls.map(function (u, i) {
        return '<div class="mb-url"><code>' + esc(u) + '</code>'
          + '<button class="btn sm" data-u="' + esc(u) + '">复制</button></div>'
          + (i === 0 ? '' : '');
      }).join("")
      + '<div class="mb-hint" style="margin-top:5px;color:var(--muted2)">'
      + '端口 ' + esc(d.port) + '；电脑上这个程序（或这个黑窗口）关掉就断。</div>';
    Array.prototype.forEach.call(host.querySelectorAll("button[data-u]"), function (b) {
      b.addEventListener("click", function () {
        var t = b.getAttribute("data-u");
        var done = function () { b.textContent = "已复制"; setTimeout(function () { b.textContent = "复制"; }, 1200); };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(t).then(done, function () { fallbackCopy(t, done); });
        } else { fallbackCopy(t, done); }
      });
    });
  }

  function fallbackCopy(t, done) {
    // 非 https / 老 WebView 里 navigator.clipboard 不可用，退回 textarea + execCommand
    var ta = document.createElement("textarea");
    ta.value = t; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); done(); } catch (e) { }
    document.body.removeChild(ta);
  }

  var statusBox = document.getElementById("status");
  if (statusBox && statusBox.parentNode) {
    var pane = document.createElement("div");
    pane.id = "mbNet";
    pane.style.cssText = "margin-top:9px;padding-top:8px;border-top:1px dashed var(--line)";
    pane.innerHTML = '<div class="mb-hint">正在探测局域网地址…</div>';
    statusBox.parentNode.insertBefore(pane, statusBox.nextSibling);

    var st = document.createElement("style");
    st.textContent = "#mbNet .mb-hint{color:var(--muted);font-size:11.5px;line-height:1.6}"
      + "#mbNet code{background:var(--panel2);border:1px solid var(--line);border-radius:6px;"
      + "padding:1px 5px;font-size:11.5px;word-break:break-all}"
      + "#mbNet .mb-url{display:flex;align-items:center;gap:6px;margin:5px 0}"
      + "#mbNet .mb-url code{flex:1 1 auto;min-width:0}"
      + "#mbNet .mb-url .btn{flex:none;padding:3px 8px;font-size:11px}";
    document.head.appendChild(st);

    fetch("/api/net", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(renderNet)
      .catch(function () { pane.innerHTML = '<div class="mb-hint">局域网地址探测失败。</div>'; });
  }
})();
