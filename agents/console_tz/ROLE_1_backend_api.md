# R1. Backend API и сборка состояния

Ты реализуешь HTTP-слой пульта и сборку `ConsoleState`. Прочитай `README.md`, `01_CONTRACT.md`, затем изучи (только чтение):
`main.py`, `src/agents/graph.py` (что возвращает `invoke`: `final_recommendation`, `decision`, `card`, `kernel`, `trace`,
`negotiation_log`, `selected_candidate`, `hold_prediction`, `alternatives`, `pareto`), `src/agents/contracts.py`
(`ArbitrationDecision`, `Alternative`, `KernelVerdict`, `NegotiationEvent`, `DecisionStatus`), `src/xai/card.py` (`DecisionCard`),
`src/agents/economics.py` (`MarginModel`), `src/supervisor/service.py` (`answer_operator`, `generate_shift_briefing`).

## Файлы
`src/console/api.py`, `src/console/service.py`, `src/console/feed.py`. Сигнатуры заданы R0 — не менять.
Сессии берёшь из `src/console/runtime.REGISTRY` (R3), прогнозы — из `src/console/forecast` (R2). Пока их нет — пиши
тесты с monkeypatch на заглушки.

## Задачи

### 1. `api.py` — все эндпоинты `01_CONTRACT.md §1`
- Тонкий слой: валидация → вызов `service`/`runtime`/`forecast`/`auto` → модель ответа. Никакой логики в хендлерах.
- 409 — через `HTTPException(status_code=409, detail=ConsoleErrorDetail(...).model_dump())`.
- `/ask` и `/shift-report` — обёртки над `DEFAULT_SUPERVISOR_SERVICE` (как `main.py: ask_supervisor`). Таймаут 20 с → 503.
- Обработчики синхронные тяжёлые вызовы (graph, twin) выполняй через `run_in_threadpool` (fastapi.concurrency).

### 2. `service.build_state(session) -> ConsoleState`
Собирает состояние из `session` (история, режим, последний `graph_result`) — **ничего не пересчитывает заново**, кроме вызовов R2:
- `series.cv` — из истории сессии (R3 хранит 73 точки). Порядок строго `sulfur, flash, dp`.
  `limit` и `source` — из `src/agents/registry.py` (ищи спецификацию по тегу/label; сера ≤ 10, вспышка ≥ 55, ΔP T1 454.5 кПа).
  `chip`: nodata если последняя точка `MISSING`/`BAD`; alarm если p50 последней точки за лимитом; warn если hold-прогноз
  пересекает лимит; иначе ok. Тексты коротко по-русски («растёт +0.5 ppm/ч» — наклон по последним 6 точкам).
- `series.mv` — 5 MV, `corridor` из `corridor.corridor_for` (R2), `lines` — T0/T1 из реестра + зона предупреждения печи 380 °C
  (spec «Запрет нагрева печи П-3 в зоне предупреждения»). `frozen = True`, если статус `REFUSAL_*` или safe-hold.
- `hold` — `forecast.build_trajectory(session, u_current, "hold")`.
- `recommendation` — из `graph_result`:
  - `changes`: `decision.delta_u` → абсолютные значения относительно текущих уставок; `decimals` по MV (F9:1, T6:1, P13:3, GOR:0, T55:1).
  - `narrative`: **шаблон** (D11), см. п. 4.
  - `kernel`: из `KernelVerdict.checks`; `limited` — из событий переговоров/ремонта (`NegotiationEvent`), где шаг был урезан.
  - `agents`: из `negotiation_log` — одна строка на агента, `stance` по типу события.
  - `alternatives`: маппинг `Alternative.kind` → `choice` (см. контракт). `balanced` = выбранный кандидат. Нет в решении → `available=false`.
  - `refusal`: при `REFUSAL_*` — `text` из `card.refusal_text`; `checklist` и `auto_resume_condition` — по таблице п. 5.
  - `valid_until = created_at + 3 такта` — запиши как ASSUMPTION.
- `margin` — `MarginModel` по текущим уставкам (как в `main.py` около `run_optimization_cycle`); `delta_rub_h` — эффект рекомендации.
- `alarms` — только T0/T1 из реестра, где текущее значение за лимитом или ближе порога `NEAR_LIMIT` (порог берёт R3, см. его ТЗ).
- `auto` — от R3 (`session.auto_info`), `feed` — `session.feed[:50]`.

### 3. `service.commit(session, req) -> CommitResult`
Порядок строго такой (безопасность, README §5 п.3):
1. `cycle_id` не совпадает с последним решением и `source != "operator_edit"` → 409 `STALE_RECOMMENDATION`.
2. `forecast.preview(...)` для `req.u_target` → если `can_commit=false` → 409 с `blocking_reason` и `kernel.checks`.
3. Применить: `session.apply(u_target)` (R3: `PlantSimulator.apply(delta_u)` + `TWIN_STORE.commit_applied_move`).
4. Записать в ленту `ok`: «Оператор применил: T6 363.3 → 364.5 °C (правка рекомендации 365.0)» — формулировка разная для `recommendation` / `alternative` / `operator_edit`.
5. Записать в журнал решений (`src/agents/decision_log.append_decision`) с пометкой источника.

### 4. Шаблон narrative (без LLM)
Функция `narrative_for(decision, card, hold_traj) -> str`, максимум 2 предложения, ≤ 220 символов. Схема:
`«{причина}. {действие}, чтобы {цель}.»`
- причина — из `card.problem_and_risk` (первое поле с риском) или из наклона серы/ΔP; если нет риска — «Режим в норме, есть резерв по марже».
- действие — глаголы по знаку дельты: «Поднимаем/снижаем {название MV}» (перечисление через «и», максимум 2 MV).
- цель — по статусу: `SUCCESS_CORRECTIVE`/риск серы → «удержать серу ≤ 10 ppm»; `SUCCESS` без риска → «увеличить маржу на {x} тыс ₽/ч»;
  `RECOVERY_ADVISORY` → «вернуть {параметр} в пределы оборудования».
Покрой тестами все ветки (строки сравнивать целиком).

### 5. Таблица отказов (refusal)
| status / причина | checklist | auto_resume_condition |
|---|---|---|
| `REFUSAL_DATA` из-за ПАК + LIMS | «ПАК {tag}: питание, пробоотборная линия, связь с ПЛК»; «Заказать внеочередной анализ LIMS»; «Не повышать загрузку HT_F9 до восстановления данных» | «ПАК {tag} вернётся в норму или придёт анализ LIMS моложе {порог} ч» (порог — `refusal_lims_age_h_without_pak` из `data_guard`) |
| `REFUSAL_NO_SAFE_ACTION` | «Проверить уставки вручную по регламенту»; «Сообщить технологу» | «Появится допустимый ход по ядру безопасности» |
| `REFUSAL_TIMEOUT` | «Повторить расчёт»; «Проверить загрузку сервера» | «Расчёт уложится в таймаут» |
Тексты чек-листов — константы в `service.py` (одно место).

### 6. `feed.py`
`feed_from_graph_result(graph_result, t)`: превращает `negotiation_log`, `kernel.checks` (не прошедшие → `shield`), вето агентов (`veto`),
предупреждения DataGuard (`warn`) в `FeedItem`. Один цикл графа даёт ≤ 3 записи (самое важное: shield > veto > warn > info).
`id` — детерминированный (`f"{cycle_id}:{index}"`), чтобы фронт не дублировал.

## Приёмка
- `pytest -q tests/console/test_api_*.py tests/console/test_service_*.py` зелёные (пишешь сам вместе с R6).
- `GET /api/console/state` после `demo/load S2` валиден по `ConsoleState` и p95 времени ответа < 150 мс (без пересчёта графа).
- `POST /commit` с правкой вне коридора → 409 `CORRIDOR`; с нарушением ядра → 409 `KERNEL`; с устаревшим `cycle_id` → 409 `STALE_RECOMMENDATION`.
- Ни одного захардкоженного числа лимита в `service.py` — только чтение реестра (проверяется grep-тестом R6).
