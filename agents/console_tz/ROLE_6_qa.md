# R6. QA: контракт, инварианты безопасности, сквозные проверки

Ты пишешь тесты, которые не дают пульту соврать оператору или обойти безопасность. Прочитай `README.md`, `01_CONTRACT.md`,
все файлы ролей (чтобы знать, что обещано). Стиль тестов — как в `tests/test_step7_graph_e2e.py` и `tests/audit/`.

## Файлы
`tests/console/**` (создай `tests/console/__init__.py`, `conftest.py` с фикстурами: `client` = `fastapi.testclient.TestClient(main.app)`,
`s2_session`, `s1_auto_session`). Метроном в тестах — выключен (`seconds_per_tick=0`), такты — только через `demo/tick`.

## Волна 1 (до готовности backend) — тесты контракта
1. `test_contract_fixtures.py`: каждая фикстура из `tests/fixtures/console/` валидна по своей модели; `static/console/fixtures/` — байт-в-байт копии.
2. `test_contract_invariants.py` (на фикстурах и позже на живом state):
   - `series.cv` порядок `sulfur, flash, dp`; у каждой истории 73 точки с шагом 10 мин; последняя точка = `clock.now`;
   - каждая `Trajectory.cv[*]` — 25 точек, первая = `clock.now`; `p10 ≤ p50 ≤ p90` где не `null`;
   - `REFUSAL_*` ⇒ `changes == []` и `refusal != null`; `mode.current=="AUTO"` ⇒ `auto != null`;
   - нигде нет `NaN`/`Infinity` (сериализация JSON строгая);
   - `corridor.max_step_per_tick is None` ⇔ `source` указывает на отсутствие в реестре.
3. `test_no_hardcoded_limits.py`: grep по `src/console/*.py` — нет литералов `10.0`, `55.0`, `454.5`, `386.4`, `380.5`, `252.8`
   (кроме как в комментариях) — всё должно читаться из реестра.

## Волна 2 — живые проверки
### Безопасность (главное)
- `test_commit_requires_kernel.py`: `POST /commit` с u_target, где T55 > T1 → 409 `KERNEL`, уставки в двойнике не изменились.
- `test_commit_requires_corridor.py`: шаг T6 > макс. шага → 409 `CORRIDOR`; шаг P13 при `max_step=None` → 409.
- `test_commit_stale.py`: после такта с новым `cycle_id` старый `cycle_id` + `source="recommendation"` → 409 `STALE_RECOMMENDATION`.
- `test_auto_never_exceeds_corridor.py`: S1, AUTO, 30 тактов — для каждого применённого шага |Δ| ≤ max_step; MV без коридора не менялись.
- `test_auto_exit_triggers.py`: по одному тесту на каждый `AutoExitCode`, который воспроизводим сценарием
  (REFUSAL_DATA через fault+lims_age; LIMS_STALE; NEAR_LIMIT через S4 — T55 = 385.0 при пороге 2.0 °C; OPERATOR через `/mode`).
  Для каждого: режим стал ADVISORY за ≤ 2 такта, `banner` есть, в ленте `warn` с причиной.
- `test_auto_unavailable.py`: в REFUSAL `POST /mode AUTO` → 409 с `reason`.
- `test_preview_no_side_effects.py`: 20 `preview` подряд → `plant.truth()` и `u_current` не изменились.

### Прогноз
- `test_preview_latency.py`: p95 < 300 мс на 20 вызовах (пометить `@pytest.mark.perf`, как в `tests/perf/`).
- `test_s2_story.py`: S2 после прогрева → hold по сере к +4 ч выше лимита **или** `note` пустая (проверить согласованность note ↔ hold);
  рекомендация к +4 ч ниже hold; commit рекомендации → через 12 тактов факт серы ниже, чем без commit (сравнить с параллельной сессией).

### Фронтенд (без браузера, если Playwright не установлен)
- `test_static_served.py`: `GET /console/` 200, `index.html` ссылается на `app.js`, `charts.js`, `vendor/echarts.min.js`, все отдаются 200.
- Если в окружении есть `playwright` (`pip show playwright`) — `test_ui_smoke.py` (skip, если нет): открыть `?fixture=refusal` на 1920×1080,
  проверить отсутствие кнопок с текстом «Отправить», наличие баннера; `?fixture=advisory` — ввести в поле T6 значение +4 °C от текущего →
  главная кнопка `disabled`; 0 ошибок в консоли.

## Отчёт
В `agents/console_tz/STATUS.md` раздел R6: таблица «тест → статус → владелец дефекта». Дефект оформляй как запись в `REQUESTS.md`
с минимальным воспроизведением (команда + ожидаемое/фактическое). Тесты, проверяющие безопасность, **нельзя** помечать `xfail`/`skip`
ради зелёного прогона — только чинить код.

## Приёмка
`pytest -q tests/console` зелёный; `pytest -q` (весь репозиторий) не хуже, чем до начала работ (сравни число падений с базовым прогоном — сделай его первым делом и запиши в `STATUS.md`).
