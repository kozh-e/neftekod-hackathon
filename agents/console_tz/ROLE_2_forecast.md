# R2. Прогноз, неопределённость, preview «что если»

Ты отвечаешь за главную фишку пульта: траектории «без изменений / рекомендация / альтернатива / моя правка» с полосой P10–P90,
вероятность нарушения лимита и мгновенную проверку правки оператора. Прочитай `README.md`, `01_CONTRACT.md` (особенно §3),
затем изучи (только чтение): `src/agents/twin_view.py` (`TwinView.predict`, коррекция bias), `src/twin/chain.py`
(`FullChainTwin.predict`, `OUTPUTS`, `clone`), `src/twin/fopdt.py` (τ, запаздывания), `src/agents/uncertainty.py`
(`chance_effective`, `z_from_alpha`, `SensitivityModel`), `src/agents/estimation.py` (`LINEAR_PROP_CONFIG`, лог-домен серы,
`Q_DRIFT_LOG`, `KalmanState`), `src/safety_kernel/kernel.py` (`SafetyKernel.verify`), `src/agents/registry.py` (T0 скорости и диапазоны),
`src/agents/policy.py`.

## Файлы
`src/console/forecast.py`, `src/console/corridor.py`. Сигнатуры заданы R0.

## Задачи

### 1. `corridor.py`
- `corridor_for(sp) -> Corridor`: `max_step_per_tick` — из спецификаций реестра «Максимальная скорость изменения …» (F9: 10 т/ч/такт,
  T6: 3 °C/такт, T55: 3 °C/такт — **проверь значения в реестре, не копируй отсюда**); `lo/hi` — T0 min/max по этому MV;
  `source` — `"registry.py:<id спецификации>"`. Для `HT_P_SP`, `HT_GOR_SP` скорости в реестре нет → `max_step_per_tick=None`
  и запись в `QUESTIONS.md` «нужен макс. шаг из паспорта для P13 и GOR».
- `check_step(u_current, u_target)` → список нарушений `{sp, requested_step, max_step, text}`; также выход за `lo/hi`.
  MV с `max_step_per_tick=None` при ненулевом шаге → нарушение «шаг не задан в паспорте, изменение только вручную в DCS».

### 2. `build_trajectory(session, u_target, kind) -> Trajectory`
- Горизонт 24 такта (4 ч) + точка now = 25 значений. Время точек = `session.now + k·10 мин`.
- `p50` — `TwinView`/`FullChainTwin.predict(u_target, horizon=24)` **на клоне** двойника сессии (никаких побочных эффектов на сессию!),
  с той же коррекцией bias, что использует пайплайн. Ключи: `HT_S_PRODUCT → sulfur`, `HT_FLASH → flash`, `HT_DP_KPA → dp`.
- σ(k): найди в коде ту σ, которую используют ограничения/ядро для каждого CV (сера — лог-домен, `sigma_meas` + `sigma_calib`,
  рост с возрастом LIMS `σ²(t) = σ²_LIMS + q_drift·Δt`; вспышка — `LINEAR_PROP_CONFIG["FLASH"]`). Рост по горизонту — та же формула
  с `Δt = age_now + k·10 мин`. **Если для CV σ не найдена (вероятно, ΔP)** → `p10=p90=None`, записать в `QUESTIONS.md`.
  В `sigma_source` — строка-ссылка, откуда взята σ (например `"estimation.py:LINEAR_PROP_CONFIG.FLASH"`).
- `mv_plan` — ступеньки: now → `u_target` на следующем такте. Для `auto_plan` — многошаговый план (R3 передаёт `u_target` по шагам,
  сделай перегрузку `build_plan_trajectory(session, steps: list[dict]) -> Trajectory`).
- `risk` — по `01_CONTRACT.md §3 п.4`, для каждого CV с лимитом. Нормальная CDF — `scipy.stats.norm` (scipy уже в requirements).
- Кэш: ключ `(session.tick, tuple(sorted(u_target.items())))`, LRU на 64 записи. `hold` для одного такта считается один раз.

### 3. `preview(session, req) -> PreviewResult`
1. Валидация: все ключи ∈ SP, значения конечны.
2. `corridor.check_step(session.u_current, req.u_target)`.
3. Ядро: собери `ArbitrationDecision`-подобный объект с `delta_u = u_target − u_current` и вызови `SafetyKernel.verify(...)` с теми же
   `estimate`, `data`, `registry`, `policy`, что в последнем цикле графа сессии (они лежат в `session.last_graph_result`).
   Если сигнатура требует полей, которых нет, — создай минимальный допустимый объект, **не ослабляя проверок**; опиши в `STATUS.md`.
4. `trajectory = build_trajectory(..., "operator")`, `effect = effect_of(...)`.
5. `can_commit = kernel.passed and corridor_ok and not stale`, `blocking_reason` — первая причина по-русски.
6. `compute_ms` — фактическое время. **Цель: p95 < 300 мс** на ноутбуке. Если не укладываешься — сократи ансамбль/кэшируй steady-state, не горизонт.

### 4. `effect_of(traj, hold, session) -> Effect`
- `sulfur_4h = traj.cv.sulfur[-1].p50`; `flash_margin_c` по `01_CONTRACT §3 п.5`; `margin_delta_rub_h` — `MarginModel` для `u_target`
  минус для `u_current` (как в пайплайне; не выдумывать цены — брать `load_params().economics`).

### 5. Допущения
Запиши в `agents/ASSUMPTIONS.md` (раздел Console): квантиль 1.2816 для P10/P90; нормальность (лог-нормальность серы);
независимость ошибок по шагам при расчёте риска (берётся max по шагам, не объединение).

## Приёмка (тесты в `tests/console/test_forecast_*.py`, пишешь сам)
- `build_trajectory(hold)` не меняет состояние двойника сессии (сравнить `measure()` до/после).
- Для S2: `hold` по сере растёт и к 4 ч выше, чем рекомендация; p10 ≤ p50 ≤ p90 для всех точек с σ.
- Правка T6 на +4 °C за такт → `can_commit=false`, `corridor_violations[0].sp=="HT_TIN_SP"`.
- Правка, из-за которой ядро не проходит (например, T55 выше T1) → `kernel.passed=false`, `can_commit=false`.
- p95 `preview` < 300 мс на 20 вызовах с разными значениями.
