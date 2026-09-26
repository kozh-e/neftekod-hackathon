/**
 * Модуль отрисовки графиков пульта старшего оператора (R5).
 * Реализует стандарты ISA-101. Все графики — инстансы Apache ECharts
 * (static/console/vendor/echarts.min.js), синхронизированные по курсору
 * через echarts.connect() в пределах одной вкладки.
 */

const TOKENS = {
  bg: "#D9DAD7",
  panel: "#EEEEEB",
  fact: "#2B2F33",
  hold: "#7D8288",
  rec: "#1D5AA6",
  edit: "#B45309",
  editInk: "#8A3F06",
  limit: "#B3261E",
  ink: "#16181A",
  ink2: "#3F4448",
  ink3: "#4A4E53",
  grid: "#C9CAC6",
  warn: "#A86A0A",
  gap: "#8A6A2A",
};

const FONT_SANS = "'IBM Plex Sans', sans-serif";
const FONT_MONO = "'IBM Plex Mono', monospace";

// Общий стиль тултипа — используется всеми графиками (CV/DP/MV и Парето),
// чтобы визуально не расходились два разных «языка» тултипов.
const TOOLTIP_BASE = {
  backgroundColor: "rgba(22, 24, 26, 0.95)",
  borderColor: "#3F4448",
  textStyle: { color: "#FFFFFF", fontFamily: FONT_SANS, fontSize: 13 },
};

/**
 * Получает существующий инстанс ECharts на контейнере либо создает новый.
 * Пересоздает инстанс, если контейнер был перезаписан не-ECharts содержимым
 * (например, текстовой заглушкой «нет данных»).
 */
function getOrCreateChart(container) {
  if (!container || !window.echarts) return null;
  let chart = window.echarts.getInstanceByDom(container);
  if (chart) {
    const hasCanvas = container.querySelector("canvas");
    if (!hasCanvas) {
      window.echarts.dispose(container);
      chart = null;
    }
  }
  if (!chart) {
    container.innerHTML = "";
    chart = window.echarts.init(container);
  }
  return chart;
}

/**
 * Показывает/скрывает центрированный текстовый оверлей поверх графика
 * (используется только для редкого случая «прогноз не строится» —
 * не относится к данным графика, поэтому не рисуется штатными средствами ECharts).
 */
function setOverlayText(container, lines) {
  if (!container) return;
  container.style.position = container.style.position || "relative";
  let overlay = container.querySelector(":scope > .chart-overlay-text");
  if (!lines || lines.length === 0) {
    if (overlay) overlay.style.display = "none";
    return;
  }
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "chart-overlay-text";
    container.appendChild(overlay);
  }
  overlay.innerHTML = lines
    .map((l, i) => `<div style="${i === 0 ? `color:${TOKENS.warn};font-weight:700;font-size:16px;` : `color:${TOKENS.ink3};font-size:13px;`}">${l}</div>`)
    .join("");
  overlay.style.display = "flex";
}

/**
 * Создает и монтирует инстансы ECharts для 8 графиков вкладки.
 * Группа синхронизации курсора определяется по id контейнера серы
 * ("chart-manual-*" -> вкладка ручного управления, иначе — обзор).
 * @param {Object} rootEls - { sulfur: HTMLElement, flash: HTMLElement, dp: HTMLElement, mv: HTMLElement[5] }
 * @returns {Object} handle
 */
export function mountCharts(rootEls) {
  const groupId = rootEls.sulfur && rootEls.sulfur.id.includes("manual") ? "manual-charts" : "overview-charts";

  const handle = {
    rootEls,
    groupId,
    view: null,
    charts: {
      sulfur: getOrCreateChart(rootEls.sulfur),
      flash: getOrCreateChart(rootEls.flash),
      dp: getOrCreateChart(rootEls.dp),
      mv: (rootEls.mv || []).map((el) => getOrCreateChart(el)),
    },
  };

  const allCharts = [handle.charts.sulfur, handle.charts.flash, handle.charts.dp, ...handle.charts.mv].filter(Boolean);
  allCharts.forEach((c) => { c.group = groupId; });
  if (window.echarts && allCharts.length > 0) {
    window.echarts.connect(groupId);
  }

  return handle;
}

/**
 * Отрисовывает все 8 графиков на основе текущего view.
 */
export function renderCharts(handle, view) {
  if (!handle || !view || !view.state) return;
  handle.view = view;

  const state = view.state;
  const historyHours = view.historyHours || 8;

  const nowMs = new Date(state.clock.now).getTime();
  const startMs = nowMs - historyHours * 3600 * 1000;
  const endMs = nowMs + 4 * 3600 * 1000;

  const activeColor = view.activeColor === "edit" ? TOKENS.edit : TOKENS.rec;
  const ctx = { state, view, startMs, endMs, nowMs, activeColor };

  if (handle.charts.sulfur && state.series.cv.length > 0) {
    renderCvChart(handle.rootEls.sulfur, handle.charts.sulfur, state.series.cv[0], ctx);
  }
  if (handle.charts.flash && state.series.cv.length > 1) {
    renderCvChart(handle.rootEls.flash, handle.charts.flash, state.series.cv[1], ctx);
  }
  if (handle.charts.dp && state.series.cv.length > 2) {
    renderDpChart(handle.charts.dp, state.series.cv[2], ctx);
  }
  if (Array.isArray(handle.charts.mv)) {
    state.series.mv.forEach((mvSeries, idx) => {
      const chart = handle.charts.mv[idx];
      if (chart) renderMvChart(chart, mvSeries, ctx);
    });
  }
}

export function resizeCharts(handle) {
  if (!handle) return;
  const allCharts = [handle.charts.sulfur, handle.charts.flash, handle.charts.dp, ...(handle.charts.mv || [])].filter(Boolean);
  allCharts.forEach((c) => c.resize());
  if (handle.view) renderCharts(handle, handle.view);
}

// -----------------------------------------------------------------------------
// CV-график (сера, вспышка): факт, hold/rec/active траектории, P10-P90, лимиты,
// зона прогноза, зона риска, пропуски сигнала ПАК.
// -----------------------------------------------------------------------------

function computeGapAreas(history, xNow) {
  const areas = [];
  let gapStart = null;
  history.forEach((pt) => {
    const tMs = new Date(pt.t).getTime();
    if (pt.quality === "MISSING" || pt.quality === "BAD") {
      if (gapStart == null) gapStart = tMs;
    } else if (gapStart != null) {
      areas.push([gapStart, tMs]);
      gapStart = null;
    }
  });
  if (gapStart != null) areas.push([gapStart, xNow]);
  return areas;
}

function gapMarkAreaData(gapAreas) {
  return gapAreas.map(([from, to]) => [
    {
      xAxis: from,
      itemStyle: { color: TOKENS.gap, opacity: 0.22 },
      label: { show: true, formatter: "нет сигнала ПАК", color: TOKENS.ink, fontWeight: 600, fontSize: 12, fontFamily: FONT_SANS },
    },
    { xAxis: to },
  ]);
}

function factSeriesData(history) {
  // null разрывает линию на пропусках — так ECharts не соединяет их отрезком.
  return history.map((pt) => {
    if (pt.v == null || pt.quality === "MISSING") return { value: [new Date(pt.t).getTime(), null] };
    return { value: [new Date(pt.t).getTime(), pt.v] };
  });
}

function bandSeries(bands, key) {
  return (bands || []).map((b) => ({ t: new Date(b.t).getTime(), v: b[key] }));
}

/** Стандартный "confidence band" рецепт ECharts: нижняя граница прозрачна,
 * верхняя стекается поверх дельты (p90 - p10) с заливкой. */
function confidenceBandSeries(bands, color) {
  const lower = bands.map((b) => [b.t, b.p10]);
  const delta = bands.map((b) => [b.t, b.p90 - b.p10]);
  return [
    {
      name: "p10",
      type: "line",
      data: lower,
      stack: "confidence-band",
      symbol: "none",
      lineStyle: { opacity: 0 },
      areaStyle: { opacity: 0 },
      silent: true,
      z: 1,
    },
    {
      name: "p90",
      type: "line",
      data: delta,
      stack: "confidence-band",
      symbol: "none",
      lineStyle: { opacity: 0 },
      areaStyle: { color, opacity: 0.18 },
      silent: true,
      z: 1,
    },
  ];
}

function stepLikeLine(points, extra) {
  return {
    type: "line",
    data: points.map((p) => [p.t, p.v]),
    symbol: "none",
    ...extra,
  };
}

function renderCvChart(container, chart, cvSeries, ctx) {
  const { state, view, startMs, endMs, nowMs, activeColor } = ctx;
  const active = view.active;
  const yMin = cvSeries.y_range ? cvSeries.y_range[0] : 0;
  const yMax = cvSeries.y_range ? cvSeries.y_range[1] : 100;

  const gapAreas = computeGapAreas(cvSeries.history, nowMs);
  const markAreaData = [
    [{ xAxis: nowMs, itemStyle: { color: "#FFFFFF", opacity: 0.55 } }, { xAxis: endMs }],
  ];
  const risk = active && active.risk ? active.risk.find((r) => r.cv === cvSeries.key) : null;
  if (risk && risk.first_breach_at && risk.probability >= 0.05) {
    markAreaData.push([
      { xAxis: new Date(risk.first_breach_at).getTime(), itemStyle: { color: TOKENS.limit, opacity: 0.12 } },
      { xAxis: endMs },
    ]);
  }
  markAreaData.push(...gapMarkAreaData(gapAreas));

  const markLineData = [
    {
      yAxis: cvSeries.limit.value,
      lineStyle: { color: TOKENS.limit, width: 2 },
      label: {
        formatter: cvSeries.limit.label,
        color: TOKENS.limit,
        fontWeight: 600,
        fontSize: 12,
        position: cvSeries.limit.sense === "max" ? "insideEndTop" : "insideEndBottom",
      },
    },
    {
      xAxis: nowMs,
      lineStyle: { color: TOKENS.ink, width: 1.5, type: [4, 3] },
      label: { formatter: "СЕЙЧАС", color: TOKENS.ink, fontWeight: 700, fontSize: 11, position: "insideEndTop" },
    },
  ];

  const series = [];

  const activeBands = active && active.cv && active.cv[cvSeries.key] ? active.cv[cvSeries.key] : null;
  const hasSigmas = activeBands && activeBands.some((b) => b.p10 != null && b.p90 != null);
  if (activeBands && hasSigmas) {
    const bands = activeBands.map((b) => ({ t: new Date(b.t).getTime(), p10: b.p10, p90: b.p90 }));
    series.push(...confidenceBandSeries(bands, activeColor));
  }

  if (state.hold && state.hold.cv && state.hold.cv[cvSeries.key]) {
    series.push(stepLikeLine(bandSeries(state.hold.cv[cvSeries.key], "p50"), {
      name: "hold",
      lineStyle: { color: TOKENS.hold, width: 2, type: "dashed" },
      z: 2,
    }));
  }

  if (view.recThin && view.recThin.cv && view.recThin.cv[cvSeries.key]) {
    series.push(stepLikeLine(bandSeries(view.recThin.cv[cvSeries.key], "p50"), {
      name: "рекомендация",
      lineStyle: { color: TOKENS.rec, width: 1.5 },
      z: 2,
    }));
  }

  let activeLastPoint = null;
  if (activeBands) {
    const pts = bandSeries(activeBands, "p50");
    activeLastPoint = pts[pts.length - 1] || null;
    series.push(stepLikeLine(pts, {
      name: "активная",
      lineStyle: { color: activeColor, width: 3 },
      z: 3,
      markPoint: activeLastPoint
        ? {
            silent: true,
            symbol: "circle",
            symbolSize: 1,
            itemStyle: { opacity: 0 },
            data: [{ coord: [activeLastPoint.t, activeLastPoint.v] }],
            label: {
              show: true,
              formatter: activeLastPoint.v.toFixed(1),
              color: activeColor,
              fontWeight: 700,
              fontSize: 14,
              fontFamily: FONT_MONO,
              position: "right",
              distance: 8,
            },
          }
        : undefined,
    }));
  }

  const factData = factSeriesData(cvSeries.history);
  series.push({
    name: "факт",
    type: "line",
    data: factData,
    symbol: (value, params) => (params.dataIndex === factData.length - 1 ? "circle" : "none"),
    symbolSize: 6,
    itemStyle: { color: TOKENS.ink },
    lineStyle: { color: TOKENS.fact, width: 2.5 },
    connectNulls: false,
    z: 4,
    markLine: { silent: true, symbol: "none", data: markLineData },
    markArea: { silent: true, data: markAreaData },
  });

  const noForecast = state.recommendation && state.recommendation.status === "REFUSAL_DATA" && !active;
  setOverlayText(container, noForecast ? ["Прогноз не строится", `нет достоверных данных по ${cvSeries.title.toLowerCase()}`] : null);

  chart.setOption(
    {
      backgroundColor: "transparent",
      animation: false,
      grid: { left: 60, right: 70, top: 28, bottom: 26 },
      tooltip: {
        trigger: "axis",
        ...TOOLTIP_BASE,
        formatter: (params) => cvTooltipFormatter(cvSeries, state, active, params),
      },
      xAxis: {
        type: "time",
        min: startMs,
        max: endMs,
        axisLine: { lineStyle: { color: TOKENS.grid } },
        axisLabel: { color: TOKENS.ink3, fontFamily: FONT_MONO, fontSize: 12, hideOverlap: true, formatter: (v) => formatHm(v) },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        min: yMin,
        max: yMax,
        splitNumber: 3,
        axisLine: { show: false },
        splitLine: { lineStyle: { color: TOKENS.grid } },
        axisLabel: { color: TOKENS.ink3, fontFamily: FONT_MONO, fontSize: 13, formatter: (v) => v.toFixed(1) },
      },
      series,
    },
    true
  );
}

function cvTooltipFormatter(cvSeries, state, active, params) {
  const hoveredMs = Array.isArray(params) ? params[0].axisValue : params.axisValue;
  const nowMs = new Date(state.clock.now).getTime();
  const isFuture = hoveredMs >= nowMs;
  let html = `<div style="font-weight:700;margin-bottom:6px;border-bottom:1px solid rgba(255,255,255,0.2);padding-bottom:3px;">Время: ${formatHm(hoveredMs)}</div>`;

  if (!isFuture) {
    const pt = closestByTime(cvSeries.history.map((p) => ({ t: new Date(p.t).getTime(), v: p.v })), hoveredMs);
    html += `<div>Факт: <b>${pt && pt.v != null ? pt.v.toFixed(1) : "—"}</b> ${cvSeries.unit}</div>`;
  } else {
    const parts = [];
    const holdPts = state.hold?.cv?.[cvSeries.key];
    const holdPt = holdPts ? closestByTime(bandSeries(holdPts, "p50").map((p) => ({ t: p.t, v: p.v })), hoveredMs) : null;
    if (holdPt && holdPt.v != null) parts.push(`Без изм.: ${holdPt.v.toFixed(1)}`);

    const actPts = active?.cv?.[cvSeries.key];
    if (actPts) {
      const actPt = closestByTime(actPts.map((b) => ({ t: new Date(b.t).getTime(), ...b })), hoveredMs);
      if (actPt && actPt.p50 != null) {
        let s = `Активная: <b>${actPt.p50.toFixed(1)}</b>`;
        if (actPt.p10 != null && actPt.p90 != null) s += ` [${actPt.p10.toFixed(1)}–${actPt.p90.toFixed(1)}]`;
        parts.push(s);
      }
    }
    html += `<div>${parts.join(" · ") || "—"} ${cvSeries.unit}</div>`;
  }
  return html;
}

function closestByTime(points, targetMs) {
  if (!points || points.length === 0) return null;
  let best = points[0];
  let bestDist = Math.abs(best.t - targetMs);
  for (let i = 1; i < points.length; i++) {
    const d = Math.abs(points[i].t - targetMs);
    if (d < bestDist) {
      best = points[i];
      bestDist = d;
    }
  }
  return best;
}

function formatHm(ms) {
  const dt = new Date(ms);
  const hh = String(dt.getUTCHours()).padStart(2, "0");
  const mm = String(dt.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

// -----------------------------------------------------------------------------
// Компактный график ΔP: минималистичный, без осей — только лимит, факт, активная.
// -----------------------------------------------------------------------------

function renderDpChart(chart, dpSeries, ctx) {
  const { view, startMs, endMs, nowMs, activeColor } = ctx;
  const active = view.active;
  const yMin = dpSeries.y_range ? dpSeries.y_range[0] : 100;
  const yMax = dpSeries.y_range ? dpSeries.y_range[1] : 500;

  const series = [
    {
      name: "факт",
      type: "line",
      data: factSeriesData(dpSeries.history),
      symbol: "none",
      lineStyle: { color: TOKENS.fact, width: 2 },
      connectNulls: false,
      markArea: {
        silent: true,
        data: [[{ xAxis: nowMs, itemStyle: { color: "#FFFFFF", opacity: 0.55 } }, { xAxis: endMs }]],
      },
      markLine: {
        silent: true,
        symbol: "none",
        data: [
          { yAxis: dpSeries.limit.value, lineStyle: { color: TOKENS.limit, width: 1.5, type: "dashed" } },
          { xAxis: nowMs, lineStyle: { color: TOKENS.ink, width: 1, type: [3, 3] } },
        ],
      },
    },
  ];

  if (active && active.cv && active.cv.dp) {
    series.push(stepLikeLine(bandSeries(active.cv.dp, "p50"), {
      name: "активная",
      lineStyle: { color: activeColor, width: 2.5 },
    }));
  }

  chart.setOption(
    {
      backgroundColor: "transparent",
      animation: false,
      grid: { left: 60, right: 60, top: 8, bottom: 8 },
      tooltip: { trigger: "axis", ...TOOLTIP_BASE, valueFormatter: (v) => (v == null ? "—" : v.toFixed(1)) },
      xAxis: { type: "time", min: startMs, max: endMs, show: false },
      yAxis: { type: "value", min: yMin, max: yMax, show: false },
      series,
    },
    true
  );
}

// -----------------------------------------------------------------------------
// Мини-график MV: ступенчатые SP/PV, коридор шага, линии T0/T1/warn.
// -----------------------------------------------------------------------------

function renderMvChart(chart, mvSeries, ctx) {
  const { view, startMs, endMs, nowMs, activeColor } = ctx;
  const active = view.active;

  const vals = [];
  mvSeries.sp_history.forEach((p) => p.v != null && vals.push(p.v));
  mvSeries.pv_history.forEach((p) => p.v != null && vals.push(p.v));
  let minV = Math.min(...vals, mvSeries.corridor.lo ?? 0);
  let maxV = Math.max(...vals, mvSeries.corridor.hi ?? 100);
  const span = Math.max(0.1, maxV - minV);
  const yMin = minV - 0.1 * span;
  const yMax = maxV + 0.1 * span;

  const markAreaData = [[{ xAxis: nowMs, itemStyle: { color: "#FFFFFF", opacity: 0.55 } }, { xAxis: endMs }]];
  if (mvSeries.corridor.max_step_per_tick && !mvSeries.frozen) {
    const spNow = mvSeries.sp_history.slice(-1)[0]?.v || 0;
    const cLo = Math.max(yMin, spNow - mvSeries.corridor.max_step_per_tick);
    const cHi = Math.min(yMax, spNow + mvSeries.corridor.max_step_per_tick);
    markAreaData.push([
      { xAxis: nowMs, yAxis: cLo, itemStyle: { color: TOKENS.rec, opacity: 0.16 } },
      { xAxis: nowMs + 10 * 60 * 1000, yAxis: cHi },
    ]);
  }

  const markLineData = mvSeries.lines.map((l) => ({
    yAxis: l.value,
    lineStyle: { color: l.kind === "warn" ? TOKENS.warn : TOKENS.limit, width: 1.2, type: [3, 2] },
  }));

  const series = [
    {
      name: "pv",
      type: "line",
      data: mvSeries.pv_history.filter((p) => p.v != null).map((p) => [new Date(p.t).getTime(), p.v]),
      symbol: "none",
      lineStyle: { color: TOKENS.hold, width: 1.2 },
      markArea: { silent: true, data: markAreaData },
      markLine: { silent: true, symbol: "none", data: markLineData },
    },
    {
      name: "sp",
      type: "line",
      step: "end",
      data: mvSeries.sp_history.filter((p) => p.v != null).map((p) => [new Date(p.t).getTime(), p.v]),
      symbol: "none",
      lineStyle: { color: TOKENS.ink, width: 2 },
    },
  ];

  const planPts = active && active.mv_plan && active.mv_plan[mvSeries.sp] ? active.mv_plan[mvSeries.sp] : null;
  if (planPts) {
    series.push({
      name: "план",
      type: "line",
      step: "end",
      data: planPts.map((p) => [new Date(p.t).getTime(), p.v]),
      symbol: "none",
      lineStyle: {
        color: mvSeries.frozen ? TOKENS.hold : activeColor,
        width: 2.5,
        type: mvSeries.frozen ? "dashed" : "solid",
      },
    });
  }

  chart.setOption(
    {
      backgroundColor: "transparent",
      animation: false,
      grid: { left: 4, right: 4, top: 4, bottom: 4 },
      tooltip: {
        trigger: "axis",
        ...TOOLTIP_BASE,
        valueFormatter: (v) => (v == null ? "—" : v.toFixed(mvSeries.decimals ?? 1)),
      },
      xAxis: { type: "time", min: startMs, max: endMs, show: false },
      yAxis: { type: "value", min: yMin, max: yMax, show: false },
      series,
    },
    true
  );
}

// -----------------------------------------------------------------------------
// Парето-фронт (D5, D10, D14): scatter-график на ECharts.
// -----------------------------------------------------------------------------

/**
 * Память по каждому DOM-контейнеру графика: отслеживаем, менял ли оператор ось X
 * вручную через <select>, чтобы автоподмена вырожденной оси по умолчанию (фикс §2)
 * никогда не переопределяла осознанный выбор оператора.
 */
const paretoAxisMemory = new WeakMap();

/**
 * Разброс (max - min) значений метрики `key` среди точек, у которых оно определено.
 */
function paretoMetricSpread(points, key) {
  let min = Infinity;
  let max = -Infinity;
  let count = 0;
  for (const p of points) {
    const v = p.metrics ? p.metrics[key] : null;
    if (v == null || isNaN(v)) continue;
    count += 1;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  if (count === 0) return { hasData: false, spread: 0 };
  return { hasData: true, spread: max - min };
}

/**
 * Персентиль по уже отсортированному по возрастанию массиву (линейная интерполяция).
 */
function paretoPercentile(sortedVals, q) {
  const pos = (sortedVals.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  if (sortedVals[base + 1] !== undefined) {
    return sortedVals[base] + rest * (sortedVals[base + 1] - sortedVals[base]);
  }
  return sortedVals[base];
}

/**
 * Границы обрезки оси по IQR (фикс §3): если среди значений есть точка дальше
 * 3×IQR от медианы, возвращает [Q1-1.5×IQR, Q3+1.5×IQR]; иначе null (обрезка не нужна).
 */
function computeParetoIqrClamp(values) {
  if (!values || values.length < 4) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const q1 = paretoPercentile(sorted, 0.25);
  const q3 = paretoPercentile(sorted, 0.75);
  const median = paretoPercentile(sorted, 0.5);
  const iqr = q3 - q1;
  if (!(iqr > 1e-9)) return null;
  const hasOutlier = values.some((v) => Math.abs(v - median) > 3 * iqr);
  if (!hasOutlier) return null;
  return { min: q1 - 1.5 * iqr, max: q3 + 1.5 * iqr };
}

// Стиль каждой из 5 категорий точек Парето-фронта. Цвет и символ заданы один раз и
// используются одновременно и для отдельных точек, и для серии целиком (фикс §1) —
// поэтому легенда ECharts (которая красит свой квадратик по цвету СЕРИИ) больше не
// расходится с фактическим цветом точек.
const PARETO_SERIES_STYLE = {
  pareto: { legend: "Парето-оптимальные", color: "#2E7D32", symbol: "diamond", symbolSize: 13, z: 3 },
  dominated: { legend: "Доминируемые", color: "#90A4AE", symbol: "circle", symbolSize: 10, z: 1 },
  vetoed: { legend: "Отклонённые (вето)", color: "#C62828", symbol: "pin", symbolSize: 13, z: 2 },
  hold: { legend: "Текущий режим (HOLD)", color: "#263238", symbol: "rect", symbolSize: 14, z: 4 },
  recommendation: { legend: "Рекомендация", color: "#F9A825", symbol: "diamond", symbolSize: 18, z: 5 },
};

/**
 * Отрисовывает Парето-фронт с использованием библиотеки Apache ECharts (D10, D14, §7.2).
 * @param {HTMLElement} container
 * @param {Object} paretoData - ParetoFrontDTO
 * @param {Object} options - { axisX, axisY, onPointClick }
 * @returns {Object} ECharts chart instance
 */
export function renderParetoChart(container, paretoData, { axisX, axisY, onPointClick } = {}) {
  if (!container) return null;
  if (!paretoData || !paretoData.points || paretoData.points.length === 0) {
    if (window.echarts && window.echarts.getInstanceByDom(container)) {
      window.echarts.dispose(container);
    }
    container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--ink-muted);font-size:16px;">Парето-анализ недоступен для этого такта</div>';
    return null;
  }

  if (!window.echarts) {
    container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--ink-muted);font-size:16px;">Библиотека ECharts не загружена</div>';
    return null;
  }

  // Найти спецификации осей
  const objectives = paretoData.objectives || [];
  let keyX = axisX;
  let keyY = axisY;
  if (!keyX || !objectives.some((o) => o.key === keyX)) {
    const defX = objectives.find((o) => o.key === "sulfur_giveaway");
    keyX = defX ? defX.key : (objectives[0]?.key || "sulfur_giveaway");
  }
  if (!keyY || !objectives.some((o) => o.key === keyY)) {
    const defY = objectives.find((o) => o.key === "net_margin");
    keyY = defY ? defY.key : (objectives[1]?.key || objectives[0]?.key || "net_margin");
  }

  // --- Фикс §2: дефолтная ось X часто вырождена (нулевой разброс) ---
  // Единственный сигнал того, что оператор осознанно сменил ось через <select> — это то,
  // что запрошенный ключ отличается от запрошенного на предыдущей отрисовке ЭТОГО контейнера.
  // Пока такого изменения не было (т.е. мы всё ещё показываем ось по умолчанию), при
  // вырождении подставляем первую метрику из objectives с ненулевым разбросом и показываем
  // подсказку. Как только оператор хоть раз выбрал ось сам — подмена больше не применяется,
  // выбор оператора всегда рисуется буквально, даже если он тоже вырожден.
  let axisMemory = paretoAxisMemory.get(container);
  if (!axisMemory) {
    axisMemory = { lastRequestedX: null, userPickedX: false };
    paretoAxisMemory.set(container, axisMemory);
  }
  if (axisMemory.lastRequestedX !== null && keyX !== axisMemory.lastRequestedX) {
    axisMemory.userPickedX = true;
  }
  axisMemory.lastRequestedX = keyX;

  let axisHint = null;
  if (!axisMemory.userPickedX) {
    const requestedSpread = paretoMetricSpread(paretoData.points, keyX);
    if (requestedSpread.hasData && requestedSpread.spread < 1e-6) {
      const requestedLabel = (objectives.find((o) => o.key === keyX) || {}).label || keyX;
      const fallback = objectives.find((o) => {
        if (o.key === keyX) return false;
        const s = paretoMetricSpread(paretoData.points, o.key);
        return s.hasData && s.spread >= 1e-6;
      });
      if (fallback) {
        axisHint = `Ось X (${requestedLabel}) не имеет разброса в этом такте — показана ${fallback.label}`;
        keyX = fallback.key;
      }
    }
  }

  const specX = objectives.find((o) => o.key === keyX) || { key: keyX, label: keyX, unit: "", sense: "min" };
  const specY = objectives.find((o) => o.key === keyY) || { key: keyY, label: keyY, unit: "", sense: "max" };

  const chart = getOrCreateChart(container);
  if (window.echarts.getInstanceByDom(container) === chart && container.querySelector("canvas")) {
    chart.resize();
  }
  chart.group = "pareto";

  // Разделение точек ровно на 5 категорий — цвет/символ берутся из PARETO_SERIES_STYLE,
  // тем же значением, что затем задаётся на уровне серии (фикс §1).
  const buckets = { pareto: [], dominated: [], vetoed: [], hold: [], recommendation: [] };
  const yValues = [];

  for (const p of paretoData.points) {
    const xv = p.metrics?.[keyX];
    const yv = p.metrics?.[keyY];
    if (xv == null || yv == null || isNaN(xv) || isNaN(yv)) continue;
    yValues.push(yv);

    let category;
    let borderColor = "transparent";
    let borderWidth = 0;

    if (p.is_recommendation) {
      category = "recommendation";
      borderColor = "#E65100";
      borderWidth = 2.5;
    } else if (p.is_hold) {
      category = "hold";
      borderColor = "#FFFFFF";
      borderWidth = 2;
    } else if (p.status === "pareto") {
      category = "pareto";
    } else if (p.status === "vetoed") {
      category = "vetoed";
    } else {
      category = "dominated";
    }

    const style = PARETO_SERIES_STYLE[category];
    buckets[category].push({
      name: p.candidate_id,
      value: [xv, yv],
      point: p,
      symbol: style.symbol,
      symbolSize: style.symbolSize,
      itemStyle: {
        color: style.color,
        borderColor: borderColor,
        borderWidth: borderWidth,
      },
    });
  }

  // --- Фикс §3: один выброс по оси Y (обычно net_margin) не должен растягивать график ---
  const iqrClamp = computeParetoIqrClamp(yValues);

  // Линии технологических лимитов
  const markLineData = [];
  if (specX.limit != null) {
    markLineData.push({
      xAxis: specX.limit,
      lineStyle: { color: "#C62828", type: "dashed", width: 2 },
      label: {
        formatter: `Лимит: ${specX.limit} ${specX.unit}`,
        position: "insideEndTop",
        color: "#C62828",
        fontSize: 11,
        fontWeight: 700,
      },
    });
  }
  if (specY.limit != null) {
    markLineData.push({
      yAxis: specY.limit,
      lineStyle: { color: "#C62828", type: "dashed", width: 2 },
      label: {
        formatter: `Лимит: ${specY.limit} ${specY.unit}`,
        position: "insideEndRight",
        color: "#C62828",
        fontSize: 11,
        fontWeight: 700,
      },
    });
  }

  const series = [
    {
      name: PARETO_SERIES_STYLE.pareto.legend,
      type: "scatter",
      data: buckets.pareto,
      symbol: PARETO_SERIES_STYLE.pareto.symbol,
      symbolSize: PARETO_SERIES_STYLE.pareto.symbolSize,
      itemStyle: { color: PARETO_SERIES_STYLE.pareto.color },
      z: PARETO_SERIES_STYLE.pareto.z,
      markLine: { silent: true, symbol: "none", data: markLineData },
    },
    {
      name: PARETO_SERIES_STYLE.dominated.legend,
      type: "scatter",
      data: buckets.dominated,
      symbol: PARETO_SERIES_STYLE.dominated.symbol,
      symbolSize: PARETO_SERIES_STYLE.dominated.symbolSize,
      itemStyle: { color: PARETO_SERIES_STYLE.dominated.color },
      z: PARETO_SERIES_STYLE.dominated.z,
    },
    {
      name: PARETO_SERIES_STYLE.vetoed.legend,
      type: "scatter",
      data: buckets.vetoed,
      symbol: PARETO_SERIES_STYLE.vetoed.symbol,
      symbolSize: PARETO_SERIES_STYLE.vetoed.symbolSize,
      itemStyle: { color: PARETO_SERIES_STYLE.vetoed.color },
      z: PARETO_SERIES_STYLE.vetoed.z,
    },
    {
      name: PARETO_SERIES_STYLE.hold.legend,
      type: "scatter",
      data: buckets.hold,
      symbol: PARETO_SERIES_STYLE.hold.symbol,
      symbolSize: PARETO_SERIES_STYLE.hold.symbolSize,
      itemStyle: { color: PARETO_SERIES_STYLE.hold.color, borderColor: "#FFFFFF", borderWidth: 2 },
      z: PARETO_SERIES_STYLE.hold.z,
    },
    {
      name: PARETO_SERIES_STYLE.recommendation.legend,
      type: "scatter",
      data: buckets.recommendation,
      symbol: PARETO_SERIES_STYLE.recommendation.symbol,
      symbolSize: PARETO_SERIES_STYLE.recommendation.symbolSize,
      itemStyle: { color: PARETO_SERIES_STYLE.recommendation.color, borderColor: "#E65100", borderWidth: 2.5 },
      z: PARETO_SERIES_STYLE.recommendation.z,
    },
  ];

  const option = {
    backgroundColor: "transparent",
    animation: false,
    title: {
      show: !!axisHint,
      text: axisHint || "",
      left: "center",
      top: 4,
      textStyle: { color: TOKENS.warn, fontSize: 12, fontWeight: 600, fontFamily: FONT_SANS },
    },
    grid: {
      left: "80px",
      right: "60px",
      top: axisHint ? "74px" : "50px",
      bottom: "60px",
      containLabel: false,
    },
    legend: {
      data: [
        PARETO_SERIES_STYLE.pareto.legend,
        PARETO_SERIES_STYLE.dominated.legend,
        PARETO_SERIES_STYLE.vetoed.legend,
        PARETO_SERIES_STYLE.hold.legend,
        PARETO_SERIES_STYLE.recommendation.legend,
      ],
      top: axisHint ? 28 : 10,
      textStyle: { color: "#16181A", fontSize: 12, fontFamily: FONT_SANS },
    },
    tooltip: {
      trigger: "item",
      ...TOOLTIP_BASE,
      textStyle: { color: "#FFFFFF", fontFamily: FONT_SANS, fontSize: 13 },
      formatter: (params) => {
        const p = params.data?.point;
        if (!p) return "";
        let html = `<div style="font-weight:700;font-size:14px;margin-bottom:4px;">${p.candidate_id}`;
        if (p.is_recommendation) html += ` <span style="color:#F9A825;">★ РЕКОМЕНДАЦИЯ</span>`;
        if (p.is_hold) html += ` <span style="color:#90A4AE;">(ТЕКУЩИЙ РЕЖИМ HOLD)</span>`;
        html += `</div>`;
        html += `<div>Статус: <b>${p.status === 'pareto' ? 'Парето-оптимальная' : (p.status === 'vetoed' ? 'Отклонена (вето)' : 'Доминируемая')}</b></div>`;
        html += `<div>${specX.label}: <b>${params.value[0].toFixed(2)} ${specX.unit}</b></div>`;
        html += `<div>${specY.label}: <b>${params.value[1].toFixed(2)} ${specY.unit}</b></div>`;

        if (p.delta_u && Object.keys(p.delta_u).length > 0) {
          const deltas = Object.entries(p.delta_u)
            .map(([k, v]) => `${k}: ${v > 0 ? "+" : ""}${v}`)
            .join(", ");
          html += `<div style="margin-top:4px;font-size:12px;color:#D4D5D1;">Δu: ${deltas}</div>`;
        }
        if (p.veto_reasons && p.veto_reasons.length > 0) {
          html += `<div style="margin-top:4px;font-size:12px;color:#FF8A80;">Вето: ${p.veto_reasons.join("; ")}</div>`;
        }
        if (p.dominated_by && p.dominated_by.length > 0) {
          html += `<div style="margin-top:4px;font-size:12px;color:#B0BEC5;">Доминируется: ${p.dominated_by.join(", ")}</div>`;
        }
        html += `<div style="margin-top:6px;font-size:11px;color:#81D4FA;">Кликните для перехода в ручное управление</div>`;
        return html;
      },
    },
    xAxis: {
      name: `${specX.label} (${specX.unit})`,
      nameLocation: "middle",
      nameGap: 34,
      nameTextStyle: { color: "#4A4E53", fontSize: 13, fontWeight: 600, fontFamily: FONT_SANS },
      type: "value",
      splitLine: { lineStyle: { color: "#C9CAC6", type: "dashed" } },
      axisLine: { lineStyle: { color: "#B9BBB7" } },
      axisLabel: { color: "#4A4E53", fontFamily: FONT_MONO },
      scale: true,
    },
    yAxis: {
      name: `${specY.label} (${specY.unit})`,
      nameLocation: "middle",
      nameGap: 55,
      nameTextStyle: { color: "#4A4E53", fontSize: 13, fontWeight: 600, fontFamily: FONT_SANS },
      type: "value",
      splitLine: { lineStyle: { color: "#C9CAC6", type: "dashed" } },
      axisLine: { lineStyle: { color: "#B9BBB7" } },
      axisLabel: { color: "#4A4E53", fontFamily: FONT_MONO },
      // Фикс §3: один выброс (напр. net_margin на 1-2 порядка больше кластера) не должен
      // растягивать шкалу — обрезаем видимый диапазон до [Q1-1.5*IQR, Q3+1.5*IQR], точка
      // остаётся в данных (и в тултипе целиком), просто уезжает за пределы видимой области.
      min: iqrClamp ? iqrClamp.min : undefined,
      max: iqrClamp ? iqrClamp.max : undefined,
      scale: true,
    },
    series,
  };

  // merge-режим (а не notMerge): вкладка Парето перерисовывается на каждый опрос
  // состояния (initParetoTab вызывается из refresh() на каждый такт), а состав серий
  // стабилен (всегда ровно 5 категорий в одном порядке) — полная пересборка тут не нужна
  // и вызывала видимую перерисовку статичных элементов (красные линии лимитов) каждый такт.
  chart.setOption(option);

  chart.off("click");
  chart.on("click", (params) => {
    if (params.data && params.data.point && onPointClick) {
      onPointClick(params.data.point);
    }
  });

  return chart;
}
