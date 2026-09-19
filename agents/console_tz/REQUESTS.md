# Запросы между ролями (REQUESTS)

Формат: `- [R?→R?] файл: что и зачем`

- [F3→B1] `src/console/service.py:565-569` (секция «5. Маржа» в `build_state`/аналогичной функции):
  `econ_p = load_params()` берёт цены **только** из `config/twin_params.json`, `MarginModel(econ_p.economics, econ_p.reactor)`
  строится без учёта `session.economics_override` (поле, которое роль B3 завела в `runtime.py` и которое
  реально применяется в `tick_single` через `graph.invoke(..., "economics": ...)`). В результате
  `ConsoleState.margin.value_rub_h` (число в статус-баре «МАРЖА») **никогда** не реагирует на правки
  оператора через `POST /api/console/economics` (эндпоинт роли B3, потреблённый моим блоком «Цены и
  тарифы» во вкладке «Константы», роль F3) — только на дефолт из файла конфигурации.
  Подтверждено вживую на `?session_id=demo`: `price_godt` 68000→100000 через живой UI/API → `GET
  /api/console/economics` подтверждает `price_godt=100000, is_override=true`; после нескольких тактов
  (`clock.tick` вырос) `GET /api/console/state` всё равно отдаёт `margin.value_rub_h=3214944` — ровно
  `219.6 * (68000*0.98 - 52000)`, т.е. **дефолтный** `price_godt=68000`, а не 100000. Это блокирует пункт
  Definition of Done из `03_STREAMLIT_MIGRATION_PLAN.md` §9: «правка цены ГО ДТ меняет `margin` в
  статус-баре на следующем такте».
  Предлагаемый минимальный фикс (по аналогии с `_build_economics_dto` в `src/console/api.py`):
  ```python
  import dataclasses
  ...
  econ_p = load_params()
  economics = (
      dataclasses.replace(econ_p.economics, **session.economics_override)
      if session.economics_override else econ_p.economics
  )
  mm = MarginModel(economics, econ_p.reactor)
  ```
  Вне периметра роли F3 (владею только `static/console/*`), не правил `service.py` сам. Подробности и
  шаги воспроизведения — `agents/console_tz/STATUS.md`, волна 11 (F3).
