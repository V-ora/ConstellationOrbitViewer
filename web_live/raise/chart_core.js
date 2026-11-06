/* ============================================================================
 * chart_core.js —— 爬升大图的公共交互核心（千帆 / 国网 / 黎明共用）
 *
 * 为什么存在（2026-11-04）：
 *   千帆、国网两张图的渲染代码是互相复制的，于是同一个 bug 各犯一次：
 *     · 千帆：按「发射日」高亮，而极轨19组/26组在 UTC 是同一天 → 多算 9 颗
 *     · 国网：x.t 是 UTC 日期、卫星 s.l 是北京时间日期，低轨27组跨日 → 0 命中，悬停无反应
 *   数据都没错，错在「哪颗星属于哪一发」的匹配口径。把这段逻辑收在一处，
 *   改一次就两张图都对，并且由 check_charts.py 在数据层兜底。
 *
 * 口径（唯一正确解）：
 *   1) 能用 COSPAR 前缀（2026-243）对上，就**只按 COSPAR**——它与日期、时区无关；
 *   2) 只有拿不到 COSPAR 时才退回「按发射日 ±0.5 天」，且要有跨日容差意识。
 *
 * 调用方约定：页面里已有 DATA / tMin / tMax / X() 等，且注入顺序在本文件之后。
 * ========================================================================== */
(function (global) {
  "use strict";

  /** 从发射行取 COSPAR 前缀；占位符 '—' 视为没有。 */
  function batchCos(x) {
    if (!x) return null;
    var cs = (x.cospar || "").trim();
    if (!cs || cs === "—" || cs === "-") return null;
    return cs.slice(0, 8);
  }

  /**
   * 判断某颗卫星是否属于「当前悬停的这次发射」。
   * @param {object} s        卫星（需有 cs / l）
   * @param {string} hoverCos 悬停发射的 COSPAR 前缀（可为 null）
   * @param {number} hoverLaunch 悬停发射的时间轴坐标（天）
   * @param {string} t0       DATA.t0（ISO 字符串）
   */
  function satMatches(s, hoverCos, hoverLaunch, t0) {
    if (!s) return false;
    if (hoverCos && s.cs) return s.cs === hoverCos;      // 首选：COSPAR 精确匹配
    if (!s.l) return false;
    var d = new Date(s.l + "T00:00:00Z").getTime() - new Date(t0).getTime();
    if (isNaN(d)) return false;
    return Math.abs(d / 86400000 - hoverLaunch) <= 0.5;   // 兜底：按发射日
  }

  /**
   * 高亮一次发射的全部卫星曲线。
   * @param {object} o {order, sats, ctx, X, Y, colorOf, hoverLaunch, hoverCos, t0, lineWidth}
   */
  function highlightBatch(o) {
    var ctx = o.ctx, X = o.X, Y = o.Y, sats = o.sats || [];
    var lw = o.lineWidth || 2.6;
    (o.order || []).forEach(function (i) {
      var s = sats[i];
      if (!satMatches(s, o.hoverCos, o.hoverLaunch, o.t0)) return;
      ctx.strokeStyle = o.colorOf(s);
      ctx.lineWidth = lw;
      ctx.beginPath();
      (s.p || []).forEach(function (q, k) {
        var x = X(q[0]), y = Y(q[1]);
        if (k === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
    });
  }

  /** 命中测试：鼠标 x 落在哪个发射刻度上（±9px）。 */
  function pickLaunch(mx, o) {
    var best = null;
    (o.launches || []).forEach(function (x) {
      if (x.t < o.tMin || x.t > o.tMax) return;
      if (Math.abs(o.X(x.t) - mx) < 9) best = x;
    });
    return best;
  }

  /** 悬停状态的三处入口统一封装：表格行进入/离开、画布进入/离开。 */
  function setHover(state, launch) {
    state.hoverLaunch = launch ? launch.t : null;
    state.hoverCos = launch ? batchCos(launch) : null;
  }

  var api = { batchCos: batchCos, satMatches: satMatches, highlightBatch: highlightBatch,
              pickLaunch: pickLaunch, setHover: setHover };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.ChartCore = api;
})(typeof window !== "undefined" ? window : this);
