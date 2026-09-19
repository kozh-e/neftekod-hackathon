# План реализации: редизайн пульта старшего оператора Р-202 (v2)

Статус: backend-часть (раздел 2) УЖЕ РЕАЛИЗОВАНА и импортируется без ошибок (см. `STATUS.md`, волна 4).
Всё остальное (разделы 4–11) — задача для исполнителя (Gemini 3.8 Flash High).

Перед началом работы исполнитель обязан прочитать:
- `agents/console_tz/01_CONTRACT.md` — базовый контракт данных v1 (не отменяется, только расширяется).
- `agents/console_tz/ROLE_4_frontend_shell.md`, `ROLE_5_frontend_charts.md` — исходные требования к фронтенду v1.
- Текущий код: `static/console/*`, `src/console/*.py`.

Не переписывать код "с нуля" — v1 рабочий и покрыт 48 тестами (`tests/console/`) плюс общей регрессией (223 теста ядра/агентов). Редизайн — это рефакторинг структуры (вкладки вместо одного экрана) и точечные новые фичи, а не замена движка графиков/бэкенда.

---

## 0. Контекст: почему это нужно

Пульт v1 — это фиксированный экран 1920×1080 без прокрутки (ISA-101, под требование "жюри без скролла"). После демонстрации получены замечания:

1. Всё зажато в один экран, есть визуально чужеродные синие поля ввода.
2. Нет возможности выбирать, какие графики показывать.
3. Нет места для ручного редактирования всех уставок сразу.
4. Не виден Парето-фронт (хотя данные для него уже считаются в `src/agents/pareto.py`).
5. Нет управления скоростью такта / ручным шагом в основном интерфейсе (есть только в скрытой `?demo=1` панели).
6. Недостаточно объяснимости (XAI) — видно не более 3 строк ленты за такт.
7. Просьба задать наводящие вопросы перед реализацией.

По пункту 7 проведены два раунда уточняющих вопросов с заказчиком. Раздел 1 фиксирует принятые решения — исполнитель должен реализовывать именно их, не изобретая альтернатив.

---

## 1. Принятые решения (все подтверждены заказчиком)

| # | Тема | Решение |
|---|------|---------|
| D1 | Структура страницы | **Верхние вкладки**: Обзор / Ручное управление / Агенты и XAI / Парето-анализ / Журнал смены. Внутри каждой вкладки — обычная страница, не более. |
| D2 | Скролл | **No-scroll ТЗ отменяется.** Адаптивная верстка с обычной прокруткой. Убрать `transform: scale()` и фиксированный контейнер 1920×1080. |
| D3 | Выбор графиков | **Панель виджетов с чекбоксами** ("Настроить вид"), выбор сохраняется в `localStorage`. |
| D4 | Ручной ввод | **Отдельная вкладка «Ручное управление»** со всеми 5 MV сразу (не только теми, что трогает рекомендация), с полями ввода + степперами. |
| D4a | Песочница | Кнопка "Показать прогноз" на вкладке ручного управления запускает предпросмотр, который **не трогает боевую установку** (уже так работает: `forecast.preview()` считает на клоне двойника `session.plant.twin.clone()`). |
| D4b | Коридор шага в песочнице | **Коридор — информация, не блокировка.** В песочнице прогноз строится и показывается для любых значений; `corridor_ok=false` отображается как нейтральная информационная подсказка, а не тревога. Реальная кнопка "Применить к боевой установке" остаётся защищена теми же проверками ядра/коридора, что и сегодня (без изменений в `service.commit()`). |
| D5 | Парето-фронт | **2D-график с переключаемыми осями, кликабельный.** По умолчанию: маржа (net_margin) ↔ переочистка по сере (sulfur_giveaway). Точки фронта/доминируемые/отклонённые вето — разными цветами и символами. Клик по точке — выбрать её как цель (аналогично текущим 3 кнопкам-альтернативам). |
| D6 | Скорость такта | **Постоянная панель «Симулятор»** в основном интерфейсе (не только `?demo=1`): слайдер сек/такт, пауза, кнопка «Следующий такт вручную». |
| D7 | XAI | В первую очередь: **полный лог переговоров агентов** (все раунды, не более 3 строк ленты как сейчас) на вкладке «Агенты и XAI». |
| D8 | Синие поля | Убрать `.change-input` с толстой синей рамкой (`border: 2px solid var(--rec)`), заменить на нейтральный степпер в стиле остального ISA-101 интерфейса. |

---

## 2. Что уже сделано в бэкенде (не переделывать, только использовать)

### 2.1 `src/console/contracts.py` — добавлены DTO (вставлены перед `class Refusal`)

```python
class ParetoObjective(Frozen):
    key: str
    label: str
    unit: str
    sense: Literal["max", "min"]
    limit: Optional[float] = None

class ParetoPointDTO(Frozen):
    candidate_id: str
    is_hold: bool
    is_recommendation: bool
    status: Literal["pareto", "dominated", "vetoed", "incomplete"]
    metrics: Dict[str, Optional[float]]
    delta_u: Dict[str, float] = Field(default_factory=dict)
    veto_reasons: List[str] = Field(default_factory=list)
    dominated_by: List[str] = Field(default_factory=list)

class ParetoFrontDTO(Frozen):
    objectives: List[ParetoObjective]
    points: List[ParetoPointDTO]
    ideal: Dict[str, float] = Field(default_factory=dict)
    nadir: Dict[str, float] = Field(default_factory=dict)
    hold_id: Optional[str] = None
    selected_id: Optional[str] = None

class NegotiationEventDTO(Frozen):
    round: int
    kind: str
    actor: str
    candidate: Optional[str] = None
    detail: str = ""

class XaiInfo(Frozen):
    cycle_id: str
    events: List[NegotiationEventDTO]
```

По умолчанию `analysis.metrics` в `src/agents/pareto.py` содержит 5 осей: `net_margin` (руб/ч, max), `sulfur_giveaway` (ppm, min), `bed_temperature`/WABT (°C, min), `sulfur_ucb` (ppm, min), `reactor_dp` (кПа, min). Все они попадают в `ParetoFrontDTO.objectives` — фронтенд должен дать выбрать любую пару как оси X/Y, а не хардкодить только net_margin/sulfur_giveaway.

### 2.2 `src/console/service.py` — добавлены функции (перед `def commit(...)`)

```python
def build_pareto(session: ConsoleSession) -> Optional[ParetoFrontDTO]: ...
def build_xai(session: ConsoleSession) -> Optional[XaiInfo]: ...
```

Обе читают `session.last_graph_result` (уже посчитан на такте, повторных тяжёлых расчётов графа НЕТ). Возвращают `None`, если такта ещё не было или `pareto`/`negotiation_log` отсутствуют (например, в первом такте после старта) — фронтенд обязан обработать `null`.

### 2.3 `src/console/api.py` — добавлены эндпоинты

```
GET /api/console/pareto?session_id=demo  -> ParetoFrontDTO | null
GET /api/console/xai?session_id=demo     -> XaiInfo | null
```

Существующие эндпоинты (`/state`, `/preview`, `/commit`, `/reject`, `/mode`, `/auto/skip`, `/ack`, `/ask`, `/shift-report`, `/demo/load`, `/demo/speed`, `/demo/tick`, `/demo/fault`) **не менялись**. В частности `/demo/speed` и `/demo/tick` уже сегодня ничем не гейтятся на бэкенде — их прятал только фронтенд за `?demo=1`. Для D6 бэкенд трогать не нужно, только вынести кнопки в основной UI.

### 2.4 Важный найденный баг (учесть, но не обязательно чинить в этом тикете)

`static/console/app.js` в двух местах (`triggerPreviewDebounced`, `executeCommit`) читает `store.state.u_current` — **такого поля нет и никогда не было в `ConsoleState`** (проверено по `contracts.py` и всем фикстурам). Spread необъявленного `undefined` в объектном литерале — это no-op, поэтому сегодня `targetU` фактически равен `{...store.edits}`, без слияния с текущими значениями остальных MV. Это не ломает v1, потому что там правится не больше 1–2 MV одновременно (из `recommendation.changes`), и preview/commit прекрасно работают с частичным набором ключей.

**Для вкладки «Ручное управление» это важно**: там правятся сразу все 5 MV, и понадобится настоящий "текущий вектор уставок". Брать его нужно **не из несуществующего `state.u_current`**, а собирать на фронтенде из `state.series.mv`:

```js
function currentU(state) {
  const u = {};
  for (const mv of state.series.mv) {
    const last = mv.sp_history.slice(-1)[0];
    if (last && last.v != null) u[mv.sp] = last.v;
  }
  return u;
}
```

Рекомендуется заодно поправить те же две строки в `app.js` (заменить `store.state.u_current` на `currentU(store.state)`) — это правильный фикс, не меняющий поведения (для существующих сценариев набор ключей совпадает), но делает код корректным по факту, а не по случайности.

---

## 3. Общая архитектура фронтенда после редизайна

### 3.1 Убрать letterbox/scale (D2)

В `static/console/styles.css` удалить/переписать:
- `#viewport-wrapper` (абсолютное позиционирование, `background: #202224`, letterbox) — убрать полностью.
- `#root-console { width: 1920px; height: 1080px; ... }` — заменить на `min-height: 100vh; width: 100%; max-width: 1920px; margin: 0 auto;`.
- `html, body { overflow: hidden }` → `overflow-y: auto` (обычная прокрутка страницы).
- В `static/console/app.js` удалить функцию `updateScale()` и её вызовы (`transform: scale(...)`), а также `resize`-листенер, который её дёргает.

Рамка режима (`border: 6px solid var(--rec|auto|warn-frame)` на `#root-console`) — можно оставить как акцентную рамку вкладочной области, но она больше не привязана к фиксированному пикселю 1920×1080.

### 3.2 Разметка вкладок

```html
<nav class="tab-bar" role="tablist">
  <button class="tab-btn active" data-tab="overview" role="tab" aria-selected="true">Обзор</button>
  <button class="tab-btn" data-tab="manual" role="tab" aria-selected="false">Ручное управление</button>
  <button class="tab-btn" data-tab="xai" role="tab" aria-selected="false">Агенты и XAI</button>
  <button class="tab-btn" data-tab="pareto" role="tab" aria-selected="false">Парето-анализ</button>
  <button class="tab-btn" data-tab="shift" role="tab" aria-selected="false">Журнал смены</button>
</nav>

<section class="tab-panel" data-tab-panel="overview">...текущий console-body...</section>
<section class="tab-panel" data-tab-panel="manual" hidden>...</section>
<section class="tab-panel" data-tab-panel="xai" hidden>...</section>
<section class="tab-panel" data-tab-panel="pareto" hidden>...</section>
<section class="tab-panel" data-tab-panel="shift" hidden>...</section>
```

- `status-bar` (шапка с режимом/маржой/тревогами/часами) остаётся общей для всех вкладок, рендерится один раз, не дублируется.
- `alert-banner` (баннер автовыхода/отказа) — тоже общий, над `tab-bar`.
- Переключение вкладок: `[hidden]` атрибут + класс `.active` на кнопке. Без роутинга по URL обязательно, но желательно синхронизировать с `location.hash` (`#manual`), чтобы можно было дать прямую ссылку на вкладку (пригодится для демонстрации жюри).
- В `app.js` завести `store.activeTab` и функцию `switchTab(name)`, которая: скрывает все `.tab-panel`, показывает нужную, обновляет `aria-selected`, и лениво подгружает данные вкладки при первом открытии (Парето/XAI не должны запрашиваться, пока их вкладка ни разу не открыта).

### 3.3 Общее состояние `store` в `app.js`

Расширить существующий объект `store` (не переписывать с нуля):

```js
const store = {
  // ...существующие поля без изменений (state, activeKind, choice, edits, preview, historyHours, pending, ...)
  activeTab: "overview",
  widgetVisibility: loadWidgetVisibility(), // из localStorage, см. §4.2
  manual: {                                  // вкладка "Ручное управление", см. §5
    edits: {},         // { sp: value }, независимо от store.edits на вкладке "Обзор"
    preview: null,
    loading: false,
  },
  pareto: { data: null, axisX: null, axisY: null, loading: false, loadedForTick: null },
  xai: { data: null, loading: false, loadedForTick: null },
  sim: { secondsPerTick: null }, // синхронизируется из state.clock.seconds_per_tick
};
```

---

## 4. Вкладка «Обзор» (переносится из текущего единственного экрана)

### 4.1 Панель «Симулятор» (D6)

Разместить как узкую полосу над `legend-bar` (или в `status-bar` — на усмотрение исполнителя, но не как модалку). Содержимое — то же, что сегодня в `#demo-host-panel` под `?demo=1`, только:
- Всегда видима (без `?demo=1`), стилизована в общей палитре (`--panel`, `--line`, не тёмный полупрозрачный фон host-панели).
- Скорость: сегмент/слайдер с значениями **Пауза / 2с / 5с / 10с / 30с** (переиспользовать `api.demo.speed(seconds)`, эндпоинт не меняется).
- Кнопка **«Следующий такт вручную»** (переиспользовать `api.demo.tick(1)`), активна всегда, даже когда не на паузе (нажатие форсирует лишний такт немедленно).
- Кнопки выбора сценария (S1/S2/S3d/S4/S5) и инжекции отказов КИП **оставить только под `?demo=1`** — это host/жюри-инструменты, а не часть повседневного пульта оператора (заказчик просил только скорость такта и ручной шаг, не сценарии).
- Существующий `#demo-host-panel` под `?demo=1` можно оставить как есть (для сценариев/отказов) либо убрать из него дублирующиеся speed/tick кнопки, раз они переехали в постоянную панель — решение оставляется на усмотрение исполнителя, лишь бы не было двух независимых источников правды для одного и того же состояния скорости.

### 4.2 Панель виджетов (D3)

Кнопка «Настроить вид» в `legend-bar` (справа, рядом с `history-switch`) открывает панель (popover или встроенная раскрывающаяся секция) со списком чекбоксов:

```
CV: Сера, Вспышка, Перепад Р-202
MV: Сырьё ГО, Вход Р-202, Давление, Кратность ВСГ, Перевал П-3
```

- Список виджетов — константа в `app.js`, например:
  ```js
  const WIDGET_DEFS = [
    { id: "cv.sulfur", group: "CV", label: "Сера" },
    { id: "cv.flash", group: "CV", label: "Вспышка" },
    { id: "cv.dp", group: "CV", label: "Перепад Р-202" },
    { id: "mv.0", group: "MV", label: "Сырьё ГО" },
    { id: "mv.1", group: "MV", label: "Вход Р-202" },
    { id: "mv.2", group: "MV", label: "Давление" },
    { id: "mv.3", group: "MV", label: "Кратность ВСГ" },
    { id: "mv.4", group: "MV", label: "Перевал П-3" },
  ];
  ```
- Состояние — `{ [id]: boolean }`, по умолчанию все `true`, хранится в `localStorage` под ключом `console_widgets_v1`.
- В `render()`/`renderCharts()` соответствующие `.cv-panel` / `.dp-compact-row` / `.mv-card` получают `style.display = visible ? "" : "none"`. При выключении CV/MV элемент не рендерится, но данные с сервера продолжают приходить (не оптимизируем трафик — это не тот масштаб).
- Не давать выключить **все** CV или **все** MV разом (минимум 1 из группы) — простая защита от пустого экрана.

### 4.3 Fix "синих полей" (D8)

Заменить `.change-input` (сейчас: `border: 2px solid var(--rec)`, обычный `<input type=text>`) на компонент-степпер:

```html
<div class="value-stepper">
  <button type="button" class="stepper-btn" data-dir="-1" aria-label="Уменьшить">−</button>
  <input id="input-..." class="stepper-value mono" type="text" inputmode="decimal" value="...">
  <button type="button" class="stepper-btn" data-dir="+1" aria-label="Увеличить">+</button>
</div>
```

CSS (замена `.change-input`):
```css
.value-stepper {
  display: flex; align-items: stretch; height: 44px;
  border: 1px solid var(--line); border-radius: var(--r); overflow: hidden; background: #FFFFFF;
}
.stepper-btn {
  width: 34px; border: none; background: var(--panel-2); color: var(--ink-2);
  font-size: 18px; font-weight: 700;
}
.stepper-btn:hover { background: var(--line-soft); }
.stepper-value {
  width: 90px; border: none; text-align: center; font-size: 20px; font-weight: 600;
  background: transparent; color: var(--ink);
}
.value-stepper.edited { border-color: var(--edit); }
.value-stepper.edited .stepper-value { color: var(--edit-ink); }
.value-stepper:focus-within { border-color: var(--rec); box-shadow: 0 0 0 2px rgba(29,90,166,0.15); }
```

Логика степпера в JS: клик на `.stepper-btn` меняет значение на `data-step` (уже есть в текущем `change-input` через `dataset.step`), клавиши ↑/↓ работают как раньше. Использовать этот же компонент и на вкладке «Ручное управление» (§5.2) — не делать два разных виджета ввода числа.

---

## 5. Вкладка «Ручное управление» (D4, D4a, D4b)

### 5.1 Назначение

Показать все 5 MV одновременно (не только те, что рекомендация предлагает менять), дать оператору/инженеру/жюри свободно поэкспериментировать «что если», не рискуя боевой установкой, без давления коридора шага как блокирующего фактора.

### 5.2 Разметка (структурно копирует `change-row`, но для всех 5 MV сразу)

Для каждого `mv` из `state.series.mv` (или сохранённого списка MV_META, чтобы порядок не зависел от бэкенда):

```html
<div class="manual-row" data-sp="HT_FEED_SP">
  <label>Сырьё ГО <span class="mono">HT_F9</span></label>
  <span class="mono manual-current">54.2</span>  <!-- текущее значение, только чтение -->
  <div class="value-stepper">...</div>            <!-- целевое значение, редактируемое -->
  <span class="manual-corridor-note">шаг ≤ 3.0 т/ч за такт · паспортный диапазон 40–70 т/ч</span>
</div>
```

Начальное значение каждого степпера = `currentU(state)[sp]` (см. §2.4). Изменение любого поля → debounce 250 мс (как на вкладке «Обзор») → вызов `api.preview(mergedTargetU, null)`, где `mergedTargetU = { ...currentU(state), ...store.manual.edits }`. `cycle_id` передаём `null` — песочница не привязана к конкретному рекомендованному циклу, «устаревание» рекомендации (`stale`) здесь неприменимо (`can_commit` может быть `false` из-за этого — не страшно, см. ниже).

### 5.3 Результат прогноза

Переиспользовать существующий рендерер графиков (`static/console/charts.js`, `mountCharts`/`renderCharts`) на отдельном наборе DOM-узлов внутри вкладки (три графика CV + 5 мини-MV, как на «Обзоре»). Смонтировать второй `chartsHandle` (`store.manual.chartsHandle = mountCharts({...})`) при первом открытии вкладки, рендерить `renderCharts(store.manual.chartsHandle, { state, active: store.manual.preview.trajectory, activeColor: "edit", historyHours: 8 })`.

Под графиками — три плитки эффекта (аналог `.effect-grid` с «Обзора»): сера через 4 ч, запас по вспышке, дельта маржи — берутся из `preview.effect`.

### 5.4 Коридор — информация, не блок (D4b)

`PreviewResult.corridor_ok` / `corridor_violations` **не должны** вызывать красную тревожную плашку в этой вкладке. Вместо `risk-badge-box` (красный, как на «Обзоре») использовать нейтральный информационный блок:

```css
.sandbox-info-box {
  background: var(--panel-2); border: 1px solid var(--line); border-radius: var(--r);
  padding: 10px 12px; color: var(--ink-2); font-size: 14px;
}
```

Текст формируется из `corridor_violations`, например: *«Сырьё ГО: запрошенный шаг 12.0 т/ч больше паспортного (макс. 3.0 т/ч за такт). Прогноз рассчитан как целевой установившийся режим — реальный привод дошёл бы до него за несколько тактов.»* Никакого disabled-состояния на кнопку «Показать прогноз» из-за `corridor_ok=false` — она должна работать всегда.

Кнопка «Применить к боевой установке» — **отдельная**, обычная (не «песочничная»): вызывает `api.commit({ u_target: mergedTargetU, source: "operator_edit" })` **без изменений в `service.commit()`** — она по-прежнему обязана пройти те же проверки ядра и коридора (409 при нарушении), что и сегодня commit из «Обзора». Это осознанно: свобода экспериментов — только в прогнозе, не в реальном применении. Двухэтапное подтверждение (5 с), как у существующей кнопки `btn-commit-action`, обязательно и здесь.

### 5.5 Явное разделение двух действий в UI

```
[ Показать прогноз (песочница) ]     — всегда активна, ничего не применяет
[ Применить к боевой установке  ]     — то же самое ограничение по ядру/коридору, что и везде; с подписью
                                          "недоступно: <blocking_reason>", если prev.can_commit === false
```

Подпись рядом с заголовком вкладки: *«Прогноз в этой вкладке не влияет на реальную установку, пока вы не нажмёте «Применить к боевой установке»»* — обязательна, чтобы не создавать у оператора ложного ощущения, что он уже что-то изменил.

---

## 6. Вкладка «Агенты и XAI» (D7)

### 6.1 Загрузка данных

При первом открытии вкладки и при каждом последующем обновлении состояния (пока вкладка активна) — `GET /api/console/xai?session_id=...` через новую функцию `api.getXai()` (см. §9). Хранить в `store.xai.data`; если `null` — показать заглушку «Для этого такта нет журнала переговоров» (актуально сразу после старта/смены сценария до первого полного такта).

### 6.2 Разметка

Таблица/лента, сгруппированная по `round` (раунды поиска решения в текущем такте):

```
Раунд 1
  10:30  optimization   PROPOSED    cand_003      Предложен ход +2.1 т/ч по сырью
  10:30  reliability    CERTIFIED   cand_003      T0–T2 пройдены с запасом
  10:30  kernel         REPAIR_PROPOSED cand_003  Шаг срезан до 3.0 т/ч по коридору
Раунд 2
  10:30  optimization   BEST_UPDATED cand_003_r   Обновлён лучший кандидат после среза
  10:30  arbitration    CONVERGED   cand_003_r    Переговоры сошлись, выбран cand_003_r
```

Значения `kind` — ровно из `NegotiationEventDTO.kind` (см. `src/agents/contracts.py::NegotiationEvent.kind`): `PROPOSED | CERTIFIED | REPAIR_PROPOSED | REPAIR_COMPOSED | BEST_UPDATED | CONVERGED | NO_NEW_CANDIDATES | BUDGET_EXHAUSTED`. Не пытаться угадывать "veto/reject" как это (некорректно) делает `feed.py` — этих значений в `kind` не бывает, отображать как есть.

Ниже полного лога — оставить/перенести сюда существующий блок сводки по агентам (`rec.agents`, сейчас это `#agents-list` под «Почему так решили» на «Обзоре») и блок статусов ядра (`kernel.checks`) — на этой вкладке они уместнее, чем спрятанные в сворачиваемом блоке карточки рекомендации. Саму сворачиваемую кнопку "Почему так решили" на «Обзоре» можно оставить как краткую версию со ссылкой/переключением на вкладку «Агенты и XAI» за подробностями.

---

## 7. Вкладка «Парето-анализ» (D5)

### 7.1 Загрузка данных

При первом открытии вкладки и после каждого нового такта (пока вкладка активна) — `GET /api/console/pareto?session_id=...`. Если `null` — заглушка «Парето-анализ недоступен для этого такта» (например, во время `REFUSAL_DATA`, когда агент качества не отработал).

### 7.2 График — впервые реально используем `vendor/echarts.min.js`

Сегодня `echarts.min.js` подключён в `index.html`, но **нигде не используется** — все существующие графики в `charts.js` нарисованы вручную через SVG-строки. Для Парето-фронта уместно наконец задействовать ECharts (уже локально захостен, конфигурация проекта это предполагала) — это единственный график пульта, которому нужны из коробки: легенда с переключением серий по клику, тултип, обработчик клика по точке. Переписывать существующие SVG-графики CV/MV на ECharts **не нужно** — вне периметра задачи.

Добавить в `static/console/charts.js` новую экспортируемую функцию, не трогая существующие:

```js
export function renderParetoChart(container, paretoData, { axisX, axisY, onPointClick }) { ... }
```

Реализация — `echarts.init(container)`, `option.series` = 3 серии по `status` (`pareto` — зелёный кружок, `dominated` — серый пустой кружок, `vetoed` — красный крестик), `symbolSize` побольше для `is_hold`/`is_recommendation` точек (или отдельная 4-я серия с ромбом/звездой, как в `src/agents/pareto.py::_highlights`/`STATUS_STYLE` — переиспользовать те же цвета для консистентности с инженерским интерфейсом: `pareto=#2E7D32, dominated=#90A4AE, vetoed=#C62828`, hold=`#263238`, выбранная=`#F9A825`). Тултип — как в `_hover()` там же: candidate_id, delta_u, статус, значения метрик, veto_reasons/dominated_by если есть.

Обработчик клика (`chart.on('click', ...)`) вызывает `onPointClick(point)` из `app.js`.

### 7.3 Селектор осей

Два `<select>` (или сегментированных переключателя) «Ось X» / «Ось Y», опции — `paretoData.objectives` (label + unit), по умолчанию `net_margin` (Y) / `sulfur_giveaway` (X) если присутствуют, иначе первые два из списка. При смене — перерисовать график тем же набором точек без нового запроса к серверу (данные по всем метрикам уже в `points[i].metrics`).

### 7.4 Клик по точке = выбор цели (кликабельно, из D5)

`onPointClick(point)`:
1. Посчитать `targetU = { ...currentU(state), ...applyDelta(point.delta_u) }`, где `applyDelta` прибавляет `delta_u[sp]` к текущему значению для каждого `sp` в `delta_u`.
2. Переключить на вкладку «Ручное управление» (`switchTab("manual")`), заполнить `store.manual.edits` этими целевыми значениями, сразу запустить preview (как будто оператор руками ввёл эти же числа).
3. Показать короткую заметку в шапке вкладки «Ручное управление»: *«Загружена точка Парето-фронта `<candidate_id>`»*.

Это переиспользует всю инфраструктуру §5 (прогноз/применение) вместо дублирования логики commit прямо на вкладке Парето — один путь применения уставок во всём приложении.

---

## 8. Вкладка «Журнал смены»

Перенести содержимое текущей модалки `#modal-overlay` (кнопка `btn-shift-report`, вызывающая `GET /api/console/shift-report`) в постоянную вкладку: при открытии вкладки — запрос отчёта и рендер `renderMarkdown(res.markdown)` в тело вкладки вместо модалки. Кнопку «Сдать смену» в `status-bar` можно оставить как быстрый переход (`switchTab("shift")`) вместо открытия модалки. Blok "Спросить систему" (`ask-box`) — оставить на вкладке «Обзор» (внизу правой колонки, как сейчас), отдельной вкладки под него не заводить (не требовалось).

---

## 9. `static/console/api.js` — новые функции

```js
export async function getPareto() {
  if (FIXTURE_MODE) {
    const res = await fetch("/console/fixtures/pareto.json");
    if (!res.ok) return null;
    return await res.json();
  }
  const res = await fetch(`/api/console/pareto?session_id=${encodeURIComponent(SESSION_ID)}`);
  if (!res.ok) throw new Error(`Ошибка получения Парето-фронта: ${res.status}`);
  const data = await res.json();
  return data; // может быть null
}

export async function getXai() {
  if (FIXTURE_MODE) {
    const res = await fetch("/console/fixtures/xai.json");
    if (!res.ok) return null;
    return await res.json();
  }
  const res = await fetch(`/api/console/xai?session_id=${encodeURIComponent(SESSION_ID)}`);
  if (!res.ok) throw new Error(`Ошибка получения XAI-журнала: ${res.status}`);
  return await res.json();
}
```

`demo.speed`/`demo.tick` уже существуют (§2.3) — переиспользовать без изменений для панели «Симулятор» (§4.1).

---

## 10. Фикстуры для офлайн-режима `?fixture=`

Создать `static/console/fixtures/pareto.json` и `static/console/fixtures/xai.json`, строго соответствующие `ParetoFrontDTO`/`XaiInfo` (см. §2.1). Пример структуры (значения — иллюстративные, не копировать бездумно, свериться с реальными единицами/лимитами из `src/agents/pareto.py`):

```json
// pareto.json
{
  "objectives": [
    {"key": "net_margin", "label": "Чистая маржа к hold", "unit": "руб/ч", "sense": "max", "limit": null},
    {"key": "sulfur_giveaway", "label": "Переочистка по сере", "unit": "ppm", "sense": "min", "limit": null},
    {"key": "bed_temperature", "label": "WABT слоя Р-202", "unit": "°C", "sense": "min", "limit": 390.0}
  ],
  "points": [
    {"candidate_id": "hold", "is_hold": true, "is_recommendation": false, "status": "dominated",
     "metrics": {"net_margin": 0.0, "sulfur_giveaway": 1.2, "bed_temperature": 372.0},
     "delta_u": {}, "veto_reasons": [], "dominated_by": ["cand_selected"]},
    {"candidate_id": "cand_selected", "is_hold": false, "is_recommendation": true, "status": "pareto",
     "metrics": {"net_margin": 18400.0, "sulfur_giveaway": 0.4, "bed_temperature": 374.5},
     "delta_u": {"HT_FEED_SP": 2.1}, "veto_reasons": [], "dominated_by": []},
    {"candidate_id": "cand_aggressive", "is_hold": false, "is_recommendation": false, "status": "vetoed",
     "metrics": {"net_margin": 31000.0, "sulfur_giveaway": 0.0, "bed_temperature": 379.0},
     "delta_u": {"HT_FEED_SP": 6.0}, "veto_reasons": ["Прогноз серы Ŝ+2σ > 10 ppm (Агент Качества)"], "dominated_by": []}
  ],
  "ideal": {"net_margin": 31000.0, "sulfur_giveaway": 0.0, "bed_temperature": 372.0},
  "nadir": {"net_margin": 0.0, "sulfur_giveaway": 1.2, "bed_temperature": 379.0},
  "hold_id": "hold",
  "selected_id": "cand_selected"
}
```

```json
// xai.json
{
  "cycle_id": "cycle_demo_1",
  "events": [
    {"round": 1, "kind": "PROPOSED", "actor": "optimization", "candidate": "cand_selected", "detail": "Предложен ход +2.1 т/ч по сырью ГО"},
    {"round": 1, "kind": "CERTIFIED", "actor": "reliability", "candidate": "cand_selected", "detail": "T0–T2 пройдены с запасом"},
    {"round": 1, "kind": "CONVERGED", "actor": "arbitration", "candidate": "cand_selected", "detail": "Переговоры сошлись"}
  ]
}
```

Добавить обе фикстуры также в `tests/fixtures/console/` по аналогии с уже существующими (`advisory.json` и т.д.), если там есть зеркальная копия для тестов — свериться с `tests/console/test_contract_fixtures.py`.

---

## 11. Тесты

Не ломать существующие 48 тестов в `tests/console/`. Дополнительно:

1. `tests/console/test_pareto_xai.py` (новый файл):
   - `build_pareto(session)` возвращает `None` до первого такта (`session.last_graph_result is None`).
   - После `session.tick_single()` (или через фикстуру сценария S1/S2) `build_pareto(session)` возвращает `ParetoFrontDTO` с непустым `points`, и хотя бы одна точка имеет `status == "pareto"`.
   - `build_xai(session)` аналогично: `None` до первого такта, затем `XaiInfo` с непустым `events` (если `negotiation_log` в графе не пуст на этом такте).
   - Оба DTO проходят `.model_dump_json()` без ошибок (frozen/extra=forbid не блокирует сериализацию с реальными данными графа — если блокирует, значит в `ParetoPoint.metrics`/`delta_u` встретился неожиданный тип, это баг, который нужно поймать здесь, а не в проде).
2. `tests/console/test_api_endpoints.py` — добавить кейсы:
   - `GET /api/console/pareto?session_id=...` → 200, тело валидно по `ParetoFrontDTO` либо `null`.
   - `GET /api/console/xai?session_id=...` → 200, тело валидно по `XaiInfo` либо `null`.
3. `tests/console/test_contract_fixtures.py` — расширить, чтобы `pareto.json`/`xai.json` (§10) валидировались через `ParetoFrontDTO.model_validate`/`XaiInfo.model_validate`.
4. `tests/console/test_no_hardcoded_limits.py` — новый код не добавляет захардкоженных лимитов (все значения идут из `src/agents/pareto.py`, который уже читает лимиты из `src/agents/limits.py`/registry) — прогнать тест как есть, ничего специально добавлять не нужно, только убедиться, что он остаётся зелёным.
5. Frontend: ручная проверка через браузер по каждому сценарию Definition of Done (§12) — автотестов на JS в проекте нет, не заводить новый фреймворк ради этого тикета.

---

## 12. Definition of Done

- [ ] `pytest tests/console -q` — 100% зелёный (существующие 48 + новые из §11).
- [ ] Полная регрессия (`pytest -q`, как указано в предыдущих волнах STATUS.md) не сломана.
- [ ] Открыть `/console/?fixture=advisory` — видно 5 вкладок, «Обзор» по умолчанию активен, скролл работает при уменьшении окна, никаких синих `<input>` не осталось.
- [ ] Панель «Настроить вид»: снять галочку с любого CV/MV — график/карточка пропадает; перезагрузить страницу — выбор сохранился (localStorage).
- [ ] Вкладка «Ручное управление»: изменить все 5 MV сразу, увидеть обновлённый прогноз (дебаунс ~250 мс), корридорное нарушение показано нейтрально (не красным), кнопка «Применить к боевой установке» по-прежнему требует прохождения ядра/коридора и двухэтапного подтверждения.
- [ ] Вкладка «Парето-анализ» (сценарий S1 или S2, `?demo=1`, дать пройти ≥1 такт): видно ≥2 точки фронта, смена осей через селектор работает без перезапроса, клик по точке переносит на вкладку «Ручное управление» с заполненными полями и уже готовым прогнозом.
- [ ] Вкладка «Агенты и XAI»: после такта показывает полный список событий по раундам (не «не более 3»), значения `kind` совпадают с `NegotiationEvent.kind` из бэкенда.
- [ ] Панель «Симулятор» в основном UI (без `?demo=1`): пауза/2с/5с/10с/30с переключают скорость, «Следующий такт вручную» продвигает такт немедленно в любом состоянии.
- [ ] Демо-сценарии из v1 всё ещё проходят: S2 (Совет + правка) → S1 (Автомат) → S3d (сбой LIMS → автовыход в safe-hold) — теперь на вкладке «Обзор».
- [ ] Обновить `STATUS.md` (добавить волну по этому редизайну) и `agents/ASSUMPTIONS.md`, если появятся новые допущения (например, выбор осей Парето по умолчанию).

---

## 13. Краткий чек-лист файлов

| Файл | Что сделать |
|---|---|
| `src/console/contracts.py` | ✅ готово (§2.1) |
| `src/console/service.py` | ✅ готово (§2.2) |
| `src/console/api.py` | ✅ готово (§2.3) |
| `static/console/index.html` | Убрать letterbox-обёртку, добавить `tab-bar` + 5 `tab-panel`, перенести текущий `console-body` в панель `overview`, добавить разметку вкладок manual/xai/pareto/shift, заменить `.change-input` на `.value-stepper` |
| `static/console/styles.css` | Убрать `#viewport-wrapper`/scale/`overflow:hidden`, стили `tab-bar`/`tab-panel`, `.value-stepper`, `.sandbox-info-box`, стили панели виджетов и панели «Симулятор» |
| `static/console/app.js` | Роутинг вкладок, `WIDGET_DEFS`+localStorage, вкладка «Ручное управление» (§5), загрузка XAI/Pareto по активации вкладки, панель «Симулятор» без `?demo=1`, (опционально) фикс `u_current` бага §2.4 |
| `static/console/charts.js` | Добавить `renderParetoChart(...)` на ECharts, не трогая существующие SVG-рендереры |
| `static/console/api.js` | Добавить `getPareto()`, `getXai()` |
| `static/console/fixtures/pareto.json`, `xai.json` | Новые файлы (§10) |
| `tests/console/test_pareto_xai.py` | Новый файл (§11.1) |
| `tests/console/test_api_endpoints.py`, `test_contract_fixtures.py` | Расширить (§11.2–3) |
| `agents/console_tz/STATUS.md` | Добавить запись о волне редизайна после завершения |
