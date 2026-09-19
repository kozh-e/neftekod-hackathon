# 01. Контракт API пульта (v1)

Единственный источник правды для backend (R1–R3) и frontend (R4–R5).
R0 переносит эти схемы в `src/console/contracts.py` (pydantic v2, `model_config = ConfigDict(frozen=True, extra="forbid")`)
и генерирует фикстуры. Изменение контракта — **только через R0** и с записью в раздел «История изменений» внизу.

Все времена — ISO 8601 в UTC (`2026-09-19T12:40:00Z`), модельное время симулятора.
Все числа — в единицах UI: ppm, °C, кПа, МПа, т/ч, нм³/м³, ₽/ч.
Отсутствие данных — `null`, **никогда** не 0 и не NaN.

---

## 1. Эндпоинты

Префикс роутера: `/api/console`. Сессия — query/body-параметр `session_id` (по умолчанию `"demo"`).

| Метод | Путь | Тело | Ответ | Ошибки |
|---|---|---|---|---|
| GET | `/state` | — | `ConsoleState` | 404 нет сессии |
| POST | `/preview` | `PreviewRequest` | `PreviewResult` | 422 неизвестный SP |
| POST | `/commit` | `CommitRequest` | `CommitResult` | **409** ядро/коридор/устаревшая рекомендация |
| POST | `/reject` | `{session_id, cycle_id, reason?}` | `{ok: true}` | 409 cycle устарел |
| POST | `/mode` | `{session_id, mode: "ADVISORY"\|"AUTO"}` | `ModeInfo` | **409** автомат недоступен (+ `reason`) |
| POST | `/auto/skip` | `{session_id}` | `AutoInfo` | 409 не в автомате |
| POST | `/ack` | `{session_id, id}` | `{ok: true}` | 404 |
| POST | `/ask` | `{session_id, question}` | `{answer_markdown, sources: [str]}` | 503 супервизор недоступен |
| GET | `/shift-report` | — | `{markdown, generated_at}` | 503 |
| POST | `/demo/load` | `{session_id, scenario: "S1"\|"S2"\|"S3d"\|"S4"\|"S5", warmup_ticks?: int=48}` | `ConsoleState` | 422 |
| POST | `/demo/speed` | `{session_id, seconds_per_tick: float}` (0 = пауза) | `ClockInfo` | 422 |
| POST | `/demo/tick` | `{session_id, n: int=1}` | `ConsoleState` | — |
| POST | `/demo/fault` | `{session_id, tag, fault_type: "frozen"\|"drift"\|"clamping"\|"nan"\|"clear", lims_age_hours?: float}` | `ConsoleState` | 422 |

Статика: `GET /console/` → `static/console/index.html` (StaticFiles, `html=True`).
Ошибка 409 всегда в формате `{"detail": {"code": str, "text": str, "checks": [KernelCheckDTO]}}`.

---

## 2. Схемы

```ts
ConsoleState {
  schema_version: "1.0"
  session_id: string
  scenario: { id: string|null, title: string|null }         // "S2", "Риск качества: утяжеление сырья"
  clock: ClockInfo
  mode: ModeInfo
  margin: { value_rub_h: number|null, delta_rub_h: number|null, delta_label: string|null }
  alarms: Alarm[]                  // только T0/T1 (D12)
  banner: Banner|null              // автовыход / отказ данных
  series: { cv: CvSeries[], mv: MvSeries[] }   // cv строго в порядке: sulfur, flash, dp
  hold: Trajectory                 // «без изменений», всегда есть (kind="hold")
  recommendation: Recommendation|null
  auto: AutoInfo|null              // не null только в режиме AUTO
  feed: FeedItem[]                 // новые сверху, максимум 50
}

ClockInfo {
  now: string                      // модельное «СЕЙЧАС»
  tick: int
  tick_minutes: 10
  seconds_per_tick: number         // реальное время на такт; 0 = пауза
  next_tick_in_s: number|null
}

ModeInfo {
  current: "ADVISORY"|"AUTO"
  auto_available: boolean
  auto_unavailable_reason: string|null      // человекочитаемо, по-русски
  auto_since: string|null
  last_auto_exit: { at: string, reason_code: AutoExitCode, text: string }|null
}
AutoExitCode = "REFUSAL_DATA"|"RECOVERY_ADVISORY"|"NEAR_LIMIT"|"LOW_CONFIDENCE"|"LIMS_STALE"|"OPERATOR"|"KERNEL_REJECT"

Alarm { id, tier: "T0"|"T1", tag, text, value: number, limit: number, at, acknowledged: boolean }
Banner { id, kind: "AUTO_EXIT"|"REFUSAL", title: string, text: string, at, ack_required: boolean }

CvSeries {
  key: "sulfur"|"flash"|"dp"
  tag: string                      // "HT_Q21" | "HT_FLASH" | "HT_P8"
  title: string                    // "СЕРА"
  unit: string
  limit: { value: number, sense: "max"|"min", label: string, tier: "T1"|"T2", source: string }  // source = "registry.py:<spec id>"
  history: Point[]                 // 12 ч назад … now, шаг 10 мин, 73 точки; v=null при отсутствии
  chip: { text: string, severity: "ok"|"warn"|"alarm"|"nodata" }
  note: string|null                // «без изменений пробьёт 10 ppm через 1 ч 50 мин»
  y_range: [number, number]        // рекомендованный диапазон оси, считает backend
}
Point { t: string, v: number|null, quality: "GOOD"|"SUSPECT"|"BAD"|"MISSING" }

MvSeries {
  sp: "HT_FEED_SP"|"HT_TIN_SP"|"HT_P_SP"|"HT_GOR_SP"|"AVT_T55_SP"
  tag: string                      // PV-тег
  title: string
  unit: string
  decimals: int
  sp_history: Point[]              // ступенчатая уставка
  pv_history: Point[]
  corridor: Corridor
  lines: { value: number, kind: "T0"|"T1"|"warn", label: string }[]   // только из registry.py
  frozen: boolean                  // safe-hold
}
Corridor { max_step_per_tick: number|null, lo: number|null, hi: number|null, source: string|null }
          // null => UI показывает «[из паспорта]», автомат MV не двигает (D4)

Trajectory {
  kind: "hold"|"recommendation"|"alternative"|"operator"|"auto_plan"
  u_target: Record<SP, number>     // абсолютные уставки
  cv: Record<"sulfur"|"flash"|"dp", Band[]>   // 25 точек: now … now+4 ч
  mv_plan: Record<SP, Point[]>     // ступеньки будущих уставок
  risk: Risk[]
  sigma_source: string|null        // откуда σ; null => полос нет, UI пишет «σ неизвестна»
}
Band { t: string, p10: number|null, p50: number, p90: number|null }
Risk { cv: "sulfur"|"flash"|"dp", probability: number, first_breach_at: string|null, limit: number }

Change { sp, tag, title, unit, current: number, target: number, delta: number, decimals: int }
KernelCheckDTO { name: string, passed: boolean, detail: string }
KernelInfo {
  passed: boolean
  checks: KernelCheckDTO[]
  limited: { sp: string, requested: number, allowed: number, reason: string }[]   // ограничения шага
}
Effect { sulfur_4h: number|null, flash_margin_c: number|null, margin_delta_rub_h: number|null }

Recommendation {
  cycle_id: string
  status: DecisionStatus           // значения из src/agents/contracts.py:301
  created_at: string
  valid_until: string              // created_at + 3 такта (ASSUMPTION, см. ASSUMPTIONS.md)
  narrative: string                // 1–2 предложения, шаблон (D11), без LLM
  changes: Change[]                // пусто при NO_CHANGE_DEADBAND / REFUSAL_*
  effect: Effect
  kernel: KernelInfo
  agents: { agent: "optimization"|"reliability"|"quality"|"supply"|"kernel", stance: "for"|"against"|"veto"|"limit", text: string }[]
  trajectory: Trajectory|null      // kind="recommendation"
  alternatives: Alternative[]      // 0–3
  refusal: Refusal|null            // не null при REFUSAL_*
}
Alternative {
  choice: "max_margin"|"balanced"|"max_safety"
  label: "Макс. маржа"|"Сбалансировано"|"Макс. запас"
  available: boolean
  changes: Change[]
  effect: Effect
  trajectory: Trajectory|null      // kind="alternative"
}
   // соответствие ArbitrationDecision.alternatives[].kind:
   // pareto_more_margin -> max_margin; selected -> balanced; pareto_safer_sulfur -> max_safety
Refusal { reason_code: string, text: string, checklist: string[], auto_resume_condition: string }

AutoInfo {
  next_step: { at: string, in_s: number, changes: Change[], narrative: string }|null
  plan: { at: string, changes: Change[] }[]
  corridor: Record<SP, Corridor>
  skipped_next: boolean
}

FeedItem { id: string, t: string, kind: "ok"|"shield"|"veto"|"warn"|"info", text: string, source: string }
   // shield = ядро ограничило/запретило; veto = агент наложил вето; ok = применено; warn = данные/тревога

PreviewRequest { session_id: string, cycle_id: string|null, u_target: Record<SP, number> }
PreviewResult {
  trajectory: Trajectory           // kind="operator"
  kernel: KernelInfo
  corridor_ok: boolean
  corridor_violations: { sp, requested_step, max_step, text }[]
  effect: Effect
  can_commit: boolean              // kernel.passed && corridor_ok && рекомендация не устарела
  blocking_reason: string|null
  compute_ms: number
}

CommitRequest { session_id, cycle_id: string|null, u_target: Record<SP, number>, source: "recommendation"|"alternative"|"operator_edit", choice?: "max_margin"|"balanced"|"max_safety" }
CommitResult { ok: true, applied: Change[], at: string, feed_item: FeedItem }
```

## 3. Правила вычисления (обязательны для backend)

1. `hold`, `recommendation.trajectory`, `alternatives[].trajectory`, `PreviewResult.trajectory` считаются **одной функцией**
   `forecast.build_trajectory(session, u_target, kind)` (R2) — иначе графики будут несравнимы.
2. `p50` — прогноз двойника (`FullChainTwin.predict` через `TwinView`, с коррекцией bias как в `src/agents/twin_view.py`).
3. `p10/p90` — `p50 ∓ 1.2816·σ(k)`; для серы — в лог-домене: `exp(ln p50 ∓ 1.2816·σ_log(k))`. σ(k) — **та же σ, что использует
   ядро/ограничения** (`src/agents/uncertainty.py`, `src/agents/estimation.py`: `sigma_meas`, `sigma_calib`, рост с возрастом LIMS).
   Если σ для CV в коде не найдена — `p10 = p90 = null`, `sigma_source = null`.
4. `Risk.probability` = max по шагам k вероятности `P(X_k нарушает лимит)` при нормальном (лог-нормальном для серы) распределении с той же σ.
   `first_breach_at` — первый шаг, где `p90` (для max-лимита) или `p10` (для min-лимита) пересекает лимит.
5. `Effect.flash_margin_c` = `p50_flash(4ч) − 2σ_flash − 55.0` (та же формула, что `scripts/replay_scenarios.py:108`).
6. `CvSeries.note` для «без изменений» формируется, только если `hold` пересекает лимит по `p50`: «без изменений пробьёт {limit} {unit} через {ч} ч {мин} мин».

## 4. История изменений

| Дата | Версия | Что | Кто |
|---|---|---|---|
| 2026-09-19 | 1.0 | Первая версия | ТЗ |
