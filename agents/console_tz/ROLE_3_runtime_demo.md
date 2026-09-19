# R3. Рантайм сессий, режим Автомат, демо-движок

Ты отвечаешь за «живую» установку за пультом: модельные часы, историю, цикл «такт → граф МАС → (автомат применяет)»,
автовыход и управление сценариями демо. Прочитай `README.md`, `01_CONTRACT.md`, затем изучи (только чтение):
`scripts/replay_scenarios.py` (эталон цикла: `plant.measure()` → `graph.invoke({"tags", "session_id"})` → `plant.apply(delta_u)` +
`TWIN_STORE.commit_applied_move`), `src/twin/plant.py` (`PlantSimulator`, `set_fault`, `schedule_lims_sample`, `truth`),
`src/agents/scenarios.py`, `src/twin/session.py` (`TWIN_STORE`), `src/agents/data_guard.py` (уровни доверия, `refusal_lims_age_h_without_pak`),
`src/agents/recovery.py`, `src/agents/graph.py` (`get_graph`).

## Файлы
`src/console/runtime.py`, `src/console/auto.py`, `src/console/demo.py`. Сигнатуры заданы R0.

## Задачи

### 1. `runtime.py`
- `ConsoleSession`: `plant: PlantSimulator`, `graph` (`get_graph()` один на процесс), `now: datetime`, `tick: int`,
  `history` — кольцевой буфер 73 тактов (12 ч) на каждый CV и MV (SP и PV отдельно) с `quality`,
  `u_current: dict[SP,float]` (из `plant.twin.u_current`), `mode`, `auto_since`, `last_auto_exit`, `feed: deque(maxlen=200)`,
  `last_graph_result`, `last_cycle_id`, `acks: set[str]`, `lock: threading.RLock` (все мутации под локом).
- `apply(u_target)`: `delta_u = u_target − u_current` → `plant.apply(delta_u)` → `TWIN_STORE.commit_applied_move(session_id, delta_u)`.
  Никаких проверок здесь нет — проверки делают вызывающие (R1 commit, `auto.auto_step`) **до** вызова. Добавь `assert`-комментарий об этом.
- `SessionRegistry.tick(n)`: для каждого такта: `tags = plant.measure()` → записать историю (quality: `MISSING` если NaN, `SUSPECT`
  если DataGuard пометил тег) → `graph.invoke({"tags": tags, "session_id": sid})` → сохранить `last_graph_result`, `last_cycle_id`
  → `feed += feed_from_graph_result(...)` (R1) → если `mode=="AUTO"`: `auto.check_auto_exit` → либо выход, либо `auto.auto_step`.
- Фоновый «метроном»: один `threading.Thread(daemon=True)` на процесс, раз в 0.5 с проверяет сессии, у которых наступило время такта
  (`seconds_per_tick > 0`). Время такта по умолчанию 10 с (демо ×60). `next_tick_in_s` отдаётся в `ClockInfo`.
- Модельное время: `now = T0 + tick·10 мин`, где `T0` — фиксированная дата сценария (`2026-09-19T04:40:00Z`), чтобы после прогрева
  на 48 тактов «СЕЙЧАС» было 12:40 (как в макете).

### 2. `demo.py`
- `load_scenario(sid, scenario, warmup_ticks=48)`: новая сессия из тегов сценария:
  | scenario | стартовые теги | настройки |
  |---|---|---|
  | S1 | `scenario_1_normal_tags()` | `q21_noise_ppm=0.05, seed=42` |
  | S2 | `scenario_2_quality_risk_tags()` | как S1 |
  | S3d | `scenario_1_normal_tags()` | после прогрева: `set_fault("HT_Q21","nan")`, `lims_age_hours=26` |
  | S4 | `scenario_4_conflict_tags()` | как S1 |
  | S5 | повтори стартовые теги из `run_s5_*` в `scripts/replay_scenarios.py` | как там |
  Прогрев: `warmup_ticks` тактов **в режиме ADVISORY без применения рекомендаций**, чтобы на графике была 8-часовая история
  (для S2 утяжеление должно проявиться ростом серы — если стартовые теги дают его сразу, прогревай на номинале, а затем переключи
  сырьё на теги S2 за 18 тактов до конца прогрева; опиши выбор в `STATUS.md`).
- `set_fault(sid, tag, fault_type, lims_age_hours)`: прокси в `PlantSimulator.set_fault` / `clear_fault`; `lims_age_hours` — запись в
  `plant.tags["lims_age_hours"]`. Эффект — со следующего такта.
- `set_speed`: `seconds_per_tick` ∈ {0, 2, 5, 10, 30, 600}; 0 = пауза.

### 3. `auto.py`
- `set_mode(session, "AUTO")`: разрешено, только если `auto_available` (см. ниже) → иначе 409 (R1 превращает исключение
  `AutoUnavailable(reason)` в ответ). Лента: «Автомат включён старшим оператором. Цель: «Сбалансировано».».
  `set_mode(..., "ADVISORY")` → `last_auto_exit = OPERATOR`.
- `auto_available` = нет активного условия автовыхода **и** последний статус не `REFUSAL_*`/`RECOVERY_ADVISORY`.
- `check_auto_exit(session)` → первое сработавшее по приоритету:
  1. `REFUSAL_DATA` — статус решения.
  2. `RECOVERY_ADVISORY` — статус решения.
  3. `NEAR_LIMIT` — любой T0/T1 из реестра: запас до лимита (по текущему значению **или** p90/p10 плана) меньше порога.
     Порог: для печи — 2.0 °C (уже используется в `src/ui/app.py` как «Запас до ПАЗ»); для остальных — **в коде нет**:
     вынеси в `PolicyConfig`-подобный словарь `AUTO_EXIT_MARGINS` в `auto.py` с `None` и запиши вопрос в `QUESTIONS.md`.
     `None` = проверка только «за лимитом».
  4. `LOW_CONFIDENCE` — для CV серы `p90 − p10` на горизонте 4 ч больше, чем расстояние от p50 до лимита
     (полоса «накрывает» лимит) — это ASSUMPTION, запиши.
  5. `LIMS_STALE` — `lims_age_hours > refusal_lims_age_h_without_pak` из `data_guard`.
  При выходе: `mode=ADVISORY`, `last_auto_exit`, `banner(kind="AUTO_EXIT", ack_required=True)`, запись `warn` в ленту с причиной.
- `auto_step(session)`: если `skipped_next` → сбросить флаг, записать `info` «Шаг пропущен оператором», выйти.
  Иначе: взять `decision.delta_u` → **обрезать** до коридора (`corridor.check_step`), MV без коридора не трогать →
  `forecast.preview` → если `can_commit` — `session.apply`, лента `ok` «Автомат: F9 226.0 → 231.0 т/ч. …»; если обрезано — ещё `shield`
  «Коридор ограничил шаг …»; если ядро не пропустило — **не применять**, `shield` + автовыход `KERNEL_REJECT`.
- `auto_info`: `next_step` (что будет применено на следующем такте — предсказание по текущему решению), `plan` (до 3 шагов, если
  решение/план восстановления их содержит), `corridor` по всем MV, `skipped_next`.

## Приёмка (тесты `tests/console/test_runtime_*.py`, `test_auto_*.py`)
- `load_scenario("S2")` → 49 точек истории у каждого CV, `now` = 12:40, сера на последних 18 тактах растёт.
- AUTO на S1, 10 тактов: каждый применённый шаг ≤ `max_step_per_tick`; MV без коридора не менялись; T1 по `plant.truth()` не нарушен.
- AUTO на S1 + `set_fault(HT_Q21, nan)` + `lims_age=26` → в течение ≤ 2 тактов `mode==ADVISORY`, `last_auto_exit.reason_code=="REFUSAL_DATA"`, баннер есть.
- `skip` → на следующем такте уставки не изменились, в ленте «Шаг пропущен».
- Метроном: при `seconds_per_tick=2` за 7 с реального времени прошло 3±1 такта; при 0 — ни одного.
- Потокобезопасность: 50 параллельных `GET /state` во время тиков не дают исключений.
