# R5. Фронтенд: графики

Ты рисуешь все графики пульта на Apache ECharts: 2 крупных CV (сера, вспышка), компактный ΔP8 и 5 мини-графиков MV.
Прочитай `README.md` (D7–D9), `01_CONTRACT.md` (`CvSeries`, `MvSeries`, `Trajectory`, `Band`, `Risk`), токены цвета в `ROLE_4_frontend_shell.md §1`.
Работаешь от фикстур `static/console/fixtures/*.json`.

## Файлы
`static/console/charts.js`, `static/console/vendor/echarts.min.js` (скачай релиз **echarts 5.x** `dist/echarts.min.js` с
`cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js` и положи в репозиторий — на площадке может не быть интернета).
Для отладки в одиночку сделай `static/console/charts-demo.html` (рисует все графики из фикстуры) — его можно удалить в волне 2.

## API модуля (контракт с R4 — не менять без R0)
```js
export function mountCharts(rootEls) // { sulfur: HTMLElement, flash: HTMLElement, dp: HTMLElement, mv: HTMLElement[5] } → handle
export function renderCharts(handle, view)
// view = {
//   state: ConsoleState,
//   active: Trajectory|null,        // активная траектория (выбирает R4)
//   activeColor: "rec"|"edit",
//   recThin: Trajectory|null,       // рекомендация тонким контуром, когда активна правка
//   historyHours: 1|8|12
// }
export function resizeCharts(handle)
```
`renderCharts` вызывается часто (каждый опрос и каждый preview) — используй `chart.setOption(opt, {notMerge:false, lazyUpdate:true})`,
без пересоздания инстансов и без анимации (`animation: false`: проектор + частые обновления = мигание).

## Общая ось времени
- Ось X — `type: "time"`, от `now − historyHours` до `now + 4 ч`. Метки каждые 2 ч (при 1 ч — каждые 15 мин), формат `HH:MM`; метка «СЕЙЧАС HH:MM» жирная.
- Зона прогноза (справа от now) — `markArea` фон `#FFFFFF` с opacity 0.55.
- Линия now — `markLine` вертикальная, пунктир `--ink`.
- **Синхронный курсор**: `echarts.connect([...все инстансы])` + `axisPointer` по X на всех графиках. Tooltip один на график, в нём
  время, факт, «без изменений», активная, P10–P90, единицы. Числа — с `decimals` MV / 1 знак для CV.

## CV-график (sulfur, flash) — слои снизу вверх
1. Сетка Y: 4 линии, подписи слева mono 13px `--ink-3`. Диапазон — `series.y_range` (не автомасштаб — иначе ось «прыгает»).
2. Красная заливка риска: если у активной траектории `risk.first_breach_at` — `markArea` от этой точки до конца, `--limit` opacity 0.12.
3. Заштрихованные пропуски данных: точки `quality ∈ {MISSING, BAD}` в истории → `markArea` со штриховкой (`decal` или
   `itemStyle.color` pattern), подпись «нет сигнала ПАК» (или «данные недостоверны» для BAD).
4. Лимит: `markLine` горизонтальная `--limit` 2px, подпись `limit.label` слева над линией (для min-лимита — под линией).
5. Полоса P10–P90 активной траектории: две line-серии со `stack` (нижняя прозрачная + разница с `areaStyle` цвета активной, opacity 0.18).
   Если `p10/p90 == null` — полосу не рисовать, в углу графика мелко «σ неизвестна».
6. «Без изменений» — `state.hold`, `--hold` 2px, `lineStyle.type: [6,5]`.
7. `recThin` — `--rec` 1.5px (только когда активна правка).
8. Активная — 3px, `--rec` или `--edit`.
9. Факт — `history`, `--fact` 2.5px, `connectNulls: false` (разрывы видны).
10. Точка «сейчас» на последнем факте, r=5, `--ink`.
11. Подписи концов прогноза справа от области построения (mono 15px, цвет серии): значения на +4 ч для hold и активной;
    раздвинуть по Y, если ближе 16px.
Отказ без прогноза (`recommendation.status == REFUSAL_DATA` и активной нет): в зоне прогноза по центру текст
«Прогноз не строится» (16px, 600, `--warn-ink`) + вторая строка «нет достоверных данных по {cv}».

## ΔP8 компактный
Высота 56px, без осей и подписей; факт + активная траектория, лимит T1 линией. Слева/справа значения рисует R4 (из state).

## MV мини-графики (5 шт., 224×84)
- Ось X та же по времени, но без подписей; Y — диапазон вокруг значений (min/max истории и плана ± 10 %, но включая линии `lines`, если они в пределах ±25 % от диапазона).
- `sp_history` — ступенчатая линия (`step: "end"`), `--ink` 2px; `pv_history` — `--hold` 1.2px.
- План: `active.mv_plan[sp]` — ступенька цвета активной, 2.5px; если `frozen` — `--hold` пунктир.
- `recThin.mv_plan[sp]` — `--rec` 1.3px.
- Коридор: прямоугольник `markArea` на первом такте прогноза по Y `[sp_now − max_step, sp_now + max_step]` (обрезать `lo/hi`),
  `--rec` opacity 0.16. Нет `max_step_per_tick` или `frozen` — не рисовать.
- `lines`: T0/T1 — `--limit` 1.5px пунктир `[4,3]`; `warn` — `#A86A0A` пунктир `[2,3]`.
- Тултип отключён (мелко), но курсор синхронизирован.

## Производительность
- Полная перерисовка всех 8 графиков < 30 мс (Chrome, ноутбук). Измерь `performance.now()` и выведи в `console.debug` только при `?debug=1`.
- `resize` по `ResizeObserver` на контейнерах; на масштабирование через `transform` (R4) не реагировать.

## Приёмка
- `charts-demo.html?fixture=advisory` — оранжевая траектория, тонкий синий контур, серый пунктир, красный участок риска, полоса.
- `?fixture=auto` — синяя траектория «план», ступеньки плана на MV F9, коридор на MV.
- `?fixture=refusal` — штриховка «нет сигнала ПАК» на сере, текст «Прогноз не строится», MV-планы серые пунктиром.
- Переключение 1/8/12 ч меняет только ось, без пересоздания графиков; курсор синхронен на всех 8 графиках.
