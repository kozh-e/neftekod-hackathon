/**
 * Модуль отрисовки графиков пульта старшего оператора (R5).
 * Реализует стандарты ISA-101 и спецификацию ROLE_5_frontend_charts.md.
 * Обеспечивает синхронизированный курсор, тултипы, P10-P90 полосы, коридоры и риски.
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
};

/**
 * Создает и монтирует структуры графиков.
 * @param {Object} rootEls - { sulfur: HTMLElement, flash: HTMLElement, dp: HTMLElement, mv: HTMLElement[5] }
 * @returns {Object} handle
 */
export function mountCharts(rootEls) {
  const handle = {
    rootEls,
    view: null,
    hoveredX: null,
    hoveredTime: null,
    tooltipEl: null,
  };

  // Создаем плавающий тултип
  let tip = document.getElementById("charts-shared-tooltip");
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "charts-shared-tooltip";
    tip.style.position = "fixed";
    tip.style.pointerEvents = "none";
    tip.style.background = "rgba(22, 24, 26, 0.94)";
    tip.style.color = "#FFFFFF";
    tip.style.padding = "8px 12px";
    tip.style.borderRadius = "6px";
    tip.style.fontSize = "13px";
    tip.style.fontFamily = "'IBM Plex Mono', monospace";
    tip.style.zIndex = "1000";
    tip.style.display = "none";
    tip.style.boxShadow = "0 4px 12px rgba(0,0,0,0.3)";
    document.body.appendChild(tip);
  }
  handle.tooltipEl = tip;

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
  const totalMs = endMs - startMs;

  const activeColor = view.activeColor === "edit" ? TOKENS.edit : TOKENS.rec;

  // 1. График серы (CV 0)
  if (handle.rootEls.sulfur && state.series.cv.length > 0) {
    const sSeries = state.series.cv[0];
    renderCvSvg(
      handle.rootEls.sulfur,
      sSeries,
      state,
      view,
      startMs,
      endMs,
      nowMs,
      totalMs,
      activeColor,
      handle,
      220
    );
  }

  // 2. График вспышки (CV 1)
  if (handle.rootEls.flash && state.series.cv.length > 1) {
    const fSeries = state.series.cv[1];
    renderCvSvg(
      handle.rootEls.flash,
      fSeries,
      state,
      view,
      startMs,
      endMs,
      nowMs,
      totalMs,
      activeColor,
      handle,
      200
    );
  }

  // 3. Компактный перепад давления (DP8)
  if (handle.rootEls.dp && state.series.cv.length > 2) {
    const dpSeries = state.series.cv[2];
    renderDpCompactSvg(
      handle.rootEls.dp,
      dpSeries,
      state,
      view,
      startMs,
      endMs,
      nowMs,
      totalMs,
      activeColor,
      handle
    );
  }

  // 4. 5 мини-графиков MV
  if (handle.rootEls.mv && Array.isArray(handle.rootEls.mv)) {
    state.series.mv.forEach((mvSeries, idx) => {
      const el = handle.rootEls.mv[idx];
      if (el) {
        renderMvSvg(
          el,
          mvSeries,
          state,
          view,
          startMs,
          endMs,
          nowMs,
          totalMs,
          activeColor,
          handle
        );
      }
    });
  }
}

export function resizeCharts(handle) {
  if (handle && handle.view) {
    renderCharts(handle, handle.view);
  }
}

// -----------------------------------------------------------------------------
// Вспомогательные функции отрисовки SVG
// -----------------------------------------------------------------------------

function renderCvSvg(container, cvSeries, state, view, startMs, endMs, nowMs, totalMs, activeColor, handle, h) {
  const w = container.clientWidth || 800;
  if (container.clientHeight > 0) {
    h = container.clientHeight;
  }
  const padL = 60;
  const padR = 60;
  const padT = 16;
  const padB = 26;
  const plotW = Math.max(10, w - padL - padR);
  const plotH = Math.max(10, h - padT - padB);

  const yMin = cvSeries.y_range ? cvSeries.y_range[0] : 0;
  const yMax = cvSeries.y_range ? cvSeries.y_range[1] : 100;

  const getX = (t) => padL + ((new Date(t).getTime() - startMs) / totalMs) * plotW;
  const getY = (v) => padT + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

  const xNow = getX(state.clock.now);

  let svg = `<svg width="100%" height="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="display:block; overflow:hidden; max-width: 100%;">`;

  // Штриховка для пропусков данных
  svg += `<defs>
    <pattern id="hatch-gap-${cvSeries.key}" width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="10" stroke="#8A6A2A" stroke-width="3" stroke-opacity="0.35" />
    </pattern>
  </defs>`;

  // Зона прогноза (фон)
  svg += `<rect x="${xNow}" y="${padT}" width="${padL + plotW - xNow}" height="${plotH}" fill="#FFFFFF" fill-opacity="0.55" />`;

  // Сетка Y: 4 линии с подписями
  for (let i = 0; i <= 3; i++) {
    const val = yMin + (i / 3) * (yMax - yMin);
    const y = getY(val);
    svg += `<line x1="${padL}" y1="${y}" x2="${padL + plotW}" y2="${y}" stroke="${TOKENS.grid}" stroke-width="1" />`;
    svg += `<text x="${padL - 8}" y="${y + 4}" fill="${TOKENS.ink3}" font-size="13" text-anchor="end" class="mono">${val.toFixed(1)}</text>`;
  }

  // Временные метки по X
  const stepMs = totalMs <= 5 * 3600 * 1000 ? 15 * 60 * 1000 : 2 * 3600 * 1000;
  const firstTick = Math.ceil(startMs / stepMs) * stepMs;
  for (let t = firstTick; t <= endMs; t += stepMs) {
    const x = padL + ((t - startMs) / totalMs) * plotW;
    const dt = new Date(t);
    const hh = String(dt.getUTCHours()).padStart(2, "0");
    const mm = String(dt.getUTCMinutes()).padStart(2, "0");
    svg += `<line x1="${x}" y1="${padT + plotH}" x2="${x}" y2="${padT + plotH + 4}" stroke="${TOKENS.grid}" stroke-width="1" />`;
    svg += `<text x="${x}" y="${padT + plotH + 18}" fill="${TOKENS.ink3}" font-size="12" text-anchor="middle" class="mono">${hh}:${mm}</text>`;
  }

  // Красная заливка участка риска
  const active = view.active;
  if (active && active.risk) {
    const r = active.risk.find((x) => x.cv === cvSeries.key);
    if (r && r.first_breach_at && r.probability >= 0.05) {
      const rx = getX(r.first_breach_at);
      const rw = padL + plotW - rx;
      if (rw > 0) {
        svg += `<rect x="${rx}" y="${padT}" width="${rw}" height="${plotH}" fill="${TOKENS.limit}" fill-opacity="0.12" />`;
      }
    }
  }

  // Заштрихованные пропуски данных в истории
  let gapStart = null;
  cvSeries.history.forEach((pt) => {
    if (pt.quality === "MISSING" || pt.quality === "BAD") {
      if (!gapStart) gapStart = pt.t;
    } else {
      if (gapStart) {
        const gx = getX(gapStart);
        const gw = getX(pt.t) - gx;
        svg += `<rect x="${gx}" y="${padT}" width="${gw}" height="${plotH}" fill="url(#hatch-gap-${cvSeries.key})" />`;
        svg += `<text x="${gx + gw / 2}" y="${padT + plotH / 2}" fill="${TOKENS.ink}" font-size="13" font-weight="600" text-anchor="middle">нет сигнала ПАК</text>`;
        gapStart = null;
      }
    }
  });
  if (gapStart) {
    const gx = getX(gapStart);
    const gw = xNow - gx;
    svg += `<rect x="${gx}" y="${padT}" width="${gw}" height="${plotH}" fill="url(#hatch-gap-${cvSeries.key})" />`;
    svg += `<text x="${gx + gw / 2}" y="${padT + plotH / 2}" fill="${TOKENS.ink}" font-size="13" font-weight="600" text-anchor="middle">нет сигнала ПАК</text>`;
  }

  // Горизонтальная линия лимита
  const yLim = getY(cvSeries.limit.value);
  svg += `<line x1="${padL}" y1="${yLim}" x2="${padL + plotW}" y2="${yLim}" stroke="${TOKENS.limit}" stroke-width="2" />`;
  svg += `<text x="${padL + 6}" y="${cvSeries.limit.sense === 'max' ? yLim - 6 : yLim + 14}" fill="${TOKENS.limit}" font-size="12" font-weight="600">${cvSeries.limit.label}</text>`;

  // Полоса P10-P90 активной траектории
  if (active && active.cv && active.cv[cvSeries.key]) {
    const bands = active.cv[cvSeries.key];
    const hasSigmas = bands.some((b) => b.p10 != null && b.p90 != null);
    if (hasSigmas) {
      let polyPts = [];
      bands.forEach((b) => polyPts.push(`${getX(b.t)},${getY(b.p90)}`));
      for (let i = bands.length - 1; i >= 0; i--) {
        polyPts.push(`${getX(bands[i].t)},${getY(bands[i].p10)}`);
      }
      svg += `<polygon points="${polyPts.join(' ')}" fill="${activeColor}" fill-opacity="0.18" />`;
    } else {
      svg += `<text x="${padL + plotW - 10}" y="${padT + 16}" fill="${TOKENS.ink3}" font-size="11" text-anchor="end">σ неизвестна</text>`;
    }
  }

  // Траектория «без изменений» (hold)
  if (state.hold && state.hold.cv && state.hold.cv[cvSeries.key]) {
    const holdPts = state.hold.cv[cvSeries.key];
    const pathD = holdPts.map((b, i) => `${i === 0 ? 'M' : 'L'} ${getX(b.t)} ${getY(b.p50)}`).join(' ');
    svg += `<path d="${pathD}" fill="none" stroke="${TOKENS.hold}" stroke-width="2" stroke-dasharray="6 5" />`;
  }

  // Рекомендация тонким контуром (recThin)
  if (view.recThin && view.recThin.cv && view.recThin.cv[cvSeries.key]) {
    const recPts = view.recThin.cv[cvSeries.key];
    const pathD = recPts.map((b, i) => `${i === 0 ? 'M' : 'L'} ${getX(b.t)} ${getY(b.p50)}`).join(' ');
    svg += `<path d="${pathD}" fill="none" stroke="${TOKENS.rec}" stroke-width="1.5" />`;
  }

  // Активная траектория
  if (active && active.cv && active.cv[cvSeries.key]) {
    const actPts = active.cv[cvSeries.key];
    const pathD = actPts.map((b, i) => `${i === 0 ? 'M' : 'L'} ${getX(b.t)} ${getY(b.p50)}`).join(' ');
    svg += `<path d="${pathD}" fill="none" stroke="${activeColor}" stroke-width="3" />`;
  }

  // Факт (история)
  let factD = "";
  let lastFactPt = null;
  cvSeries.history.forEach((pt) => {
    if (pt.v != null && pt.quality !== "MISSING") {
      const px = getX(pt.t);
      const py = getY(pt.v);
      factD += factD === "" ? `M ${px} ${py}` : ` L ${px} ${py}`;
      lastFactPt = { x: px, y: py };
    }
  });
  if (factD) {
    svg += `<path d="${factD}" fill="none" stroke="${TOKENS.fact}" stroke-width="2.5" />`;
  }

  // Точка сейчас
  if (lastFactPt) {
    svg += `<circle cx="${lastFactPt.x}" cy="${lastFactPt.y}" r="5" fill="${TOKENS.ink}" />`;
  }

  // Вертикальная линия NOW
  svg += `<line x1="${xNow}" y1="${padT}" x2="${xNow}" y2="${padT + plotH}" stroke="${TOKENS.ink}" stroke-width="1.5" stroke-dasharray="4 3" />`;
  svg += `<text x="${xNow}" y="${padT - 4}" fill="${TOKENS.ink}" font-size="11" font-weight="700" text-anchor="middle">СЕЙЧАС</text>`;

  // Значения на +4ч справа
  if (active && active.cv && active.cv[cvSeries.key]) {
    const lastActive = active.cv[cvSeries.key].slice(-1)[0];
    const yActEnd = getY(lastActive.p50);
    svg += `<text x="${padL + plotW + 8}" y="${yActEnd + 4}" fill="${activeColor}" font-size="14" font-weight="700" class="mono">${lastActive.p50.toFixed(1)}</text>`;
  }

  // Синхронизированный курсор
  if (handle.hoveredX != null && handle.hoveredX >= padL && handle.hoveredX <= padL + plotW) {
    svg += `<line x1="${handle.hoveredX}" y1="${padT}" x2="${handle.hoveredX}" y2="${padT + plotH}" stroke="${TOKENS.ink}" stroke-width="1" stroke-dasharray="2 2" />`;
  }

  // Если отказ данных и нет прогноза
  if (state.recommendation && state.recommendation.status === "REFUSAL_DATA" && !active) {
    svg += `<text x="${xNow + (padL + plotW - xNow) / 2}" y="${padT + plotH / 2 - 10}" fill="${TOKENS.warn}" font-size="16" font-weight="700" text-anchor="middle">Прогноз не строится</text>`;
    svg += `<text x="${xNow + (padL + plotW - xNow) / 2}" y="${padT + plotH / 2 + 14}" fill="${TOKENS.ink3}" font-size="13" text-anchor="middle">нет достоверных данных по ${cvSeries.title.toLowerCase()}</text>`;
  }

  svg += `</svg>`;
  container.innerHTML = svg;

  // Слушатель событий мыши для синхронного курсора
  container.onmousemove = (e) => {
    const rect = container.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    handle.hoveredX = mouseX;
    const hoveredMs = startMs + ((mouseX - padL) / plotW) * totalMs;
    handle.hoveredTime = new Date(hoveredMs).toISOString();
    updateTooltip(handle, e.clientX, e.clientY);
    renderCharts(handle, handle.view);
  };
  container.onmouseleave = () => {
    handle.hoveredX = null;
    if (handle.tooltipEl) handle.tooltipEl.style.display = "none";
    renderCharts(handle, handle.view);
  };
}

function renderDpCompactSvg(container, dpSeries, state, view, startMs, endMs, nowMs, totalMs, activeColor, handle) {
  const w = container.clientWidth || 800;
  const h = container.clientHeight > 0 ? container.clientHeight : 56;
  const padL = 60;
  const padR = 60;
  const padT = 8;
  const padB = 8;
  const plotW = Math.max(10, w - padL - padR);
  const plotH = Math.max(10, h - padT - padB);

  const yMin = dpSeries.y_range ? dpSeries.y_range[0] : 100;
  const yMax = dpSeries.y_range ? dpSeries.y_range[1] : 500;

  const getX = (t) => padL + ((new Date(t).getTime() - startMs) / totalMs) * plotW;
  const getY = (v) => padT + plotH - ((v - yMin) / (yMax - yMin)) * plotH;
  const xNow = getX(state.clock.now);

  let svg = `<svg width="100%" height="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="display:block; overflow:hidden; max-width: 100%;">`;
  svg += `<rect x="${xNow}" y="${padT}" width="${padL + plotW - xNow}" height="${plotH}" fill="#FFFFFF" fill-opacity="0.55" />`;

  // Лимит T1
  const yLim = getY(dpSeries.limit.value);
  svg += `<line x1="${padL}" y1="${yLim}" x2="${padL + plotW}" y2="${yLim}" stroke="${TOKENS.limit}" stroke-width="1.5" stroke-dasharray="4 3" />`;

  // Факт
  let factD = "";
  dpSeries.history.forEach((pt) => {
    if (pt.v != null && pt.quality !== "MISSING") {
      const px = getX(pt.t);
      const py = getY(pt.v);
      factD += factD === "" ? `M ${px} ${py}` : ` L ${px} ${py}`;
    }
  });
  if (factD) svg += `<path d="${factD}" fill="none" stroke="${TOKENS.fact}" stroke-width="2" />`;

  // Активная
  const active = view.active;
  if (active && active.cv && active.cv.dp) {
    const actD = active.cv.dp.map((b, i) => `${i === 0 ? 'M' : 'L'} ${getX(b.t)} ${getY(b.p50)}`).join(' ');
    svg += `<path d="${actD}" fill="none" stroke="${activeColor}" stroke-width="2.5" />`;
  }

  svg += `<line x1="${xNow}" y1="${padT}" x2="${xNow}" y2="${padT + plotH}" stroke="${TOKENS.ink}" stroke-width="1" stroke-dasharray="3 3" />`;
  svg += `</svg>`;
  container.innerHTML = svg;
}

function renderMvSvg(container, mvSeries, state, view, startMs, endMs, nowMs, totalMs, activeColor, handle) {
  const w = container.clientWidth || 200;
  const h = container.clientHeight > 0 ? container.clientHeight : 84;
  const padL = 4;
  const padR = 4;
  const padT = 4;
  const padB = 4;
  const plotW = Math.max(10, w - padL - padR);
  const plotH = Math.max(10, h - padT - padB);

  // Определение диапазона Y вокруг значений
  const vals = [];
  mvSeries.sp_history.forEach((p) => p.v != null && vals.push(p.v));
  mvSeries.pv_history.forEach((p) => p.v != null && vals.push(p.v));
  let minV = Math.min(...vals, mvSeries.corridor.lo || 0);
  let maxV = Math.max(...vals, mvSeries.corridor.hi || 100);
  const span = Math.max(0.1, maxV - minV);
  const yMin = minV - 0.1 * span;
  const yMax = maxV + 0.1 * span;

  const getX = (t) => padL + ((new Date(t).getTime() - startMs) / totalMs) * plotW;
  const getY = (v) => padT + plotH - ((v - yMin) / (yMax - yMin)) * plotH;
  const xNow = getX(state.clock.now);

  let svg = `<svg width="100%" height="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="display:block; overflow:hidden; max-width: 100%;">`;
  svg += `<rect x="${xNow}" y="${padT}" width="${padL + plotW - xNow}" height="${plotH}" fill="#FFFFFF" fill-opacity="0.55" />`;

  // Коридор допустимого шага на первом такте
  if (mvSeries.corridor.max_step_per_tick && !mvSeries.frozen) {
    const spNow = mvSeries.sp_history.slice(-1)[0]?.v || 0;
    const cLo = Math.max(yMin, spNow - mvSeries.corridor.max_step_per_tick);
    const cHi = Math.min(yMax, spNow + mvSeries.corridor.max_step_per_tick);
    const cy = getY(cHi);
    const ch = getY(cLo) - cy;
    const cW = (10 * 60 * 1000 / totalMs) * plotW;
    svg += `<rect x="${xNow}" y="${cy}" width="${cW}" height="${Math.max(2, ch)}" fill="${TOKENS.rec}" fill-opacity="0.16" />`;
  }

  // Линии T0/T1/warn
  mvSeries.lines.forEach((l) => {
    const ly = getY(l.value);
    const color = l.kind === "warn" ? TOKENS.warn : TOKENS.limit;
    svg += `<line x1="${padL}" y1="${ly}" x2="${padL + plotW}" y2="${ly}" stroke="${color}" stroke-width="1.2" stroke-dasharray="3 2" />`;
  });

  // PV история
  let pvD = "";
  mvSeries.pv_history.forEach((pt) => {
    if (pt.v != null) {
      const px = getX(pt.t);
      const py = getY(pt.v);
      pvD += pvD === "" ? `M ${px} ${py}` : ` L ${px} ${py}`;
    }
  });
  if (pvD) svg += `<path d="${pvD}" fill="none" stroke="${TOKENS.hold}" stroke-width="1.2" />`;

  // SP история (ступенчатая)
  let spD = "";
  for (let i = 0; i < mvSeries.sp_history.length; i++) {
    const pt = mvSeries.sp_history[i];
    if (pt.v != null) {
      const px = getX(pt.t);
      const py = getY(pt.v);
      if (i === 0) {
        spD = `M ${px} ${py}`;
      } else {
        const prevPy = getY(mvSeries.sp_history[i - 1].v);
        spD += ` L ${px} ${prevPy} L ${px} ${py}`;
      }
    }
  }
  if (spD) svg += `<path d="${spD}" fill="none" stroke="${TOKENS.ink}" stroke-width="2" />`;

  // План активной траектории (ступенька)
  const active = view.active;
  if (active && active.mv_plan && active.mv_plan[mvSeries.sp]) {
    const planPts = active.mv_plan[mvSeries.sp];
    let planD = "";
    for (let i = 0; i < planPts.length; i++) {
      const pt = planPts[i];
      const px = getX(pt.t);
      const py = getY(pt.v);
      if (i === 0) {
        planD = `M ${px} ${py}`;
      } else {
        const prevPy = getY(planPts[i - 1].v);
        planD += ` L ${px} ${prevPy} L ${px} ${py}`;
      }
    }
    const color = mvSeries.frozen ? TOKENS.hold : activeColor;
    const dash = mvSeries.frozen ? "stroke-dasharray='4 3'" : "";
    svg += `<path d="${planD}" fill="none" stroke="${color}" stroke-width="2.5" ${dash} />`;
  }

  svg += `<line x1="${xNow}" y1="${padT}" x2="${xNow}" y2="${padT + plotH}" stroke="${TOKENS.ink}" stroke-width="1" stroke-dasharray="3 3" />`;
  svg += `</svg>`;
  container.innerHTML = svg;
}

function findClosestPoint(points, targetMs) {
  if (!points || points.length === 0) return null;
  let best = points[0];
  let bestDist = Math.abs(new Date(best.t).getTime() - targetMs);
  for (let i = 1; i < points.length; i++) {
    const d = Math.abs(new Date(points[i].t).getTime() - targetMs);
    if (d < bestDist) {
      best = points[i];
      bestDist = d;
    }
  }
  return best;
}

function updateTooltip(handle, clientX, clientY) {
  const tip = handle.tooltipEl;
  if (!tip || !handle.hoveredTime || !handle.view || !handle.view.state) return;

  const state = handle.view.state;
  const active = handle.view.active;
  const hoveredMs = new Date(handle.hoveredTime).getTime();
  const nowMs = new Date(state.clock.now).getTime();
  const dt = new Date(hoveredMs);
  const timeStr = `${String(dt.getUTCHours()).padStart(2, "0")}:${String(dt.getUTCMinutes()).padStart(2, "0")}`;
  const isFuture = hoveredMs >= nowMs;

  let html = `<div style="font-weight: 700; margin-bottom: 6px; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 3px;">Время: ${timeStr}</div>`;

  // CV секция
  html += `<div style="display: flex; flex-direction: column; gap: 4px;">`;
  state.series.cv.forEach((cv) => {
    const factPt = findClosestPoint(cv.history, hoveredMs);
    const holdPt = findClosestPoint(state.hold?.cv?.[cv.key], hoveredMs);
    const actPt = findClosestPoint(active?.cv?.[cv.key], hoveredMs);

    let valStr = "";
    if (!isFuture && factPt && factPt.v != null) {
      valStr = `Факт: <b>${factPt.v.toFixed(1)}</b> ${cv.unit}`;
    } else {
      const parts = [];
      if (holdPt && holdPt.p50 != null) parts.push(`Без изм.: ${holdPt.p50.toFixed(1)}`);
      if (actPt && actPt.p50 != null) {
        let actStr = `Активная: <b>${actPt.p50.toFixed(1)}</b>`;
        if (actPt.p10 != null && actPt.p90 != null) {
          actStr += ` [${actPt.p10.toFixed(1)}–${actPt.p90.toFixed(1)}]`;
        }
        parts.push(actStr);
      }
      valStr = parts.join(" · ") + ` ${cv.unit}`;
    }
    html += `<div style="font-size: 12px;"><span style="color: #9AA0A6;">${cv.title}:</span> ${valStr}</div>`;
  });
  html += `</div>`;

  tip.innerHTML = html;
  tip.style.left = `${Math.min(window.innerWidth - 320, clientX + 16)}px`;
  tip.style.top = `${Math.min(window.innerHeight - 200, clientY + 16)}px`;
  tip.style.display = "block";
}

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

  let chart = window.echarts.getInstanceByDom(container);
  if (chart) {
    // If container innerHTML was overwritten with text, canvas is missing from DOM
    const hasCanvas = container.querySelector("canvas");
    if (!hasCanvas) {
      window.echarts.dispose(container);
      chart = null;
    }
  }

  if (!chart) {
    container.innerHTML = "";
    chart = window.echarts.init(container);
  } else {
    chart.resize();
  }

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
      markLine: markLineData.length > 0 ? { silent: true, symbol: "none", data: markLineData } : undefined,
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
    title: {
      show: !!axisHint,
      text: axisHint || "",
      left: "center",
      top: 4,
      textStyle: { color: TOKENS.warn, fontSize: 12, fontWeight: 600, fontFamily: "'IBM Plex Sans', sans-serif" },
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
      textStyle: { color: "#16181A", fontSize: 12, fontFamily: "'IBM Plex Sans', sans-serif" },
    },
    tooltip: {
      trigger: "item",
      backgroundColor: "rgba(22, 24, 26, 0.95)",
      borderColor: "#3F4448",
      textStyle: { color: "#FFFFFF", fontFamily: "'IBM Plex Sans', monospace", fontSize: 13 },
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
      nameTextStyle: { color: "#4A4E53", fontSize: 13, fontWeight: 600, fontFamily: "'IBM Plex Sans', sans-serif" },
      type: "value",
      splitLine: { lineStyle: { color: "#C9CAC6", type: "dashed" } },
      axisLine: { lineStyle: { color: "#B9BBB7" } },
      axisLabel: { color: "#4A4E53", fontFamily: "'IBM Plex Mono', monospace" },
      scale: true,
    },
    yAxis: {
      name: `${specY.label} (${specY.unit})`,
      nameLocation: "middle",
      nameGap: 55,
      nameTextStyle: { color: "#4A4E53", fontSize: 13, fontWeight: 600, fontFamily: "'IBM Plex Sans', sans-serif" },
      type: "value",
      splitLine: { lineStyle: { color: "#C9CAC6", type: "dashed" } },
      axisLine: { lineStyle: { color: "#B9BBB7" } },
      axisLabel: { color: "#4A4E53", fontFamily: "'IBM Plex Mono', monospace" },
      // Фикс §3: один выброс (напр. net_margin на 1-2 порядка больше кластера) не должен
      // растягивать шкалу — обрезаем видимый диапазон до [Q1-1.5*IQR, Q3+1.5*IQR], точка
      // остаётся в данных (и в тултипе целиком), просто уезжает за пределы видимой области.
      min: iqrClamp ? iqrClamp.min : undefined,
      max: iqrClamp ? iqrClamp.max : undefined,
      scale: true,
    },
    series,
  };

  chart.setOption(option, true);

  chart.off("click");
  chart.on("click", (params) => {
    if (params.data && params.data.point && onPointClick) {
      onPointClick(params.data.point);
    }
  });

  return chart;
}
