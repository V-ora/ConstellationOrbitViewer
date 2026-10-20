/* ============================================================================
   静态站适配层 —— 只在 GitHub Pages 构建产物（docs/）里出现
   ----------------------------------------------------------------------------
   为什么需要：本查看器原本是「网页 + 本地 Python 服务」两件套，页面一打开就向
   `/api/groups`、`/api/tle?group=…` 要数据。GitHub Pages 只托管文件、不跑 Python，
   那两个请求必然 404，界面会永远停在加载圈上。

   这层做的事（全部在前端，不改业务代码）：
     ① 拦截 fetch，把 /api/* 映射到构建时烘好的 JSON 快照；
     ② 两个 iframe 子页（batch.html / sat.html）用同一份 api/hist.json 现场切片，
        切片逻辑与 server.py 的对应分支一一对应（构建时会逐组与真接口比对，见 build_pages.py）；
     ③ 把「刷新 TLE / 退出程序」降级成提示，避免用户以为是坏掉了；
     ④ 在「数据状态」里说清楚「这是快照、截至哪天」。

   ★ 只在 docs/ 里生效：桌面版与源码版从不加载本文件。
   ========================================================================== */
(function () {
  "use strict";

  var META = window.__COV_STATIC__ || { date: "", counts: {}, total: 0 };
  var IN_RAISE = /\/web\/raise\//.test(location.pathname);   // 是不是被大图 iframe 载入的子页
  var origFetch = window.fetch ? window.fetch.bind(window) : null;
  // 子页在 web/raise/ 下，回退两级才是 api/；主页就在 docs/ 根。两处都要相对，
  // 因为 Pages 是把仓库放在 user.github.io/<仓库名>/ 这个子路径下的。
  var API = IN_RAISE ? "../../api/" : "api/";

  function jsonRes(obj) {
    return new Response(JSON.stringify(obj), {
      status: 200,
      headers: { "Content-Type": "application/json; charset=utf-8" }
    });
  }

  /* ---------------------------------------------------------------- 路由解析 */
  /* 页面里写的是 /api/tle?group=qianfan 这种**绝对路径**。
     绝对路径在 `user.github.io/仓库名/` 子目录下会解析到域名根 → 404，
     所以这里在 fetch 这一层就把它拦下来，换成同目录的 JSON 文件。 */
  function route(url) {
    var s = String(url || "");
    var i = s.indexOf("/api/");
    if (i < 0) return null;
    var rest = s.slice(i + 5);
    var q = {};
    var qi = rest.indexOf("?");
    if (qi >= 0) {
      rest.slice(qi + 1).split("&").forEach(function (kv) {
        if (!kv) return;
        var p = kv.split("=");
        q[decodeURIComponent(p[0])] = decodeURIComponent((p[1] || "").replace(/\+/g, " "));
      });
      rest = rest.slice(0, qi);
    }
    return { path: rest, q: q };
  }

  /* -------------------------------------------------- 历史数据（按需单次加载） */
  /* api/hist.json 有 4~5 MB，只有用户真的点开「组次 / 单星爬升图」时才拉，
     不进首屏。拉一次后缓存住，两个子页共用。 */
  var HIST = null, HIST_P = null;
  function loadHist() {
    if (HIST) return Promise.resolve(HIST);
    if (!HIST_P) {
      HIST_P = origFetch(API + "hist.json")
        .then(function (r) { return r.json(); })
        .then(function (j) { HIST = j; return j; });
    }
    return HIST_P;
  }

  /* 与 server.py 的 /api/batch_hist 分支逐行对应：
     按 metamap 的 batch 过滤 → 取 sat_hist 的曲线 → 按 (launch, name) 排序。
     ★ 改这里必须同步改 server.py，并重跑 build_pages.py 的比对（它会逐组核对）。 */
  function batchHist(hist, grp, bat) {
    var bm = hist.batch_map || {}, sats = hist.sats || {};
    var out = [], label = "";
    Object.keys(bm).forEach(function (norad) {
      var m = bm[norad] || {};
      if ((m.batch || "") !== bat) return;
      var h = sats[norad];
      if (!h) return;
      if (!label) label = m.group || m.mission || ("第 " + (m.batch_index || "?") + " 批");
      out.push({
        norad: String(norad),
        name: h.name || m.obj_name || ("NORAD " + norad),
        launch: h.launch || m.launch_date || "",
        target: h.target,
        src: h.src || grp,
        pts: h.pts || []
      });
    });
    out.sort(function (a, b) {
      var x = a.launch || "", y = b.launch || "";
      if (x < y) return -1;
      if (x > y) return 1;
      return a.name < b.name ? -1 : (a.name > b.name ? 1 : 0);
    });
    return {
      group: grp, batch: bat, label: label || bat,
      base: hist.base, now: hist.now, count: out.length, sats: out
    };
  }

  /* ---------------------------------------------------------------- fetch 接管 */
  if (origFetch) {
    window.fetch = function (input, init) {
      var url = (typeof input === "string") ? input : ((input && input.url) || "");
      var r = route(url);
      if (r) {
        if (r.path === "groups") return origFetch(API + "groups.json");
        if (r.path === "tle") {
          var g = r.q.group || "qianfan";
          // force=1 不再有意义：数据就是构建时那一份
          return origFetch(API + "tle-" + encodeURIComponent(g) + ".json");
        }
        if (r.path === "ping") {
          return Promise.resolve(jsonRes({
            boot: "static-" + META.date, server_time: META.date,
            static: true, network: { mode: "snapshot" }, cache: {}
          }));
        }
        if (r.path === "net") {
          return Promise.resolve(jsonRes({ bind: "", port: 0, lan_enabled: false, lan: [], static: true }));
        }
        if (r.path === "status") {
          // 静态站没有后端脚本，也就没有「大图自动更新」这回事 —— 这件事已由下面的
          // 「静态演示版」说明讲清楚，这里回 ok:true，避免同一条信息报两遍。
          return Promise.resolve(jsonRes({
            boot: "static-" + META.date, server_time: META.date,
            network: { mode: "snapshot" }, static: true,
            deliv: { ok: true, why: "", script: "" }
          }));
        }
        if (r.path === "sat_hist") {
          return loadHist().then(function (h) {
            var n = r.q.norad || "";
            var s = (h.sats || {})[n];
            if (!s) return jsonRes({ error: "no history", norad: n });
            var o = { norad: n, base: h.base, now: h.now };
            Object.keys(s).forEach(function (k) { o[k] = s[k]; });
            return jsonRes(o);
          });
        }
        if (r.path === "batch_hist") {
          return loadHist().then(function (h) { return jsonRes(batchHist(h, r.q.group || "", r.q.batch || "")); });
        }
      }
      if (String(url).indexOf("/quit") >= 0) return Promise.resolve(jsonRes({ ok: true }));
      return origFetch(input, init);
    };
  }

  /* 子页（大图里的组次 / 单星视图）只需要 fetch 接管，不再做界面改动 */
  if (IN_RAISE) return;

  /* ------------------------------------------------------------ 界面降级提示 */
  function toast(msg) {
    var b = document.getElementById("banner");
    if (!b) return;
    b.className = "overlay";
    b.style.display = "block";
    b.textContent = msg;
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { b.style.display = "none"; }, 3200);
  }

  function decorate() {
    // 顶栏副标题直接把「快照 + 日期」写出来：桌面一眼可见，手机上这段本来就不显示、不占位
    var sub = document.querySelector("header .brand span");
    if (sub) sub.textContent = "千帆 · 中国星网　数据快照 " + META.date;

    // 「退出程序」在静态站上没有意义（没有本地服务可退）
    var quit = document.getElementById("btnQuit");
    if (quit) quit.style.display = "none";

    // 「刷新 TLE」改成静态提示。用 document 的捕获阶段拦，
    // 这样一定早于按钮自己注册的 click 监听器（主脚本是在 DOMContentLoaded 里注册的）。
    var rf = document.getElementById("btnRefresh");
    if (rf) {
      rf.textContent = "⏸ 数据快照";
      rf.title = "这是发布时冻结的数据快照，不能在线刷新";
    }
    document.addEventListener("click", function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      if (t.closest("#btnRefresh")) {
        e.preventDefault();
        e.stopPropagation();
        toast("静态演示版：数据截至 " + META.date + "，不能在线刷新。需要实时请在本机运行查看器。");
      }
    }, true);

    // 「数据状态」里补一条说明
    var st = document.getElementById("status");
    if (st && st.parentNode) {
      var d = document.createElement("div");
      d.className = "hint";
      d.style.cssText = "margin-top:8px;padding-top:7px;border-top:1px dashed var(--line);color:var(--muted2)";
      d.innerHTML = "<b>静态演示版</b>　" + META.date + " 的快照，共 " + META.total
        + " 颗（千帆 " + (META.counts.qianfan || 0) + " / 星网低轨 " + (META.counts.gw || 0)
        + " / 高轨 " + (META.counts.gaogui || 0) + "）。<br>"
        + "位置仍会按当时根数实时推算、地球会转；但拿不到更新的根数。";
      st.parentNode.appendChild(d);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", decorate);
  else decorate();
})();
