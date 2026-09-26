/**
 * Главный модуль пульта старшего оператора (v2 UX Redesign).
 * Управляет состоянием интерфейса, верхними вкладками, песочницей, XAI, Парето и связью с бэкендом.
 */

import * as api from "./api.js";
import { SESSION_ID } from "./api.js";
import { mountCharts, renderCharts, resizeCharts, renderParetoChart } from "./charts.js";

// Определения виджетов для настройки отображения (D3)
const WIDGET_DEFS = [
  { id: "cv.sulfur", group: "CV", label: "Сера (HT_Q21)" },
  { id: "cv.flash", group: "CV", label: "Вспышка (HT_FLASH)" },
  { id: "cv.dp", group: "CV", label: "Перепад Р-202 (HT_P8)" },
  { id: "mv.0", group: "MV", label: "Сырьё ГО (HT_F9)" },
  { id: "mv.1", group: "MV", label: "Вход Р-202 (HT_T6)" },
  { id: "mv.2", group: "MV", label: "Давление (HT_P13)" },
  { id: "mv.3", group: "MV", label: "Кратность ВСГ (HT_GOR)" },
  { id: "mv.4", group: "MV", label: "Перевал П-3 (AVT_T55)" },
  { id: "sensors.product_quality", group: "SENSORS", label: "Качество продукта" },
  { id: "sensors.feed_quality", group: "SENSORS", label: "Качество сырья" },
];

// Каталог сценариев работы (зеркало SCENARIO_TITLES из src/console/demo.py)
const SCENARIO_TITLES = {
  S1: "S1 · Нормальный режим",
  S2: "S2 · Риск качества сырья",
  S3d: "S3d · Отказ данных серы",
  S4: "S4 · Конфликт ограничений",
  S5: "S5 · Выход за огибающую",
};

// Краткие названия для компактного отображения в выпадающем списке шапки
const SCENARIO_SHORT_NAMES = {
  S1: "Норма",
  S2: "Утяжеление",
  S3d: "Отказ серы",
  S4: "Конфликт",
  S5: "За пределом",
};

function loadWidgetVisibility() {
  try {
    const saved = localStorage.getItem("console_widgets_v1");
    if (saved) return JSON.parse(saved);
  } catch (e) {}
  const def = {};
  WIDGET_DEFS.forEach((w) => (def[w.id] = true));
  return def;
}

function saveWidgetVisibility(vis) {
  try {
    localStorage.setItem("console_widgets_v1", JSON.stringify(vis));
  } catch (e) {}
}

// История вопросов оператора супервизору («Спросить систему», вкладка «Агенты и XAI»).
// Хранится в localStorage браузера отдельно по каждой session_id — переживает обновление
// страницы и переключение вкладок, но не переносится между браузерами/устройствами (для этого
// потребовалось бы серверное хранилище, которого сейчас нет ни у одного эндпоинта /api/console/*).
const ASK_HISTORY_KEY = `console_ask_history_v1::${SESSION_ID}`;
const ASK_HISTORY_LIMIT = 50;

function loadAskHistory() {
  try {
    const saved = localStorage.getItem(ASK_HISTORY_KEY);
    if (saved) return JSON.parse(saved);
  } catch (e) {}
  return [];
}

function saveAskHistory(history) {
  try {
    localStorage.setItem(ASK_HISTORY_KEY, JSON.stringify(history.slice(-ASK_HISTORY_LIMIT)));
  } catch (e) {}
}

// История отчётов LLM (вкладка «Агенты и XAI») — каждый успешно сформированный отчёт (по любому
// режиму рекомендации) откладывается сюда, чтобы оператор мог посмотреть, как менялось
// объяснение системы по тактам, а не только видеть последний. Тот же паттерн хранения, что и у
// истории вопросов выше.
const REPORT_HISTORY_KEY = `console_xai_report_history_v1::${SESSION_ID}`;
const REPORT_HISTORY_LIMIT = 30;

function loadReportHistory() {
  try {
    const saved = localStorage.getItem(REPORT_HISTORY_KEY);
    if (saved) return JSON.parse(saved);
  } catch (e) {}
  return [];
}

function saveReportHistory(history) {
  try {
    localStorage.setItem(REPORT_HISTORY_KEY, JSON.stringify(history.slice(-REPORT_HISTORY_LIMIT)));
  } catch (e) {}
}

// Сборка текущего вектора уставок из серии MV (фикс бага §2.4)
function currentU(state) {
  const u = {};
  if (state?.series?.mv) {
    for (const mv of state.series.mv) {
      const last = mv.sp_history.slice(-1)[0];
      if (last && last.v != null) u[mv.sp] = last.v;
    }
  }
  return u;
}

// Глобальное состояние клиента
const store = {
  state: null,
  activeKind: "recommendation", // "recommendation" | "alternative" | "operator" | "auto_plan"
  choice: "balanced",           // "max_margin" | "balanced" | "max_safety"
  edits: {},                    // { "HT_TIN_SP": 364.5 }
  preview: null,
  historyHours: 8,
  pending: false,
  confirmTimeout: null,
  confirmingAction: null,
  showAgents: false,
  showSensors: false,
  chartsHandle: null,

  // Redesign v2 fields
  activeTab: "overview",
  widgetVisibility: loadWidgetVisibility(),
  manual: {
    edits: {},
    preview: null,
    loading: false,
    chartsHandle: null,
    confirmingAction: null,
    confirmTimeout: null,
    loadedPointId: null,
  },
  pareto: {
    data: null,
    axisX: null,
    axisY: null,
    loading: false,
    loadedForTick: null,
  },
  xai: {
    reportsByChoice: {},            // choice -> последний ответ /api/console/xai/report
    reportLoadingChoice: null,      // choice, для которого сейчас идёт запрос, или null
    reportLoadedTickByChoice: {},   // choice -> tick, для которого он уже загружен
    reportHistory: loadReportHistory(),
    askHistory: loadAskHistory(),
    asking: false,
  },
  blending: {
    data: null,          // BlendingStateDTO | null
    preview: null,       // BlendingPreviewDTO | null
    edits: {},            // { tank_id: newStockT }
    loading: false,
    loadedForTick: null,
  },
  sim: {
    secondsPerTick: null,
  },
  constants: {
    data: null,
    activeCategory: "all",
    searchQuery: "",
    loading: false,
  },
  // Ф3 (03_STREAMLIT_MIGRATION_PLAN.md §6): цены и тарифы во вкладке «Константы».
  economics: {
    data: null,     // EconomicsOverrideDTO с бэкенда (GET/POST /api/console/economics)
    edits: {},       // { price_godt: 71000, ... } — черновые правки до нажатия «Применить»
    loading: false,
    applying: false,
  },
  // F4 (03_STREAMLIT_MIGRATION_PLAN.md §7): панель LLM-супервизора — находки и HITL-запросы.
  // Не привязано к такту симуляции (главный API — прямой /api/v1/supervisor/*, не /api/console/*),
  // поэтому обновляется только по активации вкладки/кнопке «Обновить»/после approve-reject.
  supervisor: {
    findings: [],
    changeRequests: [],
    loading: false,
    loaded: false,
    expandedFindings: {},   // { finding_id: true }
    deciding: {},            // { request_id: true } — блокировка кнопок на время запроса
  },
  isCommitting: false,
  // Блокировка кнопки «Пересчитать прогноз» на время запроса (см. recalculateForecastForCurrentMode).
  // Как и isCommitting выше, это поле нарочно вплетено в HTML карточки (renderAdvisoryCard), а не
  // выставляется только императивно через btn.disabled — иначе фоновый опрос render() раз в 2с
  // (или обновление карточки на новом такте метронома) пересоздаёт кнопку из шаблона и стирает
  // временную блокировку ДО того, как запрос успел завершиться, снова делая кнопку кликабельной
  // прямо во время расчёта. Именно это и позволяло нетерпеливому повторному клику наращивать
  // очередь параллельных /api/console/preview поверх уже зависшего запроса (см. аудит зависания
  // «Пересчитать прогноз» от 2026-09-22).
  isRecalculating: false,
  // Непропускаемое уведомление «рекомендация обновилась, пока вы выбирали» (см. executeCommit).
  // Не снэпшот значений — store.edits/store.choice и так переживают refresh() при устаревании
  // цикла (см. refresh()), поэтому кнопка повтора там просто вызывает executeCommit() заново.
  staleNotice: false,
};

let debounceTimer = null;
let manualDebounceTimer = null;
let pollTimer = null;
let autoCountdownTimer = null;
let autoSecondsRemaining = 0;
let autoTotalSeconds = 150;

// Опрос состояния идёт каждые 2с (см. pollTimer ниже) и раньше безусловно пересобирал DOM
// интерактивных блоков на каждый тик, даже когда данные не менялись. Из-за этого клик по кнопке
// (mousedown на старом узле → узел подменяется до mouseup) иногда вообще не порождал событие
// click — ни запроса к серверу, ни тоста, ни ошибки в консоли: «применяю рекомендацию, иногда
// ничего не происходит». setHtmlIfChanged пересобирает innerHTML только когда итоговая разметка
// реально отличается от предыдущей отрисовки того же контейнера.
const _lastRenderedHtml = new WeakMap();

function setHtmlIfChanged(container, html) {
  if (!container) return false;
  if (_lastRenderedHtml.get(container) === html) return false;
  _lastRenderedHtml.set(container, html);
  container.innerHTML = html;
  return true;
}

function updateAutoTimerDisplay() {
  const timerEl = document.getElementById("auto-timer");
  const progressFill = document.querySelector(".auto-progress-fill");
  const m = Math.floor(autoSecondsRemaining / 60);
  const s = Math.floor(autoSecondsRemaining % 60);
  if (timerEl) {
    timerEl.textContent = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  if (progressFill) {
    const total = autoTotalSeconds > 0 ? autoTotalSeconds : 150;
    const pct = Math.min(100, Math.max(0, ((total - autoSecondsRemaining) / total) * 100));
    progressFill.style.width = `${pct}%`;
  }
}

// Точечно обновляет счётчик «до следующего такта» в карточке рекомендации (см. renderAdvisoryCard)
// напрямую через textContent, а не через переотрисовку всей карточки — иначе (см. setHtmlIfChanged)
// карточка пересобиралась бы на каждый опрос состояния (2с) и мог снова съедаться клик по «Применить».
function updateRecValidityCountdown(state) {
  const el = document.getElementById("rec-validity-countdown");
  if (!el) return;
  const nextTickS = state?.clock?.next_tick_in_s;
  if (nextTickS == null) {
    el.textContent = "";
    el.classList.remove("card-validity-urgent");
    return;
  }
  const secsLeft = Math.ceil(nextTickS);
  const urgent = secsLeft <= 5;
  el.textContent = urgent ? `· обновится через ${secsLeft} с` : `· до следующего такта ~${secsLeft} с`;
  el.classList.toggle("card-validity-urgent", urgent);
}

function syncAutoCountdown(state) {
  if (!state || state.mode?.current !== "AUTO") {
    if (autoCountdownTimer) {
      clearInterval(autoCountdownTimer);
      autoCountdownTimer = null;
    }
    return;
  }

  const inS = state.auto?.next_step?.in_s ?? state.clock?.next_tick_in_s ?? 150;
  autoSecondsRemaining = Math.max(0, inS);
  autoTotalSeconds = state.clock?.seconds_per_tick > 0 ? state.clock.seconds_per_tick : 150;
  updateAutoTimerDisplay();

  if (!autoCountdownTimer) {
    autoCountdownTimer = setInterval(() => {
      if (autoSecondsRemaining > 0) {
        autoSecondsRemaining -= 1;
        updateAutoTimerDisplay();
      }
    }, 1000);
  }
}

// Связывание степпера (D8)
function bindStepper(el, { getValue, setValue, onChange, step = 0.5, decimals = 1, min, max }) {
  const input = el.querySelector(".stepper-value");
  const minusBtn = el.querySelector('.stepper-btn[data-dir="-1"]');
  const plusBtn = el.querySelector('.stepper-btn[data-dir="+1"]');

  const update = (delta) => {
    let cur = parseFloat(input.value.replace(",", ".")) || 0;
    let next = Math.round((cur + delta) * 100) / 100;
    if (min != null && next < min) next = min;
    if (max != null && next > max) next = max;
    next = Number(next.toFixed(decimals));
    input.value = next.toFixed(decimals);
    setValue(next);
    onChange();
  };

  minusBtn?.addEventListener("click", () => update(-step));
  plusBtn?.addEventListener("click", () => update(step));

  input?.addEventListener("input", () => {
    const val = parseFloat(input.value.replace(",", "."));
    if (!isNaN(val)) {
      setValue(val);
    } else {
      setValue(null);
    }
    onChange();
  });

  input?.addEventListener("keydown", (e) => {
    if (e.key === "ArrowUp") {
      e.preventDefault();
      update(step);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      update(-step);
    }
  });
}

// Переключение вкладок (D1)
function switchTab(name) {
  if (!name) name = "overview";
  store.activeTab = name;

  if (store.confirmTimeout) {
    clearTimeout(store.confirmTimeout);
    store.confirmTimeout = null;
    store.confirmingAction = null;
  }
  if (store.manual.confirmTimeout) {
    clearTimeout(store.manual.confirmTimeout);
    store.manual.confirmTimeout = null;
    store.manual.confirmingAction = null;
  }

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    const isAct = btn.dataset.tab === name;
    btn.classList.toggle("active", isAct);
    btn.setAttribute("aria-selected", isAct ? "true" : "false");
  });

  document.querySelectorAll(".tab-panel").forEach((panel) => {
    if (panel.dataset.tabPanel === name) {
      panel.removeAttribute("hidden");
    } else {
      panel.setAttribute("hidden", "hidden");
    }
  });

  try {
    if (window.location.hash.slice(1) !== name) {
      window.location.hash = name;
    }
  } catch (e) {}

  if (name === "overview") {
    if (store.chartsHandle) resizeCharts(store.chartsHandle);
  } else if (name === "manual") {
    initManualTab();
  } else if (name === "xai") {
    initXaiTab();
  } else if (name === "pareto") {
    initParetoTab();
  } else if (name === "shift") {
    initShiftTab();
  } else if (name === "blending") {
    initBlendingTab();
  } else if (name === "constants") {
    initConstantsTab();
  } else if (name === "supervisor") {
    initSupervisorTab();
  }
}

// Инициализация при загрузке страницы
window.addEventListener("DOMContentLoaded", async () => {
  window.addEventListener("resize", () => {
    if (store.chartsHandle && store.activeTab === "overview") resizeCharts(store.chartsHandle);
    if (store.manual.chartsHandle && store.activeTab === "manual") resizeCharts(store.manual.chartsHandle);
    if (store.activeTab === "pareto") renderPareto();
  });

  // Инициализация графиков вкладки "Обзор"
  const chartRoots = {
    sulfur: document.getElementById("chart-sulfur"),
    flash: document.getElementById("chart-flash"),
    dp: document.getElementById("chart-dp"),
    mv: [
      document.getElementById("chart-mv-0"),
      document.getElementById("chart-mv-1"),
      document.getElementById("chart-mv-2"),
      document.getElementById("chart-mv-3"),
      document.getElementById("chart-mv-4"),
    ],
  };
  store.chartsHandle = mountCharts(chartRoots);

  // Слушатели верхних вкладок
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      switchTab(btn.dataset.tab);
    });
  });

  // Слушатели переключателя горизонта истории
  document.querySelectorAll(".history-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".history-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      store.historyHours = Number(btn.dataset.hours);
      document.getElementById("legend-hist-hours").textContent = store.historyHours;
      render();
      if (store.activeTab === "manual") renderManualPreviewResults();
    });
  });

  // Модальное окно
  document.getElementById("btn-modal-close")?.addEventListener("click", () => {
    document.getElementById("modal-overlay").style.display = "none";
  });

  // Переход на вкладку сдачи смены
  document.getElementById("btn-shift-report")?.addEventListener("click", () => {
    switchTab("shift");
  });
  document.getElementById("btn-refresh-shift")?.addEventListener("click", () => {
    initShiftTab();
  });

  // Обновление вкладки «Супервизор» (F4)
  document.getElementById("btn-refresh-supervisor")?.addEventListener("click", () => {
    initSupervisorTab();
  });

  // Вопрос супервизору — ответ уходит не во временное модальное окно, а в историю переписки
  // (item 1 запроса оператора), сохранённую в localStorage и видимую под полем ввода.
  document.getElementById("btn-ask")?.addEventListener("click", async () => {
    await submitAskQuestion();
  });
  document.getElementById("ask-input")?.addEventListener("keydown", async (e) => {
    if (e.key === "Enter") await submitAskQuestion();
  });
  document.getElementById("btn-clear-ask-history")?.addEventListener("click", () => {
    store.xai.askHistory = [];
    saveAskHistory(store.xai.askHistory);
    renderAskHistory();
  });
  renderAskHistory();

  // Квитирование баннера
  document.getElementById("btn-banner-ack")?.addEventListener("click", async () => {
    if (store.state?.banner?.id) {
      await api.ack(store.state.banner.id);
      if (store.state.banner) store.state.banner = null;
      document.getElementById("alert-banner").style.display = "none";
    }
  });

  // Кнопка режима в статус-баре
  document.getElementById("btn-mode-toggle")?.addEventListener("click", async () => {
    if (!store.state) return;
    const targetMode = store.state.mode.current === "AUTO" ? "ADVISORY" : "AUTO";
    try {
      await api.setMode(targetMode);
      await refresh();
    } catch (e) {
      alert(e.message || "Ошибка переключения режима");
    }
  });

  // Инициализация виджетов и панели симулятора
  initWidgetConfig();
  initSimulatorPanel();
  initSensorsSection();
  initConfidenceBadge();

  // Инициализация слушателей ручного управления
  initManualEvents();

  // Инициализация слушателей песочницы блендинга (F2)
  initBlendingEvents();

  // Инициализация слушателей XAI и Парето
  document.getElementById("btn-refresh-xai-report")?.addEventListener("click", () => {
    const choice = store.choice || "balanced";
    delete store.xai.reportLoadedTickByChoice[choice];
    loadXaiReport(store.state?.clock?.tick, choice);
  });
  document.getElementById("btn-refresh-pareto")?.addEventListener("click", () => {
    store.pareto.loadedForTick = null;
    initParetoTab();
  });

  const selX = document.getElementById("pareto-axis-x");
  const selY = document.getElementById("pareto-axis-y");
  selX?.addEventListener("change", () => {
    store.pareto.axisX = selX.value;
    renderPareto();
  });
  selY?.addEventListener("change", () => {
    store.pareto.axisY = selY.value;
    renderPareto();
  });

  initScenarioSelect();

  // Демо-панель инъекции неисправностей ?demo=1
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get("demo") === "1") {
    initDemoPanel();
  }

  // Первичная загрузка и старт поллинга
  await refresh();
  pollTimer = setInterval(refresh, 2000);

  // Восстановление вкладки из hash
  const initialTab = window.location.hash ? window.location.hash.slice(1) : "overview";
  if (["overview", "manual", "xai", "pareto", "shift", "blending"].includes(initialTab)) {
    switchTab(initialTab);
  }
});

// Настройка панели виджетов (D3)
function initWidgetConfig() {
  const btn = document.getElementById("btn-widget-config");
  const popover = document.getElementById("widget-popover");
  if (!btn || !popover) return;

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    popover.style.display = popover.style.display === "none" ? "flex" : "none";
  });

  document.addEventListener("click", (e) => {
    if (!popover.contains(e.target) && e.target !== btn) {
      popover.style.display = "none";
    }
  });

  popover.querySelectorAll('input[type="checkbox"]').forEach((chk) => {
    const wId = chk.dataset.widget;
    chk.checked = store.widgetVisibility[wId] !== false;

    chk.addEventListener("change", () => {
      const def = WIDGET_DEFS.find((w) => w.id === wId);
      const group = def?.group;
      const groupWidgets = WIDGET_DEFS.filter((w) => w.group === group);
      const otherChecked = groupWidgets.some((w) => w.id !== wId && store.widgetVisibility[w.id] !== false);

      if (!chk.checked && !otherChecked) {
        chk.checked = true;
        alert(`Нельзя скрыть все графики из группы ${group}`);
        return;
      }

      store.widgetVisibility[wId] = chk.checked;
      saveWidgetVisibility(store.widgetVisibility);
      applyWidgetVisibility();
    });
  });

  applyWidgetVisibility();
}

function applyWidgetVisibility() {
  const vis = store.widgetVisibility;

  const cvSulfur = document.getElementById("cv-panel-sulfur");
  if (cvSulfur) cvSulfur.style.display = vis["cv.sulfur"] !== false ? "" : "none";

  const cvFlash = document.getElementById("cv-panel-flash");
  if (cvFlash) cvFlash.style.display = vis["cv.flash"] !== false ? "" : "none";

  const cvDp = document.getElementById("cv-panel-dp");
  if (cvDp) cvDp.style.display = vis["cv.dp"] !== false ? "" : "none";

  for (let i = 0; i < 5; i++) {
    const mvCard = document.getElementById(`mv-card-${i}`);
    if (mvCard) mvCard.style.display = vis[`mv.${i}`] !== false ? "" : "none";
  }

  const sensorsProductGroup = document.getElementById("sensors-group-product");
  if (sensorsProductGroup) {
    sensorsProductGroup.closest(".sensors-group").style.display = vis["sensors.product_quality"] !== false ? "" : "none";
  }
  const sensorsFeedGroup = document.getElementById("sensors-group-feed");
  if (sensorsFeedGroup) {
    sensorsFeedGroup.closest(".sensors-group").style.display = vis["sensors.feed_quality"] !== false ? "" : "none";
  }

  if (store.chartsHandle && store.activeTab === "overview") resizeCharts(store.chartsHandle);
}

// Раскрывающаяся секция «Качество продукта и сырья» (F1, свёрнута по умолчанию)
function initSensorsSection() {
  const btn = document.getElementById("btn-toggle-sensors");
  const list = document.getElementById("sensors-list");
  const icon = document.getElementById("sensors-toggle-icon");
  if (!btn || !list) return;

  btn.addEventListener("click", () => {
    store.showSensors = !store.showSensors;
    list.style.display = store.showSensors ? "flex" : "none";
    btn.setAttribute("aria-expanded", String(store.showSensors));
    if (icon) icon.style.transform = store.showSensors ? "rotate(90deg)" : "rotate(0deg)";
    if (store.showSensors && store.chartsHandle && store.activeTab === "overview") resizeCharts(store.chartsHandle);
  });
}

// Бейдж индекса уверенности данных (F1): раскрытие причин по клику/наведению
function initConfidenceBadge() {
  const wrap = document.getElementById("confidence-badge-wrap");
  const badge = document.getElementById("confidence-badge");
  if (!wrap || !badge) return;

  badge.addEventListener("click", (e) => {
    e.stopPropagation();
    const isOpen = wrap.classList.toggle("open");
    badge.setAttribute("aria-expanded", String(isOpen));
  });

  document.addEventListener("click", (e) => {
    if (!wrap.contains(e.target)) {
      wrap.classList.remove("open");
      badge.setAttribute("aria-expanded", "false");
    }
  });
}

// Инициализация панели «Симулятор» (D6)
function initSimulatorPanel() {
  const speedBtns = document.querySelectorAll(".sim-speed-btn");
  speedBtns.forEach((btn) => {
    btn.addEventListener("click", async () => {
      const spd = Number(btn.dataset.spd);
      await api.demo.speed(spd);
      await refresh();
    });
  });

  document.getElementById("btn-sim-tick")?.addEventListener("click", async () => {
    await api.demo.tick(1);
    await refresh();
  });
}

function updateSimulatorDisplay(state) {
  if (!state?.clock) return;
  const spd = state.clock.seconds_per_tick;
  document.querySelectorAll(".sim-speed-btn").forEach((btn) => {
    const btnSpd = Number(btn.dataset.spd);
    btn.classList.toggle("active", btnSpd === spd);
  });
}

// Загрузка состояния
async function refresh() {
  try {
    const newState = await api.getState();
    const cycleChanged = store.state && store.state.recommendation?.cycle_id !== newState.recommendation?.cycle_id;

    if (cycleChanged && Object.keys(store.edits).length > 0) {
      console.warn("Система обновила рекомендацию, пока оператор правил уставки");
    } else if (cycleChanged || !store.state) {
      store.edits = {};
      store.preview = null;
    }

    store.state = newState;
    render();

    // LLM-объяснение решения (item 2/3 запроса оператора): грузится независимо от активной
    // вкладки, т.к. показывается и в карточке рекомендации «Обзора», и на вкладке «Агенты и
    // XAI». Бэкенд кэширует по cycle_id, так что при отсутствии нового такта повторный опрос
    // ничего не пересчитывает. Грузим отчёт для ТЕКУЩЕГО выбранного оператором режима
    // (store.choice), а не всегда "balanced" — иначе после переключения на «Макс. маржа» и
    // прихода нового такта карточка опять показала бы объяснение сбалансированного режима.
    const currentTick = newState.clock?.tick;
    const currentChoice = store.choice || "balanced";
    if (store.xai.reportLoadedTickByChoice[currentChoice] !== currentTick || !store.xai.reportsByChoice[currentChoice]) {
      loadXaiReport(currentTick, currentChoice);
    }

    // Обновление активных специализированных вкладок
    if (store.activeTab === "manual") {
      renderManualRows();
      if (!store.manual.preview) triggerManualPreviewDebounced(true);
    } else if (store.activeTab === "xai") {
      initXaiTab();
    } else if (store.activeTab === "pareto") {
      initParetoTab();
    } else if (store.activeTab === "blending") {
      // Только GET (никогда не авто-preview) — см. initBlendingTab().
      initBlendingTab();
    }
  } catch (err) {
    console.error("Ошибка опроса состояния:", err);
  }
}

// Основная функция рендеринга интерфейса
function render() {
  const state = store.state;
  if (!state) return;

  const rootEl = document.getElementById("root-console");

  // 1. Классы режима на корневом контейнере
  rootEl.classList.remove("mode-advisory", "mode-auto", "mode-refusal");
  const isRefusal = state.recommendation?.status?.startsWith("REFUSAL_");
  if (isRefusal) {
    rootEl.classList.add("mode-refusal");
  } else if (state.mode.current === "AUTO") {
    rootEl.classList.add("mode-auto");
  } else {
    rootEl.classList.add("mode-advisory");
  }

  // 2. Статус-бар
  const modePill = document.getElementById("mode-pill");
  const modePillText = document.getElementById("mode-pill-text");
  const btnModeToggle = document.getElementById("btn-mode-toggle");

  modePill.classList.remove("auto", "refusal");
  if (isRefusal) {
    modePill.classList.add("refusal");
    modePillText.textContent = "ОТКАЗ";
  } else if (state.mode.current === "AUTO") {
    modePill.classList.add("auto");
    modePillText.textContent = "АВТОМАТ";
  } else {
    modePillText.textContent = "СОВЕТ";
  }

  const scenarioSelect = document.getElementById("scenario-select");
  if (scenarioSelect && !scenarioSelect.disabled && document.activeElement !== scenarioSelect && state.scenario?.id) {
    scenarioSelect.value = state.scenario.id;
    scenarioSelect.title = SCENARIO_TITLES[state.scenario.id] || "";
  }

  if (state.mode.current === "AUTO") {
    btnModeToggle.textContent = "Взять управление";
    btnModeToggle.className = "btn-mode-toggle in-auto";
    btnModeToggle.disabled = false;
    btnModeToggle.title = "";
  } else {
    if (!state.mode.auto_available) {
      btnModeToggle.textContent = "Автомат недоступен";
      btnModeToggle.className = "btn-mode-toggle disabled-unavailable";
      btnModeToggle.disabled = true;
      btnModeToggle.title = state.mode.auto_unavailable_reason || "Условия автомата не выполнены";
    } else {
      btnModeToggle.textContent = "Включить автомат";
      btnModeToggle.className = "btn-mode-toggle";
      btnModeToggle.disabled = false;
      btnModeToggle.title = "";
    }
  }


  const marginVal = document.getElementById("margin-val");
  marginVal.textContent = state.margin.value_rub_h ? (state.margin.value_rub_h / 1e6).toFixed(2) : "—";

  const marginDelta = document.getElementById("margin-delta");
  if (state.margin.delta_rub_h != null) {
    const dVal = Math.round(state.margin.delta_rub_h / 1000);
    const sign = dVal > 0 ? "+" : "";
    marginDelta.textContent = `${sign}${dVal} тыс ₽/ч`;
    marginDelta.className = `margin-delta mono ${dVal >= 0 ? "pos" : "neg"}`;
  } else {
    marginDelta.textContent = "";
  }

  // Бейдж индекса уверенности данных (F1)
  renderConfidenceBadge(state);

  // Тревоги ПАЗ
  const alarmBadge = document.getElementById("alarm-badge");
  const alarmBadgeText = document.getElementById("alarm-badge-text");
  if (state.alarms && state.alarms.length > 0) {
    alarmBadge.classList.add("active");
    alarmBadgeText.textContent = `ТРЕВОГА: ${state.alarms[0].text}`;
  } else {
    alarmBadge.classList.remove("active");
    alarmBadgeText.textContent = "ПАЗ в норме";
  }

  const clockText = document.getElementById("clock-text");
  if (state.clock?.now) {
    const dt = new Date(state.clock.now);
    clockText.textContent = `${String(dt.getUTCHours()).padStart(2, "0")}:${String(dt.getUTCMinutes()).padStart(2, "0")}`;
  }

  // 3. Баннер автовыхода / отказа
  const alertBanner = document.getElementById("alert-banner");
  if (state.banner) {
    alertBanner.style.display = "flex";
    document.getElementById("banner-title").textContent = state.banner.title;
    document.getElementById("banner-text").textContent = state.banner.text;
  } else {
    alertBanner.style.display = "none";
  }

  // 4. Панель «Симулятор»
  updateSimulatorDisplay(state);

  // 5. Левая колонка: CV данные
  if (state.series.cv.length > 0) {
    const s = state.series.cv[0];
    const sLast = s.history.slice(-1)[0];
    document.getElementById("cv-sulfur-val").textContent = sLast && sLast.v != null ? sLast.v.toFixed(1) : "—";
    const sChip = document.getElementById("cv-sulfur-chip");
    sChip.className = `cv-chip ${s.chip.severity}`;
    sChip.textContent = s.chip.text;
    document.getElementById("cv-sulfur-note").textContent = s.note || "";
  }

  if (state.series.cv.length > 1) {
    const f = state.series.cv[1];
    const fLast = f.history.slice(-1)[0];
    document.getElementById("cv-flash-val").textContent = fLast && fLast.v != null ? fLast.v.toFixed(1) : "—";
    const fChip = document.getElementById("cv-flash-chip");
    fChip.className = `cv-chip ${f.chip.severity}`;
    fChip.textContent = f.chip.text;
    document.getElementById("cv-flash-note").textContent = f.note || "";
  }

  if (state.series.cv.length > 2) {
    const dp = state.series.cv[2];
    const dpLast = dp.history.slice(-1)[0];
    document.getElementById("cv-dp-val").textContent = dpLast && dpLast.v != null ? dpLast.v.toFixed(1) : "—";
    const dpChip = document.getElementById("cv-dp-chip");
    dpChip.className = `cv-chip ${dp.chip.severity}`;
    dpChip.textContent = dp.chip.text;
  }

  // 6. Левая колонка: MV данные
  state.series.mv.forEach((mv, idx) => {
    const currEl = document.getElementById(`mv-${idx}-curr`);
    const targetEl = document.getElementById(`mv-${idx}-target`);
    const corrEl = document.getElementById(`mv-${idx}-corr`);
    const cardEl = document.getElementById(`mv-card-${idx}`);

    const spLast = mv.sp_history.slice(-1)[0];
    if (currEl) currEl.textContent = spLast && spLast.v != null ? spLast.v.toFixed(mv.decimals) : "—";

    let targetVal = null;
    let targetClass = "rec";
    if (store.edits[mv.sp] != null) {
      targetVal = store.edits[mv.sp];
      targetClass = "edit";
    } else if (state.recommendation?.changes) {
      const ch = state.recommendation.changes.find((c) => c.sp === mv.sp);
      if (ch) targetVal = ch.target;
    }

    if (cardEl) {
      cardEl.classList.remove("has-target", "has-edit");
      if (targetVal != null) {
        cardEl.classList.add(targetClass === "edit" ? "has-edit" : "has-target");
      }
    }

    if (targetEl) {
      if (targetVal != null) {
        targetEl.textContent = `→ ${targetVal.toFixed(mv.decimals)}`;
        targetEl.className = `mv-target-val mono ${targetClass}`;
      } else if (mv.frozen) {
        targetEl.textContent = "заморожено";
        targetEl.className = "mv-target-val mono";
      } else {
        targetEl.textContent = "";
      }
    }

    if (corrEl) {
      if (mv.corridor.max_step_per_tick != null) {
        corrEl.textContent = `шаг ≤ ${mv.corridor.max_step_per_tick} · T0 ${mv.corridor.lo}–${mv.corridor.hi}`;
      } else {
        corrEl.textContent = "шаг [из паспорта]";
      }
    }
  });

  // MV safe-hold badge
  const mvFrozenBadge = document.getElementById("mv-frozen-badge");
  if (mvFrozenBadge) {
    mvFrozenBadge.style.display = isRefusal || state.series.mv.some((m) => m.frozen) ? "flex" : "none";
  }

  // 6b. Секция «Качество продукта и сырья» (F1)
  renderSensors(state);

  // 7. Правая колонка: Карточка решения
  // renderDecisionCard() идёт ПЕРЕД синхронизацией счётчиков: она (пере)создаёт #auto-timer /
  // #rec-validity-countdown только когда html реально изменился (setHtmlIfChanged), поэтому оба
  // счётчика ниже нужно писать заново уже после неё — иначе первое обновление могло бы попасть
  // в ещё не созданный узел.
  renderDecisionCard();
  syncAutoCountdown(state);
  updateRecValidityCountdown(state);

  // 8. Правая колонка: Лента
  renderFeed();

  // 9. Отрисовка графиков вкладки "Обзор"
  const activeData = getActiveTrajectory();
  renderCharts(store.chartsHandle, {
    state,
    active: activeData.trajectory,
    activeColor: activeData.color,
    recThin: activeData.recThin,
    historyHours: store.historyHours,
  });

  applyWidgetVisibility();
}

// Уровень уверенности данных -> русский текст причины (F1, 03_STREAMLIT_MIGRATION_PLAN.md §3)
const CONFIDENCE_LEVEL_CLASS = { HIGH: "level-high", MEDIUM: "level-medium", LOW: "level-low" };

function renderConfidenceBadge(state) {
  const wrap = document.getElementById("confidence-badge-wrap");
  const badge = document.getElementById("confidence-badge");
  const scoreEl = document.getElementById("confidence-badge-score");
  const tooltip = document.getElementById("confidence-tooltip");
  if (!wrap || !badge || !scoreEl || !tooltip) return;

  const conf = state.confidence;
  if (!conf) {
    // До первого такта (или пока источник в графе не посчитан) — бейдж не показываем вообще.
    wrap.style.display = "none";
    wrap.classList.remove("open");
    return;
  }

  wrap.style.display = "flex";
  badge.className = `confidence-badge ${CONFIDENCE_LEVEL_CLASS[conf.level] || "level-low"}`;
  scoreEl.textContent = `${Math.round(conf.score * 100)}%`;

  const reasons = [];
  if (conf.n_filled_critical > 0) {
    reasons.push(`заполнено нормативными значениями: ${conf.n_filled_critical} тег${pluralTags(conf.n_filled_critical)}`);
  }
  if (conf.q21_unavailable) {
    reasons.push("онлайн-анализатор серы недоступен");
  }
  reasons.push(`возраст ЛИМС: ${conf.lims_age_hours.toFixed(1)} ч`);

  tooltip.innerHTML = `
    <div class="confidence-tooltip-title">Индекс уверенности данных: ${Math.round(conf.score * 100)}% (${conf.level})</div>
    <ul class="confidence-tooltip-list">
      ${reasons.map((r) => `<li>${r}</li>`).join("")}
    </ul>
  `;
}

function pluralTags(n) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "";
  if ([2, 3, 4].includes(mod10) && ![12, 13, 14].includes(mod100)) return "а";
  return "ов";
}

// Секция «Качество продукта и сырья» (F1, 03_STREAMLIT_MIGRATION_PLAN.md §4): WABT/ВСГ/T11 +
// качество товарного продукта (D15/T95/CFPP/CN) + качество сырья (S_FEED/T95_FEED).
const SENSORS_REACTOR_KEYS = ["HT_BED_MEAN", "HT_VSG", "HT_T11"];
const SENSORS_PRODUCT_KEYS = ["HT_D15_PRODUCT", "HT_T95_PRODUCT", "HT_CFPP_PRODUCT", "HT_CN_PRODUCT"];
const SENSORS_FEED_KEYS = ["HT_S_FEED", "HT_T95_FEED"];

function renderSensors(state) {
  const reactorEl = document.getElementById("sensors-group-reactor");
  const productEl = document.getElementById("sensors-group-product");
  const feedEl = document.getElementById("sensors-group-feed");
  if (!reactorEl || !productEl || !feedEl) return;

  const sensors = state.sensors || [];
  const byKey = {};
  sensors.forEach((s) => (byKey[s.key] = s));

  const rowHtml = (key) => {
    const s = byKey[key];
    if (!s) return "";
    const last = s.history.length > 0 ? s.history[s.history.length - 1] : null;
    const hasValue = last && last.v != null && !Number.isNaN(last.v);
    const val = hasValue ? last.v.toFixed(2) : "—";
    return `
      <div class="sensor-row">
        <span class="sensor-row-title">${s.title} <span class="mono sensor-row-tag">${s.tag}</span></span>
        <span class="sensor-row-value mono">${val}${hasValue ? ` <span class="sensor-row-unit">${s.unit}</span>` : ""}</span>
      </div>
    `;
  };

  const reactorHtml = SENSORS_REACTOR_KEYS.map(rowHtml).join("");
  const productHtml = SENSORS_PRODUCT_KEYS.map(rowHtml).join("");
  const feedHtml = SENSORS_FEED_KEYS.map(rowHtml).join("");

  setHtmlIfChanged(reactorEl, reactorHtml || `<div class="sensors-empty">Нет данных (такт ещё не рассчитан)</div>`);
  setHtmlIfChanged(productEl, productHtml || `<div class="sensors-empty">Нет данных (такт ещё не рассчитан)</div>`);
  setHtmlIfChanged(feedEl, feedHtml || `<div class="sensors-empty">Нет данных (такт ещё не рассчитан)</div>`);
}

// Определение активной траектории для графиков
function getActiveTrajectory() {
  const state = store.state;
  const hasEdits = Object.keys(store.edits).length > 0;

  if (store.preview?.trajectory) {
    return {
      trajectory: store.preview.trajectory,
      color: hasEdits ? "edit" : "rec",
      recThin: state.recommendation?.trajectory || null,
    };
  }

  if (store.choice !== "balanced" && state.recommendation?.alternatives) {
    const alt = state.recommendation.alternatives.find((a) => a.choice === store.choice);
    if (alt && alt.trajectory) {
      return {
        trajectory: alt.trajectory,
        color: "rec",
        recThin: null,
      };
    }
  }

  if (state.mode.current === "AUTO" && state.auto?.plan) {
    return {
      trajectory: state.recommendation?.trajectory || state.hold,
      color: "rec",
      recThin: null,
    };
  }

  return {
    trajectory: state.recommendation?.trajectory || state.hold,
    color: "rec",
    recThin: null,
  };
}

// Рендеринг правой колонки (карточки решений)
function renderDecisionCard() {
  const container = document.getElementById("decision-card-container");
  const state = store.state;
  if (!container || !state) return;

  if (state.mode.current === "AUTO") {
    if (setHtmlIfChanged(container, renderAutoCard(state))) bindAutoCardEvents();
    return;
  }

  if (state.recommendation?.status?.startsWith("REFUSAL_")) {
    if (setHtmlIfChanged(container, renderRefusalCard(state))) bindRefusalCardEvents();
    return;
  }

  if (state.recommendation?.status === "NO_CHANGE_DEADBAND") {
    setHtmlIfChanged(container, `
      <div class="decision-card-advisory">
        <div class="card-header">
          <span class="card-title">ИЗМЕНЕНИЯ НЕ ТРЕБУЮТСЯ</span>
        </div>
        <div style="font-size: 14px; color: var(--ink-3);">Режим стабилен, параметры в пределах технологического коридора.</div>
        ${renderXaiExplanationHtml()}
      </div>
    `);
    return;
  }

  if (setHtmlIfChanged(container, renderAdvisoryCard(state))) bindAdvisoryCardEvents();
}

function renderAdvisoryCard(state) {
  const rec = state.recommendation;
  if (!rec) return `<div class="decision-card-advisory">Ожидание решения системы...</div>`;

  const hasEdits = Object.keys(store.edits).length > 0;
  const alt = (store.choice !== "balanced" && rec.alternatives)
    ? rec.alternatives.find((a) => a.choice === store.choice)
    : null;

  const baseChanges = (alt && alt.changes && alt.changes.length > 0) ? alt.changes : (rec.changes || []);
  const baseEffect = (alt && alt.effect) ? alt.effect : rec.effect;
  const baseLabel = alt ? alt.label : "рекомендация";
  const changes = baseChanges;

  let changesHtml = "";
  changes.forEach((ch) => {
    const isEdited = store.edits[ch.sp] != null;
    const currentVal = ch.current.toFixed(ch.decimals);
    const targetVal = isEdited ? store.edits[ch.sp].toFixed(ch.decimals) : ch.target.toFixed(ch.decimals);
    const step = ch.decimals === 0 ? 10 : (ch.sp === "HT_P_SP" ? 0.05 : 0.5);

    changesHtml += `
      <div class="change-row ${isEdited ? "edited" : ""}">
        <label for="input-${ch.sp}" style="font-size: 15px; font-weight: 600; line-height: 1.2;">
          ${ch.title}<br><span class="mono" style="font-size: 12px; font-weight: 400; color: var(--ink-3);">${ch.tag}, ${ch.unit}</span>
        </label>
        <span class="mono" style="font-size: 20px; color: var(--ink-2);">${currentVal}</span>
        <svg width="16" height="16" viewBox="0 0 22 16" fill="none" stroke="#3F4448" stroke-width="2"><path d="M2 8h16"></path><path d="M13 3l5 5-5 5"></path></svg>
        <div class="value-stepper ${isEdited ? "edited" : ""}" id="stepper-rec-${ch.sp}">
          <button type="button" class="stepper-btn" data-dir="-1" aria-label="Уменьшить">−</button>
          <input id="input-${ch.sp}" data-sp="${ch.sp}" data-step="${step}" data-decimals="${ch.decimals}" type="text" inputmode="decimal" class="stepper-value mono" value="${targetVal}">
          <button type="button" class="stepper-btn" data-dir="+1" aria-label="Увеличить">+</button>
        </div>
        <span style="font-size: 13px; line-height: 1.3; color: ${isEdited ? "var(--edit-ink)" : "var(--ink-2)"};">
          ${isEdited ? `изменено вами<br>${baseLabel} <b class="mono">${ch.target.toFixed(ch.decimals)}</b>` : `= ${baseLabel}`}
        </span>
      </div>
    `;
  });

  const kernelInfo = store.preview?.kernel || rec.kernel;
  const corridorOk = store.preview ? store.preview.corridor_ok : true;
  // can_commit_with_ack: сера вышла за лимит, но это единственное нарушение — оператор может
  // применить ход сам, увидев предупреждение (см. quality_warning в тосте после коммита), вместо
  // жёсткой блокировки кнопки. Ядру АВТОМАТА (auto.py) это поле не показывается.
  const canCommit = store.preview ? (store.preview.can_commit || store.preview.can_commit_with_ack) : true;
  const kernelPassed = kernelInfo.passed && corridorOk;

  const kernelStatusText = kernelPassed
    ? "проверено ядром T0–T3: нарушений нет · шаг в коридоре"
    : (store.preview?.quality_warning || store.preview?.blocking_reason || "Обнаружены технологические нарушения");

  const activeTraj = getActiveTrajectory().trajectory;
  const sulfurRisk = activeTraj?.risk?.find((r) => r.cv === "sulfur");
  const showRisk = sulfurRisk && sulfurRisk.probability >= 0.05;
  const riskPct = showRisk ? Math.round(sulfurRisk.probability * 100) : 0;

  let riskTimeText = "";
  if (showRisk && sulfurRisk.first_breach_at && state.clock?.now) {
    const diffMs = Math.max(0, new Date(sulfurRisk.first_breach_at).getTime() - new Date(state.clock.now).getTime());
    const diffMins = Math.round(diffMs / 60000);
    const diffH = Math.floor(diffMins / 60);
    const diffM = diffMins % 60;
    if (diffH > 0 && diffM > 0) {
      riskTimeText = ` через ${diffH} ч ${diffM} мин`;
    } else if (diffH > 0) {
      riskTimeText = ` через ${diffH} ч`;
    } else if (diffM > 0) {
      riskTimeText = ` через ${diffM} мин`;
    }
  }

  let recRiskText = "< 1%";
  const recSulfurRisk = state.recommendation?.trajectory?.risk?.find((r) => r.cv === "sulfur");
  if (recSulfurRisk && recSulfurRisk.probability >= 0.01) {
    recRiskText = `${Math.round(recSulfurRisk.probability * 100)}%`;
  }

  const sulfurLimit = sulfurRisk?.limit != null ? sulfurRisk.limit.toFixed(0) : "10";
  const sulfurUnit = state.series.cv[0]?.unit || "ppm";

  const effect = store.preview?.effect || baseEffect;
  const s4h = effect.sulfur_4h != null ? effect.sulfur_4h.toFixed(1) : "—";
  const flashMargin = effect.flash_margin_c != null ? (effect.flash_margin_c >= 0 ? `+${effect.flash_margin_c}` : effect.flash_margin_c) : "—";
  const marginDelta = effect.margin_delta_rub_h != null ? `${Math.round(effect.margin_delta_rub_h / 1000)} тыс ₽/ч` : "—";

  let commitBtnText = "Применить рекомендацию";
  if (hasEdits) {
    commitBtnText = "Применить мои значения";
  } else if (store.choice !== "balanced" && alt) {
    commitBtnText = `Применить режим «${alt.label}»`;
  }

  // Бейдж «актуально до»: valid_until сам по себе честен (service.py теперь ставит +1 такт,
  // а не искусственные +30 мин), но настоящий лимит на решение — реальное время до следующего
  // такта симулятора (next_tick_in_s), потому что именно такт меняет cycle_id и делает коммит
  // недействительным (STALE_RECOMMENDATION). Раньше это нигде не показывалось до самого клика.
  // Само число секунд сюда НЕ печатается: next_tick_in_s меняется на каждый опрос (2с), а этот
  // HTML идёт через setHtmlIfChanged — если зашить счётчик прямо в разметку, карточка снова
  // пересобиралась бы на каждый опрос и мы вернули бы баг с "съеденными" кликами. Вместо этого
  // ниже — пустой span, который update RecValidityCountdown() досчитывает точечно, в обход диффа
  // (тот же приём, что и таймер режима АВТОМАТ, см. updateAutoTimerDisplay).
  const validityHtml = (rec.valid_until ? `актуально до ${rec.valid_until.slice(11, 16)}` : "")
    + ` <span id="rec-validity-countdown" class="mono"></span>`;

  const staleNoticeHtml = store.staleNotice ? `
    <div class="stale-notice-box">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" style="flex-shrink:0;"><path d="M12 3l10 18H2z"></path><path d="M12 10v5"></path><path d="M12 18v.5"></path></svg>
      <div style="flex-grow:1;">
        <b>Рекомендация обновилась, пока вы выбирали.</b> Ваш предыдущий выбор ещё не применён к установке — ниже уже новый такт.
      </div>
      <div style="display:flex; gap:8px; flex-shrink:0;">
        <button id="btn-stale-retry" type="button" class="btn-commit" style="flex: none; width: auto; padding: 0 14px; height: 36px; font-size: 14px;">Пересчитать и повторить мой выбор</button>
        <button id="btn-stale-dismiss" type="button" class="btn-reject" style="flex: none; width: auto; height: 36px; font-size: 14px;">Понятно</button>
      </div>
    </div>
  ` : "";

  return `
    <div class="decision-card-advisory ${hasEdits ? "has-edits" : ""}">
      <div class="card-header">
        <span class="card-title">СИСТЕМА ПРЕДЛАГАЕТ</span>
        <span class="card-validity mono">${rec.created_at ? rec.created_at.slice(11, 16) : ""} · ${validityHtml}</span>
      </div>
      ${staleNoticeHtml}
      ${renderXaiExplanationHtml()}

      <div style="display: flex; flex-direction: column; gap: 8px;">
        ${changesHtml}
      </div>

      <div class="kernel-status-line ${kernelPassed ? "" : "error"}">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"></path><path d="M8.5 12l2.5 2.5 4.5-5"></path></svg>
        <span>${kernelStatusText}</span>
      </div>

      ${showRisk ? `
        <div class="risk-badge-box">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 3l10 18H2z"></path><path d="M12 10v5"></path><path d="M12 18v.5"></path></svg>
          <span><b>Риск превышения ${sulfurLimit} ${sulfurUnit}: ${riskPct}%</b>${riskTimeText} · при рекомендации: ${recRiskText}</span>
        </div>
      ` : ""}

      <div class="effect-grid">
        <div class="effect-tile">
          <span style="font-size: 13px; color: var(--ink-3);">Сера через 4 ч</span>
          <span class="mono" style="font-size: 22px; font-weight: 600; color: ${hasEdits ? "var(--edit-ink)" : "var(--rec)"};">${s4h} ppm</span>
          <span style="font-size: 12px; color: var(--ink-2);">ГОСТ ≤ 10.0 ppm</span>
        </div>
        <div class="effect-tile">
          <span style="font-size: 13px; color: var(--ink-3);">Запас по вспышке</span>
          <span class="mono" style="font-size: 22px; font-weight: 600;">${flashMargin} °C</span>
          <span style="font-size: 12px; color: var(--ink-2);">над 55 °C с учётом 2σ</span>
        </div>
        <div class="effect-tile">
          <span style="font-size: 13px; color: var(--ink-3);">Маржа</span>
          <span class="mono" style="font-size: 22px; font-weight: 600;">${marginDelta}</span>
          <span style="font-size: 12px; color: var(--ink-2);">эффект решения</span>
        </div>
      </div>

      <div style="display: flex; flex-direction: column; gap: 8px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-size: 13px; color: var(--ink-3);">Режим рекомендаций</span>
          <button id="btn-recalc-forecast" type="button" class="btn-recalc-forecast" title="Пересчитать прогноз для выбранного режима" ${store.isRecalculating ? "disabled" : ""}>
            ${store.isRecalculating ? '<span class="spinner-inline"></span> Расчёт...' : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg> Пересчитать прогноз`}
          </button>
        </div>
        <div class="alternatives-segment">
          ${(() => {
            // available=false у альтернативы означает "на Парето-фронте нет точки, реально
            // отличной от рекомендации по этому критерию" (см. service.py) — кнопка неактивна,
            // а не тихо переключает предпросмотр на текущие (hold) уставки, как было раньше
            // (аудит 2026-09-22: это и давало эффект "последствия меняются, а сами значения нет").
            const isAvail = (choice) => {
              if (choice === "balanced") return true;
              const a = rec.alternatives?.find((x) => x.choice === choice);
              return !!(a && a.available && a.changes && a.changes.length > 0);
            };
            const btn = (choice, label) => {
              const avail = isAvail(choice);
              return `<button type="button" class="alt-btn" data-choice="${choice}" aria-pressed="${store.choice === choice}" ${avail ? "" : 'disabled title="Нет отличной от рекомендации альтернативы по этому критерию на текущем такте"'}>${label}</button>`;
            };
            return btn("max_margin", "Макс. маржа") + btn("balanced", "Сбалансировано");
          })()}
        </div>
      </div>

      <button id="btn-commit-action" type="button" class="btn-commit ${canCommit ? "" : "btn-disabled"}" title="${kernelStatusText}">
        ${store.isCommitting ? '<span class="spinner-inline"></span> Применение уставок...' : commitBtnText}
      </button>

      <div class="commit-sub-row">
        ${hasEdits ? `
          <button id="btn-reset-edits" type="button" class="btn-reset-rec">Вернуть рекомендацию</button>
        ` : ""}
        <button id="btn-reject-action" type="button" class="btn-reject">Отклонить</button>
      </div>

      <button id="btn-toggle-agents" type="button" class="btn-toggle-agents">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 5l7 7-7 7"></path></svg>
        Почему так решили: ${rec.agents.length} агентов, согласовано
      </button>
      <div id="agents-list" class="agents-list" style="display: ${store.showAgents ? "flex" : "none"};">
        ${rec.agents.map((a) => `<div><b>${a.agent}:</b> [${a.stance}] ${a.text}</div>`).join("")}
      </div>
    </div>
  `;
}

function renderAutoCard(state) {
  const autoInfo = state.auto;
  const nextStep = autoInfo?.next_step;
  const ch = nextStep?.changes?.[0];

  // Таймер и прогресс-бар НЕ считаются от autoSecondsRemaining здесь: это значение тикает каждую
  // секунду (см. autoCountdownTimer) и пересинхронизируется с сервером на каждый опрос, так что
  // строка html менялась бы почти на каждый рендер и карточка пересобиралась бы через
  // setHtmlIfChanged всё так же часто, как и до фикса. Ниже — статичная заглушка; фактическое
  // значение пишет updateAutoTimerDisplay() напрямую в DOM сразу после renderDecisionCard()
  // (см. порядок вызовов в render()), в обход диффа.
  const timerInitStr = "--:--";
  const pctInit = 0;

  const mvRows = (state.series?.mv || []).map((mv) => {
    const maxStepText = mv.corridor.max_step_per_tick != null
      ? `${mv.corridor.max_step_per_tick} ${mv.unit}/такт`
      : `<span style="color: var(--ink-3);">[из паспорта]</span>`;
    const rangeText = (mv.corridor.lo != null && mv.corridor.hi != null)
      ? `${mv.corridor.lo} – ${mv.corridor.hi} ${mv.unit}`
      : `<span style="color: var(--ink-3);">[из паспорта]</span>`;
    return `
      <span style="font-weight: 600;">${mv.title} <span class="mono" style="font-size: 12px; font-weight: 400; color: var(--ink-3);">${mv.tag}</span></span>
      <span class="mono">${maxStepText}</span>
      <span class="mono">${rangeText}</span>
    `;
  }).join("");

  return `
    <div class="decision-card-auto">
      <div class="card-header">
        <span class="card-title" style="color: var(--auto);">СЛЕДУЮЩИЙ ШАГ АВТОМАТА</span>
        <span style="font-size: 15px; color: var(--ink-2);">через</span>
        <span id="auto-timer" class="auto-timer mono">${timerInitStr}</span>
      </div>
      <div class="auto-progress-bar">
        <div class="auto-progress-fill" style="width: ${pctInit}%;"></div>
      </div>

      ${ch ? `
        <div style="display: flex; align-items: center; gap: 14px; padding: 10px 12px; background: var(--panel-2); border-radius: var(--r);">
          <span style="font-size: 16px; font-weight: 600;">${ch.title} <span class="mono" style="font-size: 12px; font-weight: 400; color: var(--ink-3);">${ch.tag}</span></span>
          <span class="mono" style="font-size: 22px; color: var(--ink-2);">${ch.current.toFixed(ch.decimals)}</span>
          <svg width="22" height="16" viewBox="0 0 22 16" fill="none" stroke="#3F4448" stroke-width="2"><path d="M2 8h16"></path><path d="M13 3l5 5-5 5"></path></svg>
          <span class="mono" style="font-size: 26px; font-weight: 600; color: var(--rec);">${ch.target.toFixed(ch.decimals)}</span>
          <span style="font-size: 15px; color: var(--ink-2);">${ch.unit}</span>
          <div class="spacer"></div>
          <span class="mono" style="font-size: 14px; color: var(--ink-2);">${ch.delta > 0 ? `+${ch.delta}` : ch.delta} /такт</span>
        </div>
      ` : ""}

      ${renderXaiExplanationHtml()}

      <div style="display: flex; gap: 10px;">
        <button id="btn-auto-skip" type="button" class="btn-reset-rec" style="border-color: var(--ink); color: var(--ink);">Пропустить этот шаг</button>
        <button id="btn-auto-takeover" type="button" class="btn-commit" style="flex-grow: 1;">Взять управление</button>
      </div>

      <div class="auto-corridor-box">
        <div style="display: flex; align-items: baseline; gap: 8px;">
          <span style="font-size: 15px; font-weight: 700; letter-spacing: 0.05em;">КОРИДОР АВТОМАТА</span>
          <span style="font-size: 13px; color: var(--ink-3);">из паспорта · только просмотр</span>
        </div>
        <div style="display: grid; grid-template-columns: 170px 150px minmax(0, 1fr); gap: 4px 12px; font-size: 14px;">
          <span style="color: var(--ink-3);">Уставка</span><span style="color: var(--ink-3);">Макс. шаг</span><span style="color: var(--ink-3);">Диапазон T0</span>
          ${mvRows}
        </div>
      </div>
    </div>
  `;
}

function renderRefusalCard(state) {
  const ref = state.recommendation?.refusal;
  const cycleId = state.recommendation?.cycle_id || "default";
  const savedChecks = JSON.parse(localStorage.getItem(`checklist_${cycleId}`) || "{}");

  const checklistItems = (ref?.checklist || []).map((itemText, idx) => `
    <div class="checklist-item">
      <input id="ck-${idx}" type="checkbox" data-idx="${idx}" style="width: 22px; height: 22px; margin: 0;" ${savedChecks[idx] ? "checked" : ""}>
      <label for="ck-${idx}" style="font-size: 16px; color: #2E1E03;">${itemText}</label>
    </div>
  `).join("");

  return `
    <div class="decision-card-refusal">
      <div style="display: flex; align-items: center; gap: 10px;">
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#4A3004" stroke-width="2.2"><circle cx="12" cy="12" r="9"></circle><path d="M6 6l12 12"></path></svg>
        <span style="font-size: 20px; font-weight: 700; letter-spacing: 0.05em; color: var(--warn-ink);">НЕТ РЕКОМЕНДАЦИИ</span>
      </div>
      <p style="margin: 0; font-size: 18px; line-height: 1.4; color: #2E1E03;">${ref?.text || "Система зафиксировала отказ и перевела уставки в safe-hold."}</p>

      <div class="refusal-checklist">
        <span style="font-size: 14px; font-weight: 700; color: var(--warn-ink);">ЧТО ПРОВЕРИТЬ</span>
        ${checklistItems}
      </div>

      <div style="padding: 10px 12px; background: #FFFFFF; border: 1px solid var(--warn-frame); border-radius: var(--r); font-size: 15px; color: #2E1E03; line-height: 1.4;">
        <b>Автомат снова станет доступен</b>, когда ${ref?.auto_resume_condition || "восстановятся параметры качества данных"}.
      </div>

      <button id="btn-order-lims" type="button" class="btn-order-lims">Заказать анализ LIMS</button>
    </div>
  `;
}

// Привязка событий карточки ADVISORY (включая степперы D8)
function bindAdvisoryCardEvents() {
  const rec = store.state?.recommendation;
  if (!rec?.changes) return;

  rec.changes.forEach((ch) => {
    const stepperEl = document.getElementById(`stepper-rec-${ch.sp}`);
    if (!stepperEl) return;
    const step = ch.decimals === 0 ? 10 : (ch.sp === "HT_P_SP" ? 0.05 : 0.5);

    bindStepper(stepperEl, {
      getValue: () => store.edits[ch.sp],
      setValue: (val) => {
        if (val == null || Math.abs(val - ch.target) < 1e-5) {
          delete store.edits[ch.sp];
        } else {
          store.edits[ch.sp] = val;
        }
        stepperEl.classList.toggle("edited", store.edits[ch.sp] != null);
      },
      onChange: () => {
        triggerPreviewDebounced();
      },
      step,
      decimals: ch.decimals,
    });
  });

  document.querySelectorAll(".alt-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const newChoice = btn.dataset.choice;
      if (store.choice === newChoice) return;
      store.choice = newChoice;
      store.edits = {};
      store.preview = null;
      store.staleNotice = false;
      render();
      await recalculateForecastForCurrentMode();
      // Смена режима рекомендации (item 4 запроса оператора) — старый отчёт LLM описывал другие
      // цифры (эффект/уставки базовой рекомендации), поэтому при переключении режима нужен
      // отдельный отчёт именно для него, а не подпись старого текста под новыми цифрами.
      loadXaiReport(store.state?.clock?.tick, newChoice);
    });
  });

  document.getElementById("btn-recalc-forecast")?.addEventListener("click", async () => {
    await recalculateForecastForCurrentMode();
  });

  document.getElementById("btn-commit-action")?.addEventListener("click", async () => {
    await executeCommit();
  });

  document.getElementById("btn-reset-edits")?.addEventListener("click", () => {
    store.edits = {};
    store.preview = null;
    store.staleNotice = false;
    render();
  });

  // Непропускаемое уведомление об устаревшей рекомендации (см. executeCommit): к этому моменту
  // refresh() уже подтянул свежий cycle_id, а store.edits/store.choice не сбрасываются при смене
  // цикла (см. refresh()), так что повтор — это просто повторный вызов executeCommit().
  document.getElementById("btn-stale-retry")?.addEventListener("click", async () => {
    store.staleNotice = false;
    await executeCommit();
  });

  document.getElementById("btn-stale-dismiss")?.addEventListener("click", () => {
    store.staleNotice = false;
    render();
  });

  document.getElementById("btn-reject-action")?.addEventListener("click", async () => {
    if (confirm("Отклонить текущую рекомендацию?")) {
      await api.reject(store.state?.recommendation?.cycle_id);
      await refresh();
    }
  });

  document.getElementById("btn-toggle-agents")?.addEventListener("click", () => {
    store.showAgents = !store.showAgents;
    const el = document.getElementById("agents-list");
    if (el) el.style.display = store.showAgents ? "flex" : "none";
  });
}

// Пересчет прогноза при смене режима или по клику на кнопку «Пересчитать прогноз»
async function recalculateForecastForCurrentMode() {
  // Защита от повторного запуска: без неё нетерпеливый повторный клик (или быстрое
  // переключение Макс.маржа/Сбалансировано/Макс.запас — оба пути ведут сюда) плодит ещё
  // один параллельный /api/console/preview поверх уже выполняющегося, вместо того чтобы
  // просто подождать первый — а при заторе на бэкенде (см. TICK_EXECUTOR в runtime.py)
  // это только усугубляло видимое «зависание». store.isRecalculating дублирует то же имя
  // кнопки в HTML (см. renderAdvisoryCard), чтобы блокировка кнопки пережила промежуточный
  // render() от фонового опроса — см. комментарий у store.isRecalculating.
  if (store.isRecalculating) return;
  store.isRecalculating = true;
  render();

  const state = store.state;
  if (!state) {
    store.isRecalculating = false;
    render();
    return;
  }

  const hasEdits = Object.keys(store.edits).length > 0;
  let targetU = { ...currentU(state) };
  if (hasEdits) {
    targetU = { ...targetU, ...store.edits };
  } else if (
    store.choice !== "balanced"
    && state?.recommendation?.alternatives?.find((a) => a.choice === store.choice)?.available
    && state.recommendation.alternatives.find((a) => a.choice === store.choice).changes?.length > 0
  ) {
    const alt = state.recommendation.alternatives.find((a) => a.choice === store.choice);
    alt.changes.forEach((ch) => { targetU[ch.sp] = ch.target; });
  } else if (state?.recommendation?.changes) {
    // Альтернатива недоступна (available=false/нет данных) или выбран "Сбалансировано" —
    // предпросмотр строится от сбалансированной рекомендации, а НЕ от текущих (hold) уставок:
    // иначе карточка "последствий" показывала бы эффект hold, а список изменяемых значений —
    // старый набор сбалансированной рекомендации, создавая видимость "эффект меняется, а сами
    // значения нет" (аудит 2026-09-22, жалоба оператора). Кнопка недоступной альтернативы теперь
    // и так задизейблена (см. renderAdvisoryCard), так что сюда обычно попадает только "Сбалансировано".
    state.recommendation.changes.forEach((ch) => {
      targetU[ch.sp] = ch.target;
    });
  }

  try {
    store.preview = await api.preview(targetU, state?.recommendation?.cycle_id);
    const modeName = store.choice === "max_margin" ? "Макс. маржа" : (store.choice === "max_safety" ? "Макс. запас" : "Сбалансировано");
    showToast("Прогноз пересчитан", `Режим: ${modeName}`);
  } catch (e) {
    console.error("Recalculate forecast failed:", e);
    showToast("Ошибка расчёта прогноза", e.message || "", "error");
  } finally {
    store.isRecalculating = false;
    render();
  }
}

function bindAutoCardEvents() {
  document.getElementById("btn-auto-skip")?.addEventListener("click", async () => {
    await api.skip();
    await refresh();
  });

  document.getElementById("btn-auto-takeover")?.addEventListener("click", async () => {
    await api.setMode("ADVISORY");
    await refresh();
  });
}

function bindRefusalCardEvents() {
  const cycleId = store.state?.recommendation?.cycle_id || "default";
  document.querySelectorAll(".checklist-item input").forEach((ck) => {
    ck.addEventListener("change", () => {
      const saved = JSON.parse(localStorage.getItem(`checklist_${cycleId}`) || "{}");
      saved[ck.dataset.idx] = ck.checked;
      localStorage.setItem(`checklist_${cycleId}`, JSON.stringify(saved));
    });
  });

  document.getElementById("btn-order-lims")?.addEventListener("click", () => {
    const cv = store.state?.series?.cv || [];
    const sLast = cv[0]?.history?.slice(-1)[0]?.v;
    const fLast = cv[1]?.history?.slice(-1)[0]?.v;
    showModal("Анализ ЛИМС", `
      <div style="display:flex; flex-direction:column; gap:16px;">
        <p style="margin:0; font-size:14px; color: var(--ink-2); line-height:1.4;">
          Введите результат, если он уже известен (например, продиктован по телефону из лаборатории),
          либо закажите внеочередной анализ — результат придёт автоматически после обычной задержки лаборатории.
        </p>
        <div style="display:flex; flex-direction:column; gap:10px;">
          <label style="display:flex; justify-content:space-between; align-items:center; gap:12px; font-size:14px;">
            Сера гидрогенизата, мг/кг
            <input id="lims-input-s" type="number" step="0.01" placeholder="${sLast != null ? sLast.toFixed(2) : "—"}" style="width:120px; height:34px; text-align:right; padding:0 8px; border:1px solid var(--line); border-radius:var(--r);">
          </label>
          <label style="display:flex; justify-content:space-between; align-items:center; gap:12px; font-size:14px;">
            Температура вспышки, °C
            <input id="lims-input-f" type="number" step="0.1" placeholder="${fLast != null ? fLast.toFixed(1) : "—"}" style="width:120px; height:34px; text-align:right; padding:0 8px; border:1px solid var(--line); border-radius:var(--r);">
          </label>
        </div>
        <div style="display:flex; gap:10px; justify-content:flex-end;">
          <button id="btn-lims-order" type="button" class="btn-reject" style="width:auto; padding:0 14px; height:38px;">Заказать анализ (~4 ч)</button>
          <button id="btn-lims-save" type="button" class="btn-commit" style="width:auto; padding:0 14px; height:38px;">Сохранить введённые значения</button>
        </div>
      </div>
    `);

    document.getElementById("btn-lims-order")?.addEventListener("click", async () => {
      try {
        await api.lims.order();
        showToast("Внеочередной анализ ЛИМС заказан", "Результат придёт после задержки лаборатории (~4 ч)");
        document.getElementById("modal-overlay").style.display = "none";
        await refresh();
      } catch (e) {
        showToast("Не удалось заказать анализ", e.message || "", "error");
      }
    });

    document.getElementById("btn-lims-save")?.addEventListener("click", async () => {
      const sVal = document.getElementById("lims-input-s").value;
      const fVal = document.getElementById("lims-input-f").value;
      if (!sVal && !fVal) {
        showToast("Введите хотя бы одно значение", "", "error");
        return;
      }
      try {
        if (sVal) await api.lims.manual("HT_S_PRODUCT", parseFloat(sVal));
        if (fVal) await api.lims.manual("HT_FLASH", parseFloat(fVal));
        showToast("Результаты ЛИМС сохранены", "Возраст данных ЛИМС сброшен");
        document.getElementById("modal-overlay").style.display = "none";
        await refresh();
      } catch (e) {
        showToast("Не удалось сохранить результат", e.message || "", "error");
      }
    });
  });
}

// Запуск preview с debounce 250мс (фикс чтения u_current из §2.4)
function triggerPreviewDebounced() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(async () => {
    const targetU = { ...currentU(store.state), ...store.edits };
    try {
      store.preview = await api.preview(targetU, store.state.recommendation?.cycle_id);
      render();
    } catch (e) {
      console.error("Preview failed:", e);
    }
  }, 250);
}

// Применение уставок на вкладке «Обзор» (фикс чтения u_current из §2.4, 1-Click Commit)
async function executeCommit() {
  if (store.isCommitting) return;
  store.staleNotice = false;

  const canCommit = store.preview ? (store.preview.can_commit || store.preview.can_commit_with_ack) : true;
  if (!canCommit) {
    const reason = store.preview?.blocking_reason || "Обнаружены технологические нарушения безопасности или выход за коридор";
    showToast("Невозможно применить уставки", reason, "error");
    return;
  }

  const hasEdits = Object.keys(store.edits).length > 0;
  let targetU = { ...currentU(store.state) };
  if (hasEdits) {
    targetU = { ...targetU, ...store.edits };
  } else if (store.choice !== "balanced" && store.state?.recommendation?.alternatives) {
    const alt = store.state.recommendation.alternatives.find((a) => a.choice === store.choice);
    if (alt?.changes) {
      alt.changes.forEach((ch) => { targetU[ch.sp] = ch.target; });
    }
  } else if (store.state?.recommendation?.changes) {
    store.state.recommendation.changes.forEach((ch) => {
      targetU[ch.sp] = ch.target;
    });
  }
  const source = hasEdits ? "operator_edit" : (store.choice !== "balanced" ? "alternative" : "recommendation");

  store.isCommitting = true;
  render();

  try {
    const commitRes = await api.commit({
      cycle_id: store.state.recommendation?.cycle_id,
      u_target: targetU,
      source,
      choice: store.choice,
    });
    // Немедленное продвижение симулятора на 1 такт для видимого эффекта: значения в степперах
    // читаются из mv_sp_history (см. currentU()), а он пополняется только внутри tick_single(),
    // не внутри apply() — без этого такта applied уставки не появятся на экране до следующего
    // тика метронома (по умолчанию раз в 10с). Раньше неуспех этого вызова молча проглатывался
    // (только console.warn) и тост «успешно применено» показывался ВСЕ РАВНО — оператор видел
    // подтверждение успеха, а степпер оставался со старым значением. Теперь при неуспехе честно
    // предупреждаем: сам коммит прошёл (уставка принята боевой установкой), но отображение
    // обновится с задержкой, на следующем такте метронома, а не немедленно.
    let tickAdvanced = true;
    try {
      await api.demo.tick(1);
    } catch (e) {
      tickAdvanced = false;
      console.warn("Simulator tick after commit failed:", e);
    }

    const nothingChanged = Array.isArray(commitRes.applied) && commitRes.applied.length === 0;

    if (commitRes.quality_warning) {
      showToast("Применено с предупреждением", commitRes.quality_warning, "warn");
    } else if (nothingChanged) {
      // ok:true, но applied пуст — целевые уставки совпали с текущими (напр. повторный клик
      // после того же значения). Коммит формально прошёл, но менять на экране нечего — не
      // выдаём тот же текст про «+1 такт», чтобы не создавать впечатление изменения, которого
      // не произошло.
      showToast("Уставки подтверждены", "Целевые значения совпадают с текущими — менять нечего");
    } else if (!tickAdvanced) {
      showToast(
        "Уставки применены к боевой установке",
        "Не удалось сразу продвинуть симулятор — отображение обновится на следующем такте",
        "warn"
      );
    } else {
      showToast(
        "Уставки успешно применены к боевой установке",
        "Симулятор продвинут на +1 такт (10 мин)"
      );
    }
    store.edits = {};
    store.preview = null;
    await refresh();
  } catch (err) {
    if (err.detail?.code === "STALE_RECOMMENDATION") {
      // Раньше это был только исчезающий за 4.5с тост — легко пропустить, а выбор оператора
      // при этом тихо терялся. Теперь показываем непропускаемый блок прямо в карточке
      // (staleNotice, см. renderAdvisoryCard) с кнопкой одного клика «пересчитать и повторить»:
      // store.edits/store.choice переживают refresh() при смене цикла (см. refresh()), поэтому
      // повтор — это просто новый executeCommit() против уже свежего cycle_id.
      store.staleNotice = true;
      await refresh();
    } else {
      showToast(err.message || "Ошибка применения уставок", err.detail?.text || "", "error");
    }
  } finally {
    store.isCommitting = false;
    render();
  }
}

// -----------------------------------------------------------------------------
// Вкладка «Ручное управление» (D4, D4a, D4b)
// -----------------------------------------------------------------------------
function initManualEvents() {
  document.getElementById("btn-manual-reset")?.addEventListener("click", () => {
    store.manual.edits = {};
    store.manual.preview = null;
    store.manual.loadedPointId = null;
    updateManualLoadedPointNote();
    renderManualRows();
    triggerManualPreviewDebounced(true);
  });

  document.getElementById("btn-manual-preview")?.addEventListener("click", () => {
    triggerManualPreviewDebounced(true);
  });

  document.getElementById("btn-manual-commit")?.addEventListener("click", async () => {
    await executeManualCommit();
  });
}

function initManualTab() {
  if (!store.manual.chartsHandle) {
    const chartRoots = {
      sulfur: document.getElementById("chart-manual-sulfur"),
      flash: document.getElementById("chart-manual-flash"),
      dp: document.getElementById("chart-manual-dp"),
      mv: [
        document.getElementById("chart-manual-mv-0"),
        document.getElementById("chart-manual-mv-1"),
        document.getElementById("chart-manual-mv-2"),
        document.getElementById("chart-manual-mv-3"),
        document.getElementById("chart-manual-mv-4"),
      ],
    };
    store.manual.chartsHandle = mountCharts(chartRoots);
  }

  renderManualRows();
  updateManualLoadedPointNote();

  if (!store.manual.preview) {
    triggerManualPreviewDebounced(true);
  } else {
    renderManualPreviewResults();
  }
}

function updateManualLoadedPointNote() {
  const noteEl = document.getElementById("manual-loaded-point-note");
  if (!noteEl) return;
  if (store.manual.loadedPointId) {
    noteEl.style.display = "block";
    noteEl.textContent = `★ Загружена точка Парето-фронта: ${store.manual.loadedPointId}`;
  } else {
    noteEl.style.display = "none";
  }
}

function renderManualRows() {
  const container = document.getElementById("manual-rows-container");
  const state = store.state;
  if (!container || !state?.series?.mv) return;

  const curU = currentU(state);

  const html = state.series.mv.map((mv) => {
    const curVal = curU[mv.sp] != null ? curU[mv.sp] : 0;
    const isEdited = store.manual.edits[mv.sp] != null;
    const targetVal = isEdited ? store.manual.edits[mv.sp] : curVal;
    const step = mv.decimals === 0 ? 10 : (mv.sp === "HT_P_SP" ? 0.05 : 0.5);

    const corrText = mv.corridor?.max_step_per_tick != null
      ? `шаг ≤ ${mv.corridor.max_step_per_tick} ${mv.unit} за такт · паспорт ${mv.corridor.lo}–${mv.corridor.hi} ${mv.unit}`
      : `паспортный диапазон ${mv.corridor?.lo || "—"}–${mv.corridor?.hi || "—"} ${mv.unit}`;

    return `
      <div class="manual-row" data-sp="${mv.sp}">
        <div>
          <span style="font-weight: 600; font-size: 15px;">${mv.title}</span><br>
          <span class="mono" style="font-size: 12px; color: var(--ink-3);">${mv.tag}, ${mv.unit}</span>
        </div>
        <span class="mono manual-current" title="Текущее значение">${curVal.toFixed(mv.decimals)}</span>
        <div class="value-stepper ${isEdited ? "edited" : ""}" id="stepper-manual-${mv.sp}">
          <button type="button" class="stepper-btn" data-dir="-1" aria-label="Уменьшить">−</button>
          <input class="stepper-value mono" type="text" inputmode="decimal" value="${targetVal.toFixed(mv.decimals)}"
                 data-sp="${mv.sp}" data-step="${step}" data-decimals="${mv.decimals}">
          <button type="button" class="stepper-btn" data-dir="+1" aria-label="Увеличить">+</button>
        </div>
        <span class="manual-corridor-note">${corrText}</span>
      </div>
    `;
  }).join("");

  // Опрос идёт каждые 2с и вызывает renderManualRows() безусловно (см. refresh()) — без этой
  // проверки шаговик пересоздавался бы на каждый тик, съедая клики по +/- и сбрасывая фокус
  // поля ввода посреди набора числа (тот же баг, что и в карточке рекомендации, см. app.js:~130).
  if (!setHtmlIfChanged(container, html)) return;

  state.series.mv.forEach((mv) => {
    const stepperEl = document.getElementById(`stepper-manual-${mv.sp}`);
    if (!stepperEl) return;
    const step = mv.decimals === 0 ? 10 : (mv.sp === "HT_P_SP" ? 0.05 : 0.5);

    bindStepper(stepperEl, {
      getValue: () => store.manual.edits[mv.sp],
      setValue: (val) => {
        if (val == null || (curU[mv.sp] != null && Math.abs(val - curU[mv.sp]) < 1e-5)) {
          delete store.manual.edits[mv.sp];
        } else {
          store.manual.edits[mv.sp] = val;
        }
        stepperEl.classList.toggle("edited", store.manual.edits[mv.sp] != null);
      },
      onChange: () => {
        triggerManualPreviewDebounced();
      },
      step,
      decimals: mv.decimals,
      min: mv.corridor?.lo,
      max: mv.corridor?.hi,
    });
  });
}

function triggerManualPreviewDebounced(immediate = false) {
  clearTimeout(manualDebounceTimer);
  const doFetch = async () => {
    const state = store.state;
    if (!state) return;
    const mergedTargetU = { ...currentU(state), ...store.manual.edits };
    try {
      store.manual.preview = await api.preview(mergedTargetU, null);
      renderManualPreviewResults();
    } catch (e) {
      console.error("Manual preview error:", e);
    }
  };

  if (immediate) {
    doFetch();
  } else {
    manualDebounceTimer = setTimeout(doFetch, 250);
  }
}

function renderManualPreviewResults() {
  const prev = store.manual.preview;
  const state = store.state;
  if (!prev || !state) return;

  const s4h = prev.effect?.sulfur_4h != null ? `${prev.effect.sulfur_4h.toFixed(1)} ppm` : "—";
  const flash = prev.effect?.flash_margin_c != null ? `${prev.effect.flash_margin_c >= 0 ? "+" : ""}${prev.effect.flash_margin_c.toFixed(1)} °C` : "—";
  let marginStr = "—";
  if (prev.effect?.margin_delta_rub_h != null) {
    const dVal = Math.round(prev.effect.margin_delta_rub_h / 1000);
    marginStr = `${dVal >= 0 ? "+" : ""}${dVal} тыс ₽/ч`;
  }

  const elS = document.getElementById("manual-eff-sulfur");
  if (elS) elS.textContent = s4h;
  const elF = document.getElementById("manual-eff-flash");
  if (elF) elF.textContent = flash;
  const elM = document.getElementById("manual-eff-margin");
  if (elM) elM.textContent = marginStr;

  const sandboxInfo = document.getElementById("manual-sandbox-info");
  if (sandboxInfo) {
    if (prev.corridor_violations && prev.corridor_violations.length > 0) {
      sandboxInfo.style.display = "block";
      const lines = prev.corridor_violations.map((v) => {
        const mv = state.series?.mv?.find((m) => m.sp === v.sp);
        const title = mv?.title || v.sp;
        const unit = mv?.unit || "";
        const step = v.requested_step != null ? v.requested_step : (v.step != null ? v.step : "");
        const maxStep = v.max_step != null ? v.max_step : "";
        if (maxStep) {
          return `<b>${title}</b>: запрошенный шаг ${step} ${unit} больше паспортного (макс. ${maxStep} ${unit} за такт). Прогноз рассчитан как целевой установившийся режим — реальный привод дошёл бы до него за несколько тактов.`;
        }
        return `<b>${title}</b>: ${v.text}`;
      });
      sandboxInfo.innerHTML = lines.join("<br>");
    } else {
      sandboxInfo.style.display = "none";
    }
  }

  if (store.manual.chartsHandle) {
    renderCharts(store.manual.chartsHandle, {
      state,
      active: prev.trajectory,
      activeColor: "edit",
      recThin: state.recommendation?.trajectory || null,
      historyHours: store.historyHours,
    });
  }

  const commitBtn = document.getElementById("btn-manual-commit");
  if (commitBtn) {
    commitBtn.textContent = "Применить к боевой установке";
    if (prev.can_commit === false && !prev.can_commit_with_ack) {
      commitBtn.disabled = true;
      commitBtn.title = `Недоступно: ${prev.blocking_reason || "нарушение ограничений"}`;
    } else if (prev.can_commit_with_ack) {
      commitBtn.disabled = false;
      commitBtn.title = prev.quality_warning || "";
    } else {
      commitBtn.disabled = false;
      commitBtn.title = "";
    }
  }
}

async function executeManualCommit() {
  const targetU = { ...currentU(store.state), ...store.manual.edits };
  try {
    const commitRes = await api.commit({
      cycle_id: null,
      u_target: targetU,
      source: "operator_edit",
    });
    // Немедленное продвижение симулятора на 1 такт для видимого эффекта
    try {
      await api.demo.tick(1);
    } catch (e) {
      console.warn("Simulator tick after commit failed:", e);
    }
    if (commitRes.quality_warning) {
      showToast("Применено с предупреждением", commitRes.quality_warning, "warn");
    } else {
      showToast(
        "Ручные уставки успешно применены к установке",
        "Симулятор продвинут на +1 такт (10 мин)"
      );
    }
    store.manual.edits = {};
    store.manual.preview = null;
    store.manual.loadedPointId = null;
    updateManualLoadedPointNote();
    await refresh();
  } catch (err) {
    showToast(err.message || "Ошибка применения уставок", "", "error");
  }
}

// -----------------------------------------------------------------------------
// Вкладка «Агенты и XAI» (D7)
// -----------------------------------------------------------------------------
const CHOICE_LABELS = { balanced: "Сбалансировано", max_margin: "Макс. маржа", max_safety: "Макс. запас" };

async function initXaiTab() {
  const currentTick = store.state?.clock?.tick;
  const choice = store.choice || "balanced";
  if (store.xai.reportLoadedTickByChoice[choice] !== currentTick || !store.xai.reportsByChoice[choice]) {
    await loadXaiReport(currentTick, choice);
  }
  renderXaiReport();
  renderReportHistory();
  renderXaiAgentsAndKernel();
}

// Загружается не только при открытии вкладки XAI, но и из refresh() на каждый новый такт, и при
// переключении режима рекомендации (item 4 запроса оператора) — т.к. то же самое объяснение
// показывается и в карточке рекомендации на «Обзоре». Бэкенд кэширует результат по
// cycle_id+choice (src/console/api.py), поэтому повторные опросы одного и того же такта/режима
// не тратят новый вызов LLM.
async function loadXaiReport(currentTick, choice) {
  choice = choice || store.choice || "balanced";
  if (store.xai.reportLoadingChoice === choice) return;
  store.xai.reportLoadingChoice = choice;
  renderXaiReport();
  if (store.activeTab === "overview") renderDecisionCard();
  try {
    const report = await api.getXaiReport(choice);
    store.xai.reportsByChoice[choice] = report;
    store.xai.reportLoadedTickByChoice[choice] = currentTick;
    if (report && report.available && report.report_markdown) {
      pushReportHistory(report);
    }
  } catch (e) {
    console.error("XAI LLM report load failed:", e);
    store.xai.reportsByChoice[choice] = { available: false, reason: "llm_error", report_markdown: null, choice };
  } finally {
    if (store.xai.reportLoadingChoice === choice) store.xai.reportLoadingChoice = null;
  }
  renderXaiReport();
  renderReportHistory();
  if (store.activeTab === "overview") renderDecisionCard();
}

function pushReportHistory(report) {
  const exists = store.xai.reportHistory.some((e) => e.cycle_id === report.cycle_id && e.choice === report.choice);
  if (exists) return;
  store.xai.reportHistory.push({
    cycle_id: report.cycle_id,
    choice: report.choice,
    report_markdown: report.report_markdown,
    generated_at: report.generated_at,
  });
  if (store.xai.reportHistory.length > REPORT_HISTORY_LIMIT) {
    store.xai.reportHistory = store.xai.reportHistory.slice(-REPORT_HISTORY_LIMIT);
  }
  saveReportHistory(store.xai.reportHistory);
}

// reason различает "ключ вообще не настроен" (no_key), "ключ есть, но вызов LLM не удался"
// (llm_error, напр. опечатка в имени модели/сетевая ошибка/квота) и "для этого режима сейчас нет
// отдельной точки на Парето-фронте" (no_alternative) — раньше первые два молча схлопывались в
// один и тот же текст "нужен ключ LLM", что вводило в заблуждение при настроенном, но
// неработающем ключе. Подробности сбоя LLM — в логах бэкенда (src/console/api.py logger.exception).
function xaiReportUnavailableText(report) {
  if (report?.reason === "llm_error") {
    return "Ключ LLM настроен, но не удалось получить ответ от модели (ошибка вызова LLM). Проверьте логи сервера (NEFTEKOD_LLM_MODE/BASE_URL/MODEL) и нажмите «Обновить».";
  }
  if (report?.reason === "no_alternative") {
    return "Для этого режима сейчас нет отдельной точки на Парето-фронте, отличной от рекомендации — объяснять нечего.";
  }
  return "Для объяснения рекомендаций нужен настроенный ключ LLM (NEFTEKOD_LLM_API_KEY / OPENAI_API_KEY).";
}

// Общий блок объяснения от LLM — переиспользуется и на карточке рекомендации «Обзора»
// (renderAdvisoryCard/renderAutoCard/deadband), и явно на вкладке «Агенты и XAI». Всегда читает
// отчёт для ТЕКУЩЕГО выбранного режима (store.choice), поэтому при переключении «Макс. маржа» /
// «Сбалансировано» показывает объяснение именно под эти цифры, а не под цифры другого режима.
function renderXaiExplanationHtml() {
  const choice = store.choice || "balanced";
  const report = store.xai.reportsByChoice[choice];
  if (store.xai.reportLoadingChoice === choice && !report) {
    return '<div class="llm-explanation llm-explanation-empty">Формируется объяснение LLM...</div>';
  }
  if (report && report.available && report.report_markdown) {
    return `<div class="llm-explanation"><span class="llm-explanation-label">Почему агенты приняли такое решение (LLM):</span> ${renderMarkdown(report.report_markdown)}</div>`;
  }
  return `<div class="llm-explanation llm-explanation-empty">${xaiReportUnavailableText(report)}</div>`;
}

function renderXaiReport() {
  const body = document.getElementById("xai-llm-report-body");
  const modeEl = document.getElementById("xai-llm-report-mode");
  const choice = store.choice || "balanced";
  if (modeEl) modeEl.textContent = `· режим: ${CHOICE_LABELS[choice] || choice}`;
  if (!body) return;

  if (store.xai.reportLoadingChoice === choice) {
    body.innerHTML = '<span class="xai-llm-empty">Формируется отчёт LLM...</span>';
    return;
  }

  const report = store.xai.reportsByChoice[choice];
  if (report && report.available && report.report_markdown) {
    body.innerHTML = renderMarkdown(report.report_markdown);
  } else {
    body.innerHTML = `<span class="xai-llm-empty">${xaiReportUnavailableText(report)}</span>`;
  }
}

function renderReportHistory() {
  const container = document.getElementById("xai-report-history-container");
  if (!container) return;

  const history = store.xai.reportHistory || [];
  if (history.length === 0) {
    container.innerHTML = '<div class="xai-report-history-empty">Здесь появится история отчётов LLM по мере смены тактов и режимов рекомендации.</div>';
    return;
  }

  container.innerHTML = history.slice().reverse().map((entry) => {
    const timeStr = entry.generated_at ? new Date(entry.generated_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : "";
    const cycleShort = (entry.cycle_id || "").slice(0, 8);
    const label = CHOICE_LABELS[entry.choice] || entry.choice;
    return `
      <div class="xai-report-history-item">
        <div class="xai-report-history-meta">
          <span class="mono xai-report-history-time">${timeStr}</span>
          <span class="xai-report-history-choice">${label}</span>
          <span class="mono xai-report-history-cycle" title="${entry.cycle_id || ""}">${cycleShort}…</span>
        </div>
        <div class="xai-report-history-text">${renderMarkdown(entry.report_markdown || "")}</div>
      </div>
    `;
  }).join("");
}

// -----------------------------------------------------------------------------
// История вопросов оператора супервизору («Спросить систему»)
// -----------------------------------------------------------------------------
async function submitAskQuestion() {
  if (store.xai.asking) return;
  const input = document.getElementById("ask-input");
  const q = input?.value?.trim();
  if (!q) return;

  store.xai.asking = true;
  const entry = { question: q, answer_markdown: null, error: null, at: new Date().toISOString(), pending: true };
  store.xai.askHistory.push(entry);
  if (input) input.value = "";
  renderAskHistory();

  try {
    const res = await api.ask(q);
    entry.answer_markdown = res.answer_markdown;
  } catch (e) {
    entry.error = e.message || "Не удалось получить ответ супервизора";
  } finally {
    entry.pending = false;
    store.xai.asking = false;
    saveAskHistory(store.xai.askHistory);
    renderAskHistory();
  }
}

function renderAskHistory() {
  const container = document.getElementById("ask-history-container");
  if (!container) return;

  const history = store.xai.askHistory || [];
  if (history.length === 0) {
    container.innerHTML = '<div class="ask-history-empty">Здесь появится история ваших вопросов системе.</div>';
    return;
  }

  // Новые сверху.
  container.innerHTML = history.slice().reverse().map((entry) => {
    const timeStr = entry.at ? new Date(entry.at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : "";
    let answerHtml;
    if (entry.pending) {
      answerHtml = '<span class="ask-history-pending">Система думает над вопросом...</span>';
    } else if (entry.error) {
      answerHtml = `<span class="ask-history-error">${entry.error}</span>`;
    } else {
      answerHtml = renderMarkdown(entry.answer_markdown || "");
    }
    return `
      <div class="ask-history-item">
        <div class="ask-history-q"><span class="ask-history-time mono">${timeStr}</span> ${entry.question}</div>
        <div class="ask-history-a">${answerHtml}</div>
      </div>
    `;
  }).join("");
}

// Позиции агентов и проверки ядра безопасности — не журнал переговоров (тот полностью убран из
// UI по просьбе оператора, см. историю отчётов LLM выше), а сводка текущего такта, которая и
// раньше бралась не из /api/console/xai, а прямо из recommendation.
function renderXaiAgentsAndKernel() {
  const agentsList = document.getElementById("xai-agents-list");
  if (agentsList && store.state?.recommendation?.agents) {
    agentsList.innerHTML = store.state.recommendation.agents.map((a) => `
      <div class="xai-agent-item">
        <b>${a.agent}:</b> [${a.stance}] ${a.text}
      </div>
    `).join("");
  }

  const kernelChecks = document.getElementById("xai-kernel-checks");
  if (kernelChecks && store.state?.recommendation?.kernel?.checks) {
    kernelChecks.innerHTML = store.state.recommendation.kernel.checks.map((chk) => `
      <div class="xai-kernel-item">
        <span style="color: ${chk.passed ? "var(--auto)" : "var(--limit)"}; font-weight: 700;">${chk.passed ? "✓" : "✗"}</span>
        <span><b>${chk.name}:</b> ${chk.detail}</span>
      </div>
    `).join("");
  }
}

// -----------------------------------------------------------------------------
// Вкладка «Парето-анализ» (D5)
// -----------------------------------------------------------------------------
async function initParetoTab() {
  const container = document.getElementById("pareto-chart-container");
  const currentTick = store.state?.clock?.tick;

  // Фикс тройного фетча: пока состояние сессии ещё не загружено (currentTick == null),
  // такт неизвестен — не фетчим сейчас. Иначе loadedForTick запишется как undefined и
  // при первом же реальном такте (0 !== undefined) неизбежно спровоцирует лишний повторный
  // запрос из refresh() сразу после того, как switchTab() уже запустил первый. Как только
  // состояние подгрузится, refresh() сам вызовет initParetoTab() повторно с уже известным тактом.
  if (currentTick == null) return;

  if (store.pareto.loading) return;

  if (store.pareto.loadedForTick !== currentTick) {
    if (container && !store.pareto.data) {
      if (window.echarts && window.echarts.getInstanceByDom(container)) {
        window.echarts.dispose(container);
      }
      container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--ink-muted);font-size:16px;">Загрузка Парето-фронта...</div>';
    }
    store.pareto.loading = true;
    try {
      store.pareto.data = await api.getPareto();
      store.pareto.loadedForTick = currentTick;
    } catch (e) {
      console.error("Pareto load failed:", e);
      store.pareto.loadedForTick = currentTick;
    } finally {
      store.pareto.loading = false;
    }
  }
  renderPareto();
}

function renderPareto() {
  const pData = store.pareto.data;
  const container = document.getElementById("pareto-chart-container");
  const selX = document.getElementById("pareto-axis-x");
  const selY = document.getElementById("pareto-axis-y");

  if (!pData || !pData.objectives || !pData.points || pData.points.length === 0) {
    if (container) {
      if (window.echarts && window.echarts.getInstanceByDom(container)) {
        window.echarts.dispose(container);
      }
      container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--ink-muted);font-size:16px;">Парето-анализ недоступен для этого такта</div>';
    }
    return;
  }

  if (selX && selY && selX.options.length !== pData.objectives.length) {
    selX.innerHTML = pData.objectives.map((o) => `<option value="${o.key}">${o.label} (${o.unit})</option>`).join("");
    selY.innerHTML = pData.objectives.map((o) => `<option value="${o.key}">${o.label} (${o.unit})</option>`).join("");

    const defX = pData.objectives.find((o) => o.key === "sulfur_giveaway") || pData.objectives[0];
    const defY = pData.objectives.find((o) => o.key === "net_margin") || pData.objectives[1] || pData.objectives[0];

    store.pareto.axisX = store.pareto.axisX || defX.key;
    store.pareto.axisY = store.pareto.axisY || defY.key;

    selX.value = store.pareto.axisX;
    selY.value = store.pareto.axisY;
  }

  renderParetoChart(container, pData, {
    axisX: store.pareto.axisX,
    axisY: store.pareto.axisY,
    onPointClick: (point) => {
      onParetoPointClick(point);
    },
  });
}

function onParetoPointClick(point) {
  if (!point) return;
  const state = store.state;
  const curU = currentU(state);

  store.manual.edits = {};
  if (point.delta_u) {
    for (const [sp, du] of Object.entries(point.delta_u)) {
      if (curU[sp] != null && Math.abs(du) > 1e-4) {
        store.manual.edits[sp] = Math.round((curU[sp] + du) * 100) / 100;
      }
    }
  }

  store.manual.loadedPointId = point.candidate_id;
  store.manual.preview = null;
  switchTab("manual");
  triggerManualPreviewDebounced(true);
}

// -----------------------------------------------------------------------------
// Вкладка «Журнал смены» (§8)
// -----------------------------------------------------------------------------
async function initShiftTab() {
  const container = document.getElementById("shift-report-container");
  if (!container) return;
  container.innerHTML = "Формирование отчета сдачи смены...";
  try {
    const res = await api.shiftReport();
    container.innerHTML = renderMarkdown(res.markdown);
  } catch (e) {
    container.innerHTML = `<span style="color: var(--limit);">Ошибка: ${e.message}</span>`;
  }
}

// -----------------------------------------------------------------------------
// Вкладка «Супервизор» (F4, agents/console_tz/03_STREAMLIT_MIGRATION_PLAN.md §7)
//
// Бэкенд — прямые эндпоинты корневого API (main.py), не /api/console/*: находки и запросы
// на изменение политики не привязаны к сессии/такту консоли, а к всему процессу супервизора.
// Q&A («Спросить систему») и «Сдать смену» намеренно не дублируются здесь — они уже есть на
// вкладках «Обзор» и «Журнал смены» соответственно.
// -----------------------------------------------------------------------------

const SUPERVISOR_SEVERITY_META = {
  CRITICAL: { label: "CRITICAL", cls: "sev-critical" },
  WARNING: { label: "WARNING", cls: "sev-warning" },
  INFO: { label: "INFO", cls: "sev-info" },
};

function svNum(v, decimals = 4) {
  return v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(decimals);
}

async function initSupervisorTab() {
  if (store.supervisor.loading) return;
  store.supervisor.loading = true;
  try {
    const [findings, changeRequests] = await Promise.all([
      api.getSupervisorFindings(),
      api.getChangeRequests(),
    ]);
    store.supervisor.findings = findings || [];
    store.supervisor.changeRequests = changeRequests || [];
    store.supervisor.loaded = true;
  } catch (e) {
    console.error("Supervisor panel load failed:", e);
    showToast("Ошибка загрузки панели супервизора", e.message, "error");
    // Не оставлять список в состоянии "Загрузка..." навсегда — показываем пустые списки,
    // ошибка уже сообщена тостом; повторная попытка — кнопка «Обновить».
    store.supervisor.loaded = true;
  } finally {
    store.supervisor.loading = false;
  }
  renderSupervisor();
}

function renderSupervisor() {
  renderSupervisorFindings();
  renderSupervisorChangeRequests();
}

function renderSupervisorFindings() {
  const container = document.getElementById("supervisor-findings-list");
  if (!container) return;
  const findings = store.supervisor.findings;

  if (!store.supervisor.loaded) {
    container.innerHTML = '<div class="supervisor-empty">Загрузка...</div>';
    return;
  }
  if (!findings || findings.length === 0) {
    container.innerHTML = '<div class="supervisor-empty">Открытых диагностических инцидентов не зафиксировано</div>';
    return;
  }

  container.innerHTML = findings.map((f) => {
    const sev = SUPERVISOR_SEVERITY_META[f.severity] || { label: f.severity || "—", cls: "sev-info" };
    const expanded = !!store.supervisor.expandedFindings[f.finding_id];
    let bodyHtml = "";
    if (expanded) {
      const checksHtml = (f.checks_recommended && f.checks_recommended.length)
        ? `<div class="supervisor-finding-block">
             <span class="supervisor-finding-label">Рекомендованные проверки</span>
             <ul class="supervisor-finding-ul">${f.checks_recommended.map((c) => `<li>${c}</li>`).join("")}</ul>
           </div>`
        : "";
      const evidenceHtml = (f.evidence_refs && f.evidence_refs.length)
        ? `<div class="supervisor-finding-block">
             <span class="supervisor-finding-label">Ссылки на данные</span>
             <ul class="supervisor-finding-ul">${f.evidence_refs.map((r) => `<li class="mono">${r}</li>`).join("")}</ul>
           </div>`
        : "";
      bodyHtml = `
        <div class="supervisor-finding-body">
          <div class="supervisor-finding-block">
            <span class="supervisor-finding-label">Первопричина</span>
            <p>${f.root_cause ?? "—"}</p>
          </div>
          <div class="supervisor-finding-block">
            <span class="supervisor-finding-label">Оценка риска безопасности</span>
            <p>${f.safety_risk_assessment ?? "—"}</p>
          </div>
          ${checksHtml}
          ${evidenceHtml}
        </div>`;
    }
    return `
      <div class="supervisor-finding-card">
        <button type="button" class="supervisor-finding-header" data-toggle-finding="${f.finding_id}">
          <span class="supervisor-severity-badge ${sev.cls}">${sev.label}</span>
          <span class="supervisor-finding-title">${f.title ?? "—"}</span>
          <span class="mono supervisor-finding-id">${f.finding_id}</span>
          <span class="supervisor-finding-status">${f.status ?? "—"}</span>
          <span class="supervisor-finding-caret">${expanded ? "▲" : "▼"}</span>
        </button>
        ${bodyHtml}
      </div>
    `;
  }).join("");

  container.querySelectorAll("[data-toggle-finding]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.toggleFinding;
      store.supervisor.expandedFindings[id] = !store.supervisor.expandedFindings[id];
      renderSupervisorFindings();
    });
  });
}

function renderSupervisorChangeRequests() {
  const container = document.getElementById("supervisor-cr-list");
  if (!container) return;
  const items = store.supervisor.changeRequests;

  if (!store.supervisor.loaded) {
    container.innerHTML = '<div class="supervisor-empty">Загрузка...</div>';
    return;
  }
  if (!items || items.length === 0) {
    container.innerHTML = '<div class="supervisor-empty">Запросов на изменение политики нет</div>';
    return;
  }

  container.innerHTML = items.map((r) => {
    const proposal = r.proposal || {};
    const rowsHtml = (proposal.items || []).map((it) => `
      <div class="supervisor-cr-item-row">
        <span class="mono supervisor-cr-item-field">${it.field}</span>
        <span class="mono">${svNum(it.old_value)} → ${svNum(it.new_value)}</span>
        <span class="supervisor-cr-justification">${it.justification ?? ""}</span>
      </div>
    `).join("");
    const isPending = r.status === "PENDING_APPROVAL";
    const isDeciding = !!store.supervisor.deciding[r.request_id];
    return `
      <div class="supervisor-cr-card">
        <div class="supervisor-cr-header">
          <span class="mono supervisor-cr-id">${r.request_id}</span>
          <span class="supervisor-cr-status status-${(r.status || "").toLowerCase()}">${r.status ?? "—"}</span>
        </div>
        <div class="supervisor-cr-meta">
          <span><b>Ожидаемый эффект:</b> ${proposal.expected_kpi_impact ?? "—"}</span>
          <span><b>Проверка в shadow-режиме:</b> ${r.shadow_passed ? "✅" : "❌"}</span>
        </div>
        <div class="supervisor-cr-items">
          ${rowsHtml || '<span class="supervisor-empty-inline">Нет изменений в предложении</span>'}
        </div>
        ${isPending ? `
        <div class="supervisor-cr-actions">
          <button type="button" class="btn-cr-approve" data-approve="${r.request_id}" ${isDeciding ? "disabled" : ""}>Утвердить</button>
          <button type="button" class="btn-cr-reject" data-reject="${r.request_id}" ${isDeciding ? "disabled" : ""}>Отклонить</button>
        </div>` : ""}
      </div>
    `;
  }).join("");

  container.querySelectorAll("[data-approve]").forEach((btn) => {
    btn.addEventListener("click", () => handleSupervisorDecision(btn.dataset.approve, "approve"));
  });
  container.querySelectorAll("[data-reject]").forEach((btn) => {
    btn.addEventListener("click", () => handleSupervisorDecision(btn.dataset.reject, "reject"));
  });
}

async function handleSupervisorDecision(requestId, action) {
  const user = "Оператор консоли";
  store.supervisor.deciding[requestId] = true;
  renderSupervisorChangeRequests();
  try {
    if (action === "approve") {
      await api.approveChangeRequest(requestId, user);
      showToast("Запрос утверждён", requestId);
    } else {
      await api.rejectChangeRequest(requestId, user);
      showToast("Запрос отклонён", requestId);
    }
    store.supervisor.changeRequests = await api.getChangeRequests();
  } catch (e) {
    showToast("Ошибка обработки запроса", e.message, "error");
  } finally {
    delete store.supervisor.deciding[requestId];
    renderSupervisorChangeRequests();
  }
}

// Рендеринг ленты действий (строго последние 5 сообщений)
function renderFeed() {
  const container = document.getElementById("feed-items-container");
  if (!container || !store.state?.feed) return;

  const items = store.state.feed.slice(0, 5);

  const html = items.map((item) => {
    let iconSvg = "";
    if (item.kind === "shield") {
      iconSvg = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#1D5AA6" stroke-width="2.2"><path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"></path></svg>`;
    } else if (item.kind === "ok") {
      iconSvg = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#17784F" stroke-width="2.5"><path d="M5 12.5l4.5 4.5L19 7.5"></path></svg>`;
    } else if (item.kind === "veto") {
      iconSvg = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#B3261E" stroke-width="2.2"><circle cx="12" cy="12" r="9"></circle><path d="M6 6l12 12"></path></svg>`;
    } else if (item.kind === "warn") {
      iconSvg = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#8A5A0B" stroke-width="2.2"><path d="M12 3l10 18H2z"></path><path d="M12 10v5"></path></svg>`;
    } else {
      iconSvg = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#4A4E53" stroke-width="2.2"><circle cx="12" cy="12" r="8"></circle><path d="M12 11v5"></path><path d="M12 8v.5"></path></svg>`;
    }

    return `
      <div class="feed-row">
        <span class="mono" style="font-size: 14px; color: var(--ink-2); padding-top: 1px;">${item.t}</span>
        ${iconSvg}
        <span style="font-size: 15px; line-height: 1.35;">${item.text}</span>
      </div>
    `;
  }).join("");

  setHtmlIfChanged(container, html);
}

// Отображение всплывающих уведомлений (Toast)
function showToast(message, detail = "", type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = `toast-notification ${type === "warn" ? "toast-warn" : (type === "error" ? "toast-error" : "")}`;

  const iconSvg = type === "error"
    ? `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#B3261E" stroke-width="2.2"><circle cx="12" cy="12" r="9"></circle><path d="M6 6l12 12"></path></svg>`
    : `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#17784F" stroke-width="2.2"><path d="M5 13l4 4L19 7"></path></svg>`;

  toast.innerHTML = `
    ${iconSvg}
    <div style="display:flex;flex-direction:column;gap:2px;">
      <span style="font-weight:600;">${message}</span>
      ${detail ? `<span style="font-size:13px;color:#D4D5D1;">${detail}</span>` : ""}
    </div>
  `;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(20px)";
    setTimeout(() => toast.remove(), 300);
  }, 4500);
}

// -----------------------------------------------------------------------------
// Вкладка «Блендинг» (F2, agents/console_tz/03_STREAMLIT_MIGRATION_PLAN.md §5)
//
// Важные ограничения бэкенда (см. STATUS.md волна 8 «B2», QUESTIONS.md [B2]):
// - GET /blending НИЧЕГО не пересчитывает (снимок последнего такта графа). Вызывается
//   ТОЛЬКО при открытии вкладки/на каждый новый такт — без ручных правок оператора.
// - POST /blending/preview даже с пустыми overrides с высокой вероятностью вернёт
//   feasible=false (захардкоженная сигма серы в build_blend_problem, известное
//   ограничение модели — не баг фронтенда). Поэтому preview НИКОГДА не запускается
//   автоматически при открытии вкладки — только по явному действию оператора
//   (степпер/кнопка «Пересчитать»). feasible=false рендерится как нейтральная
//   подсказка (.sandbox-info-box), а не как красная тревога.
// - Некоторые метрики сертификата (BlendSpecMetricDTO.value) могут быть null (граф
//   core_v3 отдаёт более бедный контракт, чем legacy) — рендерится как «—», не 0/NaN.
// -----------------------------------------------------------------------------

// Человекочитаемые подписи известных ключей ComponentTank.props (src/agents/tanks.py) —
// только оформление, набор и количество самих компонентов/свойств не хардкодятся:
// список компонентов и состав props берутся из ответа сервера как есть.
const BLEND_PROP_META = {
  S_ppm: { label: "Сера", unit: "ppm" },
  D15: { label: "Плотность (D15)", unit: "кг/м³" },
  T95: { label: "T95", unit: "°C" },
  E360: { label: "Выкипание до 360°C (E360)", unit: "% об." },
  CN: { label: "Цетановое число", unit: "" },
  CFPP: { label: "ПТФ (CFPP)", unit: "°C" },
  Flash: { label: "Вспышка", unit: "°C" },
};

let blendingDebounceTimer = null;

function blendFmtNum(v, decimals = 2) {
  return v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(decimals);
}

function blendingStatusLabel(status) {
  if (status === "FEASIBLE") return "РЕАЛИЗУЕМО";
  if (status === "INFEASIBLE_ELASTIC") return "НАРУШЕНИЕ (elastic)";
  if (status === "FAILED") return "НЕ РЕШЕНО";
  return status || "—";
}

// Переиспользует существующий компонент .cv-chip (ok/warn/alarm/nodata) вместо нового виджета.
function blendingStatusChipClass(status) {
  if (status === "FEASIBLE") return "ok";
  if (status === "FAILED") return "alarm";
  if (status === "INFEASIBLE_ELASTIC") return "warn";
  return "nodata";
}

async function initBlendingTab() {
  const currentTick = store.state?.clock?.tick;
  // Тот же фикс, что F0 сделал для Парето: пока такт неизвестен — не фетчим, refresh()
  // сам повторно вызовет initBlendingTab(), как только состояние подгрузится.
  if (currentTick == null) return;
  if (store.blending.loading) return;

  if (store.blending.loadedForTick !== currentTick) {
    store.blending.loading = true;
    try {
      store.blending.data = await api.getBlending();
      store.blending.loadedForTick = currentTick;
    } catch (e) {
      console.error("Blending load failed:", e);
      store.blending.loadedForTick = currentTick;
    } finally {
      store.blending.loading = false;
    }
  }
  renderBlending();
}

function renderBlending() {
  const data = store.blending.data;
  const emptyEl = document.getElementById("blending-empty");
  const contentEl = document.getElementById("blending-content");

  if (!data) {
    if (emptyEl) emptyEl.style.display = "flex";
    if (contentEl) contentEl.style.display = "none";
    return;
  }
  if (emptyEl) emptyEl.style.display = "none";
  if (contentEl) contentEl.style.display = "flex";

  renderBlendingComponents(data.components);

  const statusEl = document.getElementById("blending-cert-status");
  if (statusEl) {
    statusEl.textContent = blendingStatusLabel(data.cert.status);
    statusEl.className = `cv-chip ${blendingStatusChipClass(data.cert.status)}`;
  }
  renderBlendingCertRows(document.getElementById("blending-cert-tbody"), data.cert.metrics);

  const errEl = document.getElementById("blending-cert-error");
  if (errEl) {
    if (data.cert.error_message) {
      errEl.style.display = "block";
      errEl.textContent = data.cert.error_message;
    } else {
      errEl.style.display = "none";
    }
  }

  renderBlendingSandboxRows(data.components);
  renderBlendingPreview();
}

function renderBlendingComponents(components) {
  const container = document.getElementById("blending-components");
  if (!container) return;
  if (!components || components.length === 0) {
    container.innerHTML = '<div style="color: var(--ink-3); font-size: 14px;">Нет данных о резервуарах.</div>';
    return;
  }
  container.innerHTML = components.map((c) => {
    const propEntries = Object.entries(c.props || {});
    const propsHtml = propEntries.length
      ? propEntries.map(([key, val]) => {
          const meta = BLEND_PROP_META[key] || { label: key, unit: "" };
          return `<div class="blending-prop-row"><span>${meta.label}</span><span class="mono">${blendFmtNum(val, 2)} ${meta.unit}</span></div>`;
        }).join("")
      : '<span style="color: var(--ink-3); font-size: 13px;">нет данных о качестве</span>';
    return `
      <div class="blending-component-card">
        <div class="blending-component-title">${c.label} <span class="mono" style="font-size: 11px; color: var(--ink-3); font-weight: 400;">${c.id}</span></div>
        <div class="blending-component-stock">Остаток: <span class="mono">${blendFmtNum(c.stock_t, 1)}</span> т</div>
        <div class="blending-component-props">${propsHtml}</div>
      </div>
    `;
  }).join("");
}

// Таблица «показатель / план / лимит» — переиспользуется и для текущего сертификата, и для preview.
function renderBlendingCertRows(tbody, metrics) {
  if (!tbody) return;
  if (!metrics || metrics.length === 0) {
    tbody.innerHTML = `<tr><td colspan="3" style="color: var(--ink-3);">Нет данных сертификата</td></tr>`;
    return;
  }
  tbody.innerHTML = metrics.map((m) => {
    const senseSign = m.sense === "min" ? "≥" : (m.sense === "max" ? "≤" : "");
    const unitSuffix = m.unit ? ` ${m.unit}` : "";
    // Прочерк на null (README.md §5 / указание по BlendCertDTO) — никогда не рисуем null/NaN/undefined.
    const limitText = m.limit != null ? `${senseSign} ${blendFmtNum(m.limit, 1)}${unitSuffix}`.trim() : "—";
    const valueText = m.value != null ? `${blendFmtNum(m.value, 2)}${unitSuffix}`.trim() : "—";
    let violated = false;
    if (m.value != null && m.limit != null) {
      if (m.sense === "max" && m.value > m.limit) violated = true;
      if (m.sense === "min" && m.value < m.limit) violated = true;
    }
    return `
      <tr class="${violated ? "blending-row-violation" : ""}">
        <td>${m.label}</td>
        <td class="mono">${valueText}</td>
        <td class="mono">${limitText}</td>
      </tr>
    `;
  }).join("");
}

function renderBlendingSandboxRows(components) {
  const container = document.getElementById("blending-sandbox-rows");
  if (!container) return;
  if (!components || components.length === 0) {
    container.innerHTML = "";
    return;
  }

  container.innerHTML = components.map((c) => {
    const isEdited = store.blending.edits[c.id] != null;
    const target = isEdited ? store.blending.edits[c.id] : c.stock_t;
    return `
      <div class="blending-sandbox-row" data-tank="${c.id}">
        <span class="blending-sandbox-row-label">${c.label} <span class="mono" style="font-size: 11px; color: var(--ink-3); font-weight: 400;">${c.id}</span></span>
        <span class="mono blending-sandbox-current" title="Текущий остаток">${blendFmtNum(c.stock_t, 1)} т</span>
        <div class="value-stepper ${isEdited ? "edited" : ""}" id="stepper-blend-${c.id}">
          <button type="button" class="stepper-btn" data-dir="-1" aria-label="Уменьшить остаток">−</button>
          <input class="stepper-value mono" type="text" inputmode="decimal" value="${blendFmtNum(target, 1)}">
          <button type="button" class="stepper-btn" data-dir="+1" aria-label="Увеличить остаток">+</button>
        </div>
        <span class="blending-sandbox-unit">т остатка в резервуаре</span>
      </div>
    `;
  }).join("");

  components.forEach((c) => {
    const el = document.getElementById(`stepper-blend-${c.id}`);
    if (!el) return;
    bindStepper(el, {
      getValue: () => store.blending.edits[c.id],
      setValue: (val) => {
        if (val == null || Math.abs(val - c.stock_t) < 1e-5) {
          delete store.blending.edits[c.id];
        } else {
          store.blending.edits[c.id] = val;
        }
        el.classList.toggle("edited", store.blending.edits[c.id] != null);
      },
      onChange: () => {
        // Оператор реально что-то поменял — только тут можно звать /preview (см. шапку раздела).
        triggerBlendingPreviewDebounced();
      },
      step: 50,
      decimals: 1,
      min: 0,
    });
  });
}

function triggerBlendingPreviewDebounced(immediate = false) {
  clearTimeout(blendingDebounceTimer);
  const doFetch = async () => {
    const tankOverrides = {};
    for (const [tankId, stockT] of Object.entries(store.blending.edits)) {
      tankOverrides[tankId] = { stock_t: stockT };
    }
    try {
      store.blending.preview = await api.previewBlending(tankOverrides, {});
      renderBlendingPreview();
    } catch (e) {
      // Сетевая/серверная ошибка (не "инфизибл" — тот приходит как feasible=false в теле ответа
      // и рендерится нейтральной подсказкой, см. renderBlendingPreview). Настоящая ошибка — тост.
      console.error("Blending preview error:", e);
      showToast("Ошибка пересчёта блендинга", e.message || "", "error");
    }
  };
  if (immediate) {
    doFetch();
  } else {
    blendingDebounceTimer = setTimeout(doFetch, 250);
  }
}

function renderBlendingPreview() {
  const prev = store.blending.preview;
  const resultEl = document.getElementById("blending-preview-result");
  const infoEl = document.getElementById("blending-preview-info");

  if (!prev) {
    if (resultEl) resultEl.style.display = "none";
    if (infoEl) infoEl.style.display = "none";
    return;
  }

  if (resultEl) resultEl.style.display = "flex";

  const statusEl = document.getElementById("blending-preview-status");
  if (statusEl) {
    statusEl.textContent = blendingStatusLabel(prev.cert.status);
    statusEl.className = `cv-chip ${blendingStatusChipClass(prev.cert.status)}`;
  }

  renderBlendingCertRows(document.getElementById("blending-preview-cert-tbody"), prev.cert.metrics);

  if (infoEl) {
    // §5/QUESTIONS.md [B2]: feasible=false у preview (в т.ч. на пустых overrides) — известное
    // ограничение LP-модели, не ошибка ввода. Нейтральная подсказка, НЕ красная тревога.
    if (!prev.feasible) {
      infoEl.style.display = "block";
      infoEl.textContent = prev.error_message
        || "Рецепт не сходится по ограничениям LP-модели блендинга с текущими вводными.";
    } else {
      infoEl.style.display = "none";
    }
  }

  const costEl = document.getElementById("blending-eff-cost");
  const deltaEl = document.getElementById("blending-eff-delta");
  const baseCost = store.blending.data?.cert?.cost_per_ton;
  const newCost = prev.cert.cost_per_ton;
  if (costEl) costEl.textContent = newCost != null ? blendFmtNum(newCost, 0) : "—";
  if (deltaEl) {
    if (newCost != null && baseCost != null) {
      const d = newCost - baseCost;
      deltaEl.textContent = `${d >= 0 ? "+" : ""}${blendFmtNum(d, 0)}`;
      deltaEl.style.color = d > 0 ? "var(--limit)" : (d < 0 ? "var(--auto)" : "var(--ink)");
    } else {
      deltaEl.textContent = "—";
      deltaEl.style.color = "";
    }
  }
}

function initBlendingEvents() {
  document.getElementById("btn-blending-preview")?.addEventListener("click", () => {
    triggerBlendingPreviewDebounced(true);
  });
  document.getElementById("btn-blending-reset")?.addEventListener("click", () => {
    store.blending.edits = {};
    store.blending.preview = null;
    renderBlendingSandboxRows(store.blending.data?.components || []);
    renderBlendingPreview();
  });
}

// -----------------------------------------------------------------------------
// Вкладка «Константы системы»
// -----------------------------------------------------------------------------
async function initConstantsTab() {
  initEconomicsBlock();

  if (!store.constants.data) {
    store.constants.loading = true;
    try {
      store.constants.data = await api.getConstants();
    } catch (e) {
      console.error("Failed to load constants:", e);
    } finally {
      store.constants.loading = false;
    }
  }

  // Привязка поиска и фильтров
  const searchInput = document.getElementById("constants-search");
  if (searchInput && !searchInput.dataset.bound) {
    searchInput.dataset.bound = "true";
    searchInput.addEventListener("input", (e) => {
      store.constants.searchQuery = e.target.value.toLowerCase().trim();
      renderConstants();
    });
  }

  const filterContainer = document.getElementById("constants-cat-filters");
  if (filterContainer && !filterContainer.dataset.bound) {
    filterContainer.dataset.bound = "true";
    filterContainer.querySelectorAll(".constants-cat-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        filterContainer.querySelectorAll(".constants-cat-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        store.constants.activeCategory = btn.dataset.cat;
        renderConstants();
      });
    });
  }

  renderConstants();
}

function renderConstants() {
  const tbody = document.getElementById("constants-tbody");
  if (!tbody) return;

  const data = store.constants.data || [];
  if (data.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--ink-muted);">Загрузка констант системы...</td></tr>';
    return;
  }

  const query = store.constants.searchQuery || "";
  const catFilter = store.constants.activeCategory || "all";

  // Фильтрация
  const filtered = data.filter((c) => {
    if (catFilter !== "all") {
      if (catFilter === "T0" && !c.category.startsWith("T0")) return false;
      if (catFilter === "T1" && !c.category.startsWith("T1")) return false;
      if (catFilter === "T2" && !c.category.startsWith("T2")) return false;
      if (catFilter === "T3" && !c.category.startsWith("T3")) return false;
      if (catFilter === "POLICY" && !c.category.includes("Политика")) return false;
    }
    if (query) {
      const matchKey = (c.key || "").toLowerCase().includes(query);
      const matchLabel = (c.label || "").toLowerCase().includes(query);
      const matchRef = (c.source_ref || "").toLowerCase().includes(query);
      const matchDesc = (c.description || "").toLowerCase().includes(query);
      const matchCat = (c.category || "").toLowerCase().includes(query);
      if (!matchKey && !matchLabel && !matchRef && !matchDesc && !matchCat) return false;
    }
    return true;
  });

  if (filtered.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--ink-muted);">Константы по заданному фильтру не найдены</td></tr>';
    return;
  }

  // Группировка по категориям
  let currentCategory = "";
  let html = "";

  filtered.forEach((c) => {
    if (c.category !== currentCategory) {
      currentCategory = c.category;
      html += `
        <tr class="constants-category-row">
          <td colspan="7">${currentCategory}</td>
        </tr>
      `;
    }

    let provClass = "assumption";
    if (c.provenance === "NORM") provClass = "norm";
    else if (c.provenance === "POLICY") provClass = "policy";
    else if (c.provenance === "REGISTRY") provClass = "registry";

    const formattedVal = typeof c.value === "number" ? c.value.toLocaleString("ru-RU") : c.value;

    html += `
      <tr>
        <td class="mono" style="font-weight:600;font-size:13px;color:var(--ink);">${c.key}</td>
        <td style="font-weight:500;">${c.label}</td>
        <td class="mono" style="text-align:right;font-weight:700;font-size:15px;">${formattedVal}</td>
        <td class="mono" style="color:var(--ink-3);font-size:13px;">${c.unit}</td>
        <td><span class="provenance-pill ${provClass}">${c.provenance}</span></td>
        <td style="font-size:13px;color:var(--ink-2);">${c.source_ref}</td>
        <td style="font-size:13px;color:var(--ink-3);">${c.description}</td>
      </tr>
    `;
  });

  tbody.innerHTML = html;
}

// -----------------------------------------------------------------------------
// Блок «Цены и тарифы» во вкладке «Константы» (Ф3, 03_STREAMLIT_MIGRATION_PLAN.md §6)
//
// Единственный редактируемый блок этой вкладки — 5 полей EconomicsOverrideDTO
// (GET/POST /api/console/economics, роль B3). Подписи, шаг и диапазон ввода — как в
// удалённом src/ui/app.py (боковая панель «Параметры рынка и тарифов», секция «Сырье и
// дистилляты СПбМТСБ»), не придуманы заново (см. git show HEAD:src/ui/app.py:135-179).
// is_override в DTO — один общий флаг на все 5 цен (допущение B3, см. STATUS.md волна 8),
// поэтому бейдж «изменено оператором» рисуется на весь блок, а не на отдельные поля.
// -----------------------------------------------------------------------------
const ECONOMICS_FIELDS = [
  { key: "price_godt", label: "ГО ДТ Евро-5", tag: "price_godt", unit: "руб/т", step: 500, min: 30000, max: 120000 },
  { key: "price_straight_run", label: "Прямогонный дизель F30+F32", tag: "price_straight_run", unit: "руб/т", step: 500, min: 25000, max: 100000 },
  { key: "price_crude_oil", label: "Сырая нефть Urals", tag: "price_crude_oil", unit: "руб/т", step: 500, min: 20000, max: 80000 },
  { key: "price_kerosene", label: "Керосин ТС-1", tag: "price_kerosene", unit: "руб/т", step: 500, min: 40000, max: 150000 },
  { key: "price_gasoil", label: "Газойль вторичный", tag: "price_gasoil", unit: "руб/т", step: 500, min: 25000, max: 100000 },
];

async function initEconomicsBlock() {
  if (!store.economics.data && !store.economics.loading) {
    store.economics.loading = true;
    try {
      store.economics.data = await api.getEconomics();
    } catch (e) {
      console.error("Failed to load economics:", e);
      showToast("Не удалось загрузить цены и тарифы", e.message || "", "error");
    } finally {
      store.economics.loading = false;
    }
  }

  renderEconomicsFields();

  const applyBtn = document.getElementById("btn-economics-apply");
  if (applyBtn && !applyBtn.dataset.bound) {
    applyBtn.dataset.bound = "true";
    applyBtn.addEventListener("click", applyEconomics);
  }
}

function renderEconomicsFields() {
  const grid = document.getElementById("economics-fields-grid");
  const badge = document.getElementById("economics-override-badge");
  if (!grid) return;

  const data = store.economics.data;
  if (badge) badge.style.display = data?.is_override ? "inline-block" : "none";

  if (!data) {
    grid.innerHTML = '<span style="font-size: 13px; color: var(--ink-3);">Загрузка цен...</span>';
    return;
  }

  grid.innerHTML = ECONOMICS_FIELDS.map((f) => {
    const isEdited = store.economics.edits[f.key] != null;
    const val = isEdited ? store.economics.edits[f.key] : data[f.key];
    return `
      <div class="economics-field">
        <span class="economics-field-label">${f.label} <span class="mono" style="font-size: 11px; color: var(--ink-3); font-weight: 400;">${f.unit}</span></span>
        <div class="value-stepper ${isEdited ? "edited" : ""}" id="stepper-econ-${f.key}">
          <button type="button" class="stepper-btn" data-dir="-1" aria-label="Уменьшить">−</button>
          <input class="stepper-value mono" type="text" inputmode="decimal" value="${val.toFixed(0)}">
          <button type="button" class="stepper-btn" data-dir="+1" aria-label="Увеличить">+</button>
        </div>
      </div>
    `;
  }).join("");

  ECONOMICS_FIELDS.forEach((f) => {
    const el = document.getElementById(`stepper-econ-${f.key}`);
    if (!el) return;
    bindStepper(el, {
      getValue: () => store.economics.edits[f.key],
      setValue: (val) => {
        if (val == null || Math.abs(val - data[f.key]) < 1e-6) {
          delete store.economics.edits[f.key];
        } else {
          store.economics.edits[f.key] = val;
        }
        el.classList.toggle("edited", store.economics.edits[f.key] != null);
      },
      onChange: () => {},
      step: f.step,
      decimals: 0,
      min: f.min,
      max: f.max,
    });
  });
}

async function applyEconomics() {
  const applyBtn = document.getElementById("btn-economics-apply");
  const hintEl = document.getElementById("economics-hint");
  const errEl = document.getElementById("economics-error");
  if (hintEl) hintEl.style.display = "none";
  if (errEl) errEl.style.display = "none";

  // Отправляем все 5 текущих значений формы (не только правки) — POST мёрджит частичный
  // словарь поверх session.economics_override, отправка неизменённых полей идемпотентна.
  const prices = {};
  const data = store.economics.data || {};
  ECONOMICS_FIELDS.forEach((f) => {
    const raw = store.economics.edits[f.key] != null ? store.economics.edits[f.key] : data[f.key];
    prices[f.key] = Number(raw);
  });

  if (applyBtn) applyBtn.disabled = true;
  store.economics.applying = true;
  try {
    store.economics.data = await api.updateEconomics(prices);
    store.economics.edits = {};
    renderEconomicsFields();
    if (hintEl) hintEl.style.display = "inline";
    showToast("Цены и тарифы обновлены", "Изменение вступит в силу со следующего такта.", "info");
  } catch (e) {
    console.error("Economics update error:", e);
    if (errEl) {
      errEl.textContent = e.message || "Ошибка обновления цен";
      errEl.style.display = "inline";
    }
    showToast("Ошибка обновления цен и тарифов", e.message || "", "error");
  } finally {
    if (applyBtn) applyBtn.disabled = false;
    store.economics.applying = false;
  }
}

// Отображение модального окна
function showModal(title, htmlContent) {
  const overlay = document.getElementById("modal-overlay");
  const titleEl = document.getElementById("modal-title");
  const contentEl = document.getElementById("modal-content");
  if (overlay && titleEl && contentEl) {
    titleEl.textContent = title;
    contentEl.innerHTML = htmlContent;
    overlay.style.display = "flex";
  }
}

function renderMarkdown(md) {
  if (!md) return "";
  return md
    .replace(/^### (.*$)/gim, "<h3>$1</h3>")
    .replace(/^## (.*$)/gim, "<h2>$1</h2>")
    .replace(/^# (.*$)/gim, "<h1>$1</h1>")
    .replace(/^\> (.*$)/gim, "<blockquote>$1</blockquote>")
    .replace(/\*\*(.*)\*\*/gim, "<b>$1</b>")
    .replace(/\*(.*)\*/gim, "<i>$1</i>")
    .replace(/\n/gim, "<br>");
}

// Выбор сценария работы в шапке пульта (всегда виден, не требует ?demo=1)
function initScenarioSelect() {
  const select = document.getElementById("scenario-select");
  if (!select) return;

  Object.entries(SCENARIO_TITLES).forEach(([id, title]) => {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = `${id} · ${SCENARIO_SHORT_NAMES[id] || ""}`;
    opt.title = title;
    select.appendChild(opt);
  });

  select.addEventListener("change", async () => {
    if (!select.value) return;
    const scenarioId = select.value;
    select.disabled = true;
    select.title = SCENARIO_TITLES[scenarioId] || "";
    showToast(`Загрузка сценария ${scenarioId}`, "Прогрев модели, это может занять до минуты…");
    try {
      await api.demo.load(scenarioId);
      await refresh();
      showToast(`Сценарий ${scenarioId} загружен`, SCENARIO_TITLES[scenarioId] || "");
    } catch (err) {
      console.error("Не удалось загрузить сценарий:", err);
      showToast("Не удалось загрузить сценарий", err.message || "", "error");
    } finally {
      select.disabled = false;
    }
  });
}

// Инициализация демо-панели ?demo=1 (инъекция неисправностей КИПиА)
function initDemoPanel() {
  const panel = document.getElementById("demo-host-panel");
  if (!panel) return;
  panel.style.display = "flex";

  document.getElementById("btn-demo-fault-q21")?.addEventListener("click", async () => {
    await api.demo.fault("HT_Q21", "nan", 26.0);
    await refresh();
  });

  document.getElementById("btn-demo-clear-faults")?.addEventListener("click", async () => {
    await api.demo.fault("HT_Q21", "clear", 1.0);
    await refresh();
  });
}
