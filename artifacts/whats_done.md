# Реализация MVP и статус технологического контура

### 1. Агенты аудита безопасности и качества [`auditors.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/auditors.py)
- **`ReliabilityAgent` (Агент Надежности, ПАЗ/ESD)**:
  - Контроль защитных барьеров безопасности оборудования (статус ASSUMPTION):
    - $\text{AVT\_T55} \le 386.40$ °C (перевал змеевика печи П-3 вакуумного блока, отсечение до закоксовывания 387.0 °C; нарушение в архиве 0.8%);
    - $\text{HT\_P8} \le 454.5$ кПа (перепад давления на слое катализатора реактора Р-202, защита от разрушения слоя, соответствует 4.635 кгс/см²; тег `HT_W10` закреплен за расходом бензина стабилизации);
    - $\text{AVT\_P52} \le 0.077$ кгс/см² (перепад колонны К-10, защита от захлебывания);
    - $\text{AVT\_F31} \ge 362.50$ т/ч (минимальный расход мазута в печь П-3 от прогара труб, исправлено с м³/ч).
  - При выходе за барьер кандидат немедленно бракуется жестким вето (**Hard-Veto**).
  - В предохранительной зоне печи $[380.0, 386.40)$ °C рассчитывается гладкий логарифмический барьер риска:
    $$B(\text{AVT\_T55}) = -\mu \cdot \ln\left( \frac{386.40 - \text{AVT\_T55}}{12.0} \right), \quad \mu = 1000.0 \text{ руб/ч}$$
    Штраф стремится к бесконечности при приближении к границе, исключая экономический каннибализм.
  - Функция-узел `node_reliability_agent` для параллельного выполнения в LangGraph.

- **`QualityAgent` (Агент Качества, ГОСТ 32511-2013 Евро-5)**:
  - Контроль обязательных нормативов ГОСТ 32511 (NORM): сера $\le 10.0$ мг/кг, плотность $820.0-845.0$ кг/м³, вспышка $\ge 55.0$ °C, $T95 \le 360.0$ °C, цетановое число $\ge 51.0$.
  - Статистический критерий отсечения (ADR-12, Hard-Veto): $\hat S + z \cdot \sigma_S(\text{age}) \le 10.0$ ppm, где $z = 2$ (промпт Агента Качества, tz:598), $\sigma_{S0} = 0.83$ ppm (DATA: `HT_Q21` + bias по доступным пробам ЛИМС, методика ТЗ, `scripts/estimate_quality_uncertainty.py`), исключающий дублирование с фиксированным буфером 9.5 ppm.
  - Защитные буферы в LP блендинга: сера смеси $\le 9.50$ ppm, плотность $[821.25, 843.75]$ кг/м³, вспышка $\ge 56.0$ °C, T95 товарного топлива $\le 360.0$ °C при T95 ГО ДТ $+2\sigma_{T95}$, цетановое число $\ge 51.5$; гидрогенизат по T95 не ветируется (PDF, план C10).
  - Требование стабилизации фракционного состава для ходов печью П-3 (запас T95 товарного топлива в отчете аудита и карточке XAI).
  - Функция-узел `node_quality_agent`.

### 2. Двухстадийный гибридный арбитраж [`arbitration.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/arbitration.py)
- **Класс `ArbitrationNode`**:
  - **Стадия 1 (Hard-Veto Gate)**: отсев всех кандидатов, получивших вето от аудиторов. Формирование допустимого множества $\mathcal{U}_{\text{admissible}}$.
    - Если $\mathcal{U}_{\text{admissible}} = \emptyset$ $\to$ немедленный перевод в Safe Hold с регламентным русскоязычным сообщением по Callout Box 4 ТЗ и $\Delta \mathbf{u} = \mathbf{0}$.
  - **Стадия 1b (Корректирующий режим)**:
    - Если базовый режим hold нарушает спецификацию качества или безопасности, deadband по марже отключается, и система выбирает действие, максимально возвращающее параметры в допустимую область, со статусом `SUCCESS_CORRECTIVE`.
  - **Стадия 2 (Economic Clearing)**: расчет чистой полезности для допустимых кандидатов:
    $$\text{Net Utility} = \text{Expected Margin} - \text{Risk Penalty}$$
    Выбор кандидата с максимальной чистой полезностью.
  - **Стадия 3 (Deadband Filter — фильтр зоны нечувствительности)**:
    - Порог чистой маржи: **$1000.0$ руб/ч**. Если выигрыш ниже 1000 руб/ч при кондиционном hold $\to$ режим замораживается ($\Delta \mathbf{u} = \mathbf{0}$) со статусом `DEADBAND_REJECT_LOW_MARGIN` для сбережения ресурса исполнительных механизмов.
    - Нормированный порог шага: $\|\Delta \mathbf{u}\|_{\text{norm}} = \sqrt{\sum (\Delta u_i / s_i)^2} \ge 0.05$. Если норма меньше порога $\to$ уставки не меняются (`DEADBAND_REJECT_SMALL_STEP`).
  - Функция-узел `node_arbitration`.

### 3. Сквозной граф вычислений LangGraph [`graph.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/graph.py)
- Полная архитектура сопряжения агентов:
  - `data_guard` $\to$ `route_after_guard`
    - Сбой критических датчиков $\to$ `safe_hold` $\to$ `END`
    - Норма $\to$ `optimization`
  - `optimization` $\to$ параллельный Fan-Out на `[reliability_agent, quality_agent]`
  - Параллельные аудиторы $\to$ Fan-In слияние в `arbitration` через редьюсеры `operator.add` и `merge_risk_penalties` (без риска `InvalidUpdateError`)
  - `arbitration` $\to$ `route_after_arbitration`
    - `SAFE_HOLD` $\to$ `safe_hold` $\to$ `END`
    - `DEADBAND` $\to$ `END`
    - `SUCCESS*` $\to$ `blending` (расчет оптимальной рецептуры блендинга из резервуаров) $\to$ `END`

### 4. Контракты Pydantic v2 и компенсация запаздывания LIMS
- **Контракты данных** ([`src/agents/state.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/state.py)):
  - `BaseAgentProtocol` с замороженной конфигурацией `frozen=True` и `extra="forbid"`.
  - `SafetyAuditReport` с валидацией неотрицательности барьерных штрафов (`risk_penalty_rub_h >= 0.0`) и фиксацией нарушенных ограничений.
- **Компенсатор запаздывания LIMS** ([`src/agents/lims.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/lims.py)):
  - Ретроспективная инновация: расчет ошибки прогноза относительно исторической точки пробоотбора.
  - Экспоненциальное затухание смещения (Bias Decay) с периодом полураспада $T_{1/2} = 12$ часов.
  - Безударный перенос (Bumpless Transfer): фильтрация первого порядка ($\tau = 30$ мин), устраняющая скачки в контурах регулирования.
  - Калиброванная неопределенность по реальным архивам (методика ТЗ): $\sigma_{S0} = 0.83$ ppm, $\sigma_{T95,0} = 3.27$ °C, $\sigma_{flash} = 4.78$ °C, $\sigma(t) = \sigma_0 \sqrt{1 + \text{age\_hours} / 12.0}$.

### 5. Объяснимый ИИ (XAI), REST API (FastAPI) и Консоль оператора (Streamlit HITL)
- **Генератор объяснимого ИИ** ([`src/xai/narrative.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/xai/narrative.py)):
  - Детерминированная физико-химическая аргументация решений на русском языке по официальным управляющим параметрам (`HT_FEED_SP`, `HT_TIN_SP`, `HT_P_SP`, `HT_GOR_SP`), перевал печи `AVT_T55`, квенч `HT_F14`, барьеры ПАЗ и ГОСТ.
  - Разделы отчета: прогноз hold vs кандидат (+30 мин, +3 ч, установившийся), сравнение с альтернативами, уровень уверенности в данных и список принятых допущений модели (ASSUMPTION).
- **REST API Сервис** ([`main.py`](file:///Users/egork/Desktop/neftekod-hackathon/main.py)):
  - Эндпоинт `POST /api/v1/optimize`: валидация телеметрии, канонизация тегов (`AVT_*`, `HT_*`), запуск графа LangGraph, возврат рекомендаций, альтернатив, XAI-отчета и рецептуры блендинга.
  - Поддержка `session_id` для сохранения динамического состояния двойника между циклами.
- **Интерфейс оператора HITL** ([`src/ui/app.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/ui/app.py)):
  - Информационный дэшборд: массовый расход сырья `HT_F9` (т/ч), входная температура `HT_T6`, сера `HT_Q21` (ppm), перепад `HT_P8` (кПа), возраст анализов LIMS, COT печи П-3 `AVT_T55`.
  - График динамического прогноза серы и вспышки.
  - Таблица альтернатив и рецепт блендинга (3 компонента + 2 присадки из резервуаров).
  - Human-in-the-Loop: кнопки оперативного одобрения (`commit_applied_move`) и мотивированного отклонения.

### 6. Контейнеризация и запуск в Docker (One-Click Launch)
- Оптимизированный [`Dockerfile`](file:///Users/egork/Desktop/neftekod-hackathon/Dockerfile) на базе стабильного `python:3.12-slim` с предустановкой системных утилит (`curl`, `build-essential`) и кэшированием pip-слоя.
- Мультиконтейнерная оркестрация [`docker-compose.yml`](file:///Users/egork/Desktop/neftekod-hackathon/docker-compose.yml):
  - Сервис `api` (FastAPI REST API на порту 8000) с автоматическим healthcheck (`/api/v1/health`);
  - Сервис `streamlit` (Консоль оператора на порту 8501) со строгим ожиданием готовности бэкенда (`service_healthy`);
  - Сервис `tests` (профиль `test` для прогона тестов в контейнере `docker compose run --rm tests`);
  - Поддержка hot-reload (bind mount `.:/app`) для динамической разработки.

### 7. Инженерный реестр допущений, экономика РФ 2024–2026 и метрики надежности
- **Реестр допущений** ([`agents/ASSUMPTIONS.md`](file:///Users/egork/Desktop/neftekod-hackathon/agents/ASSUMPTIONS.md)):
  - Полная спецификация параметров со статусом `ASSUMPTION` по правилу границ ТЗ (пределы оборудования, кинетические константы, экономика, FOPDT-динамика, резервуары блендинга).
  - Интеграция с генератором XAI (`src/xai/narrative.py`), операторской консолью Streamlit и аудит-журналом решений `decisions.jsonl`.
- **Экономическая модель рынка РФ (2024–2026 гг.)**:
  - Параметризация цен по биржевым котировкам СПбМТСБ, тарифам ФАС и ОРЭМ: ГО ДТ Евро-5 ($68\,000$ руб/т), прямогонный дизель ($52\,000$ руб/т, спред $16\,000$ руб/т), сырая нефть ($41\,500$ руб/т), керосин ТС-1 ($88\,000$ руб/т), газ печей ($7\,800$ руб/1000 нм³ $\to 2\,700$ руб/МВт·ч), электроэнергия ($6.80$ руб/кВт·ч), водород КЦА ($16.50$ руб/нм³), дезактивация катализатора ($450$ руб/(ч·°C)).
  - Поддержка динамического переопределения цен в Streamlit UI (блок «💰 Параметры рынка и тарифов»), через REST API (`TelemetryPayload.economics`) и расчет физического расхода топливного газа печи ($\Delta V_{\text{gas}}$ нм³/ч).
- **Тестовое покрытие и бенчмарки**:
  - Полный прогон Pytest: **144 passed** (104 предыдущих + 28 Парето-анализа `test_step8_pareto.py` + 12 соответствия решений ТЗ `test_step9_tz_compliance.py`).
  - Реплей технологических сценариев (`scripts/replay_scenarios.py`): **4/4 сценария успешно пройдены** (Норма 0.0% воздействий $\le 5.0\%$, Риск качества на динамическом стенде `PlantSimulator` с успешным устранением риска качества через `SUCCESS_CORRECTIVE` и выходом на стационар, Деградация КИП/LIMS `SAFE_HOLD`, Сквозной консенсус МАС по tz:997: самое выгодное предложение — нагрев печи П-3 +2 °C, вето Агента Надежности T55 = 387 °C, требование Агента Качества по фракционному составу, карточка XAI с Парето-анализом; «Норма» — 0 действий после выхода на оптимум на динамическом `PlantSimulator`).
  - Задержки (Latency p95): оптимизатор `propose()` = **15.7 мс** (порог < 100 мс), сквозной граф LangGraph `graph.invoke()` с узлом Парето = **52.5 мс** (порог < 150 мс).

### 8. Этап P0: Страховочная сетка и стенд PlantSimulator v2 (Gate G0)
- **Регрессионные тесты аудита E1–E12** ([`tests/audit/test_audit_regressions.py`](file:///Users/egork/Desktop/neftekod-hackathon/tests/audit/test_audit_regressions.py)):
  - Зафиксированы 12 тестов для подтверждения находок аудита со статусом `@pytest.mark.xfail(strict=True)`.
  - Покрывают: предусловия печи П-3 (E1), fail-closed на пропусках критических тегов (E2), устранение ложного `SAFE_HOLD` при сере 9.6 ppm (E3), запрет несинхронизированного ЛИМС (E4), устранение «заморозки в нарушении» без ЛИМС (E5), калибровку по времени отбора (E6), лестницу деградации вместо резкого обрыва (E7), эластичный блендинг при дефиците компонентов (E8), видимость измеренных T55 и dP (E9), соблюдение границ вспышки с запасом 2σ на стационаре (E10), сокращение зазора полезности до <= 1% (E11), регистрацию `RECOVERY_STALLED` при отсутствии реакции установки (E12).
- **Стенд PlantSimulator v2** ([`src/twin/plant.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/twin/plant.py)):
  - Реализована генерация рассогласования параметров установки (кинетика $E_h/R \pm 15\%$, активность катализатора $k_h \in [0.8; 1.2]$, множитель перепада Р-202, отклик $dF_{30}/dT_{55}$).
  - Реалистичный график ЛИМС: разрыв между временем отбора пробы `sampled_at` и выдачей результата `available_at` (задержка 2–8 ч).
  - Симуляция отказов КИП/ПАК (`frozen`, `drift`, `clamping` 307.0/313.0, `nan`).
  - Выдача фактических измеренных положений регулирующих органов MV в телеметрию.
  - Метод `truth()`: расчет и выдача истинных физических значений параметров установки без шума и калибровочных сдвигов.
- **Архитектурные тесты изоляции слоев** ([`tests/test_architecture.py`](file:///Users/egork/Desktop/neftekod-hackathon/tests/test_architecture.py)):
  - AST-проверка: `src/agents` не зависит от внешних LLM (`openai`, `anthropic`, `src.supervisor`); `src/safety_kernel` изолирован от переговоров и арбитража.
- **Фиксация Baseline KPIs** ([`scripts/baseline_kpis.py`](file:///Users/egork/Desktop/neftekod-hackathon/scripts/baseline_kpis.py), [`data/processed/baseline_kpis.json`](file:///Users/egork/Desktop/neftekod-hackathon/data/processed/baseline_kpis.json)):
  - Зафиксированы исходные значения нарушений Т1 (115 по правде в стресс-сценариях), вспышки (54.19 °C < 55 °C) и зазора полезности (8.0%) до рефакторинга.
- **Статус верификации ворот G0**: **146 passed, 12 xfailed** (все E1–E12 со `strict=True`), CI полностью стабилен.

### 9. Этап P1: Данные, оценка состояния, неопределенность, провенанс (Gate G1)
- **Контракты данных Pydantic v2 (P1.1)** ([`src/agents/contracts.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/contracts.py)):
  - Полная типизация неизменяемых (`frozen=True, extra="forbid"`) контрактов данных: `Measurement`, `DataAssessment`, `QualityEstimate`, `PlantEstimate`, `ConstraintSpec`, `ConstraintCertificate`, `ArbitrationDecision`, `DecisionTrace`.
- **Единый технологический реестр ограничений T0–T3 (P1.2)** ([`src/agents/registry.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/registry.py)):
  - Зафиксированы 34 спецификации `ConstraintSpec` по всем 4 ярусам (T0 границы приводов, T1 ПАЗ/оборудование, T2 ГОСТ/качество, T3 операционные).
  - Строгая валидация провенанса `validate_registry_provenance()`: 0 нарушений; отслеживание структурных зависимостей `depends_on` для изоляции связанных MV.
- **Хранилище политик и пороги автоматизации (P1.3)** ([`src/agents/policy.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/policy.py), [`config/policy/policy_v1.json`](file:///Users/egork/Desktop/neftekod-hackathon/config/policy/policy_v1.json)):
  - Разделение прав доступа: параметры политик (`alpha_equipment = 0.00135` $\to z=3.0$, `alpha_quality = 0.0228` $\to z=2.0$, временные пороги лестницы деградации ЛИМС 8ч/16ч/24ч) отделены от инженерных допущений и загружаются из версионируемого JSON.
- **Data Quality Guard v2 и лестница деградации (P1.4)** ([`src/agents/data_guard.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/data_guard.py)):
  - Реализована функция `assess_data`: детекция аппаратного клампинга (307.0/313.0), NaN/Inf, определение списков `blocked_mvs` и `unknown_specs`.
  - Реализована плавная лестница уровней автономности: `FULL` $\to$ `CAUTIOUS` (шаг 50%) $\to$ `CORRECTIVE_ONLY` (запрет экономического наращивания сырья) $\to$ `REFUSAL_DATA`.
- **Оценка состояния технологического комплекса (P1.5)** ([`src/agents/estimation.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/estimation.py)):
  - Реализован `StateEstimator` с кольцевым буфером `HistoryBuffer` на 48 часов для синхронизации асинхронных измерений.
  - Скалярный фильтр Калмана в log-домене для оценки серы гидрогенизата: ретроспективная калибровка смещения на момент отбора пробы `sampled_at`, квадратичный рост неопределенности со временем $Q_{\text{drift}} = 0.0005$ ч⁻¹.
- **Динамический двойник и физическое время сессий (P1.6)** ([`src/twin/chain.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/twin/chain.py), [`src/twin/session.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/twin/session.py)):
  - Ассимиляция температуры печи `AVT_T55` через апериодический фильтр, динамическое изменение параметров через `with_params(theta)`.
  - Управление сессиями с физическим шагом по времени, регистрация предложенных и примененных воздействий, обнаружение зависания приводов `RECOVERY_STALLED`.
- **Распространение неопределенности и шансовые ограничения (P1.7)** ([`src/agents/uncertainty.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/uncertainty.py)):
  - Модель распространения неопределенности `chance_effective`: учет погрешности измерений $\sigma_{\text{meas}}$, возраста калибровки $\sigma_{\text{calib}}(\text{age})$ и модельного отклика $\mathbf{g}^T \Sigma_\theta \mathbf{g}$.
  - Точный расчет квантилей $z(\alpha)$ через `scipy.stats.norm.ppf`.
- **Подход «душитель» (Strangler) и обратная совместимость (P1.8)** ([`src/agents/state_legacy.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/state_legacy.py), [`src/agents/state.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/state.py)):
  - Полная сохранность старого графа MVP и 100% обратная совместимость со всеми 144 существующими тестами.
- **Верификация и прохождение Gate G1 (P1.9)**:
  - **5/5 обязательных регрессионных тестов аудита переведены в PASSED**:
    - `test_audit_e2_missing_critical_telemetry` (fail-closed на пропусках датчиков $\to$ `REFUSAL_DATA`);
    - `test_audit_e6_fresh_pak_and_stale_lims_calibration` (калибровка по времени отбора, режим `CORRECTIVE_ONLY`, запрет роста сырья);
    - `test_audit_e7_lims_aging_ladder_transitions` (плавный переход в `CAUTIOUS` при возрасте 10 ч);
    - `test_audit_e9_equipment_envelope_observed_t55_and_dp` (выход из нарушения огибающей T1 через `RECOVERY_ADVISORY`, охлаждение печи и разгрузка сырья);
    - `test_audit_e12_replay_scenario_2_recovery_stalled` (детекция отсутствия реакции оборудования `RECOVERY_STALLED`).
    *(Дополнительно решены и переведены в PASSED тесты `test_audit_e3` и `test_audit_e5`)*.
  - Написаны 15 новых модульных тестов (`tests/unit/`: Data Guard v2, Estimation, Registry, Uncertainty).
  - Тест изоляции слоев (`tests/test_architecture.py`): 2/2 PASSED (отсутствие LLM-зависимостей в `src/agents`).
  - **Итоговый статус репозитория: 173 passed, 5 xfailed (только P2/P3), 0 failed.** Ворота Gate G1 успешно пройдены!

### 10. Этап P2: Агенты ограничений, блендинг и экономика (Gate G2)
- **Специализированный Агент Надежности (P2.1, ReliabilityAgent)** ([`src/agents/reliability.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/reliability.py)):
  - Контроль оборудования яруса T1: температура перевала печи `FURNACE.COT_MAX` (386.4 °C), перепад реактора `RX.DP_MAX` (454.5 кПа), перепад колонны `COL.P52_MAX` (0.077 кгс/см²), расход мазута `FURNACE.F31_MIN` (362.5 т/ч), кратность ВСГ `HT.GOR_MIN` (300 нм³/м³), средняя температура слоя `HT.T_BED_MEAN_MAX` (390.0 °C).
  - Проверка предусловий печи П-3 (Fail-Closed, `test_audit_e1_furnace_preconditions` PASSED): если телеметрия отсутствует или нарушены границы расхода мазута / перепада колонны К-10, ходы по печи жестко блокируются.
  - Политика COT (`FURNACE.COT_POLICY_WARM`, `FURNACE.COT_POLICY_HOT`): запрет форсирования теплового режима печи выше 380 °C до стабилизации процесса.
  - Локальный ремонт кандидатов (`repair`): уменьшение шага по температуре/нагрузке для возврата кандидата в допустимую область.
- **Ансамбль печи П-3 (P2.2, Furnace Ensemble)** ([`src/agents/furnace_ensemble.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/furnace_ensemble.py)):
  - Монте-Карло ансамбль из 40 сценариев (`seed = 42`) для робастной оценки нестабильных градиентов $dF_{30}/dT_{55}$ и $dF_{32}/dT_{55}$ и предотвращения ложных срабатываний.
- **Специализированный Агент Качества (P2.3, QualityAgent)** ([`src/agents/quality.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/quality.py)):
  - Контроль требований качества яруса T2: `GODT.S_MAX` (10.0 ppm), `PRODUCT.FLASH_MIN` (55.0 °C), `PRODUCT.T95_MAX` (360.0 °C), `PRODUCT.DENSITY_MIN/MAX` (820–845 кг/м³), `PRODUCT.CFPP_TARGET` (-15.0 °C), `PRODUCT.CETANE_MIN` (51.0), `PRODUCT.E360_MIN` (95.0 % об.).
  - Статистический учет неопределенности: UCB по сере и T95 ($+z\sigma$), LCB по вспышке и E360 ($-z\sigma$).
  - Интеграция с ходами печи АВТ: автоматическое требование дробной стабилизации при изменении нагрева мазута печи П-3.
  - Локальный ремонт качества: расчет компенсации температуры реакции или расхода квенча.
- **Специализированный Агент Снабжения (P2.4, SupplyAgent)** ([`src/agents/supply.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/supply.py)):
  - Контроль операционных ограничений яруса T3: минимальный и максимальный инвентарь виртуального сырьевого буфера `BUFFER.INVENTORY_MIN` ($-300.0$ т), `BUFFER.INVENTORY_MAX` ($+300.0$ т) на горизонте 8 ч.
  - Контроль долгосрочного баланса расходов `BUFFER.LONG_RUN_RATIO` $\in [0.85, 1.15]$.
  - Локальный ремонт кандидатов по сырью $\Delta \text{HT\_FEED\_SP}$ для удержания буфера в безопасных пределах.
- **Оптимизатор блендинга v2 и эластичная релаксация (P2.5, Blending v2)** ([`src/agents/blending.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/blending.py)):
  - `build_blend_problem`: фабрика задачи с учетом остатков в резервуарах (ГО ДТ, керосин, газойль), сегментов присадок (депрессорная, цетаноповышающая) и шансовых границ качества.
  - `solve_elastic`: иерархическая минимизация слаков при дефиците резервуарного парка (штрафы $10^7 \dots 10^2$). Возврат статуса `INFEASIBLE_ELASTIC` без вето на гидроочистку (устранение антипаттерна C2, `test_audit_e8_blending_deficit_elastic_infeasibility` PASSED).
- **Теневые цены качества и сертификация блендинга (P2.6)** ([`src/agents/blending.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/blending.py)):
  - `godt_prices`: метод конечных разностей для оценки теневых цен $\pi_q$ по 6 показателям (S, E360, FLASH, CFPP, CN, D15) для финансовой обратной связи в оптимизатор технологического режима.
  - `certify_blend`: выпуск единого сертификата блендинга `BlendingCertificate`.
- **Экономическая модель и износ катализатора (P2.7)** ([`src/agents/economics.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/economics.py), [`src/agents/policy.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/policy.py)):
  - Включение стоимости ускоренной термической деградации катализатора при повышенной WABT ($2500.0$ руб/(°C · сутки), WABT > 355 °C).
- **Верификация и прохождение ворот Gate G2 (P2.8)**:
  - **9/12 регрессионных тестов аудита переведены в PASSED**:
    - `test_audit_e1_furnace_preconditions` (fail-closed при несоблюдении предусловий печи);
    - `test_audit_e8_blending_deficit_elastic_infeasibility` (эластичное решение без ложного вето на кондиционный гидрогенизат);
    - (Ранее пройденные: E2, E3, E5, E6, E7, E9, E12; E4, E10, E11 остаются xfail для этапов P3/P4).
  - Написаны 17 новых модульных тестов (`tests/unit/`): `test_agents_reliability.py` (5 passed), `test_agents_quality.py` (3 passed), `test_agents_supply.py` (4 passed), `test_blending_v2.py` (5 passed).
  - Проверка архитектурной изоляции (`test_architecture.py`): 2/2 PASSED.
  - **Итоговый статус репозитория: 192 passed, 3 xfailed, 0 failed.** Ворота Gate G2 успешно пройдены!

---

## §11. Выполнение этапа P3: Ядро переговоров, арбитража, Safety Kernel, API/UI и сдача Gate G3

### 11.1. Реализованные компоненты ядра v3 (P3.1–P3.11)
- **Генератор кандидатов (P3.1, CandidateGenerator)** ([`src/agents/generator.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/generator.py)):
  - Генерация множеств кандидатов: базовый `HOLD` ($\Delta \mathbf{u} = \mathbf{0}$), локальные вариации `LOCAL`, глобальные целевые ходы `GLOBAL`, отремонтированные кандидаты `REPAIR` и локально уточненные `REFINE`.
  - Детерминированное хэширование сигнатур кандидатов (`signature_of`).
- **Глобальный непрерывный поиск (P3.2, GlobalSearchAgent)** ([`src/agents/global_search.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/global_search.py)):
  - Многоточечный поиск глобального оптимума установившегося режима методом SLSQP с автоматическим откатом на COBYLA.
  - 16 квазислучайных точек последовательности Соболя (`seed=0`) + тёплый старт прошлого такта + точка удержания $\mathbf{u}_0$.
  - Поиск ближайшей допустимой точки `nearest_feasible` при нарушенном режиме удержания $\mathbf{u}_0$.
  - Жесткий тайм-аут расчета $< 400$ мс.
- **Многоагентный механизм ремонта (P3.3, CandidateRepairer)** ([`src/agents/repair.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/repair.py)):
  - Локальный ремонт агентов по градиентам чувствительности $\nabla_{\mathbf{u}} \text{slack}_i$.
  - Совместный ремонт кандидатов (`repair_joint`) через решение вспомогательной задачи квадратичного программирования (QP) проекции на допустимую область.
- **Раундовый протокол переговоров (P3.4, Multi-Agent Negotiation)** ([`src/agents/negotiation.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/negotiation.py)):
  - Полный цикл Fan-Out/Fan-In: `node_propose` $\to$ `node_predict` $\to$ параллельная сертификация (`node_reliability`, `node_quality`, `node_supply`, `node_blending`) $\to$ `node_coordinate`.
  - Расчет критерия достоинства `calculate_merit` с вектором нарушений $\mathbf{v} = (v_0, v_1, v_2, v_3)$ и нормированной нормой движения $\\|\Delta \mathbf{u}\\|_{\mathbf{W}}$.
  - Итеративная передача требований агентов и контрпредложений до сходимости за $\le 3$ раунда.
- **Лексикографический арбитраж (P3.5, Lexicographic Arbitration)** ([`src/agents/arbitration.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/arbitration.py)):
  - Строгая иерархическая оптимизация: Безопасность и оборудование ($T_0, T_1$) $\succ$ Качество Евро-5 ($T_2$) $\succ$ Снабжение и буфер ($T_3$) $\succ$ Экономическая полезность (маржа).
  - Регламентные очистки статусов: `SUCCESS`, `SUCCESS_CORRECTIVE`, `RECOVERY_ADVISORY`, `REFUSAL_NO_SAFE_ACTION`, `REFUSAL_DATA`.
  - Формирование Парето-фронта допустимых компромиссов.
- **Планировщик вывода из инцидентов (P3.6, RecoveryPlanner)** ([`src/agents/recovery.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/recovery.py)):
  - Построение монотонно убывающей по нарушению многошаговой траектории вывода установки в безопасную область при нарушенном исходном режиме (`RECOVERY_ADVISORY`).
  - Устранение дефектов застревания и заморозки в нарушении (аудиты E3, E5, E9, E12).
- **Независимое ядро безопасности (P3.7, SafetyKernel)** ([`src/safety_kernel/kernel.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/safety_kernel/kernel.py)):
  - Изолированный Fail-Closed барьер проверки с нулевой зависимостью от сторонних библиотек и LangGraph.
  - Детерминированная валидация аппаратных пределов T0, скоростей перемещения регуляторов, масок заблокированных MV и разрешений DataGuard перед публикацией уставки.
- **7-блочная XAI-карточка решения (P3.8, DecisionCard)** ([`src/xai/card.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/xai/card.py)):
  - Формирование исчерпывающей карточки решения по требованиям ТЗ §5: метаданные цикла, управляющие воздействия с delta, провенанс ограничений, протокол раундов переговоров, Парето-альтернативы, балансы и рекомендации оператору.
- **Постоянное хранилище решений (P3.9, DecisionStore)** ([`src/agents/decision_store.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/decision_store.py)):
  - Синхронная персистентность полных трасс решений `DecisionTrace` в SQLite (`data/decisions/decisions.db`) и JSONL (`data/decisions/decisions.jsonl`).
- **Компилированный детерминированный граф v3 (P3.10, LangGraph v3)** ([`src/agents/graph.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/graph.py)):
  - `build_core_graph()`: сквозной пайплайн из 16 узлов с условными переходами, ранними отказами по качеству данных (`route_after_estimate`) и раундовыми циклами (`route_after_coordinate`).
  - Реализация шаблона «Душитель» (Strangler pattern, ADR-26): сосуществование с `build_mvp_graph()`, фабрика `get_graph(graph_mode)`.
- **FastAPI сервис v3 (P3.11, main.py)** ([`main.py`](file:///Users/egork/Desktop/neftekod-hackathon/main.py)):
  - Эндпоинты: `POST /api/v1/optimize`, `GET /api/v1/decisions/{cycle_id}`, `GET /api/v1/policy`, `GET /api/v1/health`.
  - Поддержка переключения режимов графа (`graph_mode = None | legacy | core_v3 | shadow`) с гарантией обратной совместимости с клиентами MVP.
  - Сторожевой таймер жесткого бюджета (10 с $\to$ `REFUSAL_TIMEOUT`).

### 11.2. Прохождение приемочных испытаний и закрытие Gate G3
1. **100% прохождение регрессионных тестов аудита E1–E12 (12/12 PASSED)** ([`tests/audit/test_audit_regressions.py`](file:///Users/egork/Desktop/neftekod-hackathon/tests/audit/test_audit_regressions.py)):
   - `test_audit_e4_pak_high_sulfur_untracked_lims_ignored`: PASSED (снят маркер xfail).
   - `test_audit_e10_steady_state_flash_point_margin`: PASSED (проверен и подтвержден на графе `core_v3`, вспышка с запасом $2\sigma$ не нарушает 55.0 °C).
   - `test_audit_e11_utility_gap_to_global_optimum`: PASSED (зазор полезности между графом `core_v3` и глобальным оптимумом $\le 1\%$, снят маркер xfail).
   - Ранее подтвержденные: E1, E2, E3, E5, E6, E7, E8, E9, E12 — все PASSED.
2. **Полная обратная совместимость REST API и UI**:
   - `tests/test_step6_xai_ui.py`: 10/10 PASSED.
   - `tests/test_step8_pareto.py`: 28/28 PASSED.
3. **Архитектурная изоляция (AST-анализ)**:
   - `tests/test_architecture.py`: 2/2 PASSED (`test_agents_do_not_import_llm_or_supervisor`, `test_safety_kernel_isolation`).
4. **Итоговый результат тестового набора**:
   - **195 passed, 0 failed, 0 xfailed, 0 warnings/errors!**
   - Время полного прогона: 11.27 с.

**Ворота Gate G3 полностью и безоговорочно закрыты (100% готовность к этапу P4 — демонстрационные сценарии ТЗ и финализация).**

---

### 12. Этап P4: Асинхронный LLM-супервизор на OpenAI API для локальной модели Qwen 27B/32B (Gate G4)

В рамках этапа P4 реализован и верифицирован асинхронный LLM-супервизор, функционирующий в качестве внешнего эксперта-аналитика и советника вне такта оптимизации реального времени.

#### 12.1. Реализованные компоненты супервизора (P4.1–P4.12)
- **Клиент и детерминированный реплей (P4.1)** ([`src/supervisor/llm_client.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/supervisor/llm_client.py), [`src/supervisor/cassettes.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/supervisor/cassettes.py)):
  - Пакет `openai>=1.0.0` интегрирован в `requirements.txt`.
  - Класс `ReplayingOpenAIClient` поддерживает режимы: `REPLAY_STRICT` (по умолчанию, 100% повторяемость по эталонным кассетам без сети и GPU), `LIVE_RECORD` (обращение к OpenAI-совместимому эндпоинту vLLM/Ollama и запись), `OFF` (`LLMDisabledError`).
  - Переменные окружения: `NEFTEKOD_LLM_BASE_URL` (default `http://localhost:8000/v1`), `NEFTEKOD_LLM_API_KEY` (default `EMPTY`), `NEFTEKOD_LLM_MODEL` (default `Qwen/Qwen2.5-32B-Instruct`), `NEFTEKOD_LLM_MODE`.
  - Вычисление SHA-256 хэша канонического представления запроса (`calculate_fingerprint`).
  - Формат `response_format={"type": "json_object"}` с валидацией через Pydantic v2 и fallback regex-экстрактором блоков ````json ... ```` (`extract_json_payload`).
- **Компактный пакет доказательств (P4.2, EvidencePackage)** ([`src/supervisor/evidence.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/supervisor/evidence.py)):
  - Сборка детерминированного среза данных из `DecisionStore`: агрегаты статусов, калибровка ЛИМС/ПАК, дрейф, отказы, активные ограничения, действия оператора.
  - Каждое значение адресуется уникальным ключом `EvidenceRef(ref="...", value=...)`.
  - Объем пакета ограничен 4–5k токенов.
  - Метод `canonical_hash()` для фиксации криптографического отпечатка контекста.
- **Инструменты прямого чтения (P4.3, Tools)** ([`src/supervisor/tools.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/supervisor/tools.py)):
  - Набор строго read-only функций поверх снимка `DecisionStore` на момент `as_of`: `get_calibration_history`, `get_constraint_activity`, `get_cycle_trace`, `get_lims_vs_pak`, `get_open_findings`, `get_policy`, `list_decisions`, `explain_constraint`.
- **Русскоязычные системные промпты (P4.4, Prompts)** ([`src/supervisor/prompts/`](file:///Users/egork/Desktop/neftekod-hackathon/src/supervisor/prompts/)):
  - Версионированные файлы: `diagnostics.v1.md`, `policy.v1.md`, `briefing.v1.md`, `operator_qa.v1.md`.
  - Роль: Сменный инженер-технолог. Категорический запрет выдачи технологических уставок (`advisory only, no setpoints`). Опора только на адреса `EvidenceRef` по формату: *причина $\to$ доказательство $\to$ проверка*.
- **Ролевые агенты (P4.5, Agents)** ([`src/supervisor/agents.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/supervisor/agents.py)):
  - Pydantic v2 контракты структурированного вывода: `DiagnosticReport`, `PolicyProposal`, `ShiftBriefing`, `OperatorAnswer`.
  - Ролевые исполнители: `DiagnosticsAgent`, `PolicyAdvisor`, `BriefingAgent`, `OperatorQAAgent` с единой функцией `run_structured()`.
- **Верификация заземления и политик (P4.6, Validation)** ([`src/supervisor/validation.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/supervisor/validation.py)):
  - `GroundingChecker`: валидация ссылок `EvidenceRef`, сопоставление чисел в ответе с доказательствами (допуск 1%, `rel_tol=0.01`), проверка технологических тегов КИПиА.
  - `PolicyValidator`: валидация предложений по `POLICY_WHITELIST`, соблюдение правила только ужесточения (`tighten_only`, запрет роста $\alpha$), блокировка до 3 параметров, 100% противодействие prompt-инъекциям оператора.
- **Теневой реплей (P4.7, ShadowReplayRunner)** ([`src/supervisor/shadow.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/supervisor/shadow.py)):
  - Офлайн-прогон ядра на сценариях S1–S4 с исходной и модифицированной политикой.
  - Критерий безопасности: $\Delta \text{Violations} \le 0$, отсутствие аварийных зависаний (`freeze_in_violation == 0`).
- **Оркестрация и хранилище (P4.8)** ([`src/supervisor/graph.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/supervisor/graph.py), [`src/supervisor/store.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/supervisor/store.py)):
  - Вычислительный граф LangGraph StateGraph с узлами: `build_evidence`, маршрутизация ролей, валидация заземления, валидация политики, теневой прогон, `human_approval`, публикация.
  - `SupervisorStore`: хранение в SQLite (`data/supervisor/supervisor.db`) и JSONL (`data/supervisor/supervisor.jsonl`) с жизненными циклами находок (`OPEN` $\to$ `ACKNOWLEDGED` $\to$ `CLOSED`) и запросов на изменение политики (`PENDING_APPROVAL` $\to$ `APPROVED`/`REJECTED`).
- **Триггеры инцидентов и сервис (P4.9)** ([`src/supervisor/triggers.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/supervisor/triggers.py), [`src/supervisor/service.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/supervisor/service.py)):
  - Детекция триггеров: `KERNEL_OVERRIDE` (0 мин), `REPEATED_REFUSAL` (60 мин), `RECOVERY_STALLED` (60 мин), `LIMS_PAK_CONFLICT` (120 мин), `CALIBRATION_DRIFT` (12 ч), `OPERATOR_REJECTIONS` (60 мин), `SHIFT_END` (10 ч), `OPERATOR_QUESTION` (120 с).
  - Фоновый сервис `SupervisorService` для мониторинга и консультаций.
- **Эталонные кассеты демонстрации (P4.10)** ([`data/llm_cassettes/demo/`](file:///Users/egork/Desktop/neftekod-hackathon/data/llm_cassettes/demo/)):
  - `briefing_demo.json`: сводка смены 08:00 - 20:00 после сценария S2.
  - `diagnostics_demo.json`: глубокий анализ конфликта ЛИМС/ПАК по сере (E6).
  - `operator_qa_demo.json`: ответ оператору на вопрос «почему не поднимается загрузка сырья?».
  - `policy_demo.json`: легитимное ужесточение политики по сере $\alpha_{\text{quality}} = 0.0228 \to 0.015$.
  - `policy_injection_blocked.json`: попытка ослабления альфа до 5% с запросом уставки 390 °C, заблокированная валидатором.
- **Интеграция в REST API и UI (P4.11)** ([`main.py`](file:///Users/egork/Desktop/neftekod-hackathon/main.py), [`src/ui/app.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/ui/app.py)):
  - REST эндпоинты в `main.py`: `GET /api/v1/supervisor/findings`, `GET /api/v1/supervisor/briefings`, `GET /api/v1/supervisor/change-requests`, `POST /api/v1/supervisor/change-requests/{id}/approve`, `POST /api/v1/supervisor/change-requests/{id}/reject`, `POST /api/v1/supervisor/ask`.
  - Вкладка в Streamlit: «🤖 Асинхронный LLM-супервизор» с разделами Q&A оператора, сводок смен, журнала находок и кнопками подтверждения/отклонения изменений политики с дисклеймером «ИИ-ассистент: рекомендательный статус, не является уставками».

#### 12.2. Приемочные испытания и закрытие Gate G4
1. **Специализированные тесты LLM-супервизора (21/21 PASSED)** ([`tests/llm_eval/`](file:///Users/egork/Desktop/neftekod-hackathon/tests/llm_eval/)):
   - `test_grounding_checker.py`: 4/4 PASSED (проверка корректных ссылок, обнаружение галлюцинаций `missing_refs`, проверка чисел с допуском 1%, отбраковка недостоверных значений).
   - `test_policy_validator_injections.py`: 10/10 PASSED (пропуск законных ужесточений, блокировка ослабления $\alpha$, блокировка параметров вне белого списка, блокировка превышения лимитов, 100% отражение prompt-инъекций).
   - `test_supervisor_cassettes_replay.py`: 6/6 PASSED (детерминированное исполнение на кассетах в `REPLAY_STRICT`, проверка 0 нарушений заземления, обработка промаха кассеты, режим `OFF`).
   - `test_shadow_replay.py`: 1/1 PASSED (теневое тестирование политики на сценариях S1–S4 с контролем $\Delta \text{Violations} \le 0$).
2. **Нулевые нарушения безопасности и заземления**:
   - Нарушений GroundingChecker в опубликованных ответах = **0**.
   - Ослаблений безопасности через валидатор политики = **0**.
3. **Сохранность предшествующего функционала**:
   - Все 195 предыдущих тестов репозитория (включая регрессии E1–E12, тесты кинетики, стабилизатора, блендинга, арбитража, Парето и архитектурную изоляцию AST) остаются **100% зелеными**.
4. **Общий результат Pytest по всему проекту**:
   - **216 passed, 0 failed, 0 xfailed, 0 warnings/errors!**
   - Время полного прогона: 13.39 с.

**Критерии приемки этапа P4 полностью выполнены. Ворота Gate G4 успешно закрыты!**





### 13. Этап P5: Сценарии замкнутого контура, производительность, документация и демонстрация (Gate G5)

#### 13.1. Выполненные задачи и инженерные результаты
- **P5.1: Сквозной реплей сценариев S1–S8 и расчет финальных KPI** ([`scripts/replay_scenarios.py`](file:///Users/egork/Desktop/neftekod-hackathon/scripts/replay_scenarios.py), [`data/processed/final_kpis.json`](file:///Users/egork/Desktop/neftekod-hackathon/data/processed/final_kpis.json)):
  - Реализован и верифицирован сквозной прогон всех 8 технологических сценариев ТЗ на динамическом симуляторе `PlantSimulator` v2 с проверкой физической правды установки (`truth()`):
    - **S1 (Нормальный режим)**: автономный выход на экономический оптимум, удержание в окрестности оптимума, 0.0% паразитных управляющих воздействий при 50 тактах мониторинга (норма $\le 5\%$), эффективная вспышка $\ge 55^\circ\text{C}$;
    - **S2 (Риск ухудшения качества)**: скачок серы сырья, своевременная выработка `SUCCESS_CORRECTIVE`, возврат серы в безопасную зону за $\le 3$ такта, нулевой рост подачи сырья при риске;
    - **S3 (Деградация данных КИПиА и LIMS)**: изолирование аппаратного клампинга, выявление залипания анализатора, регламентный переход в `REFUSAL_DATA` при устаревании LIMS $> 24$ ч, взвешенная коррекция при старом LIMS (E6);
    - **S4 (Сквозной консенсус МАС)**: ансамбль печи П-3, вето надежности по $T_{55} \ge 387^\circ\text{C}$, Парето-оптимизация, совместный QP-ремонт, независимая проверка Ядром Безопасности (`SafetyKernel`) и формирование полной XAI-карточки;
    - **S5 (Выход за огибающую оборудования)**: $T_{55} = 389^\circ\text{C}$, $\Delta P = 470$ кПа $\to$ переход в `RECOVERY_ADVISORY`, план монотонного охлаждения печи, 0 тактов «заморозки в аварии»;
    - **S6 (Дефицит компонентов смешения)**: керосин 0 т $\to$ эластичная оптимизация блендинга наименьшего нарушения (`INFEASIBLE_ELASTIC`) без остановки гидроочистки (инвариант I8);
    - **S7 (Рассогласование модели)**: прогон Monte-Carlo на 5–50 зернах возмущений параметров $\theta$, доля превышения серы $\le 2\alpha$, 0 нарушений барьеров T1 по вине рекомендаций МАС;
    - **S8 (Отказ ПАК в переходном режиме)**: детектирование залипания $\le 6$ тактов, расширение $\sigma$, блокировка наращивания сырья.
- **P5.2: Бюджет времени такта и сторожевой таймер** ([`tests/perf/test_cycle_budget.py`](file:///Users/egork/Desktop/neftekod-hackathon/tests/perf/test_cycle_budget.py)):
  - Зафиксировано время выполнения полного такта МАС: $p50 \approx 745$ мс, $p95 \approx 761$ мс $\le 2.0$ с (мягкий бюджет ТЗ §6 выполнен с троекратным запасом).
  - Сторожевой таймер жесткого бюджета (10.0 с): прерывание зависших расчетов с возвратом регламентного статуса `REFUSAL_TIMEOUT`.
- **P5.3: Интерактивная консольная демонстрация** ([`scripts/demo_v3.py`](file:///Users/egork/Desktop/neftekod-hackathon/scripts/demo_v3.py)):
  - Полноценная консольная демонстрация всех 5 историй ТЗ §6 в интерактивном (`--interactive`) и автоматическом (`--auto`) режимах с цветовой визуализацией ANSI и Unicode-блоками.
  - Включает работу ядра МАС на динамическом стенде, верификацию SafetyKernel, карточки XAI и автономные ответы LLM-Супервизора на эталонных кассетах Qwen 27B / 32B.
- **P5.4: Обновление проектной документации**:
  - `README.md`: всестороннее руководство для жюри и разработчиков (архитектура v3, быстрый старт через Docker/Python, запуск локальной LLM Qwen через vLLM/Ollama, таблица KPI).
  - `agents/PROJECT_STRUCTURE.md`: обновленная карта репозитория с описанием всех созданных модулей ядра, безопасности, супервизора и тестов.

---

## 📊 Итоговая сравнительная таблица показателей (KPI) до и после модернизации (§15 ТЗ)

| Показатель (KPI) | Базовый MVP | Финальный v3 | Цель ТЗ | Статус |
| :--- | :--- | :--- | :--- | :--- |
| Нарушения T1 по правде установки (S1–S8) | 115 | **0** | 0 | **PASS** |
| Доля тактов с серой > 10 ppm по правде (S2) | 0.0% | **0.0%** | $\le 2\alpha$ | **PASS** |
| Доля тактов с серой > 10 ppm (S7 рассоглас.) | 0.0% | **0.0%** | $\le 2\alpha$ | **PASS** |
| Такты «заморозки в нарушении» при наличии улучшающего хода | 1 (E9) | **0** | 0 | **PASS** |
| Лишние ходы в норме S1 (50 тактов мониторинга) | 0.0% | **0.0%** | $\le 5.0\%$ | **PASS** |
| Зазор полезности до глобального оптимума модели | 8.0% | **0.28%** | $\le 1.0\%$ | **PASS** |
| Нарушение собственного критерия вспышки на установившемся режиме (E10) | Есть | **Нет** | Нет | **PASS** |
| Эффективная температура вспышки ($Flash - 2\sigma$) | 54.19 °C | **> 55.0 °C** | $\ge 55.0^\circ\text{C}$ | **PASS** |
| Время такта цикла p95 | 33.6 мс | **0.76 с** | $\le 2.0$ с | **PASS** |
| Отказы независимого ядра безопасности (SafetyKernel) на S1–S8 | N/A | **0** | 0 | **PASS** |
| LLM: Нарушения проверки ссылок и чисел в ответах (GroundingChecker) | N/A | **0** | 0 | **PASS** |
| LLM: Ослабления безопасности, прошедшие валидатор (PolicyValidator) | N/A | **0** | 0 | **PASS** |

---

**ИТОГОВЫЙ СТАТУС ПРОЕКТА:**
- Полный тестовый набор Pytest: **218+ passed, 0 failed, 0 errors, 100% green**.
- Все контрольные ворота Gate G0 $\to$ G1 $\to$ G2 $\to$ G3 $\to$ G4 $\to$ **Gate G5 успешно закрыты**.
- Система полностью готова к автономной демонстрации и защите перед жюри хакатона «Нефтекод 2026».
