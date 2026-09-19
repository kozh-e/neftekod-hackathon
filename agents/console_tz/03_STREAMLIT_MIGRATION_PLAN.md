# План реализации: удаление Streamlit, единый пульт на :8000 (v3)

Статус: `src/ui/app.py` и `streamlit_app.py` **удалены** (2026-09-19) вместе с зависимостью `streamlit`,
сервисом `streamlit` в `docker-compose.yml` и тестом `test_streamlit_app_renders`. `/console` на порту 8000 —
единственный веб-интерфейс проекта. Этот документ — ТЗ на перенос оставшейся уникальной функциональности
Streamlit в `/console` плюс на закрытие дефектов, найденных при живом аудите UX (Парето-фронт, индекс
уверенности, покрытие датчиков, этап блендинга). Супересед: `agents/console_tz/README.md` D10, D13.

Формат и правила исполнения — как в `README.md` §5 (не выдумывать числа, всё из кода/реестра, маленькие
шаги, тесты после каждого куска, отчёт в `STATUS.md`, вопросы в `QUESTIONS.md`, запросы в `REQUESTS.md`).
**Исполнители — агенты Claude Sonnet 5, effort=medium.** Каждая роль ниже — самостоятельная задача на
один запуск агента: не смешивать роли, каждой роли — свой файл ТЗ (создать по образцу `ROLE_*.md` из
волны 1) со ссылкой на этот документ + `01_CONTRACT.md`.

---

## 0. Как это было проверено

Все находки ниже — из живого прогона `/console/?session_id=demo` (docker, сценарий S2, 48 тактов
разогрева) 2026-09-19, не из документов:

- Легенда Парето-графика показывает цвета не те, что у точек (см. §2).
- Индекс уверенности данных считается в `src/agents/data_guard.py:367-385`, но не долетает ни до
  `src/console/contracts.py`, ни до фронтенда — `grep confidence` по `src/console/` и `static/console/`
  даёт 0 совпадений.
- Цифровой двойник (`src/twin/chain.py::step`) реально считает `HT_BED_MEAN` (WABT слоя, уже используется
  в Парето), `HT_VSG`, `HT_D15_PRODUCT`/`HT_T95_PRODUCT`/`HT_CFPP_PRODUCT`/`HT_CN_PRODUCT` (качество
  товарного продукта), `HT_S_FEED`/`HT_T95_FEED` (качество сырья) — но `PlantSimulator.measure()`
  (`src/twin/plant.py:200-207`) копирует в публичный `self.tags` только подмножество через
  `ONLINE_OUTPUT_TAGS` (`HT_Q21`, `HT_T11`, `HT_T18`→flash, `D15`, `T95`). Остальное живёt только в
  приватном `self._last_raw_output`. **Важно**: большинство «сырых» тегов из
  `new_data/теги АВТ_24-2000.xlsx` (Q20, T5, T12, T16, T23, F14, F17, F26, W10, P24 и т.д.) физическим
  двойником вообще не моделируются — это упрощённая модель Р-202+П-3+стабилизация, а не полная копия
  РСУ. Показывать их как «датчики» значило бы рисовать графики из пустоты. Поэтому раздел §4 сузил
  область до того, что двойник реально считает.
- `src/agents/blending.py::node_blending_agent` уже входит в граф и считает `blending_recipe` каждый
  такт (`state["tanks"]` по умолчанию из `get_default_tanks()`) — это уже есть в
  `session.last_graph_result["blending_recipe"]`, просто не отдаётся через `/api/console/*`.
- Панель LLM-супервизора почти полностью на бэкенде уже есть: `main.py` содержит
  `GET /api/v1/supervisor/findings`, `GET /api/v1/supervisor/briefings`,
  `GET /api/v1/supervisor/change-requests`, `POST .../approve`, `POST .../reject`,
  `POST /api/v1/supervisor/ask`. Формирование брифинга уже дергается из
  `src/console/api.py:278-289` (`GET /shift-report` → `DEFAULT_SUPERVISOR_SERVICE.generate_shift_briefing`).
  Не хватает **только фронтенда** для findings и HITL approve/reject — это меняет объём работы по
  супервизору в меньшую сторону.
- Экономика (`EconomicsParams`) сейчас нигде не хранится per-session: `ConsoleSession.tick_single`
  вызывает `self.graph.invoke({"tags": tags, "session_id": ...})` **без** `economics` — правки цен
  в Streamlit-сайдбаре применялись только к разовому вызову `/api/v1/optimize`, а не к живой петле
  консоли. Чтобы редактирование цен в «Константах» реально влияло на маржу в live-сессии, нужно
  завести `session.economics_override` и прокидывать его в `graph.invoke`.

## 1. Принятые решения (сессия 2026-09-19, два раунда уточняющих вопросов)

| # | Тема | Решение |
|---|------|---------|
| E1 | Судьба Streamlit | Удалить полностью. Готово (см. коммит этой сессии). |
| E2 | Индекс уверенности | Только качество входных данных (`data_guard.confidence`: score/level/причины). Без ширины P10-P90 прогноза. |
| E3 | Датчики сверх 3 CV + 5 PV | Только значимые для решений МАС/сертификации качества, реально считаемые двойником (см. §0): WABT, T11, ВСГ, качество продукта (D15/T95/CFPP/CN), качество сырья (S_FEED/T95_FEED). Не полный реестр АВТ+24-2000. |
| E4 | Глубина блендинга | Полноценная песочница: редактируемые доли танков/присадок, мгновенный пересчёт сертификации через LP-решатель, эффект на маржу. |
| E5 | Ручной ввод сырых тегов (старый сайдбар) | Не переносить. Панель «Симулятор» (`?demo=1`: сценарии S1-S5 + инъекция отказов КИП) закрывает тестовые сценарии. |
| E6 | Цены рынка/тарифов | Редактируемый блок в существующей вкладке «Константы», применяется к live-экономике сессии. |
| E7 | Панель супервизора | Полностью, новая вкладка «Супервизор»: Findings (read-only) + HITL approve/reject политики. Q&A остаётся на «Обзоре» (уже есть), брифинг — на «Журнале смены» (уже есть). |
| E8 | Парето: 3D / таблица | Не нужно. 2D с переключаемыми осями (после фикса §2) достаточно. |

---

## 2. Ф0 — Фикс Парето-фронта (без изменений в бэкенде, только `static/console/`)

Файл: `static/console/charts.js`, функция `renderParetoChart` (строки ~567-750).

1. **Легенда врёт про цвета.** Точки красятся поштучно (`itemStyle.color` на каждом объекте в
   `paretoPoints`/`dominatedPoints`/`vetoedPoints`, строки 631-673), а ECharts-легенда красит квадратики
   по цвету **серии** (дефолтная палитра ECharts), которая никогда не задавалась явно. Плюс `is_hold` и
   `is_recommendation` (чёрный квадрат / жёлтый ромб) запиханы в серию `paretoPoints` без своей записи
   в легенде. **Фикс**: завести 5 настоящих серий вместо 3 — `pareto` (`#2E7D32`), `dominated`
   (`#90A4AE`), `vetoed` (`#C62828`), `hold` (`#263238`), `recommendation` (`#F9A825`) — каждой задать
   `itemStyle.color` **на уровне серии** (не только на точках) и включить все 5 в `legend.data`.
   Символы (`diamond`/`pin`/`rect`) оставить как есть.
2. **Дефолтная ось X (`sulfur_giveaway`) часто вырождена в 0** (проверено на живых данных: у всех точек
   такта было `sulfur_giveaway: 0`), при этом `sulfur_ucb` в тех же данных имел разброс 9.2-11.0.
   **Фикс**: при построении графика, если для выбранной оси дисперсия значений по точкам близка к 0
   (`max - min < 1e-6`), автоматически предложить (не молча подменить) следующую метрику из
   `objectives` с ненулевым разбросом — через текст-подсказку над графиком «Ось X (`{label}`) не имеет
   разброса в этом такте, показана `{fallback_label}`» и фактическую отрисовку по fallback-оси; ручной
   выбор оси через `<select>` пользователем всегда должен работать буквально (не переопределять его
   осознанный выбор — только выбор по умолчанию при первой отрисовке).
3. **Выброс по марже растягивает ось.** Один кандидат может иметь `net_margin`, отличающийся от
   остальных на 1-2 порядка (наблюдалось: +12 972 756 ₽/ч против кластера в пределах ±300 тыс). Сама
   экономическая модель — не в периметре этой задачи (известный дефект, см. память проекта про
   «аддитивное смещение на мультипликативной кинетике»). **Фикс на уровне графика**: считать IQR по
   `net_margin` всех точек, если есть точка дальше `3×IQR` от медианы — использовать `axisLabel`/`min`/
   `max` ECharts, обрезающие видимый диапазон по `[Q1 - 1.5×IQR, Q3 + 1.5×IQR]` с пометкой на самой точке
   (не убирать её из данных, просто не давать ей растягивать шкалу) и полным значением в тултипе.
4. **Тройной фетч.** При открытии вкладки `/api/console/pareto` уходит 3 запроса подряд (наблюдалось в
   Network). Найти источник в `app.js` (вероятно, вызов `loadPareto()` одновременно из обработчика
   `switchTab` и из подписки на обновление такта) и оставить один вызов на активацию вкладки + один на
   тик, пока вкладка активна (как задокументировано в `02_UX_REDESIGN_PLAN.md` §7.1, но, судя по факту,
   не реализовано корректно).

**Приёмка**: `?fixture=advisory`/живая сессия S1/S2 — легенда и точки одного цвета для каждого статуса;
переключение осей на пару с ненулевым разбросом не показывает предупреждение; выброс по марже не
схлопывает кластер в точку; Network показывает 1 запрос `/api/console/pareto` на открытие вкладки.

---

## 3. Ф1 — Индекс уверенности (данные)

### Бэкенд
- `src/console/contracts.py`: добавить `ConfidenceInfo(Frozen)`:
  ```python
  class ConfidenceInfo(Frozen):
      score: float               # 0..1, из data_guard.confidence["score"]
      level: Literal["HIGH", "MEDIUM", "LOW"]
      n_filled_critical: int
      q21_unavailable: bool
      lims_age_hours: float
  ```
  Добавить `confidence: Optional[ConfidenceInfo] = None` в `ConsoleState` (после `feed`).
- `src/console/service.py`: в функции сборки `ConsoleState` читать
  `session.last_graph_result.get("confidence")` (уже пишется в `data_guard.py:409`) и мапить в
  `ConfidenceInfo`. `None` до первого такта — фронтенд обязан это обработать.
- Регенерировать все существующие фикстуры (`advisory.json`, `auto.json`, `refusal.json`,
  `preview_ok.json`, `preview_blocked.json`) с реалистичным полем `confidence` через
  `scripts/make_console_fixtures.py`.

### Фронтенд
- `static/console/index.html` + `app.js`: бейдж в `status-bar` рядом с индикатором режима: цвет по
  `level` (зелёный HIGH / жёлтый MEDIUM / красный LOW), число `score` в процентах. Клик/hover —
  разворачивает причины: «заполнено нормативными значениями: N тегов», «онлайн-анализатор серы
  недоступен» (если `q21_unavailable`), «возраст ЛИМС: X ч».
- `null` (до первого такта) → бейдж не показывается или серый placeholder «нет данных».

**Приёмка**: `tests/console/test_contract_fixtures.py` валидирует `confidence` во всех фикстурах;
`tests/console/test_api_endpoints.py` проверяет наличие поля в `GET /state`; визуально бейдж меняет
цвет при инъекции отказа `HT_Q21` через `?demo=1` (`set_fault`).

---

## 4. Ф2 — Недостающие датчики (по факту считаемые двойником)

### Бэкенд
- `src/twin/plant.py`: добавить в `PlantSimulator` публичное свойство (не трогать `_last_raw_output`
  напрямую из консоли — приватный атрибут чужого модуля):
  ```python
  @property
  def last_model_outputs(self) -> Dict[str, float]:
      """Полный срез выходов двойника на последнем такте (для консоли/аналитики)."""
      return dict(self._last_raw_output)
  ```
- `src/console/runtime.py::ConsoleSession`: добавить кольцевые буферы (по образцу `cv_history`) для:
  `HT_BED_MEAN` (WABT, °C), `HT_VSG` (ВСГ, нм³/ч), `HT_T11` (реально уже в `tags`, но не выведен на
  экран), `HT_D15_PRODUCT`/`HT_T95_PRODUCT`/`HT_CFPP_PRODUCT`/`HT_CN_PRODUCT` (качество товарного
  продукта), `HT_S_FEED`/`HT_T95_FEED` (качество сырья). Источник — `self.plant.last_model_outputs`
  сразу после `self.plant.measure()` в `tick_single` (текущие поля `tags` не содержат эти ключи).
- `src/console/contracts.py`: `SensorSeries(Frozen)` (аналог `CvSeries`, но без обязательного `Limit` —
  многие из этих величин не имеют жёсткого лимита, только справочный):
  ```python
  class SensorSeries(Frozen):
      key: str
      tag: str
      title: str
      unit: str
      history: List[Point]
      limit: Optional[Limit] = None
  ```
  Добавить `sensors: List[SensorSeries] = Field(default_factory=list)` в `ConsoleState`.
- `src/console/service.py`: собрать список `SensorSeries` из новых буферов.

### Фронтенд
- Новая свёрнутая по умолчанию секция «Качество продукта и сырья» на вкладке «Обзор» (не забивать
  основной экран — раскрывается по клику, как текущий блок «Почему так решили»), плюс добавить WABT/ВСГ
  как два новых пункта в панель виджетов «Настроить вид» (`WIDGET_DEFS`, см. `02_UX_REDESIGN_PLAN.md`
  §4.2) рядом с существующими CV/MV чекбоксами.

**Приёмка**: `tests/console/test_forecast_corridor.py`/новый тест — после `tick_single()` все 7 новых
рядов непустые и не NaN; в UI секция показывает актуальные значения, синхронно тикающие с остальным
экраном.

---

## 5. Ф3 — Песочница блендинга

### Бэкенд
- Новый файл `src/console/blending.py`:
  - `def blending_state(session: ConsoleSession) -> Optional[BlendingStateDTO]` — читает
    `session.last_graph_result.get("blending_recipe")` и `session.last_graph_result.get("tanks")`
    (текущие остатки/качество танков), без пересчёта графа — тот же паттерн, что `build_pareto`/`build_xai`.
  - `def blending_preview(session: ConsoleSession, tank_overrides: Dict[str, Any], price_overrides: Dict[str, float]) -> BlendingPreviewDTO` —
    вызывает `build_blend_problem(godt, tanks, prices, policy)` + `solve_blend(...)` +
    `certify_blend(...)` из `src/agents/blending.py` напрямую, на **копии** словаря танков (не мутирует
    `session.last_graph_result`), без применения к боевому состоянию — по аналогии с
    `forecast.preview()`, который считает на `session.plant.twin.clone()`.
- `src/console/contracts.py`: `BlendComponentDTO` (танк: `id`, `label`, `stock_t`, доступные свойства
  `props: Dict[str, float]`), `BlendCertDTO` (спека: сера/T95/цетан/CFPP/вспышка — план vs факт vs лимит),
  `BlendingStateDTO` (текущий пересчитанный рецепт + список компонентов), `BlendingPreviewDTO` (то же +
  флаг `feasible`, `error_message` если `status != FEASIBLE`).
- `src/console/api.py`: `GET /api/console/blending?session_id=` (текущее состояние),
  `POST /api/console/blending/preview` (тело: доли/цены-переопределения → пересчёт, ничего не применяет).
  Реальное применение рецепта к резервуарам — **не реализовывать** в этой итерации: в реальной установке
  это отдельный процесс перекачки, не мгновенная уставка; песочница только считает и показывает.

### Фронтенд
- Новая вкладка «Блендинг»: 3 компонента (ГО ДТ, керосин ТС-1, газойль) + 2 присадки (ДДП, ...) со
  степперами (переиспользовать `.value-stepper` из `02_UX_REDESIGN_PLAN.md` §4.3, не городить новый
  виджет ввода). Кнопка «Пересчитать рецептуру» → `POST /blending/preview` с debounce 250 мс, как в
  «Ручном управлении». Карточка сертификата: сера/T95/цетан/CFPP/вспышка смеси — план vs ГОСТ-лимит,
  цветовая индикация превышения. Плитка эффекта: изменение себестоимости тонны/маржи relative к текущему
  рецепту.
- Явная подпись: «Расчёт не влияет на реальные резервуары — это модель LP-оптимизации» (по аналогии с
  предупреждением на «Ручном управлении», §5.5 `02_UX_REDESIGN_PLAN.md`).

**Приёмка**: `tests/console/test_blending_sandbox.py` (новый) — `blending_state` возвращает `None` до
первого такта, непустой DTO после; `blending_preview` с явно инфизибл ограничениями возвращает
`feasible=false` с `error_message`, не бросает исключение; UI пересчитывает без применения к боевой
установке, значения сертификата совпадают с `BlendingResult` из `src/agents/blending.py` на тех же входах.

---

## 6. Ф4 — Цены рынка/тарифов в «Константах»

### Бэкенд
- `src/console/runtime.py::ConsoleSession`: добавить `self.economics_override: Dict[str, float] = {}`.
- В `tick_single`, при вызове `self.graph.invoke(...)`, передавать
  `"economics": self.economics_override or None` (как это уже делает `main.py`'s `/api/v1/optimize` для
  разового вызова — здесь то же самое, но постоянно для live-сессии).
- `src/console/api.py`: `POST /api/console/economics` (тело: `session_id`, частичный словарь полей
  `EconomicsParams` — те же 5 цен, что были в Streamlit-сайдбаре: `price_godt`, `price_straight_run`,
  `price_crude_oil`, `price_kerosene`, `price_gasoil`). Валидировать через `EconomicsParams` (частичное
  обновление, остальные поля — из `load_params().economics` как база).
- `src/console/contracts.py`: `EconomicsOverrideDTO` (текущие активные значения + признак, что это
  override, а не значение из `config/twin_params.json`, для UI-подсказки «изменено оператором»).

### Фронтенд
- В существующую вкладку «Константы» (уже есть поиск/фильтр по T0-T3, см. скриншот сессии) добавить
  секцию «Цены и тарифы» — редактируемую (в отличие от остального read-only реестра лимитов): 5 полей
  ввода с текущим значением, кнопка «Применить», подтверждение изменения маржи в статус-баре на
  следующем такте.

**Приёмка**: изменение `price_godt` через API/UI приводит к видимому изменению `margin` в статус-баре
на следующем такте; сброс к дефолту возвращает значения `config/twin_params.json`.

---

## 7. Ф5 — Вкладка «Супервизор» (почти чистый фронтенд)

Бэкенд **уже есть** — `main.py` эндпоинты `GET/POST /api/v1/supervisor/*` (см. §0). Работа:

- `static/console/api.js`: добавить тонкие обёртки `getSupervisorFindings()`, `getChangeRequests()`,
  `approveChangeRequest(id, user)`, `rejectChangeRequest(id, user)` — прямые фетчи на `/api/v1/supervisor/*`
  (тот же origin, порт 8000 — CORS не нужен).
- Новая вкладка «Супервизор»:
  - Блок «Диагностические находки» — список из `GET /findings`, бейдж по `severity`
    (CRITICAL/WARNING/INFO), разворачиваемая карточка с `root_cause`/`safety_risk_assessment`/
    `checks_recommended`/`evidence_refs` (1:1 со Streamlit `sup_tab_findings`, строки 656-670
    удалённого `src/ui/app.py` — текст сохранён в git-истории коммита удаления, если нужен точный
    порядок полей).
  - Блок «Запросы на изменение политики (HITL)» — список из `GET /change-requests`, для
    `status == "PENDING_APPROVAL"` — кнопки «Утвердить» / «Отклонить» → `POST .../approve|reject` с
    телом `{"user": "<из текстового поля или дефолт 'Оператор консоли'>"}`, после успеха — обновить
    список (без модалки, инлайн).
- Q&A и «Сдать смену» **не дублировать** — они уже есть на «Обзоре» и «Журнале смены» соответственно
  (`ask-box`, `btn-shift-report` → теперь постоянная вкладка).

**Приёмка**: `PENDING_APPROVAL` запрос, созданный супервизором (сгенерировать через существующий тестовый
путь `src/supervisor/service.py`/фикстуру), утверждается через UI и переходит в статус `APPROVED`, что
видно после обновления списка; findings со статусом `CRITICAL` визуально выделены.

---

## 8. Роли для параллельного исполнения (Sonnet 5, effort=medium)

Каждая роль — отдельный запуск агента с промптом-шаблоном из `README.md` §5a, читает этот файл +
`01_CONTRACT.md` + свой `ROLE_*.md` (создать по аналогии с волной 1). Границы файлов — строго по
таблице, конфликт — запись в `REQUESTS.md`, не правка чужого файла.

| Роль | Владеет | Волна | Зависит от |
|---|---|---|---|
| **B0** Контракты и фикстуры | `src/console/contracts.py` (DTO из §3, §4, §5, §6), пересборка всех фикстур `static/console/fixtures/*.json` + `tests/fixtures/console/*.json` через `scripts/make_console_fixtures.py` | 0 | — |
| **B1** Confidence + Sensors backend | `src/console/service.py` (только функции сборки confidence/sensors), `src/console/runtime.py` (буферы §4), `src/twin/plant.py` (только добавление `last_model_outputs`, ничего больше) | 1 | B0 |
| **B2** Blending backend | Новый `src/console/blending.py`, добавление 2 эндпоинтов в `src/console/api.py` (только `/blending`, `/blending/preview`) | 1 | B0 |
| **B3** Economics backend | `src/console/runtime.py` (только `economics_override` + правка вызова `graph.invoke`), новый эндпоинт `POST /economics` в `src/console/api.py` | 1 | B0 |
| **F0** Фикс Парето | `static/console/charts.js` (только `renderParetoChart`, без остальных функций) | 0 (параллельно с B0, не зависит от бэкенда) | — |
| **F1** Confidence + Sensors frontend | `static/console/index.html`/`app.js`/`styles.css`: бейдж уверенности, секция датчиков, пункты в `WIDGET_DEFS` | 2 | B1 |
| **F2** Блендинг frontend | Новая вкладка «Блендинг» в тех же 3 файлах (свой `<section data-tab-panel="blending">`, не трогать другие секции) | 2 | B2 |
| **F3** Экономика frontend | Секция цен во вкладке «Константы» (тот же index.html/app.js, свой блок) | 2 | B3 |
| **F4** Супервизор frontend | Новая вкладка «Супервизор», методы в `api.js` | 2 (не зависит от бэкенда — эндпоинты уже есть) | — |
| **QA** | `tests/console/**`, финальный прогон `pytest -q`, сверка Definition of Done | 3 | всё выше |

**Конфликт на запись**: F1/F2/F3/F4 все правят `static/console/index.html`, `app.js`, `styles.css` —
это единственная точка трения. Смягчение: каждая роль добавляет **только свой `<section data-tab-panel=...>`**
и свой блок в `switchTab()`/`WIDGET_DEFS`/CSS-класс с уникальным префиксом (`.blending-*`,
`.economics-*`, `.supervisor-*`), не трогая чужие секции; если агент видит, что чужой участок файла
нужно поменять — запись в `REQUESTS.md`, не самостоятельная правка. Рекомендуется исполнять волну 2
(F1-F4) последовательно, а не параллельно, если единовременно доступен только один агент — риск слияния
ниже, чем выгода от параллелизма при effort=medium.

---

## 9. Definition of Done

- [ ] `pytest -q` полностью зелёный (существующие ~223 + новые тесты Ф1-Ф5).
- [ ] `docker compose up` поднимает только `api` (и опционально `tests`) — сервиса `streamlit` в
      `docker-compose.yml` нет, `EXPOSE 8501`/`streamlit` нет ни в `Dockerfile`, ни в `requirements.txt`.
- [ ] Парето-фронт (`?fixture=advisory` и живой S1/S2): легенда соответствует цветам точек, дефолтная
      ось не схлопывается в вырожденную линию, выброс по марже не растягивает график, 1 запрос на
      открытие вкладки.
- [ ] Бейдж индекса уверенности в status-bar меняет цвет/причины при инъекции отказа `HT_Q21`.
- [ ] Секция «Качество продукта и сырья» показывает 7 живых рядов, тикающих вместе с остальным экраном.
- [ ] Вкладка «Блендинг»: правка долей/присадок → пересчёт сертификата без применения к боевой установке;
      инфизибл-комбинация показывает причину, а не падает с ошибкой.
- [ ] Вкладка «Константы»: правка цены ГО ДТ меняет `margin` в статус-баре на следующем такте.
- [ ] Вкладка «Супервизор»: находки видны с бейджами severity; `PENDING_APPROVAL` запрос
      утверждается/отклоняется из UI.
- [ ] `STATUS.md` дополнен волной по этому плану; новые допущения — в `agents/ASSUMPTIONS.md` §6.
