# Реализация MVP
### 1. Агенты аудита безопасности и качества [`auditors.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/auditors.py)
- **`ReliabilityAgent` (Агент Надежности, ПАЗ/ESD)**:
  - Контроль 5% защитных барьеров безопасности оборудования:
    - $T55 \le 386.40$ °C (перевал змеевика печи П-3, отсечение до закоксовывания 387.0 °C);
    - $W10 \le 4.635$ кгс/см² (перепад реактора Р-202, защита катализатора);
    - $P52 \le 0.077$ кгс/см² (перепад колонны К-10, защита от захлебывания);
    - $F31 \ge 362.50$ м³/ч (минимальный расход сырья печи от прогара труб).
  - При выходе за 5% барьер кандидат немедленно бракуется жестким вето (**Hard-Veto**).
  - В предохранительной зоне печи $[380.0, 386.40)$ °C рассчитывается гладкий логарифмический барьер риска:
    $$B(T55) = -\mu \cdot \ln\left( \frac{386.40 - T55}{12.0} \right), \quad \mu = 1000.0 \text{ руб/ч}$$
    Штраф стремится к бесконечности при приближении к границе, исключая экономический каннибализм.
  - Функция-узел `node_reliability_agent` для параллельного выполнения в LangGraph.

- **`QualityAgent` (Агент Качества, ГОСТ 32511-2013 Евро-5)**:
  - Проверка 5% буфера по содержанию серы: $S_{\text{diesel}} \le 9.50$ ppm (норматив 10.0 ppm).
  - Проверка допустимого диапазона плотности дизеля: $[821.25, 843.75]$ кг/м³.
  - При нарушении — безусловное наложение жесткого вето.
  - Функция-узел `node_quality_agent`.

### 2. Двухстадийный гибридный арбитраж [`arbitration.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/arbitration.py)
- **Класс `ArbitrationNode`**:
  - **Стадия 1 (Hard-Veto Gate)**: отсев всех кандидатов, получивших вето от аудиторов. Формирование допустимого множества $\mathcal{U}_{\text{admissible}}$.
    - Если $\mathcal{U}_{\text{admissible}} = \emptyset$ $\to$ немедленный перевод в Safe Hold с регламентным русскоязычным сообщением по Callout Box 4 ТЗ и $\Delta \mathbf{u} = \mathbf{0}$.
  - **Стадия 2 (Economic Clearing)**: расчет чистой полезности для допустимых кандидатов:
    $$\text{Net Utility} = \text{Expected Margin} - \text{Risk Penalty}$$
    Выбор кандидата с максимальной чистой полезностью.
  - **Стадия 3 (Deadband Filter — фильтр зоны нечувствительности)**:
    - Порог чистой маржи: **$1000.0$ руб/ч** (настроен по прямому указанию пользователя). Если выигрыш ниже 1000 руб/ч $\to$ режим замораживается ($\Delta \mathbf{u} = \mathbf{0}$) со статусом `DEADBAND_REJECT_LOW_MARGIN` для сбережения ресурса клапанов.
    - Порог нормы шага: **$0.05$**. Если $\|\Delta \mathbf{u}\|_2 < 0.05 \to$ уставки не меняются (`DEADBAND_REJECT_SMALL_STEP`).
  - Метод `calculate_norm(delta_u)`: вычисление $L_2$-нормы вектора воздействий.
  - Функция-узел `node_arbitration`.

### 3. Сквозной граф вычислений LangGraph [`graph.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/graph.py)
- Полная архитектура сопряжения агентов:
  - `data_guard` $\to$ `route_after_guard`
    - Сбой $\to$ `safe_hold` $\to$ `END`
    - Норма $\to$ `optimization`
  - `optimization` $\to$ параллельный Fan-Out на `[reliability_agent, quality_agent]`
  - Параллельные аудиторы $\to$ Fan-In слияние в `arbitration` через редьюсеры `operator.add` и `merge_risk_penalties` (без риска `InvalidUpdateError`)
  - `arbitration` $\to$ `route_after_arbitration`
    - `SAFE_HOLD` $\to$ `safe_hold` $\to$ `END`
    - `DEADBAND` $\to$ `END`
    - `SUCCESS` $\to$ `blending` (расчет оптимальной рецептуры) $\to$ `END`


### 4: Контракты Pydantic v2 и компенсация запаздывания LIMS
- **Контракты данных** ([`src/agents/state.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/state.py)):
  - `BaseAgentProtocol` с замороженной конфигурацией `frozen=True` и `extra="forbid"`.
  - `SafetyAuditReport` с валидацией неотрицательности барьерных штрафов (`risk_penalty_rub_h >= 0.0`).
- **Компенсатор запаздывания LIMS** ([`src/agents/lims.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/lims.py)):
  - Ретроспективная инновация: расчет ошибки прогноза относительно исторической точки пробоотбора.
  - Экспоненциальное затухание смещения (Bias Decay) с периодом полураспада $T_{1/2} = 12$ часов.
  - Безударный перенос (Bumpless Transfer): фильтрация первого порядка ($\tau = 30$ мин), устраняющая скачки в контурах регулирования.
  - Верхняя доверительная граница (UCB, 95% = $+1.96\sigma_t$) для наихудшего сценария по сере.
  - Экспорт в [`src/agents/__init__.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/__init__.py).

### 5: Объяснимый ИИ (XAI), REST API (FastAPI) и Консоль оператора (Streamlit HITL)
- **Генератор объяснимого ИИ** ([`src/xai/narrative.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/xai/narrative.py)):
  - Детерминированная физико-химическая аргументация решений на русском языке (квенч F15, перевал печи T55, орошение К-2 F19, барьеры ПАЗ и ГОСТ).
  - Интеграция в узел арбитража [`src/agents/arbitration.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/arbitration.py) и узел [`src/agents/safe_hold.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/safe_hold.py).
  - Экспорт в [`src/xai/__init__.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/xai/__init__.py).
- **REST API Сервис** ([`main.py`](file:///Users/egork/Desktop/neftekod-hackathon/main.py)):
  - Эндпоинт `POST /api/v1/optimize`: валидация телеметрии, запуск мультиагентного графа LangGraph, возврат рекомендаций, XAI-отчета и рецептуры блендинга.
  - Эндпоинт `GET /api/v1/health`: проверка доступности микросервиса.
- **Интерфейс оператора HITL** ([`src/ui/app.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/ui/app.py)):
  - Информационный дэшборд: сырье F26, сера ПАК, возраст анализов LIMS, COT печи П-3.
  - Отображение вердиктов МАС (SUCCESS / SAFE_HOLD / DEADBAND).
  - Human-in-the-Loop: кнопки оперативного одобрения (запись в DCS/APC) и мотивированного отклонения.
  - Ручное переопределение тегов (Tag Override / ISA-18.2 Alarm Suppression).

### 6. Комплексный аудит кода и фиксация архитектурных расширений
Для всех выявленных улучшений, требующих >100 строк кода, добавлены структурированные комментарии `# TODO (Post-MVP)`:
- В [`src/agents/arbitration.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/agents/arbitration.py): Распределенный арбитраж ADMM и рыночный аукцион Вальраса (Walrasian Auction) между установками.
- В [`src/twin/fopdt.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/twin/fopdt.py): Расширенный фильтр Калмана (EKF) и оцениватель на скользящем горизонте (MHE) с динамической матрицей ковариаций $R(t)$.
- В [`src/xai/narrative.py`](file:///Users/egork/Desktop/neftekod-hackathon/src/xai/narrative.py): Интеграция LLM-ассистента через vLLM с RAG по технологическим регламентам ЭЛОУ-АВТ-6 и 24-2000.

# Результаты тестирования - все работает.
