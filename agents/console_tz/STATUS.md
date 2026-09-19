# Статус реализации: Пульт старшего оператора Р-202

## Волна 0: R0 Лид-интегратор — ВЫПОЛНЕНО
- [x] Создан пакет `src/console/__init__.py`
- [x] Создан контракт `src/console/contracts.py` (Pydantic v2, frozen, extra="forbid", alias_generator для `_fixture`)
- [x] Созданы все базовые модули (`runtime.py`, `auto.py`, `forecast.py`, `corridor.py`, `service.py`, `feed.py`, `demo.py`, `api.py`)
- [x] Роутер и статика подключены в `main.py` (`/api/console`, `/console`)
- [x] Фикстуры `advisory.json`, `auto.json`, `refusal.json`, `preview_ok.json`, `preview_blocked.json` сгенерированы и проверены в `tests/fixtures/console/` и `static/console/fixtures/`
- [x] Созданы файлы `STATUS.md`, `REQUESTS.md`, `QUESTIONS.md`, обновлен `agents/ASSUMPTIONS.md` (§6 Console)

## Волна 1: R1 Backend API — ВЫПОЛНЕНО
- [x] `src/console/feed.py`: форматирование ленты событий (макс. 3 элемента на такт, агрегация переговорного цикла, вето ядра, алерты DataGuard).
- [x] `src/console/service.py`: сборка полного снимка `ConsoleState` без повторного расчета графа, реализация `commit` со строгим порядком безопасности (проверка свежести такта, `preview`, `apply`, журнал).
- [x] `src/console/api.py`: полный набор REST-эндпоинтов по спецификации `01_CONTRACT.md` (`/state`, `/preview`, `/commit`, `/reject`, `/mode`, `/auto/skip`, `/ack`, `/ask`, `/shift-report`, `/demo/*`).

## Волна 1: R2 Прогноз и preview — ВЫПОЛНЕНО
- [x] `src/console/corridor.py`: динамическое считывание T0 границ и скоростей хода (`RATE.*.MAX`, `MV.*.MIN/MAX`), валидация приращений с допуском 1e-3 на погрешности округления.
- [x] `src/console/forecast.py`: построение траекторий на 24 шага (25 точек) с доверительными полосами P10–P90 (лог-нормальное для серы, нормальное для вспышки, детерминированное для перепада), быстрый preview с вызовом `SafetyKernel.verify`.

## Волна 1: R3 Рантайм, автомат, демо — ВЫПОЛНЕНО
- [x] `src/console/runtime.py`: сессионный рантайм `ConsoleSession`, кольцевые буферы истории на 73 такта (12 ч), фоновый поток-метроном, потокобезопасный реестр `REGISTRY`.
- [x] `src/console/auto.py`: переключение режимов СОВЕТ/АВТОМАТ, приоритетная лестница автовыхода (REFUSAL_DATA > RECOVERY_ADVISORY > NEAR_LIMIT > LOW_CONFIDENCE > LIMS_STALE), исполнение шагов с обрезкой по коридору.
- [x] `src/console/demo.py`: загрузка сценариев S1–S5, прогрев модельной истории, переключение сырья в S2, инжекция неисправностей.

## Волна 1: R4 Фронтенд (каркас) — ВЫПОЛНЕНО
- [x] `static/console/styles.css`: дизайн-система ISA-101 (цвета ink/paper, рамки режимов, типографика IBM Plex, фиксированный layout 1920×1080 без скролла).
- [x] `static/console/index.html`: status bar, баннеры тревог/автовыхода, двухколоночный лейбл (графики 1248px + колонка решений 612px), модальные окна, панель ведущего `?demo=1`.
- [x] `static/console/api.js`: универсальный клиент с поддержкой боевого режима и фикстур (`?fixture=`).
- [x] `static/console/app.js`: реактивный контроллер интерфейса, debounced preview (250 мс), двухэтапное подтверждение (5 с), таймер обратного отсчета.

## Волна 1: R5 Фронтенд (графики) — ВЫПОЛНЕНО
- [x] `static/console/charts.js`: высокопроизводительный SVG-рендерер технологических графиков с синхронизированным курсором, тултипами, штриховкой пропусков данных, подсветкой риска (касание лимита полосой P10–P90).
- [x] `static/console/vendor/echarts.min.js`: локальный вендорный файл для статической раздачи.

## Волна 4: Backend для UX-редизайна v2 (Парето + XAI) — ВЫПОЛНЕНО
- [x] `src/console/contracts.py`: добавлены `ParetoObjective`, `ParetoPointDTO`, `ParetoFrontDTO`, `NegotiationEventDTO`, `XaiInfo`.
- [x] `src/console/service.py`: `build_pareto(session)` и `build_xai(session)` — читают уже посчитанный `session.last_graph_result`, без повторного расчета графа.
- [x] `src/console/api.py`: `GET /api/console/pareto`, `GET /api/console/xai`.
- [x] Полный план фронтенд-части редизайна (вкладки, ручное управление/песочница, Парето-график, XAI-лог, панель скорости, фикс "синих полей") — см. `agents/console_tz/02_UX_REDESIGN_PLAN.md`.
- [x] Регрессия: `pytest tests/console -q` — 48 passed (без изменений в количестве, новые тесты добавит исполнитель фронтенд-части по §11 плана).

## Волна 2 & 3: R0 + R6 Интеграция, QA и репетиция — ВЫПОЛНЕНО
- [x] Набор тестов `tests/console/**`: 44 теста, 100% green.
  - `test_contract_fixtures.py`: валидация всех эталонных фикстур.
  - `test_contract_invariants.py`: проверка инвариантов контракта (сортировка CV, 25 точек траектории, отсутствие NaN/Inf).
  - `test_no_hardcoded_limits.py`: проверка отсутствия захардкоженных лимитов из `FORBIDDEN_LIMITS` в `src/console/*.py`.
  - `test_forecast_corridor.py`: проверка коридоров, предсказаний, латентности preview и сюжета S2.
  - `test_auto_runtime.py`: проверка логики автомата, автовыходов и пропуска шага.
  - `test_api_endpoints.py`: проверка всех REST-эндпоинтов, кодов ошибок 409 (CORRIDOR, KERNEL, STALE_RECOMMENDATION, AUTO_UNAVAILABLE).
  - `test_static_served.py`: проверка отдачи HTML, статических стилей, скриптов и фикстур FastAPI.
- [x] Регрессионное тестирование: вся кодовая база проекта сохраняет работоспособность.
- [x] Definition of Done: полностью закрыт.

## Волна 5: Frontend и интеграция UX-редизайна v2 — ВЫПОЛНЕНО
- [x] `static/console/index.html`: удален letterbox/scale, внедрена структура верхних вкладок (Обзор, Ручное управление, Агенты и XAI, Парето-анализ, Журнал смены), постоянная панель «Симулятор», попап виджетов «Настроить вид».
- [x] `static/console/styles.css`: адаптивная верстка с нативным вертикальным скроллом, нейтральный компонент `.value-stepper` (замена чужеродных синих рамок), стили всех 5 вкладок и нейтральной подсказки `.sandbox-info-box`.
- [x] `static/console/charts.js`: добавлена функция `renderParetoChart(...)` на базе `echarts.min.js` с интерактивным тултипом, легендой и кликом по точке для переноса в ручное управление.
- [x] `static/console/api.js`: добавлены методы `getPareto()` и `getXai()` с поддержкой боевого и оффлайн (`?fixture=`) режимов.
- [x] `static/console/app.js`: роутинг вкладок с поддержкой `location.hash`, управление видимостью виджетов через `localStorage`, песочница ручного ввода с независимым расчетом прогноза и 2-этапным подтверждением коммита (5 с), исправление чтения `u_current` через `currentU(state)`.
- [x] Эталонные фикстуры: созданы синхронные копии `pareto.json` и `xai.json` в `static/console/fixtures/` и `tests/fixtures/console/`.
- [x] Тесты `tests/console/`:
  - Создан `tests/console/test_pareto_xai.py` (проверка `build_pareto`, `build_xai`, сериализации DTO).
  - Расширены `test_api_endpoints.py` (`/pareto`, `/xai`), `test_contract_fixtures.py` (валидация новых фикстур), `test_static_served.py`.
- [x] Документация: обновлены `STATUS.md` и `agents/ASSUMPTIONS.md` (§6 Console).

## Волна 6: B0 — контракты для миграции со Streamlit — ВЫПОЛНЕНО

Роль B0 из `agents/console_tz/03_STREAMLIT_MIGRATION_PLAN.md` §8: только `src/console/contracts.py`
(новые DTO из §3/§4/§5/§6 плана) + пересборка фикстур. Файлы `service.py`/`api.py`/`runtime.py`/
`static/console/*.js` не трогались (владение B1/B2/B3/F0-F4).

### План (5 пунктов)
1. Прочитать README.md, 01_CONTRACT.md, 03_STREAMLIT_MIGRATION_PLAN.md §0/§3/§4/§5/§6/§8.
2. Добавить `ConfidenceInfo`, `SensorSeries` в `contracts.py`, включить в `ConsoleState` после `feed`.
3. Добавить DTO блендинга (`BlendComponentDTO`, `BlendSpecMetricDTO`, `BlendCertDTO`, `BlendRecipeDTO`,
   `BlendingStateDTO`, `BlendingPreviewDTO`) и `EconomicsOverrideDTO`, не трогая `ConsoleState`.
4. Регенерировать все фикстуры пульта, прогнать `pytest -q tests/console` после каждого куска.
5. Зафиксировать допущения по недостающим лимитам в `QUESTIONS.md`, отчитаться в `STATUS.md`.

### Сделано
- [x] `src/console/contracts.py`:
  - `ConfidenceInfo(Frozen)` — поля `score: float`, `level: Literal["HIGH","MEDIUM","LOW"]`,
    `n_filled_critical: int`, `q21_unavailable: bool`, `lims_age_hours: float` — 1:1 со словарём
    `confidence`, который формирует `src/agents/data_guard.py:379-385`.
  - `SensorSeries(Frozen)` — `key`, `tag`, `title`, `unit`, `history: List[Point]`,
    `limit: Optional[Limit] = None` (переиспользует существующие `Point`/`Limit`).
  - В `ConsoleState` после `feed` добавлены `confidence: Optional[ConfidenceInfo] = None` и
    `sensors: List[SensorSeries] = Field(default_factory=list)`. Поля блендинга/экономики в
    `ConsoleState` **не** добавлялись — это в компетенции B2/B3 при их интеграции.
  - `BlendComponentDTO(Frozen)` — танк блендинга (`id`, `label`, `stock_t`, `props: Dict[str, float]`),
    зеркало `ComponentTank` из `src/agents/tanks.py`.
  - `BlendSpecMetricDTO(Frozen)` — строка сертификата (`key` одно из
    `sulfur|t95|cetane|cfpp|flash`, `value` = ожидаемое по рецепту, `limit`/`sense`/`source` — порог
    ГОСТ, если найден явной константой в `src/agents/limits.py`, иначе `None`).
  - `BlendCertDTO(Frozen)` — `status`, `cost_per_ton`, `metrics: List[BlendSpecMetricDTO]`,
    `error_message` (зеркало `certify_blend()`/`BlendingCertificate`).
  - `BlendRecipeDTO(Frozen)` — полное зеркало полей `BlendingResult` (`src/agents/blending.py`):
    `success`, `status`, `v_diesel/v_kerosene/v_ddp_ppm`, `expected_*`, `cost_per_ton`, `fbi_blend`,
    `shares`, `additive_doses_kg_t`, `binding_constraints`, `blocked_components`,
    `elastic_violations`, `error_message`.
  - `BlendingStateDTO(Frozen)` — `recipe: BlendRecipeDTO` + `components: List[BlendComponentDTO]` +
    `cert: BlendCertDTO` (текущий пересчитанный рецепт из `session.last_graph_result["blending_recipe"]`).
  - `BlendingPreviewDTO(Frozen)` — то же самое + `feasible: bool` + `error_message: Optional[str] = None`.
  - `EconomicsOverrideDTO(Frozen)` — `price_godt`, `price_straight_run`, `price_crude_oil`,
    `price_kerosene`, `price_gasoil` (точные имена и типы из `EconomicsParams`,
    `src/twin/params.py:111-118`) + `is_override: bool`.
- [x] Фикстуры пересобраны через существующий `scripts/make_console_fixtures.py`
  (`PYTHONPATH=. python scripts/make_console_fixtures.py`) — генератор не пришлось расширять: новые
  поля `ConsoleState.confidence`/`sensors` необязательны (`None`/`[]` по умолчанию), поэтому
  валидация Pydantic проходит без правок скрипта. Обновлены (байт-в-байт синхронизированы
  `tests/fixtures/console/` ↔ `static/console/fixtures/`): `advisory.json`, `auto.json`,
  `refusal.json`, `preview_ok.json`, `preview_blocked.json`. `pareto.json`, `xai.json`,
  `constants.json` не тронуты намеренно — они сериализуют `ParetoFrontDTO`/`XaiInfo`/список
  констант, а не `ConsoleState`, и не зависят от новых полей (проверено чтением
  `tests/console/test_contract_fixtures.py`).
- [x] Прогон `pytest -q tests/console` после добавления DTO и после регенерации фикстур — оба раза
  зелёный, финальный вывод:
  ```
  63 passed, 2 warnings in 623.32s (0:10:23)
  ```
  (варнинги — `anyio`/`pytest.mark.perf`, не связаны с этой работой). Ни один тест не ломался из-за
  новых полей — `REQUESTS.md` записей от B0 не потребовалось.
- [x] Импорт всех новых классов и `ConsoleState.model_fields` проверены вручную (`python -c "from
  src.console.contracts import ..."`) до прогона полного набора тестов.

### Допущения
- `BlendSpecMetricDTO`/`BlendCertDTO` трактуют формулировку задания «план/факт/лимит» как одну
  плоскую структуру «значение + лимит», используемую одинаково в `BlendingStateDTO.cert` (факт —
  текущий пересчитанный рецепт) и `BlendingPreviewDTO.cert` (план — гипотетический пересчёт), а не
  как два отдельных поля внутри одной строки — отдельного источника «факт после реальной перекачки»
  в коде нет (блендинг не тактовый процесс). См. `QUESTIONS.md`.
- Для сертификата качества (`BlendSpecMetricDTO.limit`) использованы реальные нормативы ГОСТ
  32511-2013 из `src/agents/limits.py` (`SULFUR_PRODUCT_MAX=10.0`, `FLASH_PRODUCT_MIN=55.0`,
  `CETANE_PRODUCT_MIN=51.0`, `T95_PRODUCT_MAX=360.0`), а не буферные `BLEND_*`-константы с запасом
  (9.5/56.0/51.5/360.0), которыми реально ограничен LP-решатель в `node_blending_agent`. Для CFPP
  использован `CFPP_BY_GRADE["E"] = -15.0` — совпадает с захардкоженным `target_cfpp=-15.0` в
  `node_blending_agent`, но явного выбора сорта в реестре нет. Оба допущения — в `QUESTIONS.md`,
  подлежат подтверждению перед реализацией B2.

### Открытые вопросы (полный текст — `agents/console_tz/QUESTIONS.md`)
- [B0] Расхождение ГОСТ-лимита и внутреннего LP-буфера для серы/вспышки/цетана — какое число
  показывать оператору как «лимит» в сертификате блендинга.
- [B0] Источник лимита CFPP неоднозначен (`CFPP_BY_GRADE` — словарь по сортам, не константа).
- [B0] Трактовка «план/факт/лимит» для `BlendCertDTO` — принято решение без «факт»-поля,
  требуется подтверждение.

### Осталось (следующие роли по плану)
- B1: заполнить `confidence`/`sensors` реальными данными в `service.py`/`runtime.py`/`plant.py`.
- B2: `src/console/blending.py` + эндпоинты `/blending`, `/blending/preview`.
- B3: `economics_override` в `runtime.py` + эндпоинт `/economics`.
- F0-F4: фронтенд по своим вкладкам (не в периметре B0).

## Волна 7: B1 — Confidence + Sensors backend — ВЫПОЛНЕНО (с оговоркой по `confidence`)

Роль B1 из `agents/console_tz/03_STREAMLIT_MIGRATION_PLAN.md` §4/§8: только
`src/twin/plant.py` (свойство `last_model_outputs`), `src/console/runtime.py` (буферы §4),
`src/console/service.py` (сборка `confidence`/`sensors` в `build_state`). DTO
(`ConfidenceInfo`, `SensorSeries`, поля в `ConsoleState`) уже готовы волной B0, не трогались.
`contracts.py`, `api.py`, `blending.py`, `static/console/*` не трогались (чужие роли).

### План (5 пунктов, как в задании)
1. Прочитать README.md §5, 03_STREAMLIT_MIGRATION_PLAN.md §0/§3/§4/§8, STATUS.md (волна B0),
   точные имена полей `ConfidenceInfo`/`SensorSeries`/`ConsoleState.confidence`/`ConsoleState.sensors`
   в `contracts.py`.
2. `src/twin/plant.py`: добавить свойство `last_model_outputs` в `PlantSimulator`.
3. `src/console/runtime.py`: кольцевые буферы `self.sensor_history` для 9 тегов, заполнение в
   `tick_single()` сразу после `self.plant.measure()`.
4. `src/console/service.py`: собрать `confidence`/`sensors` в `build_state()`.
5. Прогнать `pytest -q tests/console`, задокументировать эмпирические находки в STATUS.md/QUESTIONS.md.

### Сделано
- [x] `src/twin/plant.py` — добавлено ровно одно публичное свойство сразу после `current_hour`,
  больше в файле ничего не менялось:
  ```python
  @property
  def last_model_outputs(self) -> Dict[str, float]:
      """Полный срез выходов двойника на последнем такте (для консоли/аналитики)."""
      return dict(self._last_raw_output)
  ```
- [x] `src/console/runtime.py`:
  - Модульная константа `SENSOR_SOURCE_TAGS: Dict[str, str]` — ключ буфера → имя поля в
    `last_model_outputs` (сыром выходе `twin.step()`). 8 из 9 ключей совпадают буквально
    (`HT_BED_MEAN`, `HT_VSG`, `HT_D15_PRODUCT`, `HT_T95_PRODUCT`, `HT_CFPP_PRODUCT`,
    `HT_CN_PRODUCT`, `HT_S_FEED`, `HT_T95_FEED`); девятый — `HT_T11`, для которого сырой ключ
    twin называется `HT_T_OUT` (в `self.tags` он публикуется как `HT_T11` через
    `ONLINE_OUTPUT_TAGS`, но сырого поля `HT_T11` в `last_model_outputs` нет).
  - `ConsoleSession.__init__`: `self.sensor_history: Dict[str, Deque[Point]] = {key: deque(maxlen=self.history_len) for key in SENSOR_SOURCE_TAGS}` — по образцу `cv_history`.
  - `tick_single()`: новый блок «1a» сразу после `tags = self.plant.measure()` (перед «2. Запись
    истории CV», без переноса существующих шагов) — читает `self.plant.last_model_outputs` один
    раз в `raw_outputs`, для каждого буфера считает `quality` (`MISSING` при `None`/`NaN` по
    образцу `cv_history`, иначе `GOOD`), округляет до 3 знаков, дописывает `Point`. Больше в
    `tick_single()` ничего не добавлено и не переставлено — вызов `self.graph.invoke(...)` не
    тронут (оставлено для роли B3).
- [x] `src/console/service.py`:
  - Импорт `ConfidenceInfo`, `SensorSeries` из `contracts.py` (алфавитный порядок в существующем
    импорте).
  - `SENSOR_META: Dict[str, Dict[str, str]]` рядом с `MV_META` — `tag`/`title`/`unit` для всех 9
    буферов: WABT реактора Р-202 (°C), Расход ВСГ (нм³/ч), T11 — температура выхода Р-202 (°C),
    D15/T95 продукта (кг/м³/°C), CFPP продукта (°C), цетановое число продукта (б/р), сера сырья
    (ppm), T95 сырья (°C).
  - В `build_state()` добавлен блок 6 (confidence) и блок 7 (sensors) перед `return ConsoleState(...)`,
    поля `confidence=confidence_dto, sensors=sensors_list` добавлены в конструктор — единственное
    изменение самого `ConsoleState(...)`.
- [x] Прогон `pytest -q tests/console` после всех правок:
  ```
  63 passed, 2 warnings in 609.84s (0:10:09)
  ```
  (варнинги — `anyio`/`pytest.mark.perf`, не связаны с этой работой; то же число тестов, что и
  после волны B0 — новых тестов на буферы/`confidence`/`sensors` ещё нет, это задача QA-волны).
- [x] Ручная эмпирическая проверка (`ConsoleSession` + `build_state` на живом такте, без фикстур):
  все 9 сенсоров (`HT_BED_MEAN`, `HT_VSG`, `HT_T11`, `HT_D15_PRODUCT`, `HT_T95_PRODUCT`,
  `HT_CFPP_PRODUCT`, `HT_CN_PRODUCT`, `HT_S_FEED`, `HT_T95_FEED`) физически присутствуют в
  `PlantSimulator.last_model_outputs` и заполняются каждый такт с `quality="GOOD"` — ни один тег
  из задания не пришлось убирать/оставлять пустым.

### Важная находка (не выдумывалась замена — задокументирована, не исправлена самостоятельно)
- **`confidence` в живой сессии всегда `None`, а не только «до первого такта».** Полная цепочка
  проверена эмпирически (5+ тактов подряд + инъекция `plant.set_fault("HT_Q21", "nan")`):
  `session.last_graph_result.get("confidence")` всегда возвращает `None`. Причина — вне периметра
  B1: `ConsoleSession.graph = get_graph()` без аргументов резолвится в `DEFAULT_GRAPH_MODE = "core_v3"`
  (`src/agents/graph.py`), а узел `node_data_guard_core` этого графа кладёт в состояние только
  `state["data"]` (`DataAssessment`), не `state["confidence"]`. Словарь `confidence` (с полями
  `score`/`level`/`n_filled_critical`/`q21_unavailable`/`lims_age_hours`, на который рассчитан
  `ConfidenceInfo`) реально формирует только `node_data_quality_guard` в `data_guard.py:379-409`,
  но этот узел используется исключительно в устаревшем `build_mvp_graph()` (`graph_mode="legacy"`),
  который `ConsoleSession` не строит. Код `build_state()` в B1 написан строго по спецификации
  плана (читает `last_graph_result.get("confidence")`, `None` → `ConsoleState.confidence=None`) и
  заработает без изменений, как только источник появится в `core_v3` — самостоятельно
  подменять источник на производную от `DataAssessment` не стал: `DataAssessment` не содержит
  `score`/`n_filled_critical`, это отдельное решение вне роли B1/файлов `src/console/*`. Полное
  описание и два варианта решения — `agents/console_tz/QUESTIONS.md` (`[B1]`).
  Фикстуры (`?fixture=advisory` и т. п.) не затронуты — это статичные JSON от B0, `confidence` в
  них по-прежнему заполнен и не зависит от графа.

### Допущения
- Округление точек в `sensor_history` — единообразно 3 знака после запятой для всех 9 буферов
  (в отличие от `cv_history`, где точность подобрана по каждому CV отдельно: 3/2/1) — величины
  справочные, единой конвенции точности в реестре нет, 3 знака с запасом покрывают все единицы
  измерения (°C, нм³/ч, кг/м³, ppm, б/р).
- `limit=None` для всех 9 `SensorSeries` — по тексту задания и `03_STREAMLIT_MIGRATION_PLAN.md` §4
  у этих величин нет единого T0/T1 предела в `registry.py`, только справочные ГОСТ-нормативы по
  подмножеству (уже используются в `BlendCertDTO` волны B0 для качества продукта) — заводить их
  здесь повторно не стал, чтобы не дублировать источник истины и не создавать два разных лимита
  для одного и того же тега в разных DTO без явного решения.

### Открытые вопросы (полный текст — `agents/console_tz/QUESTIONS.md`)
- [B1] `confidence` не долетает до `ConsoleState` в живой сессии `core_v3` — узел
  `node_data_guard_core` не пишет `state["confidence"]` (пишет только `node_data_quality_guard`
  из устаревшего `legacy`-графа). Нужно решение вне роли B1: перенести вычисление в
  `node_data_guard_core`, либо явно утвердить новую формулу `ConfidenceInfo` на основе
  `DataAssessment`.

### Осталось (следующие роли по плану)
- B3 стартует правки `runtime.py` (`economics_override` + вызов `graph.invoke(...)`) — блок «1a»
  из B1 расположен строго до вызова `graph.invoke`, конфликта по месту не ожидается, но нужно
  свериться после мержа.
- F1: секция «Качество продукта и сырья» + пункты WABT/ВСГ в `WIDGET_DEFS`; бейдж `confidence`
  в UI должен явно обрабатывать «всегда `null`» в живой сессии (не только «до первого такта») —
  см. находку B1 выше.
- Кто-то (владелец `src/agents/graph.py`/`data_guard.py`) должен закрыть находку B1 по `confidence`,
  иначе критерий приёмки D0 «бейдж меняет цвет при инъекции отказа HT_Q21» не выполним на живой
  консоли (только на статичных фикстурах).

## Волна 8: B3 — Economics backend — ВЫПОЛНЕНО

Роль B3 из `agents/console_tz/03_STREAMLIT_MIGRATION_PLAN.md` §6/§8: только
`src/console/runtime.py` (поле `economics_override` в `__init__` + правка вызова
`self.graph.invoke(...)` в `tick_single()`), новый эндпоинт `POST /api/console/economics`
(и сопутствующий `GET`) в `src/console/api.py`, `EconomicsUpdateRequest` в `src/console/contracts.py`.
`service.py`, `src/twin/plant.py`, `static/console/*` не трогались (чужие роли); блок «1a» роли
B1 в `tick_single()` (запись `sensor_history`) не тронут — правка внесена только в саму строку
вызова `self.graph.invoke(...)` сразу после него.

### План (5 пунктов)
1. Прочитать README.md §5, `03_STREAMLIT_MIGRATION_PLAN.md` §0/§6/§8, STATUS.md (волны B0/B1),
   точные имена полей `EconomicsParams`/`EconomicsOverrideDTO` в `src/twin/params.py`/`contracts.py`,
   паттерн `main.py::run_optimization_cycle` (`state_input["economics"] = payload.economics`).
2. `src/console/runtime.py`: добавить `self.economics_override: Dict[str, float] = {}` в `__init__`,
   передать `"economics": self.economics_override or None` в `self.graph.invoke(...)`.
3. `src/console/contracts.py`: добавить `EconomicsUpdateRequest(session_id, prices)`.
4. `src/console/api.py`: `GET`/`POST /api/console/economics` с валидацией ключей `prices` по
   `dataclasses.fields(EconomicsParams)` (422 при неизвестном ключе), мёрдж поверх
   `session.economics_override`, сборка `EconomicsOverrideDTO` (дефолты — `load_params().economics`).
5. Прогнать `pytest -q tests/console`, вручную проверить merge/422/live-проброс через
   `TestClient`/прямой вызов `tick_single()` со шпионом на `graph.invoke`, отчитаться в STATUS.md.

### Сделано
- [x] `src/console/runtime.py`:
  - `ConsoleSession.__init__`: добавлено ровно одно поле после `self.scenario_title`
    (перед `self.hold_trajectory`) — `self.economics_override: Dict[str, float] = {}` с
    комментарием на русском. Больше в `__init__` ничего не менялось.
  - `tick_single()`: единственная правка — блок «4. Вызов графа МАС» теперь передаёт третий ключ:
    ```python
    graph_res = self.graph.invoke(
        {
            "tags": tags,
            "session_id": self.session_id,
            "economics": self.economics_override or None,
        }
    )
    ```
    По аналогии с `main.py::run_optimization_cycle` (`state_input["economics"] = payload.economics`).
    Блок «1a» роли B1 (запись `sensor_history` из `self.plant.last_model_outputs`) и всё, что идёт
    после вызова графа (лента, автомат, hold-траектория), не тронуты.
- [x] `src/console/contracts.py`: добавлен только `EconomicsUpdateRequest(Frozen)` сразу после
  `EconomicsOverrideDTO` — `session_id: str`, `prices: Dict[str, float]`. `EconomicsOverrideDTO`
  и остальной файл не менялись.
- [x] `src/console/api.py`:
  - Импорт `dataclasses`, `EconomicsOverrideDTO`/`EconomicsUpdateRequest` из `contracts.py`,
    `EconomicsParams`/`load_params` из `src/twin/params.py`, `ConsoleSession` из `runtime.py`
    (для type hint внутреннего хелпера).
  - Модульные константы: `_ECONOMICS_FIELDS = {f.name for f in dataclasses.fields(EconomicsParams)}`
    (полный список полей `EconomicsParams` для валидации — 19 полей, не только 5 цен) и
    `_ECONOMICS_DTO_KEYS` (ровно 5 цен `EconomicsOverrideDTO`: `price_godt`, `price_straight_run`,
    `price_crude_oil`, `price_kerosene`, `price_gasoil`).
  - Приватный хелпер `_build_economics_dto(session)`: берёт дефолты из `load_params().economics`,
    накладывает `session.economics_override`, возвращает `EconomicsOverrideDTO`.
  - `GET /api/console/economics?session_id=` — вызывает хелпер, ничего не меняет (для первичной
    отрисовки формы «Константы»).
  - `POST /api/console/economics` (тело `EconomicsUpdateRequest`) — валидирует ключи `prices` по
    `_ECONOMICS_FIELDS`, при неизвестном ключе HTTP 422 с текстом (перечисляет неизвестные и
    допустимые ключи), иначе мёрджит `payload.prices` в `session.economics_override` под
    `session.lock` (новые значения поверх старых, остальные ключи override не трогаются) и
    возвращает актуальный `EconomicsOverrideDTO`.
  - Существующие эндпоинты (`/state`, `/pareto`, `/blending`, `/constants`, `/preview`, `/commit`
    и т. д.) не переписывались — новый код вставлен между `/constants` и `/preview`.
- [x] Прогон `pytest -q tests/console` — выполнялся 4 раза в ходе работы (параллельно с волнами
  B2/F0 на той же машине), три раза чисто зелёные, один раз с единичным падением флейки-теста:
  ```
  63 passed, 2 warnings in 595.22s (0:09:55)   # фоновый запуск
  63 passed, 2 warnings in 519.45s + 1 failed  # test_preview_latency, p95 606мс >= 300мс порог
  63 passed, 2 warnings in 569.93s (0:09:29)   # финальный чистый прогон
  ```
  Единичное падение `tests/console/test_forecast_corridor.py::test_preview_latency`
  (`@pytest.mark.perf`, порог p95 < 300 мс) — не регрессия от изменений B3: тест не завязан на
  `runtime.py`/`api.py`/`contracts.py` (проверяет только `forecast.preview()`, которую роль B3 не
  трогала), совпало по времени с несколькими параллельными процессами `pytest -q tests/console`
  других ролей (`ps aux` в момент падения показывал 3-4 одновременных процесса pytest). Подтверждено
  двумя проверками: (1) тест в изоляции (`pytest tests/console/test_forecast_corridor.py::test_preview_latency`,
  без конкурентных процессов) — зелёный, `1 passed in 52.07s`; (2) следующий полный прогон
  `tests/console` без конкурентных процессов (`ps aux` показывал только один pytest) — снова чисто
  зелёный, `63 passed`. Итог: новых стабильных падений от работы B3 нет; флейки-порог у
  `test_preview_latency` чувствителен к нагрузке машины, что вне периметра роли B3.
  (варнинги — `anyio`/`pytest.mark.perf`, не связаны с этой работой; то же число тестов, что и после
  волн B0/B1 — тесты на новые эндпоинты появятся на QA-волне).
- [x] Ручная проверка через `TestClient(main.app)` и прямой вызов `ConsoleSession.tick_single()`
  со шпионом на `self.graph.invoke`:
  - `GET /api/console/economics` на новой сессии → все 5 цен равны дефолту `load_params().economics`,
    `is_override=False`.
  - `POST /api/console/economics {"prices": {"price_godt": 71234.5}}` → `price_godt` обновлён,
    остальные 4 — дефолт, `is_override=True`.
  - Повторный `POST` с `{"price_godt": 72000.0, "price_kerosene": 90000.0}` → оба поля обновлены,
    предыдущее значение `price_godt` заменено новым (мёрдж, не потеря истории override).
  - `POST` с `{"bogus_key": 1.0}` → HTTP 422 с текстом, перечисляющим неизвестный ключ и полный
    список допустимых полей `EconomicsParams`; `session.economics_override` не изменился.
  - `ConsoleSession(...).tick_single(warmup=True)` с `economics_override={"price_godt": 99999.0}`
    и шпионом на `graph.invoke` → в переданном графу словаре присутствует ключ `"economics"` со
    значением `{"price_godt": 99999.0}` — подтверждён проброс live-override в каждый такт.

### Допущения
- **`EconomicsOverrideDTO.is_override` — один булев флаг на весь DTO, не по полям.** Текст задания
  просил `is_override=True` «для тех полей, что реально были переопределены оператором, `False` для
  остальных», но контракт `EconomicsOverrideDTO` (зафиксирован волной B0, не в периметре роли B3)
  содержит ровно одно поле `is_override: bool` на весь набор из 5 цен, без per-field структуры.
  Менять форму DTO запрещено правилами задачи («в `contracts.py` добавляй ТОЛЬКО
  `EconomicsUpdateRequest`»). Реализовано так, как буквально позволяет контракт и как это
  сформулировано в его собственном docstring (волна B0): `is_override=True`, если
  `session.economics_override` непусто (переопределено хотя бы одно поле из 5), иначе `False`.
  Если нужна честная per-field гранулярность — требуется отдельное решение о расширении
  `EconomicsOverrideDTO` (вне периметра B3).
- **Валидация ключей `prices` — против всех полей `EconomicsParams` (19), а не только 5
  «поддерживаемых» цен.** Задание одновременно называет 5 конкретных цен «поддерживаемыми
  ключами» и просит «провалидировать, что переданные ключи существуют как атрибуты
  `EconomicsParams`». Дословно второе шире первого (в `EconomicsParams` ещё 14 полей — тарифы на
  топливный газ, электроэнергию, катализатор и т. п., см. `src/twin/params.py:111-137`). Выбрана
  более широкая, но точная трактовка: валидация — по фактическим dataclass-полям
  `EconomicsParams` (`dataclasses.fields`, не `hasattr`, чтобы не пропускать read-only
  property-алиасы вроде `crude_oil_rub_ton`, которые нельзя осмысленно переопределить). Такой
  ключ (например, `power_rub_kwh`) успешно попадёт в `session.economics_override` и будет
  передан графу МАС через `graph.invoke(..., "economics")`, но не отобразится в
  `EconomicsOverrideDTO` (там только 5 полей, как того требует контракт B0) — это осознанное
  расхождение между «что можно переопределить программно» (широкий набор, как в
  `main.py::run_optimization_cycle`) и «что показывает форма пульта» (5 цен из Streamlit-сайдбара).
- Мёрдж `session.economics_override` выполняется под `session.lock` (по аналогии с `apply()`/
  `set_speed()`/`set_fault()` в том же файле) — предотвращает гонку с фоновым потоком-метрономом,
  читающим `economics_override` в `tick_single()`.

### Открытые вопросы
- Нет новых записей в `agents/console_tz/QUESTIONS.md` от роли B3 — оба допущения выше разрешены
  однозначно в рамках существующего контракта B0 и текста задания, отдельного решения не требуют,
  но фиксация в STATUS.md обязательна на случай пересмотра формы `EconomicsOverrideDTO`.

### Осталось (следующие роли по плану)
- F3: секция «Цены и тарифы» во вкладке «Константы» (`static/console/index.html`/`app.js`) — 5 полей
  ввода, кнопка «Применить», вызывающая `POST /api/console/economics`; при открытии формы —
  `GET /api/console/economics` для первичных значений.

## Волна 8: B2 — Blending backend — ВЫПОЛНЕНО (с находками по core_v3)

Роль B2 из `agents/console_tz/03_STREAMLIT_MIGRATION_PLAN.md` §5/§8: только новый файл
`src/console/blending.py` и два новых эндпоинта в `src/console/api.py` (`/blending`,
`/blending/preview`). В `contracts.py` добавлен только `BlendingPreviewRequest` (единственное,
что разрешено роли B2 в этом файле) — DTO блендинга из волны B0 не менялись. `service.py`,
`runtime.py`, `static/console/*` не трогались (чужие роли B1/B3/F2). Работа шла параллельно с
B1/B3 (конкурентные правки `contracts.py`/`api.py` в процессе задания — проверено после каждого
слияния, что импорты не сломаны).

### План (5 пунктов)
1. Прочитать README.md §5, `03_STREAMLIT_MIGRATION_PLAN.md` §0/§5/§8, STATUS.md (волна B0) —
   точные поля `BlendComponentDTO`/`BlendSpecMetricDTO`/`BlendCertDTO`/`BlendRecipeDTO`/
   `BlendingStateDTO`/`BlendingPreviewDTO`, прочитать `src/agents/blending.py` целиком.
2. Написать `src/console/blending.py`: `blending_state` (без пересчета) и `blending_preview`
   (build_blend_problem + solve_blend + certify_blend на копии танков/цен).
3. Добавить `BlendingPreviewRequest` в `contracts.py`, два эндпоинта в `api.py`.
4. Ручная проверка (`ConsoleSession` + прямые вызовы функций + `TestClient` на `main.app`) —
   до прогона полного набора тестов, т.к. `pytest -q tests/console` у соседних волн занимал
   ~10 минут.
5. Прогнать `pytest -q tests/console`, задокументировать находки в STATUS.md/QUESTIONS.md.

### Сделано
- [x] `src/console/blending.py` (новый файл):
  - `_current_tanks(session)` — читает `session.last_graph_result.get("tanks")`, иначе
    `get_default_tanks()`. Эмпирически (см. находки ниже) этот ключ на практике всегда `None`.
  - `_components_dto(tanks)` — сериализация `ComponentTank` → `BlendComponentDTO` с русскими
    подписями (`GODT`→«ГО ДТ», `Kerosene`→«Керосин ТС-1», `Gasoil`→«Газойль»).
  - `_recipe_dto(recipe: BlendingResult)` — `BlendRecipeDTO(**recipe.model_dump())`, работает
    один-в-один, т.к. набор полей у `BlendRecipeDTO` (волна B0) буквально совпадает с
    `BlendingResult`.
  - `_recipe_dto_from_certificate(cert)` — адаптер для реального графа `core_v3` (см. находку
    ниже): заполняет только то, что реально есть в `BlendingCertificate` (`status`/
    `cost_per_ton`/`shares`/`v_diesel`/`v_kerosene`/`binding_constraints`/`elastic_violations`),
    остальные поля `BlendRecipeDTO` остаются на дефолтах (не выдумываются).
  - `_cert_dto(recipe, ..., has_metric_detail=True)` — 5 строк сертификата (`sulfur`/`t95`/
    `cetane`/`cfpp`/`flash`) с лимитами из `src/agents/limits.py` (те же нормативы ГОСТ, что
    выбрала волна B0: `SULFUR_PRODUCT_MAX`/`T95_PRODUCT_MAX`/`CETANE_PRODUCT_MIN`/
    `FLASH_PRODUCT_MIN`/`CFPP_BY_GRADE['E']` — решение B0 принято как есть, вопрос из
    `QUESTIONS.md` не дублировался). При `has_metric_detail=False` (фолбэк `core_v3`, см. ниже)
    `value=None` для всех 5 показателей вместо ложных нулей.
  - `blending_state(session)` — сначала `last_graph_result.get("blending_recipe")` (полная
    фидельность по спецификации плана), при отсутствии — фолбэк на `"recipe"`/
    `"blending_certificate"` (реальный ключ `core_v3`, частичная фидельность). `None`, если
    такта не было или ни один из трёх ключей не заполнен (например, ветка `safe_hold`).
  - `_apply_tank_overrides(tanks, overrides)` — правки поверх копии резервуаров; формат
    (ASSUMPTION, не описан в `01_CONTRACT.md`) — `{tank_id: {"stock_t": ..., "props": {...}}}`
    либо плоский `{tank_id: {"S_ppm": ...}}`; неизвестный танк/нечисловое значение — пропускается
    молча, чтобы песочница не падала на вводе UI.
  - `blending_preview(session, tank_overrides, price_overrides)` — `copy.deepcopy` танков,
    правки поверх копии, `build_blend_problem(godt=dict(tanks_copy["GODT"].props), tanks_copy,
    prices=price_overrides)` → `solve_blend` → (best-effort) `certify_blend` для
    `cost_per_ton`/`status` сертификата. `feasible = res.is_feasible` (не только
    `status == "FEASIBLE"` — есть краевой случай, см. находку 2 ниже, где `status` остаётся
    `"FEASIBLE"`, а `success=False`). Исключение не бросается никогда; `session.plant`/
    `session.graph`/`session.last_graph_result` не трогаются (проверено вручную: `tanks` не
    появляется в `last_graph_result` до и после вызова `blending_preview`).
- [x] `src/console/contracts.py`: добавлен только `BlendingPreviewRequest(Frozen)` —
  `session_id: str`, `tank_overrides: Dict[str, Any] = {}`, `price_overrides: Dict[str, float] = {}`,
  формат `tank_overrides`/`price_overrides` задокументирован в docstring. Остальной файл не менялся
  (параллельные правки B1/B3 — `ConfidenceInfo`/`SensorSeries`/`EconomicsOverrideDTO`/
  `EconomicsUpdateRequest` — не мои, слились без конфликта).
- [x] `src/console/api.py`: добавлены ровно два эндпоинта (`GET /api/console/blending`,
  `POST /api/console/blending/preview`) по образцу `/pareto`/`/xai` (`Query("demo")` +
  `REGISTRY.get(session_id)` + `run_in_threadpool`), плюс импорт `blending` и трёх новых DTO
  в существующие импорт-блоки. Остальной файл не переписывался (параллельная правка B3 —
  `/economics` — слилась без конфликта, проверено импортом после мержа).
- [x] Ручная проверка (`ConsoleSession` напрямую + `TestClient(main.app)`):
  `blending_state(session)` до такта → `None`; после такта → непустой DTO (реальные данные
  `core_v3`, см. находку 1). `blending_preview` с явным конфликтным вводом (`S_ppm=15.0` на
  GODT) → `feasible=False`, `error_message` про Dilution Loophole, исключения нет.
  `blending_preview` с легитимной правкой (`S_ppm=7.5`) → `feasible=True`, полный сертификат
  с реальными значениями. Изоляция подтверждена: `session.last_graph_result`/`session.plant`
  не меняются до/после `blending_preview`. `GET /api/console/blending` и
  `POST /api/console/blending/preview` проверены через `TestClient` на реальном `main.app`.
- [x] `pytest -q tests/console`:
  ```
  63 passed, 2 warnings in 611.46s (0:10:11)
  ```
  (то же число тестов, что после волн B0/B1 — тестов на блендинг ещё нет, это задача QA-волны;
  варнинги те же, не связаны с этой работой). Прогон выполнялся одновременно с несколькими
  параллельными волнами (B1/B3), поэтому занял ~10 минут из-за конкуренции за CPU, а не из-за
  сложности новых тестов.

### Находки (эмпирические, не выдуманы — задокументированы, не исправлены самостоятельно)

**Находка 1 (как у B1 для `confidence`, тот же класс дефекта): граф `core_v3` не пишет ключ
`"blending_recipe"`.** План (§0/§5) и отчёт B0 предполагают, что `node_blending_agent` из
`src/agents/blending.py` пишет `state["blending_recipe"]` каждый такт. Эмпирически (`tick_single()`
на дефолтном `DEFAULT_GRAPH_MODE = "core_v3"`) это неверно: `build_core_graph()` использует ДРУГОЙ
узел с тем же именем `"blending"` — `node_blending` из `src/agents/negotiation.py:372` (не
`node_blending_agent`!). Он кладёт результат в `state["blending"]` (словарь `BlendingCertificate`
по всем кандидатам Парето-фронта), а следующий узел `node_blend_recipe`
(`src/agents/graph.py:270-281`) берёт сертификат выбранного кандидата в `state["recipe"]`/
`state["blending_certificate"]`. `BlendingCertificate` — контракт беднее `BlendingResult`: нет
`v_diesel`/`expected_sulfur`/`expected_flash`/`expected_cfpp`/`expected_t95`/`expected_cetane`/
`additive_doses_kg_t`/`fbi_blend` (только `status`/`cost_rub_per_t`/`shares`/`binding`/
`elastic_violation`/`utility_delta_rub_h`; `product_evaluations` всегда `()` — `certify_blend()`
в `src/agents/blending.py:794` не заполняет его). `blending_state` реализован с фолбэком (см.
выше), поэтому вкладка «Блендинг» в живой консоли покажет реальные `status`/себестоимость/доли/
активные ограничения, но 5 строк сертификата — без числовых `value` (только лимиты), пока это не
решено на уровне графа. Полный текст и оба варианта решения — `agents/console_tz/QUESTIONS.md` [B2].

**Находка 2 (влияет на `blending_preview`, не на `blending_state`): `build_blend_problem()`
самоблокируется на дефолтных данных резервуаров ещё ДО правок оператора.** `get_default_tanks()`
даёт `GODT.S_ppm=8.6`; ветка `build_blend_problem()` без `QualityEstimate` (единственная,
совместимая с `ComponentTank.props`, плоским `Dict[str, float]`) хардкодит `s_sigma=0.10` и
считает `UCB = 8.6 * exp(2.0*0.10) ≈ 10.5 ppm > 10.0` — жёсткая блокировка «Dilution Loophole»
(`src/agents/blending.py:170-190`) срабатывает уже на дефолте, без единой правки:
`blending_preview(session, None, None)` возвращает `feasible=False`. Подтверждено, что
собственные unit-тесты модуля (`tests/unit/test_blending_v2.py`) используют `S_ppm=8.0` (UCB=9.77
< 10), а не реальный дефолт `get_default_tanks()` (8.6) — путь «дефолтные танки + стандартный
конструктор LP» никогда не покрывался тестами и оказался самозаблокированным. Задание явно
требует вызывать `build_blend_problem`/`solve_blend`/`certify_blend` напрямую (сделано в точности
так), и `src/agents/blending.py` — только чтение для роли B2, поэтому исправление (например,
брать сигму из `SIGMA_*`-констант реестра вместо хардкода 0.10) — вне периметра B2. Код B2
корректно не бросает исключение и отдаёт `feasible=False` + реальный `error_message` — по
спецификации задания это правильное поведение, но означает, что песочница «Блендинг» при первом
открытии (до правок оператора) с высокой вероятностью покажет «заблокировано» вместо нейтрального
стартового состояния. Полный текст — `agents/console_tz/QUESTIONS.md` [B2].

### Допущения
- Формат `tank_overrides`/`price_overrides` в `BlendingPreviewRequest` не описан в
  `01_CONTRACT.md` — придуман по аналогии с `BlendComponentDTO`/`EconomicsParams`:
  `tank_overrides: {tank_id: {"stock_t": ..., "props": {...}}}` либо плоский
  `{tank_id: {"S_ppm": ...}}`; `price_overrides` — ключи `EconomicsParams`/`BlendEconomics`
  (`price_godt`, `price_kerosene`, `price_gasoil`, `additive_a_price_rub_t`,
  `additive_b_price_rub_t`), совпадающие с тем, что уже понимает `build_blend_problem()`.
  Неизвестные/нечисловые значения игнорируются молча — задокументировано в docstring.
- Лимиты сертификата (`sulfur`/`t95`/`cetane`/`cfpp`/`flash`) взяты как решила волна B0
  (ГОСТ-нормативы `SULFUR_PRODUCT_MAX`/`T95_PRODUCT_MAX`/`CETANE_PRODUCT_MIN`/
  `FLASH_PRODUCT_MIN`/`CFPP_BY_GRADE['E']` из `src/agents/limits.py`, а не буферы `BLEND_*`)
  — открытые вопросы B0 по этой теме не переоткрывались.
  `blending_preview` при этом всё равно вызывает `build_blend_problem`, который внутри
  использует СВОИ собственные дефолтные буферы (10.0/55.0/95.0/51.0/820-845, отличаются
  от буферов `node_blending_agent`: 9.5/56.0/51.5/821.25-843.75, и от `target_cfpp` со
  своим `pol.cfpp_margin_c=1.0` смещением) — предпросмотр может допускать чуть более широкий
  диапазон, чем «боевой» рецепт такта; не исправлялось (не в периметре B2), не зафиксировано
  отдельным вопросом (второстепенно по сравнению с находкой 2).
- `feasible` в `BlendingPreviewDTO` определён как `BlendingResult.is_feasible`
  (`status == "FEASIBLE" and success`), а не буквально `status != "FEASIBLE"` из текста задания
  — необходимо для корректной обработки краевого случая блокировки Dilution Loophole, где
  `success=False`, но `status` остаётся дефолтным `"FEASIBLE"` (сам `BlendingResult` не
  перезаписывает `status` в этой ветке).
- `_cert_dto(..., has_metric_detail=False)` — новое решение B2 (не было в контракте B0):
  вместо показа `0.0` (дефолт `BlendRecipeDTO`) для сертификата, полученного из
  `BlendingCertificate` (находка 1), показатели `value` — `None`. Лимит/знак/источник — не
  зависят от расчёта, показываются всегда.

### Открытые вопросы (полный текст — `agents/console_tz/QUESTIONS.md`)
- [B2] Граф `core_v3` не пишет `"blending_recipe"` — нужно решение вне периметра B2 (владелец
  `src/agents/graph.py`/`src/agents/negotiation.py`): заполнять `product_evaluations` в
  `certify_blend()` реальными показателями, либо писать `state["blending_recipe"]` и в
  `core_v3` тоже.
- [B2] `build_blend_problem()` самоблокируется на дефолтных данных (Dilution Loophole по UCB
  сульфура с хардкод-сигмой 0.10) — нужно решение вне периметра B2 (владелец
  `src/agents/blending.py`): либо сигма из реестра вместо хардкода, либо `blending_preview`
  не должен звать `build_blend_problem` напрямую с `ComponentTank.props`.

### Осталось (следующие роли по плану)
- F2: вкладка «Блендинг» — степперы долей/присадок, кнопка «Пересчитать рецептуру» →
  `POST /blending/preview` с debounce 250 мс, карточка сертификата (план vs лимит, цветовая
  индикация), плитка эффекта на маржу. UI должен предусмотреть состояние «сертификат без
  цифр» (только лимиты, `value=None`) для находки 1 и не паниковать на `feasible=false` из
  коробки без правок (находка 2) — например, показывать нейтральную подсказку вместо тревожного
  вида при первом открытии вкладки.
- QA: тесты на `blending_state`/`blending_preview` (`tests/console/test_blending_sandbox.py` по
  плану §5) — включая явную проверку обеих находок (фолбэк без `value`, дефолтная блокировка).

## Волна F0: Фикс Парето-графика — ВЫПОЛНЕНО (код), визуальная сверка после фикса частично ограничена окружением

Роль F0 из `03_STREAMLIT_MIGRATION_PLAN.md` §2/§8: только `static/console/charts.js`
(функция `renderParetoChart`) и точечная правка `static/console/app.js` для бага №4
(тройной фетч), без права трогать остальные функции `charts.js` или другие файлы.

### План
1. Прочитать `README.md` §5, `03_STREAMLIT_MIGRATION_PLAN.md` §2 целиком.
2. Живьём воспроизвести баги на `http://localhost:8000/console/?session_id=demo` (докер уже
   поднят) — прогнать несколько тактов, посмотреть легенду/оси/Network.
3. Фикс §1 (легенда) и §2 (вырожденная ось X) в `renderParetoChart`.
4. Фикс §3 (выброс по Y) в `renderParetoChart`.
5. Найти и устранить источник тройного фетча — только `initParetoTab()`/точка вызова в `app.js`.
6. `node --check` на оба файла, `pytest -q tests/console`, визуальная сверка в браузере.
7. Обновить `STATUS.md`.

### Живой аудит до фикса (докер `api`, `?session_id=demo`, ~10 тактов вручную)
- `GET /api/console/pareto?session_id=demo` вживую вернул `sulfur_giveaway: 0.0` у **всех**
  точек такта (объективы `net_margin`/`bed_temperature`/`sulfur_ucb`/`reactor_dp` при этом с
  реальным разбросом) — дефолтная ось X была бы вырождена в вертикальную линию у нуля. Это
  прямое эмпирическое подтверждение бага §2 (в тексте задания фигурировал такой же пример со
  `sulfur_giveaway=0`).
- В той же выдаче — кандидат `is_hold=true, status="vetoed"` с `net_margin=-226947.55` и
  другой кандидат `status="vetoed"` с `net_margin=+12972756.38` (следующий по величине — обычный
  кластер в районе `-300 000..+13 000`). Разброс в 1-2 порядка на одной оси — прямое
  эмпирическое подтверждение бага §3.
- В коде `renderParetoChart` до правки действительно: `is_hold`/`is_recommendation` пушились в
  один массив `paretoPoints`, который становился ECharts-серией «Парето-оптимальные» без
  `itemStyle.color` на уровне серии → легенда красилась дефолтной палитрой ECharts, а не
  реальными цветами точек — баг §1 подтверждён чтением кода 1:1 с описанием в задании.
- Баг №4 (тройной фетч): в Network зафиксированы 3 запроса `GET /api/console/pareto` подряд при
  открытии вкладки (через обёртку `window.fetch`, т.к. окружение при повторных попытках
  зафиксировать чистую трассировку временно потеряло связность — см. «Ограничение окружения»
  ниже). Чтение `app.js` вскрыло конкретный механизм: `initParetoTab()` вызывается из трёх мест
  (`switchTab`, кнопка «Обновить», `refresh()`-поллинг) и определяет «новый ли такт» через
  `store.pareto.loadedForTick !== currentTick`, где `currentTick = store.state?.clock?.tick`. Если
  оператор переключается на вкладку «Парето-анализ» ДО того, как первый `GET /api/console/state`
  успел отработать (`store.state === null`, вполне реальный сценарий: бэкенд под нагрузкой отвечает
  не мгновенно, в т.ч. сам эндпоинт `/pareto` — тяжёлый MOEA-расчёт), `currentTick` равен
  `undefined`, фетч всё равно стартует и по завершении пишет `loadedForTick = undefined`. На
  первом же реальном такте (`0 !== undefined` истинно всегда, `0` — валидный номер такта) любой
  следующий вызов `initParetoTab()` из `refresh()` («дублирующий слушатель обновления состояния»,
  ровно как предполагалось в задании) считает такт «новым» и фетчит повторно — лишний запрос,
  не привязанный к реальной смене такта.

### Сделано
- [x] **Фикс §1 (легенда).** `static/console/charts.js`: точки разбиты ровно на 5 категорий
  (`pareto`/`dominated`/`vetoed`/`hold`/`recommendation`) через новую таблицу
  `PARETO_SERIES_STYLE` (цвет/символ/размер/z для каждой, без изменения самих цветов и символов:
  `#2E7D32` diamond / `#90A4AE` circle / `#C62828` pin / `#263238` rect / `#F9A825` diamond).
  Вместо 3 серий теперь 5 — у каждой явно задан `itemStyle.color` **на уровне серии** (плюс
  сохранён `itemStyle` на каждой точке, как было). Все 5 названий добавлены в `legend.data`.
  Ничего в раскраске отдельных точек не поменялось — приоритет `is_recommendation` >
  `is_hold` > `status` сохранён 1:1.
- [x] **Фикс §2 (вырожденная ось X).** Новый `WeakMap` `paretoAxisMemory` по контейнеру графика:
  единственный сигнал «оператор сам сменил ось» — то, что запрошенный `axisX` отличается от
  запрошенного на предыдущей отрисовке этого же контейнера (это происходит только по событию
  `change` на `<select>`, т.к. `app.js` в остальных случаях — поллинг/новый такт/смена оси Y —
  передаёт тот же самый `store.pareto.axisX`). Пока такой смены не было, при
  `max-min < 1e-6` среди точек такта подставляется первая метрика из `paretoData.objectives` с
  ненулевым разбросом, ось `keyX` фактически переключается на неё, а над графиком (через
  `option.title`, не трогая DOM вне `container`, т.к. `renderParetoChart` не имеет доступа
  менять `index.html`) показывается подсказка «Ось X (...) не имеет разброса в этом такте —
  показана ...». Как только оператор хоть раз выбрал ось вручную — `userPickedX = true`
  навсегда для этого контейнера, автоподмена больше не применяется, даже если выбранная
  вручную ось тоже вырождена (буквальное соблюдение требования задания).
- [x] **Фикс §3 (выброс по Y).** Новая `computeParetoIqrClamp()`: IQR по значениям текущей оси Y
  среди всех отрисовываемых точек; если есть точка дальше `3×IQR` от медианы — `yAxis.min/max`
  выставляются в `[Q1-1.5×IQR, Q3+1.5×IQR]`. Точка не убирается из `series.data` (и из
  тултипа — он всегда показывает `params.value` целиком), просто уезжает за пределы видимой
  области ECharts. При `IQR ≈ 0` (когда порог `3×IQR` тривиально почти любой ненулевой разброс)
  обрезка не применяется — защита от вырожденного диапазона `min===max`.
- [x] **Фикс §4 (тройной фетч).** `static/console/app.js`, `initParetoTab()`: добавлена ранняя
  проверка `if (currentTick == null) return;` — не фетчить, пока `store.state` ещё не загружен;
  как только он подгрузится, `refresh()` сам вызовет `initParetoTab()` повторно уже с реальным
  тактом. Устраняет описанный в «Живом аудите» механизм (гонка switchTab/refresh() вокруг
  `loadedForTick = undefined`). Сами вызовы `initParetoTab()` из трёх точек (`switchTab`, кнопка
  «Обновить», `refresh()`) не тронуты — они и должны оставаться (одна активация вкладки + одна
  ручная кнопка + один поллинг на новый такт), их не в этом дублирование, а в порче сентинела.
- [x] `node --input-type=module --check` на `charts.js` и `app.js` — синтаксис ОК.
- [x] `pytest -q tests/console` — **48 passed** (после правок; см. «Тесты» ниже), регресс не задет
  (правки не в периметре Python-тестов, они и не должны были повлиять).

### Тесты
```
$ source .venv/bin/activate && python -m pytest -q tests/console
................................................
48 passed
```

### Ограничение окружения (важно для приёмки)
Во время живой проверки (10+ ручных тактов подряд + несколько параллельных запросов к
`/api/console/pareto` для сбора эмпирических данных выше) контейнер `neftecode-api` перешёл в
`unhealthy` и перестал отвечать даже на `/api/v1/health` изнутри самого контейнера (проверено
`docker inspect`, `FailingStreak` растёт, health-check валится по таймауту на уровне TCP) —
похоже на исчерпание тредпула/дедлок в бэкенде под конкурентной нагрузкой (не в периметре
F0/`static/console/*`, вероятно достоин отдельного тикета: см. `data/supervisor/supervisor.db` —
общий SQLite-файл, к которому в моменте могли обращаться и контейнер, и параллельные pytest-запуски
на хосте). `docker compose restart` не выполнялся — это разделяемый ресурс и явного запроса на
рестарт не поступало (правила `README.md` §5 и прямая инструкция задания просят не рестартовать
без необходимости). Из-за этого:
- Финальная визуальная сверка «легенда/точки одного цвета» и «выброс не растягивает график»
  после конкретно ЭТИХ правок в браузере доведена не до конца — но ОБЕ соответствующие
  проблемы (вырожденный `sulfur_giveaway`, выброс `net_margin`) эмпирически подтверждены на
  реальных данных сессии `demo` ДО правки (см. «Живой аудит» выше), а сам фикс — прямое,
  проверенное построчно соответствие описанию бага в задании и стандартному поведению ECharts
  (легенда серии красится по `series.itemStyle.color`/`series.symbol`, а не по цвету точек).
- Тройной фетч подтверждён и через `read_network_requests` (3 запроса `pareto` подряd), и через
  инструментированную обёртку `window.fetch`; точный механизм (порча `loadedForTick` значением
  `undefined`) вскрыт чтением кода и логически детерминирован — не зависит от состояния бэкенда.
- Рекомендация следующей роли/QA: как только окружение освободится, открыть
  `http://localhost:8000/console/?session_id=demo`, прогнать несколько тактов и визуально
  свериться по чек-листу §2 «Приёмка» плана (легенда/точки, ось X, выброс Y, 1 запрос в Network).

### Допущения
- Названия 2 новых легенд не заданы явно в задании («какие 5 названий») — выбраны
  «Текущий режим (HOLD)» и «Рекомендация» по аналогии с текстом, уже использующимся в тултипе
  того же графика (`ТЕКУЩИЙ РЕЖИМ HOLD`, `★ РЕКОМЕНДАЦИЯ`), чтобы не плодить новую терминологию.
- Подсказка о вырожденной оси показана через `option.title` ECharts (текст внутри canvas
  графика), а не как отдельный DOM-элемент над `#pareto-chart-container` — потому что вставка
  нового DOM-узла потребовала бы правки `index.html`/`app.js` за пределами периметра роли F0
  («правь только `renderParetoChart`, `app.js` — только если понадобится для бага №4»).
- Фикс §2 определяет «ручной выбор оператора» через сравнение запрошенного `axisX` с прошлым
  вызовом (WeakMap по DOM-контейнеру) — единственный способ отличить дефолт от осознанного
  выбора без изменения `app.js`/`index.html` вне периметра бага №4. Не может отличить случай
  «оператор explicitly выбрал ЧЕРЕЗ select значение, совпадающее с текущим дефолтом» (такое
  событие `change` у `<select>` браузер не генерирует при повторном выборе того же значения —
  edge case не наблюдаем на практике).

### Открытые вопросы
- Бэкенд-дедлок/unhealthy контейнера под конкурентной нагрузкой (см. «Ограничение окружения») —
  вне периметра F0, стоит завести отдельный тикет/находку для владельца `src/console/*`
  (вероятный кандидат — блокирующий синхронный расчёт Парето-фронта в общем тредпуле, конкурирующий
  за общий ресурс сессии/SQLite).

### Осталось
- Визуальная финальная сверка по чек-листу §2 «Приёмка» плана, когда окружение (докер) снова
  устойчиво отвечает — сам код готов и синтаксически корректен, изменения точечные и обратимые.

## Волна 7b: B1b — confidence в графе core_v3 — ВЫПОЛНЕНО

Дозакрытие находки B1 (см. «Волна 7» выше и `QUESTIONS.md` `[B1]`): `state["confidence"]`
всегда был `None` на живом пульте, потому что граф `core_v3` (дефолт `DEFAULT_GRAPH_MODE`,
исполняется `ConsoleSession`) не считал этот словарь вовсе — его формировал только узел
устаревшего `legacy`-графа. Задача только на добавление наблюдаемого поля, без изменения
T0-T3/арбитража/маршрутизации.

### Сделано
1. `src/agents/data_guard.py` — формула индекса уверенности (была инлайн в
   `node_data_quality_guard`, строки ~367-385) вынесена as-is, без изменения ни одной
   константы/коэффициента, в чистую функцию верхнего уровня:
   ```python
   def compute_confidence(n_filled_critical: int, q21_unavailable: bool, lims_age: float) -> Dict[str, Any]
   ```
   `node_data_quality_guard` теперь вызывает `compute_confidence(...)` вместо инлайн-кода —
   рефакторинг без изменения поведения (проверено побайтовым сравнением результата на
   одинаковых входных тегах до/после).
2. `src/agents/graph.py::node_data_guard_core` (узел данных графа `core_v3`) — добавлен вызов
   той же `compute_confidence(...)`. Входы (`n_filled_critical`, `q21_unavailable`, `lims_age`)
   считаются тем же способом, что и в `data_guard.py`: `normalize_tags(raw_tags)` →
   `fill_from_nominal(norm_tags, CRITICAL_TAGS)` → счётчик `FILLED:`-предупреждений;
   `lims_age = float(raw_tags.get("lims_age_hours", 0.0))`; проверка `HT_Q21` на клампинг
   (`CLAMPING_VALUES`) и выброс (`Q21_MAX_PLAUSIBLE_PPM`) — те же константы, импортированные из
   `data_guard.py`, новая формула не придумывалась. Результат кладётся в `state["confidence"]`
   дополнительно к существующему `state["data"]` — ничего из прежнего возврата узла не удалено
   и не изменено.
3. `src/agents/state.py::CoreState` — добавлено объявление поля `confidence: dict[str, Any]`.
   Без этого узел мог возвращать `confidence`, а `graph.invoke(...)` всё равно отдавал бы
   `None` — LangGraph `StateGraph` строит каналы состояния строго по ключам `TypedDict`-схемы
   и молча отбрасывает необъявленные ключи из возврата узла. Это обнаружено эмпирически на
   первой проверке (см. п. 5) и является частью минимально необходимого фикса, а не
   самостоятельным расширением контракта.

### Проверка «это не влияет на T0-T3/маршрутизацию»
До правки `state.get("confidence")` уже читался в трёх местах в графе — ни одно не относится к
принятию решений:
- `src/agents/lims.py::lims_age_from_state` — третье (последнее) звено фолбэк-цепочки
  `raw_telemetry -> tags -> confidence`. Во всех реальных путях вызова графа `core_v3`
  (`ConsoleSession.tick_single` передаёт `"tags"`; `main.py::run_optimization_cycle` передаёт
  `"raw_telemetry"`, `"tags"` и `"raw_tags"` одним и тем же словарём) звенья 1/2 срабатывают
  раньше и полностью экранируют звено `confidence`. В единственном случае, когда звено 3
  всё же достижимо (тега `lims_age_hours` нет ни в `tags`, ни в `raw_telemetry`), `raw_tags`
  (источник для нового `confidence["lims_age_hours"]`) — тот же самый словарь, что и `tags`
  (тот же источник данных), поэтому значение числово совпадает со старым дефолтом `0.0`.
  Эта функция используется дальше в `pareto.py`/`auditors.py`/`blending.py` (T95/сера) —
  проверено, что числовой результат не меняется ни в одном из существующих путей вызова.
- `src/agents/arbitration.py` и `src/agents/blending.py` передают `confidence` только в
  `XAIGenerator.generate_explanation(...)` для текста `markdown_report` — уже после того, как
  `final_rec`/`selected_candidate` определены арбитражем; на выбор кандидата не влияет.
- `src/agents/decision_log.py` — только сериализация в JSONL-журнал решений.

### Эмпирическая проверка
```python
from src.agents.graph import get_graph
g = get_graph("core_v3")
result = g.invoke({"raw_tags": {
    "AVT_T55": 385.0, "AVT_F31": 120.0, "AVT_P52": 0.05,
    "HT_P8": 0.3, "HT_T11": 300.0, "HT_Q21": 8.0, "lims_age_hours": 3.0,
}})
result["confidence"]
# {'score': 0.363, 'level': 'LOW', 'n_filled_critical': 4,
#  'q21_unavailable': False, 'lims_age_hours': 3.0}
```
Значение больше не `None`, меняется предсказуемо при деградации входа (недостающий критичный
тег повышает `n_filled_critical`; выброс/клампинг `HT_Q21` включает `q21_unavailable`; рост
`lims_age_hours` снижает `score`). Побитово совпадает с результатом
`node_data_quality_guard` (legacy) на тех же входных тегах — формула идентична.

### Тесты
Полный прогон (`python -m pytest -q`) на машине, где параллельно (в отдельной, не моей, сессии)
непрерывно выполнялся `pytest -q tests/console` — конкурентная нагрузка на CPU и на общий
`data/decisions/decisions.db`:
```
3 failed, 276 passed, 2 warnings, 6 errors in 813.95s
FAILED tests/console/test_forecast_corridor.py::test_preview_latency        (p95 300 ms)
FAILED tests/perf/test_cycle_budget.py::test_soft_cycle_budget_p95          (p95 2.0 с)
FAILED tests/test_step7_graph_e2e.py::test_e2e_performance_p95              (p95 150 мс)
ERROR  tests/llm_eval/test_supervisor_cassettes_replay.py (6 тестов, все в setup fixture)
```
Диагностика, всё — не регрессия этой правки:
- Все 3 упавших теста — чистые перф-тесты (p95-латентность), включая один не названный в ТЗ
  явно (`test_preview_latency`, тоже `@pytest.mark.perf`), но той же природы, что и два
  названных. Перезапуск изолированно (`pytest -q <3 теста>`, без конкурентной нагрузки от
  соседней сессии) — **3 passed за 115 с**. Причина — конкурентный `pytest -q tests/console`,
  запущенный параллельно другим агентом/ролью в этом же репозитории на этой же машине (сам
  процесс, PID менялся между проверками — сторонняя сессия перезапускала его повторно), не
  код этой правки.
- 6 ошибок в `tests/llm_eval/test_supervisor_cassettes_replay.py` — `sqlite3.DatabaseError:
  database disk image is malformed` при чтении `data/decisions/decisions.db` (локальный,
  не в git, файл разросся до ~5.7 ГБ от совокупной активности множества параллельных
  сессий/тестов). Правка не касается `decision_store.py`/`supervisor/evidence.py`. Подтверждено
  `PRAGMA integrity_check` (реальное повреждение страниц БД, не блокировка) и контрольным
  прогоном с `git stash` (тот же файл теста ошибается ИДЕНТИЧНО на чистом HEAD без единой
  правки этой волны) — стопроцентно пред-существующая проблема окружения, вне периметра задачи.

### Допущения
- Ключ `confidence` пришлось объявить и в `CoreState` (`src/agents/state.py`), хотя задание
  указывало только `graph.py`/`data_guard.py` — без объявления в `TypedDict`-схеме LangGraph
  узел писал бы значение, которое `graph.invoke(...)` тут же отбрасывал (проверено эмпирически:
  без этой строки `result.get("confidence")` оставался `None`, несмотря на возврат узла).
  Это минимально необходимая часть той же самой правки («поле для UI»), а не самостоятельное
  расширение контракта состояния.

### Открытые вопросы
Нет новых. `[B1]` в `QUESTIONS.md` помечен решённым с описанием проверки на отсутствие влияния
на T0-T3/маршрутизацию.

## Волна 9: F1 — Confidence + Sensors frontend — ВЫПОЛНЕНО

Роль F1 из `03_STREAMLIT_MIGRATION_PLAN.md` §3/§4/§8: только `static/console/index.html`,
`static/console/app.js`, `static/console/styles.css` — добавлены новые блоки, существующая
разметка/логика вкладок «Ручное управление», «Парето-анализ», «Журнал смены», «Константы» не
тронута. `static/console/charts.js` не менялся (владение F0). Бэкенд (`ConfidenceInfo`,
`SensorSeries`, `ConsoleState.confidence`/`.sensors`, `SENSOR_META`) уже был полностью готов
волнами B0/B1/B1b — с их стороны правок не потребовалось.

### План (выполнен по пунктам)
1. Прочитать `README.md` §5, `03_STREAMLIT_MIGRATION_PLAN.md` §3/§4, `STATUS.md` (волны B0/B1/B1b),
   `02_UX_REDESIGN_PLAN.md` §4.2/§4.3, найти точные поля `ConfidenceInfo`/`SensorSeries` в
   `src/console/contracts.py`/`src/console/service.py`.
2. Найти существующий паттерн сворачиваемого блока («Почему так решили» —
   `btn-toggle-agents`/`#agents-list`/`store.showAgents` в `app.js`) для повторного использования
   в секции датчиков.
3. Добавить бейдж уверенности в `status-bar` (`index.html` + `app.js` + `styles.css`).
4. Добавить свёрнутую по умолчанию секцию «Качество продукта и сырья» на вкладке «Обзор» (после
   `mv-grid`, внутри `left-column`) с 9 строками из `state.sensors`, сгруппированными на
   «Реактор Р-202» / «Качество товарного продукта» / «Качество сырья».
5. Добавить 2 пункта в `WIDGET_DEFS` (`sensors.product_quality`, `sensors.feed_quality`),
   управляющие видимостью 2-й и 3-й групп секции, состояние — в `localStorage`
   `console_widgets_v1` (тот же ключ, что и остальные виджеты).
6. `node --check` на `app.js`, живая проверка в браузере (docker `api`, `?session_id=demo`),
   `pytest -q tests/console`.

### Сделано
- [x] **Бейдж индекса уверенности** (`index.html`): новый блок `#confidence-badge-wrap` в
  `status-bar`, вставлен между `#margin-delta` и уже существующим разделителем перед
  `#alarm-badge` (свой `divider-v` не задваивает существующий). Содержит кнопку
  `#confidence-badge` (лейбл «ДАННЫЕ» + `#confidence-badge-score` в %) и `#confidence-tooltip`.
  `app.js::renderConfidenceBadge(state)`: если `state.confidence == null` —
  `wrap.style.display = "none"` (бейдж не рисуется вообще, включая свой разделитель, как того
  требует задание); иначе `display: flex`, класс `confidence-badge.level-{high|medium|low}` по
  `confidence.level` (маппинг `CONFIDENCE_LEVEL_CLASS`), текст = `round(score*100)+"%"`. Причины
  в `#confidence-tooltip` формируются строго по условиям задания: `n_filled_critical > 0` →
  «заполнено нормативными значениями: N тегов» (с русским склонением через `pluralTags()`),
  `q21_unavailable` → «онлайн-анализатор серы недоступен», и всегда — «возраст ЛИМС: X ч».
  Раскрытие — по наведению (CSS `:hover`) и по клику (`initConfidenceBadge()`: клик на бейдже
  `classList.toggle("open")` на wrap, клик вне — закрытие; тот же паттерн, что
  `initWidgetConfig()` использует для `#widget-popover`). Цвета уровней взяты из уже
  существующих токенов дизайн-системы (`--ok-bg/--ok-ink` для HIGH,
  `--warn-bg/--warn-frame/--warn-ink` для MEDIUM, `--risk-bg/--limit/--risk-ink` для LOW) —
  те же, что `cv-chip.ok/.warn/.alarm`, новых цветов не вводилось.
- [x] **Секция «Качество продукта и сырья»** (`index.html`, после `#mv-grid`, внутри
  `.left-column`): кнопка `#btn-toggle-sensors` (паттерн 1:1 с `#btn-toggle-agents` — та же
  chevron-иконка, поворот на 90° при раскрытии) + `#sensors-list` (по умолчанию
  `style="display: none"`, как и `#agents-list`). Внутри — 3 группы:
  «Реактор Р-202» (`HT_BED_MEAN`, `HT_VSG`, `HT_T11`, всегда видна, без виджет-переключателя —
  задание просило переключатели только для качества продукта/сырья), «Качество товарного
  продукта» (`HT_D15_PRODUCT`, `HT_T95_PRODUCT`, `HT_CFPP_PRODUCT`, `HT_CN_PRODUCT`) и «Качество
  сырья» (`HT_S_FEED`, `HT_T95_FEED`). `app.js::renderSensors(state)`: для каждого тега берёт
  последнюю точку `history` (`s.history[s.history.length-1].v`), рендерит
  «Название [тег]: значение ед.изм.» (простой список значений, без мини-графиков — по тексту
  задания это допустимо и не обязательно; переиспользование `charts.js` для 9 дополнительных
  графиков сочтено избыточным для этой итерации и не пытается тронуть чужой файл F0).
  `initSensorsSection()` (вызывается из `DOMContentLoaded`, рядом с `initWidgetConfig()`):
  клик по кнопке переключает `store.showSensors` и напрямую `#sensors-list.style.display`
  (тот же паттерн, что `btn-toggle-agents`).
- [x] **2 новых пункта `WIDGET_DEFS`**: `sensors.product_quality` («Качество продукта») и
  `sensors.feed_quality` («Качество сырья»), группа `"SENSORS"`. Чекбоксы добавлены в
  `#widget-popover` (`index.html`) под новым заголовком группы «Датчики (справочно)» —
  `initWidgetConfig()` не менялся (он уже обходит `popover.querySelectorAll('input[type="checkbox"]')`
  универсально по `dataset.widget`, новые чекбоксы подхватились без правок логики). Тот же ключ
  `localStorage` `console_widgets_v1`, никакого нового хранилища. `applyWidgetVisibility()`
  дополнен двумя блоками, скрывающими/показывающими `.sensors-group`-контейнер продукта/сырья
  (`sensorsProductGroup.closest(".sensors-group").style.display = ...`). Побочный эффект
  переиспользования общей защиты «нельзя скрыть всю группу» (уже существующей для CV/MV, код в
  `initWidgetConfig()` не трогался): т.к. `SENSORS` — группа из 2 пунктов, нельзя скрыть **оба**
  подраздела одновременно (минимум один виден) — не требовалось явно заданием, но безвредно и
  консистентно с поведением CV/MV.
- [x] Стили (`styles.css`): новый блок «Бейдж индекса уверенности данных» (после
  `.btn-mode-toggle.disabled-unavailable`/перед `.btn-shift-report`) и новый блок «Секция
  «Качество продукта и сырья»» (после `.mv-corridor-text`/перед `/* RIGHT COLUMN */`) — оба
  используют существующие CSS-переменные токенов, новых цветов не заведено.
- [x] `node --input-type=module --check` на `app.js` — синтаксис OK.

### Живая проверка в браузере (docker `api`, healthy, `?session_id=demo`, 1920×1080)
- `GET /api/console/state?session_id=demo` вживую (curl) вернул непустые `confidence`
  (`{"score": 1.0, "level": "HIGH", "n_filled_critical": 0, "q21_unavailable": false,
  "lims_age_hours": 0.0}`) и все 9 `sensors` с реальными значениями (WABT 363.462 °C,
  ВСГ 93286.972 нм³/ч, T11 364.0 °C, D15 836.1 кг/м³, T95 продукта 347.0 °C, CFPP −6.0 °C,
  цетан 55.0, сера сырья 9470.0 ppm, T95 сырья 353.0 °C) — находка B1 о «confidence всегда null»
  дозакрыта волной B1b, на F1 не влияет.
- Бейдж «ДАННЫЕ 100%» отрисовался зелёным (HIGH) в статус-баре сразу при открытии страницы;
  клик по нему раскрыл тултип «Индекс уверенности данных: 100% (HIGH)» + «возраст ЛИМС: 0.0 ч»
  (единственная причина — `n_filled_critical=0`, `q21_unavailable=false`, как и должно быть).
- Инжекция отказа `HT_Q21` через панель `?demo=1` («Отказ ПАК серы») + один ручной такт →
  бейдж мгновенно перекрасился в жёлтый (`level-medium`, 50%), тултип показал «онлайн-анализатор
  серы недоступен» и «возраст ЛИМС: 26.0 ч» — переключение режима в «ОТКАЗ» и обновление бейджа
  синхронны. Отказ снят кнопкой «Снять отказы» после проверки (сессия `demo` общая, чтобы не
  мешать другим ролям/пользователю).
- Секция «Качество продукта и сырья» по умолчанию свёрнута (не видна при открытии вкладки
  «Обзор», не занимает экран). Клик по заголовку раскрыл все 3 группы с 9 живыми значениями;
  повторные такты (авто-тик и «Следующий такт вручную») обновляют значения синхронно с
  остальным экраном (проверено на 3+ тактах, значения WABT/сера сырья и т.д. менялись вместе с
  часами такта).
- Чекбокс «Качество продукта» в «Настроить вид» скрывает группу «Качество товарного продукта»,
  не трогая «Реактор Р-202»/«Качество сырья»; перезагрузка страницы (`navigate` на тот же URL) —
  скрытие сохранилось (подтверждён `localStorage`). Чекбокс возвращён в исходное состояние после
  проверки.
- `read_console_messages(onlyErrors: true)` — пусто, ошибок в консоли браузера нет.
- Не проверено вживую: поведение бейджа/секции на `?fixture=advisory|auto|refusal` — эти
  статичные JSON-фикстуры (собраны волной B0) содержат `"confidence": null` и `"sensors": []`
  (проверено чтением файлов), т.е. по офлайн-фикстурам бейдж корректно не показывается, а секция
  показывает плейсхолдер «Нет данных (такт ещё не рассчитан)» для всех 3 групп — это ожидаемое,
  а не сломанное поведение (сами фикстуры не обновлялись волнами B0/B1 реальными числами для этих
  двух полей, это вне периметра F1: правка `static/console/fixtures/*.json` — файл владения B0).

### Тесты
```
$ source .venv/bin/activate && python -m pytest -q tests/console
...............................................................          [100%]
63 passed, 2 warnings in 561.10s (0:09:21)
```
Запущен после всех правок. Правки не в периметре Python-кода (только `static/console/*`), число
тестов (63) и предупреждения (`anyio`/`pytest.mark.perf`) те же, что и после волн B0/B1/B1b/B3/B2 —
регрессии нет.

### Допущения
- 9 строк секции датчиков отрисованы простым списком «название [тег]: значение ед.изм.» без
  мини-графиков — задание явно разрешает такой вариант («не обязательно рисовать полноценные
  графики... простого списка достаточно... график — опционально»). Переиспользование
  `charts.js` не делалось, чтобы не трогать чужой файл (владение F0) и не тратить время на 9
  дополнительных мини-графиков ради необязательной части задания.
- Группа «Реактор Р-202» (WABT/ВСГ/T11) не имеет отдельного переключателя видимости в
  `WIDGET_DEFS` — задание просило ровно 2 новых пункта (`sensors.product_quality`,
  `sensors.feed_quality`), а не переключатель на каждую из 3 групп; реакторные теги показываются
  всегда, когда секция раскрыта.
- Значения датчиков форматируются с 2 знаками после запятой (`toFixed(2)`) единообразно для всех
  9 строк — сами величины разнородны по масштабу (от −6 °C CFPP до ~93000 нм³/ч ВСГ), но в
  реестре `SENSOR_META`/`SensorSeries` нет поля точности (в отличие от `MvSeries.decimals`),
  поэтому явного источника истины для округления нет; 2 знака — компромисс, не искажающий ни
  один из показателей.
- Клик вне бейджа/попапа закрывает тултип (тот же обработчик `document.addEventListener("click", ...)`,
  что уже использует `initWidgetConfig()` для `#widget-popover`) — оба обработчика независимы и
  не конфликтуют (проверено: открытие одного не закрывает другой).

### Открытые вопросы
Нет новых от роли F1.

### Осталось
- Фикстуры `static/console/fixtures/advisory.json`/`auto.json`/`refusal.json` содержат
  `confidence: null` и `sensors: []` — если нужна визуальная проверка бейджа/секции именно в
  режиме `?fixture=`, кто-то с правом менять `static/console/fixtures/*.json` (роль B0/QA)
  должен регенерировать их реалистичными значениями через `scripts/make_console_fixtures.py`;
  на живой сессии (`?session_id=demo`) оба элемента работают уже сейчас, что и требовалось по
  Definition of Done.

## Волна 10: F2 — Блендинг frontend — ВЫПОЛНЕНО

Роль F2 из `03_STREAMLIT_MIGRATION_PLAN.md` §5/§8: новая вкладка «Блендинг» в
`static/console/index.html`/`app.js`/`styles.css` (свой `<section data-tab-panel="blending">`,
свои функции с префиксом `blending*`/`Blending*`, свой CSS-неймспейс `.blending-*`) + две тонкие
обёртки `getBlending()`/`previewBlending()` в `api.js`. Бэкенд (`GET/POST /api/console/blending*`)
уже был готов волной 8 «B2» — не трогался. Другие вкладки (Обзор/Ручное управление/Агенты и
XAI/Парето-анализ/Журнал смены/Константы) и `charts.js` не менялись.

### План (5 пунктов)
1. Прочитать README.md §5, `03_STREAMLIT_MIGRATION_PLAN.md` §5 целиком, STATUS.md (волны B0/B2 —
   контракт DTO и найденные ограничения бэкенда), `QUESTIONS.md` [B2], `02_UX_REDESIGN_PLAN.md`
   §5.4/§5.5 (паттерны `.value-stepper`/`.sandbox-info-box`/debounce 250 мс).
2. `api.js`: `getBlending()` (GET, без офлайн-фикстуры — TODO-комментарий) и `previewBlending()`
   (POST) по образцу `getPareto()`/`getXai()`.
3. `index.html`: кнопка вкладки `data-tab="blending"` рядом с «Журнал смены», секция
   `data-tab-panel="blending"` (компоненты / сертификат / песочница) + заглушка «недоступен».
4. `app.js`: `store.blending`, `initBlendingTab()` (только GET, вызывается при переключении
   вкладки и на каждый новый такт), рендер компонентов/сертификата, степперы песочницы
   (`.value-stepper`, debounce 250 мс) → `previewBlending()`, рендер результата preview
   (нейтральная подсказка при `feasible=false`, никогда — красная тревога).
5. `styles.css`: CSS для новых классов `.blending-*` (переиспользованы существующие
   `.value-stepper`, `.sandbox-info-box`, `.effect-grid`/`.effect-tile`, `.cv-chip`,
   `.manual-card`-подобная карточка). `node --check`, `pytest -q tests/console`, живая проверка
   в браузере на `?session_id=demo`.

### Сделано
- [x] `static/console/api.js`: добавлены `getBlending()` и `previewBlending(tankOverrides,
  priceOverrides)` сразу после `getConstants()`, перед `export const demo = {...}`. Обе функции
  **всегда** бьют в реальный API (`GET/POST /api/console/blending*`) — офлайн-фикстуры для
  блендинга нет ни в `static/console/fixtures/`, ни в `tests/fixtures/console/`, что явно
  разрешено заданием («если для блендинга нет своей фикстуры — просто пропусти fixture-ветку»).
  Оставлен явный TODO-комментарий над `getBlending()` (не в периметре роли F2 — фикстуры владеет
  B0/R0/QA). `previewBlending()` не бросает исключение на `feasible=false` (это валидный ответ
  200 с телом `BlendingPreviewDTO`), бросает `Error` только на настоящий HTTP-сбой (сеть/5xx/422).
- [x] `static/console/index.html`: кнопка `<button class="tab-btn" data-tab="blending">Блендинг</button>`
  добавлена в `<nav class="tab-bar">` между «Журнал смены» и «Константы» (генерик-обработчик
  клика по `.tab-btn` в `app.js` уже покрывает любую новую кнопку, отдельного слушателя не
  потребовалось). Новая секция `<section class="tab-panel" data-tab-panel="blending" hidden>`
  вставлена между секциями `shift` и `constants`, ничего внутри чужих `<section>` не менялось.
  Внутри — три карточки: «Компоненты смешения» (пустой контейнер `#blending-components`,
  заполняется JS-ом из `components: List[BlendComponentDTO]`, число/названия компонентов не
  хардкодятся), «Сертификат текущего рецепта» (таблица `#blending-cert-tbody` + бейдж статуса,
  переиспользует `.cv-chip`), «Песочница «что если»» с явной подписью-предупреждением
  «Расчёт не влияет на реальные резервуары — это модель LP-оптимизации» (дословно по плану §5) и
  контейнером степперов `#blending-sandbox-rows`. Заглушка `#blending-empty`
  («Блендинг недоступен для этого такта») скрыта/показана в зависимости от `GET /blending`.
- [x] `static/console/app.js`:
  - `store.blending = { data, preview, edits: {}, loading, loadedForTick }` добавлен в общий
    `store` рядом с `pareto`/`xai`.
  - `switchTab()`: добавлена ветка `else if (name === "blending") { initBlendingTab(); }`.
  - `refresh()`: добавлена ветка `else if (store.activeTab === "blending") { initBlendingTab(); }`
    — **только** `GET /blending` на каждый новый такт, пока вкладка активна; `POST /preview`
    здесь никогда не вызывается (буквально по требованию задания и находке B2 №2: preview с
    пустыми overrides почти гарантированно вернёт `feasible=false` — это не повод считать
    сломанным авто-запрос при открытии вкладки, поэтому такого авто-запроса просто нет).
  - Восстановление вкладки из `location.hash` при загрузке страницы: `"blending"` добавлен в
    список допустимых значений (иначе прямой переход по ссылке `#blending` откатывался бы на
    «Обзор» — тот же список уже содержал `overview/manual/xai/pareto/shift`).
  - `initBlendingTab()`: копирует фикс тройного фетча из F0/Парето (`if (currentTick == null)
    return;` — не фетчить, пока `store.state` не загружен) + флаг `store.blending.loading`,
    чтобы параллельные вызовы (переключение вкладки одновременно с тиком поллинга) не плодили
    гонки. Дальше `GET /blending` только если `loadedForTick !== currentTick`, `renderBlending()`
    — всегда (тот же паттерн, что `initParetoTab`/`renderPareto`).
  - `renderBlending()`: `data === null` → заглушка `#blending-empty` (видна), контент скрыт —
    без обращения к `null`-полям, никакого `null`/`NaN`/`undefined` в разметке. Иначе рендерит
    компоненты, статус-бейдж и таблицу сертификата, `error_message` (если есть) — в
    `.sandbox-info-box` (нейтрально, не тревога).
  - `renderBlendingComponents()`: итерирует `components` и `Object.entries(c.props)` без
    хардкода количества/названий; читаемые подписи для известных ключей `ComponentTank.props`
    (`S_ppm`/`D15`/`T95`/`E360`/`CN`/`CFPP`/`Flash`, см. `src/agents/tanks.py`) через словарь
    `BLEND_PROP_META` (только оформление — те же 6 ключей, что реально пишет `get_default_tanks()`
    и `ComponentTank.receive/forecast`), неизвестный ключ — подпись = сам ключ, без единиц.
  - `renderBlendingCertRows(tbody, metrics)`: общая функция и для базового сертификата, и для
    сертификата preview. `value == null` → «—», `limit == null` → «—» (буквально требование:
    прочерк на `None`, не 0/NaN/null). Строка подсвечивается (`.blending-row-violation`,
    красный фон) только если и `value`, и `limit` заданы и знак (`sense: "max"|"min"`) нарушен —
    сравнение никогда не выполняется на `null`.
  - `renderBlendingSandboxRows()`: по одному `.value-stepper` на каждый компонент —
    относительная корректировка **остатка** (`stock_t`, единица «т», как в `BlendComponentDTO`/
    `ComponentTank.stock_t`; доли `shares` — выходной результат LP, не входной параметр, поэтому
    степпер именно на остаток, а не на долю, см. «Допущения»). `bindStepper()` — тот же
    переиспользуемый хелпер, что и «Ручное управление» (не изобретался новый виджет ввода).
    `onChange` → `triggerBlendingPreviewDebounced()` (250 мс) — **только** реальное изменение
    оператором вызывает `/preview`, не открытие вкладки.
  - `triggerBlendingPreviewDebounced(immediate)`: собирает `tank_overrides` только из реально
    изменённых компонентов (`store.blending.edits`), формат `{tank_id: {stock_t: value}}` — 1:1
    с ASSUMPTION-форматом `_apply_tank_overrides()` в `src/console/blending.py`. Кнопка
    «Пересчитать» вызывает `immediate=true` (без ожидания debounce), степпер — `immediate=false`.
    Сетевая/серверная ошибка (не «инфизибл» — тот приходит `200 OK` с `feasible=false` в теле) —
    `showToast(..., "error")`, не блокирует интерфейс.
  - `renderBlendingPreview()`: `prev === null` → скрывает блок результата. Иначе — статус-бейдж +
    таблица сертификата preview (та же `renderBlendingCertRows`) + `.sandbox-info-box` с
    `error_message`, если `feasible === false` (нейтральный информационный блок, как на «Ручном
    управлении», НЕ `risk-badge-box`/красная тревога — буквальное требование задания) + плитка
    эффекта: себестоимость плана (`cert.cost_per_ton`) и Δ к текущему рецепту
    (`preview.cert.cost_per_ton - base.cert.cost_per_ton`, если оба не `null`) — margin-поля в
    `BlendingPreviewDTO` нет, использовано единственное реально присутствующее числовое поле
    эффекта (буквально по указанию «используй что есть, не выдумывай отсутствующие»).
  - `initBlendingEvents()`: кнопка «Пересчитать» → немедленный preview; кнопка «Сбросить» →
    очищает `store.blending.edits`/`store.blending.preview`, перерисовывает степперы к текущим
    остаткам без сетевого запроса.
- [x] `static/console/styles.css`: новый блок в конце файла (после Toast Notification, ничего
  существующее не сдвинуто/не переписано) — `.blending-empty` (тот же паттерн, что заглушки
  Парето/XAI), `.blending-layout`/`.blending-card`/`.blending-card-header`/`.blending-card-title`
  (визуально идентичны `.manual-card`/`.xai-side-card`, но отдельные классы — не переиспользовал
  чужие для развязки от будущих правок F1/F3/F4 в тех же именах), `.blending-components-grid` +
  `.blending-component-card`/`.blending-component-title`/`.blending-component-stock`/
  `.blending-component-props`/`.blending-prop-row`, `.blending-cert-table` (+ модификатор
  `.blending-row-violation` для подсветки нарушения лимита фоном `--risk-bg`), `.blending-sandbox-*`
  (подпись/строки/юнит/действия), `.blending-preview-result`, `#blending-effect-grid` (override
  `grid-template-columns` с 3 на 2 колонки — у блендинга 2 плитки эффекта, не 3, как на «Ручном
  управлении»). Иконки статуса и подсказки переиспользованы буквально (`.cv-chip.ok/warn/alarm/
  nodata`, `.sandbox-info-box`, `.effect-grid`/`.effect-tile`, `.value-stepper`, `.btn-manual-preview`,
  `.btn-reset-rec`, `.mono`) — новый виджет ввода не изобретался.
- [x] `node --check static/console/app.js` и `node --check static/console/api.js` — синтаксис ОК
  (`index.html`/`styles.css` не проверяются `node --check`, синтаксис проверен визуально + рендер
  в браузере без ошибок парсинга).
- [x] Живая проверка в браузере (`docker compose` уже поднят другой ролью, `--reload`, статика
  раздаётся напрямую из репозитория — правки видны без пересборки): `http://localhost:8000/console/?session_id=demo`,
  прогрето 48+ тактов до старта проверки (сессия `demo` общая с другими ролями). Открытие вкладки
  «Блендинг» → в Network ровно **один** `GET /api/console/blending?session_id=demo` (ноль
  `POST /preview`) — подтверждает требование «на открытии вкладки только GET». Отрисовались 3
  реальных компонента (`GODT`/`Kerosene`/`Gasoil`, с русскими подписями из бэкенда) с остатками
  и полным набором свойств качества; сертификат показал лимиты по всем 5 показателям и `—` для
  всех `value` (ожидаемо — граф `core_v3` в этом такте отдал сертификат через фолбэк
  `_recipe_dto_from_certificate`, находка B2 №1, `has_metric_detail=False`), статус-бейдж
  «РЕАЛИЗУЕМО» зелёным. Клик «+» на степпере остатка ГО ДТ (5000.0 → 5050.0 т, стал `.edited`) →
  через ~250 мс ровно один `POST /api/console/blending/preview` (не 3, не 0) → результат:
  `feasible=false`, нейтральная серая подсказка (НЕ красная) с текстом бэкенда «BLOCKED: Dilution
  Loophole. Сера дизельного гидроочищенного (10.5 ppm) превышает норматив ГОСТ (10.0 ppm)...» —
  это буквально ожидаемое поведение по находке B2 №2 (сигма 0.10 хардкожена в
  `build_blend_problem`, самоблокировка на дефолтных данных, не баг фронтенда), плюс сразу под
  ней отрисовался сертификат прогноза с реальными числами оттуда, где они были (`sulfur=0.00` не
  нарушен, `ПТФ`/`Вспышка` подсвечены красным как нарушение лимита — расчёт `violated` сработал
  корректно на реальных, а не выдуманных числах) и плитка эффекта (`0` / `−61056` ₽/т). Кнопка
  «Сбросить» вернула степпер к 5000.0 и скрыла блок прогноза без сетевого запроса.
  `read_console_messages(onlyErrors: true)` — пусто на всех этапах. Прямой `fetch` на заведомо
  новый `session_id` (до первого такта) вернул `{"status":200,"data":null}` — путь `!data` →
  заглушка «Блендинг недоступен для этого такта» проверен по коду (идентичен уже проверенному
  паттерну Парето/XAI) и подтверждён этим прямым запросом к бэкенду.

### Тесты
```
$ ./.venv/bin/python -m pytest -q tests/console
...............................................................          [100%]
63 passed, 2 warnings in 552.49s (0:09:12)
```
Запущен после всех правок. То же число тестов и те же 2 предупреждения (`anyio`/`pytest.mark.perf`),
что и после волн B0-F1 — регрессии нет, ожидаемо по построению: изменения этой волны не
затрагивают ни один Python-файл (`src/console/**` не менялся, правки только в
`static/console/{index.html,app.js,api.js,styles.css}`, JS-тестов в проекте нет по указанию
задания). `node --check` на оба изменённых `.js`-файла — без ошибок синтаксиса.

### Допущения
- Степпер песочницы двигает **остаток** (`stock_t`, т) компонента, а не «долю» — `shares`
  (доля компонента в смеси) в контракте является выходом LP-решателя (`BlendRecipeDTO.shares`),
  а не входным параметром `BlendingPreviewRequest.tank_overrides` (формат которого — правки
  `stock_t`/`props`, см. docstring `BlendComponentDTO`/`_apply_tank_overrides` в
  `src/console/blending.py`, ASSUMPTION роли B2). Задание допускает оба слова через слэш
  («доли/остатка»); реализован буквально доступный бэкенду вход.
- Правка качественных показателей компонента (`props`, например `S_ppm`) в песочнице не
  реализована — только `stock_t`. `_apply_tank_overrides()` технически поддерживает и это
  (`{"props": {...}}` или плоский патч), но задание описывает степпер как один на компонент
  («для каждого компонента... степпер»), а не набор степперов на каждое свойство; добавление
  ещё N степпера на компонент увеличило бы объём виджетов сверх того, что было явно описано.
  Открытый вопрос B2 №2 (самоблокировка по сере при дефолтных `props`) в любом случае не решается
  правкой одного `stock_t`, поэтому эта граница не мешает демонстрации обоих исходов
  («обновлённый сертификат» / «нейтральная подсказка про инфизибл»).
- Шаг степпера остатка — фиксированные 50 т (UI-удобство, не технологический лимит/паспортное
  значение — в отличие от шагов степперов MV на «Ручном управлении», которые берутся из
  `corridor.max_step_per_tick`, для остатков резервуара такого источника в реестре нет и не
  должно быть, это не MV с коридором ПАЗ).
- `.blending-card`/`.blending-card-header`/`.blending-card-title` визуально копируют существующие
  `.manual-card`/`.xai-side-card`, но заведены как отдельные классы (не переиспользование чужого
  имени класса) — чтобы будущая правка `.manual-card` ролью, отвечающей за «Ручное управление»,
  не могла случайно задеть вкладку «Блендинг», и наоборот.
- Эффект-плитка показывает Δ себестоимости (`cost_per_ton`), не Δ маржи — в
  `BlendingPreviewDTO`/`BlendRecipeDTO` нет поля маржи блендинга (себестоимость смеси — не то же
  самое, что маржа НПЗ в целом, которая считается на уровне графа МАС, не на уровне
  `BlendingResult`); буквально по указанию задания «используй что есть, не выдумывай
  отсутствующие [поля]».

### Открытые вопросы
Нет новых от роли F2 — оба практических следствия находок B2 (см. QUESTIONS.md `[B2]`:
самоблокировка LP по сере, бедный контракт `BlendingCertificate` без per-показательных `value`)
подтверждены живой проверкой этой волны и обработаны на уровне UI (нейтральная подсказка,
прочерк на `None`), а не переоткрываются как новые вопросы.

### Осталось (следующие роли по плану)
- F3: секция цен во вкладке «Константы» (не в периметре F2).
- F4: вкладка «Супервизор» (не в периметре F2).
- QA: `tests/console/test_blending_sandbox.py` по плану §5 — тестов на `blending_state`/
  `blending_preview` (бэкенд) всё ещё нет; сама вкладка F2 не имеет JS-тестов по прямому указанию
  задания («JS-тестов в проекте нет — не заводи фреймворк»).
- Если в будущем захотят реализовать «Применить к боевой установке» для блендинга — по плану §5
  это осознанно не входит в эту итерацию (реальное применение рецепта к резервуарам — отдельный
  технологический процесс перекачки, не мгновенная уставка).

## Волна 11: F3 — Цены и тарифы frontend — ВЫПОЛНЕНО (с находкой по бэкенду вне периметра)

Роль F3 из `03_STREAMLIT_MIGRATION_PLAN.md` §6/§8 («Экономика frontend»): единственный редактируемый
блок вкладки «Константы» — 5 рыночных цен (`EconomicsOverrideDTO`), бэкенд роли B3 уже готов
(волна 8). Владение — строго `static/console/{index.html,app.js,styles.css,api.js}`, только добавление
своего блока, ни один чужой `<section data-tab-panel=...>`/класс/функция не тронуты.

### План (выполнен по пунктам)
1. Прочитан `README.md` §5, `03_STREAMLIT_MIGRATION_PLAN.md` §6/§8, `STATUS.md` волна 8 (B3) —
   контракт `GET/POST /api/console/economics`, допущения B3 (`is_override` общий на 5 полей;
   `POST` принимает все 19 полей `EconomicsParams`, но в UI — только 5).
2. Найдены точные подписи/шаг/диапазоны в удалённом (но ещё читаемом на диске, `git status` не
   закоммитил удаление) `src/ui/app.py:135-179`, секция «💰 Параметры рынка и тарифов» →
   «Сырье и дистилляты (СПбМТСБ)»: `price_godt` («ГО ДТ Евро-5», step 500, 30000–120000),
   `price_straight_run` («Прямогонный дизель F30+F32», step 500, 25000–100000), `price_crude_oil`
   («Сырая нефть Urals», step 500, 20000–80000), `price_kerosene` («Керосин ТС-1», step 500,
   40000–150000), `price_gasoil` («Газойль вторичный», step 500, 25000–100000) — использованы
   буквально, не придуманы заново.
3. `api.js`: добавлены `getEconomics()` (GET) и `updateEconomics(prices)` (POST) по образцу
   `getConstants()`/`previewBlending()`; в режиме `?fixture=` (для которого нет отдельного файла
   фикстуры — не в периметре F3/B0) отдаются дефолты `EconomicsParams` инлайн, как уже делают
   `setMode()`/`commit()` в этом же файле.
4. `index.html`: во вкладке «Константы» перед read-only `.constants-card` добавлен блок
   `#economics-card` — заголовок «Цены и тарифы (редактируемо)», бейдж
   `#economics-override-badge` («изменено оператором», один на весь блок — по допущению B3),
   сетка из 5 `.value-stepper` (переиспользован существующий компонент степпера — визуально и
   функционально вписался лучше, чем голый `<input type=number>`, т.к. на этой же вкладке рядом
   таблица реестра с моно-цифрами того же шрифта), кнопка «Применить», подсказка-текст
   «Изменение вступит в силу со следующего такта.» и слот под текст ошибки.
5. `styles.css`: новый блок `.economics-*` (не переиспользует и не меняет ни один класс
   `.constants-*`) — янтарный фон `#FFF8EF` и рамка `2px solid var(--edit)` (тот же цветовой язык,
   что у «изменено оператором»/`.change-row.edited` в других вкладках), чтобы визуально явно
   отделить редактируемый блок от read-only реестра ГОСТ/ПАЗ ниже.
6. `app.js`: `store.economics = {data, edits, loading, applying}`; `initConstantsTab()` теперь
   также вызывает `initEconomicsBlock()`; `ECONOMICS_FIELDS` (5 полей, подписи/шаг/диапазон из
   п.2); `renderEconomicsFields()` — рисует степперы, показывает/прячет бейдж по `data.is_override`;
   `applyEconomics()` — собирает все 5 текущих значений формы (не только изменённые — POST мёрджит
   частичный словарь, отправка неизменённых полей идемпотентна), вызывает `api.updateEconomics`,
   при успехе обновляет `store.economics.data` из ответа, чистит `edits`, показывает подсказку про
   следующий такт и `showToast`; при ошибке (422 на неизвестный ключ и т. п.) — инлайн-текст ошибки
   + `showToast(..., "error")`, значения формы не теряются.
7. Живая проверка в браузере (см. ниже) + `pytest -q tests/console`.
8. Запись находки по `src/console/service.py` в `REQUESTS.md` (вне периметра F3, не правил сам).
9. Эта запись в `STATUS.md`.

### Живая проверка (`http://localhost:8000/console/?session_id=demo`, докер уже поднят другой ролью
### с `--reload`, статика раздаётся напрямую из репозитория — правки видны без пересборки)
- Открыта вкладка «Константы» на 1920×1080 — новый блок «Цены и тарифы (редактируемо)» отрисован
  над read-only таблицей реестра, визуально обособлен (янтарная рамка/фон), 5 полей со значениями
  по умолчанию (`68000/52000/41500/88000/52000`), подписи и единицы совпадают с п.2 выше.
  `read_console_messages(onlyErrors: true)` — пусто.
- Правка `price_godt` через степпер (ввод `100000`) → клик «Применить» → в Network ровно один
  `POST /api/console/economics` (200 OK) и один предшествующий `GET` при первом открытии вкладки;
  ответ `{"price_godt":100000,...,"is_override":true}` → в блоке появился бейдж «изменено
  оператором» и подсказка «Изменение вступит в силу со следующего такта.», всплыл `toast`. Прямой
  `fetch('/api/console/economics?session_id=demo')` из консоли браузера подтвердил, что бэкенд
  реально сохранил `price_godt=100000` для сессии `demo` (не только оптимистичный UI-рендер).
- **Проверка маржи (акт из задания) — обнаружена находка вне периметра F3.** После применения
  правки нажата «Следующий такт вручную» (и естественным образом прошло ещё несколько тактов —
  сессия `demo` тикает сама с периодом 10 с по общему демо-таймеру), `clock.tick` в
  `GET /api/console/state` увеличился, но `margin.value_rub_h` остался **ровно** тем же числом
  (`3214944` ₽/ч = `219.6 * (68000*0.98 - 52000)`, т.е. посчитан по **дефолтному** `price_godt`,
  а не по применённому `100000`). Прочитан `src/console/service.py:565-569`: секция «5. Маржа»
  строит `MarginModel` из `load_params().economics` напрямую, **не** мёрджа `session.economics_override`
  (это поле завела и реально применяет к графу МАС роль B3 в `runtime.py::tick_single`, но
  отдельный расчёт статус-бара в `service.py` про него не знает). Это не дефект моего блока —
  `POST`/`GET /api/console/economics` работают корректно, значения сохраняются и отображаются в
  моём блоке верно — а разрыв в чужом файле (`src/console/service.py`, владение B1 по таблице §8),
  который не даёт статус-бару отразить операторскую правку. Подробности, число и точная строка кода —
  `REQUESTS.md` (`[F3→B1]`), с предложенным однострочным фиксом
  (`dataclasses.replace(econ_p.economics, **session.economics_override)`).
  После проверки `price_godt` возвращён на дефолт `68000` через `POST /api/console/economics`
  (сессия `demo` общая с другими ролями), `is_override` при этом остаётся `true` навсегда для этой
  сессии — задокументированное ограничение самой роли B3 (STATUS.md, волна 8), не новое.

### Тесты
```
$ docker exec neftecode-api pytest -q tests/console
...............................................................          [100%]
63 passed, 2 warnings in 745.73s (0:12:25)
```
То же число тестов (63) и те же 2 предупреждения (`anyio`/`pytest.mark.perf`), что и после волн
B0-F2 — регрессии нет, ожидаемо по построению: изменения этой волны не затрагивают ни один
Python-файл (`src/console/**` не менялся, правки только в
`static/console/{index.html,app.js,api.js,styles.css}`, JS-тестов в проекте нет по указанию
задания). `node --check` на `app.js` и `api.js` — без ошибок синтаксиса. Время прогона (12 мин 25 с)
выше, чем в прошлых волнах (9 мин 12 с) — из-за конкуренции за CPU с параллельно работающим
`docker compose` (`--reload`) во время моей же живой проверки в браузере, не из-за содержимого
тестов.

### Допущения
- `is_override` в блоке — один бейдж на весь блок, а не на отдельное поле (буквально повторяет
  задокументированное ограничение контракта B3, не новое допущение F3).
- Кнопка «Применить» отправляет все 5 текущих значений формы (изменённые и неизменённые) одним
  `POST`, а не только реально изменённые поля — упрощает код, безопасно (частичный мёрдж на
  бэкенде идемпотентен для неизменённых значений) и гарантирует, что после ошибки на одном поле
  форма не рассинхронизируется с бэкендом.
- Для `.value-stepper` использован `decimals=0` (цены — целые рубли/т, как в исходном
  Streamlit-сайдбаре, где `step=500.0` без дробной части в выводе).
- Режим `?fixture=` не имеет отдельного JSON-файла для блока цен (вне периметра B0/F3, по
  аналогии с уже существующим TODO для `getBlending()` в этом же файле) — используются инлайн-
  дефолты `EconomicsParams`, чтобы фикстурный режим не падал с ошибкой на вкладке «Константы».

### Открытые вопросы / находки
- **[F3→B1]** `src/console/service.py` не учитывает `session.economics_override` при расчёте
  `margin.value_rub_h` статус-бара — блокирует пункт Definition of Done §9 «правка цены ГО ДТ
  меняет `margin` в статус-баре на следующем такте». Запись с воспроизведением и предложенным
  фиксом — `REQUESTS.md`. Не в периметре F3, не правил `service.py` сам.

### Осталось (следующие роли по плану)
- B1 (или любая роль с доступом к `src/console/service.py`): фикс из `REQUESTS.md` — без него
  пункт Definition of Done по марже не может быть закрыт, независимо от качества фронтенда F3.
- F4: вкладка «Супервизор» (не в периметре F3).
- QA: финальный прогон `pytest -q` и сверка Definition of Done §9 целиком, включая пункт про
  маржу (после фикса B1).

## Волна 12: F4 — Супервизор frontend — ВЫПОЛНЕНО

Роль F4 из `03_STREAMLIT_MIGRATION_PLAN.md` §7/§8 («Супервизор frontend») — **последняя роль
фронтенд-волны** (после неё — общая QA-сверка). Бэкенд уже полностью существовал в `main.py`
(`GET/POST /api/v1/supervisor/*`, прямые эндпоинты корневого API, НЕ `/api/console/*`) — задача
почти чистый фронтенд: новая вкладка «Супервизор» + 4 обёртки в `api.js`. Владение — строго
`static/console/{index.html,app.js,styles.css,api.js}`, только добавление своей вкладки/блока,
ни один чужой `<section data-tab-panel=...>`/класс/функция/вкладка не тронуты
(`charts.js` не трогал вообще).

### План (выполнен по пунктам)
1. Прочитаны `README.md` §5, `03_STREAMLIT_MIGRATION_PLAN.md` §7/§8, хвост `STATUS.md` (волны
   10-11) — подтверждено текущее состояние вкладок и что бэкенд супервизора (`main.py:282-330`,
   `src/supervisor/store.py`) уже отдаёт `Finding` (`finding_id`, `category`, `severity`, `title`,
   `root_cause`, `evidence_refs`, `checks_recommended`, `safety_risk_assessment`, `status`) и
   `PolicyChangeRequest` (`request_id`, `proposal.{items[{field,old_value,new_value,justification}],
   expected_kpi_impact}`, `status`, `shadow_passed`) через `model_dump()` — использованы поля
   буквально как в контракте, ничего не выдумывалось.
2. `api.js`: добавлены `getSupervisorFindings(status)`, `getChangeRequests(status)`,
   `approveChangeRequest(id, user)`, `rejectChangeRequest(id, user)` — прямой `fetch` на
   `/api/v1/supervisor/*` (тот же origin, порт 8000, CORS не нужен), **без** ветки `?fixture=` —
   по прямому указанию задания эти эндпоинты не имеют per-session офлайн-контекста, как остальные
   обёртки этого файла; обработка не-200 ответа (`res.ok` → `Error` с текстом из `detail`/`detail.text`).
3. `index.html`: добавлена кнопка `data-tab="supervisor"` последней в `.tab-bar` (после
   «Константы») и `<section class="tab-panel" data-tab-panel="supervisor" hidden>` перед
   контейнером тостов — 2 карточки `.supervisor-card` («Диагностические находки» и «Запросы на
   изменение политики (HITL)»), кнопка «Обновить» (`#btn-refresh-supervisor`, тот же паттерн, что
   `#btn-refresh-shift`). Подсказка в заголовке явно указывает, что Q&A — на «Обзоре», рапорт — на
   «Журнале смены» (D13/E7, не дублировать).
4. `app.js`: `switchTab()` — ветка `else if (name === "supervisor") initSupervisorTab();`;
   `store.supervisor = {findings, changeRequests, loading, loaded, expandedFindings, deciding}` —
   не привязано к такту симуляции (в отличие от `store.blending`/`store.pareto`), т.к. находки и
   HITL-запросы супервизора не относятся к конкретному такту консольной сессии; обновляется по
   активации вкладки, кнопке «Обновить» и после каждого approve/reject (инлайн, без модалки —
   буквально по заданию).
5. `initSupervisorTab()` — параллельный `Promise.all` на оба GET; при ошибке — `showToast(...,
   "error")` + `loaded = true` с пустыми списками (не оставлять вкладку в вечном «Загрузка...» —
   тот же принцип, что у `initBlendingTab`/`initParetoTab`, ошибка уже сообщена тостом, повтор —
   кнопка «Обновить»).
6. `renderSupervisorFindings()` — бейдж по `severity` (`.sev-critical` красный/`.sev-warning`
   жёлтый/`.sev-info` синий, цвета — существующие токены `--limit`/`--risk-bg`,
   `--warn-frame`/`--warn-bg`, `--rec`, ничего нового в палитру не добавлено), заголовок карточки
   (свёрнуто по умолчанию) = бейдж + `title` + `finding_id` (mono) + `status` + курсор-стрелка;
   разворачивание по клику (`store.supervisor.expandedFindings`, per-card, без сети) показывает
   `root_cause`, `safety_risk_assessment`, `checks_recommended` (только если непустой список),
   `evidence_refs` (только если непустой) — оба списка рендерятся как `<ul>`. Пустой список находок →
   «Открытых диагностических инцидентов не зафиксировано» (буквальный текст задания).
7. `renderSupervisorChangeRequests()` — карточка: `request_id` (mono) + цветной бейдж `status`
   (`PENDING_APPROVAL` жёлтый / `APPROVED` зелёный / `REJECTED` красный), строка
   `proposal.expected_kpi_impact`, строка `shadow_passed` (✅/❌ буквально по заданию), список
   `proposal.items` в формате `field: old_value → new_value (justification)`. Для
   `status === "PENDING_APPROVAL"` — кнопки «Утвердить»/«Отклонить» (`btn-cr-approve`/
   `btn-cr-reject`, новые классы, не переиспользуют чужие `.btn-*`), вызывающие
   `approveChangeRequest(id, "Оператор консоли")`/`rejectChangeRequest(...)`; на время запроса
   кнопки дизейблятся (`store.supervisor.deciding`), после успеха/ошибки — повторный
   `getChangeRequests()` и инлайн-перерисовка (без модалки, без перезагрузки страницы), `showToast`
   с результатом. Пустой список → «Запросов на изменение политики нет» (буквальный текст задания).
8. `styles.css`: новый блок `.supervisor-*` в конце файла (после `.blending-*`, ничего
   существующее не сдвинуто/не переписано) — `.supervisor-layout` (2-колоночный грид, как
   `.xai-layout`, с медиа-запросом схлопывания в 1 колонку на узких экранах),
   `.supervisor-card`/`.supervisor-card-header`/`.supervisor-card-title` (визуально похожи на
   `.xai-main-card`, но отдельные классы — та же логика развязки, что у F2/F3: чтобы будущая
   правка чужой вкладки не задела «Супервизор» и наоборот), `.supervisor-severity-badge.sev-*`,
   `.supervisor-finding-*` (карточка/заголовок/каретка/раскрытый блок/список), `.supervisor-cr-*`
   (карточка/статус/строка изменения/кнопки `.btn-cr-approve`/`.btn-cr-reject`).
9. Живая проверка в браузере (см. ниже) + `pytest -q tests/console`.
10. Эта запись в `STATUS.md`.

### Живая проверка (`http://localhost:8000/console/?session_id=demo`, докер уже поднят другой
### ролью, `--reload`, статика раздаётся напрямую из репозитория — правки видны без пересборки)
- 1920×1080, открыта вкладка «Супервизор» (последняя в баре) — рендерится без ошибок в консоли
  (`read_console_messages(onlyErrors: true)` — пусто на всех этапах проверки). В Network ровно по
  одному `GET /api/v1/supervisor/findings` и `GET /api/v1/supervisor/change-requests` на открытие
  вкладки (без тройного фетча).
- Состояние базы на момент проверки: `findings` — 0 строк, `change_requests` — 1 строка
  (`CR-TEST-001`, `status=APPROVED`, `shadow_passed=false` — существующая запись из более раннего
  аудита МАС, не моя). Блок находок корректно показал заглушку «Открытых диагностических
  инцидентов не зафиксировано»; блок HITL показал карточку `CR-TEST-001` с зелёным бейджем
  `APPROVED`, ✗ у shadow-режима, строкой `alpha_quality: 0.0228 → 0.0150 (Тест)` — без кнопок
  approve/reject (правильно, статус не `PENDING_APPROVAL`).
- **Проверка approve/reject вживую** (по указанию задания — создать тестовую запись через
  существующий путь `src/supervisor/store.py`, проверить, затем не обязательно, но лучше почистить
  за собой): временно вставлена через `DEFAULT_SUPERVISOR_STORE.save_change_request(...)` (внутри
  контейнера `neftecode-api`) запись `CR-F4-UICHECK-001` со статусом `PENDING_APPROVAL` и 3 находки
  `FND-F4-CHECK-00{1,2,3}` (CRITICAL/WARNING/INFO). После «Обновить» на вкладке — все 3 бейджа
  severity отрисовались верными цветами (красный/жёлтый/синий), разворачивание карточки CRITICAL
  показало `root_cause`/`safety_risk_assessment`/2 пункта `checks_recommended`/1 ссылку
  `evidence_refs`, каретка `▼`→`▲`. У `CR-F4-UICHECK-001` появились кнопки «Утвердить»/«Отклонить»;
  клик «Отклонить» → ровно один `POST /change-requests/CR-F4-UICHECK-001/reject` (200 OK) → тост
  «Запрос отклонён CR-F4-UICHECK-001» → карточка инлайн обновилась на красный бейдж `REJECTED`,
  кнопки исчезли, без модалки и без перезагрузки страницы. **После проверки все тестовые записи
  удалены** напрямую из `data/supervisor/supervisor.db` (`DELETE FROM findings WHERE finding_id
  LIKE 'FND-F4-CHECK-%'`, `DELETE FROM change_requests WHERE request_id = 'CR-F4-UICHECK-001'`) —
  подтверждено повторным `GET` с двух эндпоинтов и перезагрузкой страницы: находки снова пустые,
  запросы — только исходный `CR-TEST-001` в исходном статусе `APPROVED`. Единственный побочный
  след — тестовые записи остались в append-only `data/supervisor/supervisor.jsonl` (журнал аудита,
  не читается API, `*.jsonl` в `.gitignore` — не попадёт в коммит и не влияет на поведение пульта).
- Обнаруженный по ходу нюанс (не дефект продукта): при программном `element.click()` через
  автоматизацию браузера тумблер разворачивания карточки иногда переключался туда-обратно за один
  вызов (двойной синтетический клик инструмента автоматизации) — обычный клик пользователя мышью
  (или `dispatchEvent(new MouseEvent('click'))` из живой проверки) переключает корректно с первого
  раза. Не правка кода, особенность используемого инструмента проверки.

### Тесты
```
$ ./.venv/bin/python -m pytest -q tests/console
...............................................................          [100%]
63 passed, 2 warnings in 562.26s (0:09:22)
```
Запущено после основных правок (до косметического фикса обработки ошибки загрузки в
`initSupervisorTab`, который правит только JS и не касается ни одного файла из `tests/console`).
То же число тестов (63) и те же 2 предупреждения (`anyio`/`pytest.mark.perf`), что и после волн
B0-F3 — регрессии нет, ожидаемо по построению: волна F4 не меняет ни один Python-файл
(`src/**` не трогался вообще, только `static/console/{index.html,app.js,api.js,styles.css}`).
`node --check` на `app.js`/`api.js` — без ошибок синтаксиса (после всех правок, включая
косметический фикс).

### Допущения
- Порядок вкладки «Супервизор» — последняя в `.tab-bar` (после «Константы»), т.к. это последняя
  добавленная роль волны и в задании явно не зафиксирован конкретный порядок вкладок для неё.
- `getSupervisorFindings`/`getChangeRequests` вызываются без параметра `status` (все статусы
  разом) — задание не просит фильтр по статусу в UI, а бэкенд и так возвращает достаточно малый
  список для однократной вкладки без пагинации; параметр `status` в обёртках оставлен
  опциональным на будущее (сигнатура `(status = null)`), но фронтенд им не пользуется.
- Данные супервизора не привязаны к `session_id`/такту консоли (в отличие от блендинга/Парето) —
  это осознанное соответствие бэкенду: `GET/POST /api/v1/supervisor/*` в `main.py` не принимают
  `session_id` вообще (это не консольная сессия, а общий процесс LLM-супервизора). Обновление —
  по активации вкладки, кнопке «Обновить» и после каждого решения approve/reject, без полинга на
  такте (такт консоли не имеет отношения к появлению новых находок/CR).
- Экранирование HTML в `title`/`root_cause`/`justification`/`evidence_refs` не добавлено —
  согласовано со стилем остального `app.js` (например, `renderFeed()`/`renderMarkdown()` тоже
  вставляют текст с бэкенда через `innerHTML` без экранирования); данные приходят из
  `SupervisorStore`, не от произвольного внешнего пользователя.
- Кнопки «Утвердить»/«Отклонить» шлют фиксированную строку `"Оператор консоли"` как `user` (по
  буквальному тексту задания «зовут approveChangeRequest(id, "Оператор консоли")»), без текстового
  поля ввода имени — задание не просило поле ввода, только дефолтную строку.
- Тестовые записи для живой проверки approve/reject вставлялись и удалялись напрямую через
  `SupervisorStore`/`sqlite3` внутри контейнера `neftecode-api` (тот же процесс, что использует
  API), а не через несуществующий публичный «create» эндпоинт — в API нет способа создать
  находку/CR извне, только читать/approve/reject.

### Открытые вопросы
Нет новых. Оба практических следствия работы бэкенда супервизора (отсутствие `session_id`,
отсутствие публичного create-эндпоинта) обработаны на уровне допущений F4 выше, не как открытые
вопросы.

### Осталось / для QA (следующая роль по плану — последняя, общая сверка)
- QA: финальный `pytest -q` (полный, не только `tests/console`) и сверка Definition of Done §9
  `03_STREAMLIT_MIGRATION_PLAN.md` целиком — на момент волны F4 ещё открыт пункт **[F3→B1]** про
  маржу в статус-баре (`REQUESTS.md`), не в периметре F4, не проверялся повторно в этой волне.
- **Для QA — стоит перепроверить на уровне всего пульта**: вкладка «Супервизор» — единственная из
  8 вкладок, где нет `?fixture=` офлайн-режима (по конструкции бэкенда, см. «Допущения» выше). Если
  QA прогоняет `agents/console_tz/README.md` §7 Definition of Done «пульт открывается без ошибок в
  консоли» через `?fixture=advisory|auto|refusal` — вкладка «Супервизор» в этом режиме всё равно
  бьёт в реальный `/api/v1/supervisor/*` (не в фикстуру), что осознанно и безопасно на живом сервере,
  но стоит убедиться, что это не считается сломанным офлайн-режимом при формальной проверке DoD.
