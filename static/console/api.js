/**
 * Модуль API-клиента для пульта старшего оператора (R4).
 * Поддерживает режим ?fixture=advisory|auto|refusal для автономного тестирования.
 */

const params = new URLSearchParams(window.location.search);
const FIXTURE_MODE = params.get("fixture");
const SESSION_ID = params.get("session") || "demo";
export { SESSION_ID };

export async function getState() {
  if (FIXTURE_MODE) {
    const res = await fetch(`/fixtures/${FIXTURE_MODE}.json`);
    if (!res.ok) throw new Error(`Ошибка загрузки фикстуры ${FIXTURE_MODE}`);
    return await res.json();
  }
  const res = await fetch(`/api/console/state?session_id=${encodeURIComponent(SESSION_ID)}`);
  if (!res.ok) throw new Error(`Ошибка получения состояния: ${res.status}`);
  return await res.json();
}

export async function preview(uTarget, cycleId = null) {
  if (FIXTURE_MODE) {
    // Если шаг по T6 > 3, отдаем preview_blocked
    const t6Val = uTarget["HT_TIN_SP"];
    if (t6Val && Math.abs(t6Val - 363.3) > 3.0) {
      const res = await fetch("/fixtures/preview_blocked.json");
      return await res.json();
    }
    const res = await fetch("/fixtures/preview_ok.json");
    return await res.json();
  }

  const res = await fetch("/api/console/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: SESSION_ID,
      cycle_id: cycleId,
      u_target: uTarget,
    }),
  });
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    throw new Error(errData.detail?.text || `Ошибка preview: ${res.status}`);
  }
  return await res.json();
}

export async function commit(req) {
  if (FIXTURE_MODE) {
    return {
      ok: true,
      applied: [],
      at: new Date().toISOString(),
      feed_item: {
        id: "mock_commit",
        t: "12:40",
        kind: "ok",
        text: "Оператор применил уставки (режим фикстуры)",
        source: "operator",
      },
    };
  }

  const res = await fetch("/api/console/commit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: SESSION_ID,
      ...req,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = err.detail || {};
    const e = new Error(detail.text || "Ошибка применения уставок");
    e.status = res.status;
    e.detail = detail;
    throw e;
  }
  return await res.json();
}

export async function reject(cycleId = null, reason = null) {
  if (FIXTURE_MODE) return { ok: true };
  const res = await fetch("/api/console/reject", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: SESSION_ID,
      cycle_id: cycleId,
      reason,
    }),
  });
  if (!res.ok) throw new Error("Ошибка отклонения решения");
  return await res.json();
}

export async function setMode(mode) {
  if (FIXTURE_MODE) {
    return {
      current: mode,
      auto_available: true,
      auto_unavailable_reason: null,
      auto_since: mode === "AUTO" ? new Date().toISOString() : null,
      last_auto_exit: null,
    };
  }

  const res = await fetch("/api/console/mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: SESSION_ID,
      mode,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = err.detail || {};
    const e = new Error(detail.text || "Ошибка переключения режима");
    e.detail = detail;
    throw e;
  }
  return await res.json();
}

export async function skip() {
  if (FIXTURE_MODE) return {};
  const res = await fetch("/api/console/auto/skip", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: SESSION_ID }),
  });
  if (!res.ok) throw new Error("Ошибка пропуска шага автомата");
  return await res.json();
}

export async function ack(id) {
  if (FIXTURE_MODE) return { ok: true };
  const res = await fetch("/api/console/ack", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: SESSION_ID, id }),
  });
  return await res.json();
}

export async function ask(question) {
  if (FIXTURE_MODE) {
    return {
      answer_markdown: "В режиме фикстуры LLM-супервизор недоступен.",
      sources: [],
    };
  }
  const res = await fetch("/api/console/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: SESSION_ID, question }),
  });
  if (!res.ok) throw new Error("Супервизор временно недоступен (503)");
  return await res.json();
}

export async function shiftReport() {
  if (FIXTURE_MODE) {
    return {
      markdown: "### Сменный рапорт (демо фикстура)\nСмена сдана без замечаний.",
      generated_at: new Date().toISOString(),
    };
  }
  const res = await fetch(`/api/console/shift-report?session_id=${encodeURIComponent(SESSION_ID)}`);
  if (!res.ok) throw new Error("Ошибка формирования сменного рапорта");
  return await res.json();
}

export async function getPareto() {
  if (FIXTURE_MODE) {
    const res = await fetch("/fixtures/pareto.json");
    if (!res.ok) return null;
    return await res.json();
  }
  const res = await fetch(`/api/console/pareto?session_id=${encodeURIComponent(SESSION_ID)}`);
  if (!res.ok) throw new Error(`Ошибка получения Парето-фронта: ${res.status}`);
  const data = await res.json();
  return data;
}

export async function getXaiReport(choice = "balanced") {
  if (FIXTURE_MODE) {
    return { available: false, reason: "no_key", report_markdown: null, choice };
  }
  const res = await fetch(
    `/api/console/xai/report?session_id=${encodeURIComponent(SESSION_ID)}&choice=${encodeURIComponent(choice)}`
  );
  if (!res.ok) throw new Error(`Ошибка получения LLM-отчёта XAI: ${res.status}`);
  return await res.json();
}

export async function getConstants() {
  if (FIXTURE_MODE) {
    const res = await fetch("/fixtures/constants.json");
    if (!res.ok) return [];
    return await res.json();
  }
  const res = await fetch(`/api/console/constants?session_id=${encodeURIComponent(SESSION_ID)}`);
  if (!res.ok) throw new Error(`Ошибка получения констант: ${res.status}`);
  return await res.json();
}

// Ф3 (Цены и тарифы, 03_STREAMLIT_MIGRATION_PLAN.md §6): пять рыночных цен во вкладке
// «Константы», редактируемых оператором live-сессии. Бэкенд (роль B3) — GET/POST
// /api/console/economics, тело EconomicsUpdateRequest{session_id, prices}, ответ
// EconomicsOverrideDTO (5 цен + общий признак is_override). Фикстур для этого блока нет
// (не в периметре роли B0) — в режиме ?fixture= отдаём дефолты EconomicsParams инлайн, как
// это уже делают setMode()/commit() выше.
export async function getEconomics() {
  if (FIXTURE_MODE) {
    return {
      price_godt: 68000.0,
      price_straight_run: 52000.0,
      price_crude_oil: 41500.0,
      price_kerosene: 88000.0,
      price_gasoil: 52000.0,
      is_override: false,
    };
  }
  const res = await fetch(`/api/console/economics?session_id=${encodeURIComponent(SESSION_ID)}`);
  if (!res.ok) throw new Error(`Ошибка получения цен и тарифов: ${res.status}`);
  return await res.json();
}

export async function updateEconomics(prices) {
  if (FIXTURE_MODE) {
    return { ...prices, is_override: true };
  }
  const res = await fetch("/api/console/economics", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: SESSION_ID, prices }),
  });
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    const detail = errData.detail;
    const text = typeof detail === "string" ? detail : detail?.text;
    throw new Error(text || `Ошибка обновления цен: ${res.status}`);
  }
  return await res.json();
}

// F2 (Блендинг): офлайн-фикстура для этой вкладки ещё не создана (не в периметре роли F2,
// см. STATUS.md/03_STREAMLIT_MIGRATION_PLAN.md §5 — фикстуры пульта владеет роль B0/R0).
// TODO(любая роль с доступом к scripts/make_console_fixtures.py): добавить
// static/console/fixtures/blending.json и включить сюда ветку FIXTURE_MODE по аналогии
// с getPareto(). До этого функция всегда бьёт в реальный API.
export async function getBlending() {
  const res = await fetch(`/api/console/blending?session_id=${encodeURIComponent(SESSION_ID)}`);
  if (!res.ok) throw new Error(`Ошибка получения состояния блендинга: ${res.status}`);
  return await res.json(); // может быть null
}

export async function previewBlending(tankOverrides = {}, priceOverrides = {}) {
  const res = await fetch("/api/console/blending/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: SESSION_ID,
      tank_overrides: tankOverrides,
      price_overrides: priceOverrides,
    }),
  });
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    throw new Error(errData.detail?.text || errData.detail || `Ошибка preview блендинга: ${res.status}`);
  }
  return await res.json();
}

// F4 (Супервизор, 03_STREAMLIT_MIGRATION_PLAN.md §7): панель LLM-супервизора — диагностические
// находки и HITL-запросы на изменение технологической политики. Бэкенд — прямые эндпоинты
// корневого API (main.py: GET/POST /api/v1/supervisor/*), НЕ через /api/console/*: эти пути не
// поддерживают офлайн-режим ?fixture= остальных обёрток этого файла (нет per-session контекста),
// поэтому здесь обычный fetch с тем же origin (порт 8000, CORS не нужен) и обработкой не-200
// ответа, без отдельной фикстурной ветки.
export async function getSupervisorFindings(status = null) {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  const res = await fetch(`/api/v1/supervisor/findings${qs}`);
  if (!res.ok) throw new Error(`Ошибка получения диагностических находок: ${res.status}`);
  return await res.json();
}

export async function getChangeRequests(status = null) {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  const res = await fetch(`/api/v1/supervisor/change-requests${qs}`);
  if (!res.ok) throw new Error(`Ошибка получения запросов на изменение политики: ${res.status}`);
  return await res.json();
}

export async function approveChangeRequest(requestId, user) {
  const res = await fetch(`/api/v1/supervisor/change-requests/${encodeURIComponent(requestId)}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user }),
  });
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    const detail = errData.detail;
    const text = typeof detail === "string" ? detail : detail?.text;
    throw new Error(text || `Ошибка утверждения запроса: ${res.status}`);
  }
  return await res.json();
}

export async function rejectChangeRequest(requestId, user) {
  const res = await fetch(`/api/v1/supervisor/change-requests/${encodeURIComponent(requestId)}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user }),
  });
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    const detail = errData.detail;
    const text = typeof detail === "string" ? detail : detail?.text;
    throw new Error(text || `Ошибка отклонения запроса: ${res.status}`);
  }
  return await res.json();
}

export const demo = {
  load: async (scenario, warmupTicks = 48) => {
    const res = await fetch("/api/console/demo/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: SESSION_ID, scenario, warmup_ticks: warmupTicks }),
    });
    return await res.json();
  },
  speed: async (secondsPerTick) => {
    const res = await fetch("/api/console/demo/speed", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: SESSION_ID, seconds_per_tick: secondsPerTick }),
    });
    return await res.json();
  },
  tick: async (n = 1) => {
    // Раньше здесь не было проверки res.ok: неуспешный ответ (напр. 5xx при заторе тактов,
    // см. src/console/runtime.py::TICK_EXECUTOR) молча возвращался как обычный JSON, и вызывающий
    // код (app.js::executeCommit — вызывает demo.tick(1) сразу после commit(), чтобы такт
    // подхватил только что применённые уставки в mv_sp_history) не мог отличить настоящий успех
    // от ошибки — отсюда репорт «применил, тост про успех есть, а стрелка/значение не сдвинулись».
    const res = await fetch("/api/console/demo/tick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: SESSION_ID, n }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail?.text || err.detail || `Ошибка продвижения такта: ${res.status}`);
    }
    return await res.json();
  },
  fault: async (tag, faultType, limsAgeHours = null) => {
    const res = await fetch("/api/console/demo/fault", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: SESSION_ID, tag, fault_type: faultType, lims_age_hours: limsAgeHours }),
    });
    return await res.json();
  },
};

export const lims = {
  order: async (delayHours = null) => {
    const res = await fetch("/api/console/lims/order", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: SESSION_ID, delay_hours: delayHours }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail?.text || "Не удалось заказать анализ ЛИМС");
    }
    return await res.json();
  },
  manual: async (prop, value) => {
    const res = await fetch("/api/console/lims/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: SESSION_ID, prop, value }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail?.text || "Не удалось сохранить результат ЛИМС");
    }
    return await res.json();
  },
};
