# R0. Лид-интегратор

Ты — лид-интегратор пульта оператора. Работаешь в волнах 0, 2 и 3. Прочитай `README.md` и `01_CONTRACT.md` полностью.

## Волна 0 — фундамент (до старта остальных ролей)

### 0.1 Пакет и контракт
- Создай `src/console/__init__.py` (docstring на русском: назначение пакета).
- Создай `src/console/contracts.py`: все схемы из `01_CONTRACT.md` §2 как pydantic v2 модели.
  - Базовый класс — как `Frozen` в `src/agents/contracts.py` (frozen, extra="forbid").
  - Перечисления — `Literal[...]` или `StrEnum`; `DecisionStatus` импортируй из `src/agents/contracts.py`, не дублируй.
  - `SP = Literal["HT_FEED_SP","HT_TIN_SP","HT_P_SP","HT_GOR_SP","AVT_T55_SP"]`.
  - `ConsoleErrorDetail {code, text, checks}` для 409.

### 0.2 Заглушки модулей (сигнатуры — закон для R1–R3)
Создай файлы с сигнатурами, docstring и `raise NotImplementedError`:

```python
# src/console/runtime.py  (владелец R3)
class ConsoleSession:            # состояние одной сессии: PlantSimulator, история, режим, лента, последний graph_result
    session_id: str
    ...
class SessionRegistry:
    def get(self, session_id: str) -> ConsoleSession: ...
    def load_scenario(self, session_id: str, scenario: str, warmup_ticks: int = 48) -> ConsoleSession: ...
    def tick(self, session_id: str, n: int = 1) -> ConsoleSession: ...
    def set_speed(self, session_id: str, seconds_per_tick: float) -> None: ...
    def set_fault(self, session_id: str, tag: str, fault_type: str, lims_age_hours: float | None = None) -> None: ...
REGISTRY: SessionRegistry

# src/console/auto.py  (владелец R3)
def check_auto_exit(session: "ConsoleSession") -> tuple[AutoExitCode, str] | None: ...
def auto_step(session: "ConsoleSession") -> list[FeedItem]: ...
def set_mode(session: "ConsoleSession", mode: str) -> ModeInfo: ...

# src/console/forecast.py  (владелец R2)
def build_trajectory(session: "ConsoleSession", u_target: dict[str, float], kind: str) -> Trajectory: ...
def preview(session: "ConsoleSession", req: PreviewRequest) -> PreviewResult: ...
def effect_of(traj: Trajectory, hold: Trajectory, session: "ConsoleSession") -> Effect: ...

# src/console/corridor.py  (владелец R2)
def corridor_for(sp: str) -> Corridor: ...
def check_step(u_current: dict[str, float], u_target: dict[str, float]) -> list[dict]: ...

# src/console/service.py  (владелец R1)
def build_state(session: "ConsoleSession") -> ConsoleState: ...
def commit(session: "ConsoleSession", req: CommitRequest) -> CommitResult: ...

# src/console/feed.py  (владелец R1)
def feed_from_graph_result(graph_result: dict, t: datetime) -> list[FeedItem]: ...
```

### 0.3 Фикстуры (для фронта и тестов)
Сгенерируй 3 JSON, валидные по `ConsoleState` (проверь `ConsoleState.model_validate_json`):
- `tests/fixtures/console/advisory.json` — сценарий S2, режим ADVISORY, рекомендация `SUCCESS` с изменениями `HT_TIN_SP` и `HT_GOR_SP`,
  3 альтернативы, `hold` уходит выше 10 ppm, лента 5 записей (включая `shield`).
- `tests/fixtures/console/auto.json` — S1, режим AUTO, `auto.next_step` не null, `plan` из 2 шагов, лента с `ok`, `shield`, `veto`.
- `tests/fixtures/console/refusal.json` — S3d, режим ADVISORY, `mode.last_auto_exit.reason_code="REFUSAL_DATA"`, `banner` не null,
  `recommendation.status="REFUSAL_DATA"`, `changes=[]`, `refusal` заполнен, в `series.cv[0].history` последние 7 точек `v=null, quality="MISSING"`.
- Числа в фикстурах — правдоподобные для номинала (`src/twin/tags.py: NOMINAL_OPERATING_POINT`) и лимитов реестра; пометь в
  каждом файле поле `"_fixture": true` (добавь его в модель как `Optional`, по умолчанию `None`).
- Скопируй их в `static/console/fixtures/` (фронт читает оттуда).
- Также `tests/fixtures/console/preview_ok.json` и `preview_blocked.json` (`PreviewResult`, второй — `can_commit=false`, нарушение коридора T6).

Лучший способ получить правдоподобные фикстуры — **временный скрипт** `scripts/make_console_fixtures.py`, который прогоняет
`PlantSimulator` + `get_graph()` (как в `scripts/replay_scenarios.py`) и сериализует. Если R1–R3 ещё не готовы — собирай руками,
но форма должна строго соответствовать контракту.

### 0.4 Подключение к FastAPI
В `main.py` добавь **только**:
```python
from fastapi.staticfiles import StaticFiles
from src.console.api import router as console_router
app.include_router(console_router, prefix="/api/console")
app.mount("/console", StaticFiles(directory="static/console", html=True), name="console")
```
(в `src/console/api.py` создай пустой `router = APIRouter()` — дальше им владеет R1).

### 0.5 Служебные файлы
Создай пустые `agents/console_tz/STATUS.md`, `REQUESTS.md`, `QUESTIONS.md` с заголовками.
В `agents/ASSUMPTIONS.md` добавь раздел `## Console (пульт оператора)` — туда роли пишут свои допущения.

**Приёмка волны 0:** `python -c "import src.console.contracts"` ок; 5 фикстур валидны; `pytest -q` не сломан;
`uvicorn main:app` поднимается, `GET /console/` отдаёт 404/пустую страницу без исключения.

## Волна 2 — интеграция
1. Собери `REQUESTS.md`, выполни или распредели запросы.
2. Прогони `pytest -q` и `pytest -q tests/console`. Красное — верни владельцу роли с точным выводом.
3. Запусти сервер, открой пульт без `?fixture`, прогони `POST /api/console/demo/load {"scenario":"S2"}` и сверь с чек-листом `README.md §7`.
4. Замени ручные фикстуры на сгенерированные реальным пайплайном (скрипт из 0.3), если они расходятся.

## Волна 3 — репетиция демо
Сценарий ведущего (всё через панель `?demo=1`):
1. `S2`, 1 такт/10 с. Показать серый пунктир «без изменений» → рекомендация → изменить T6 на меньшее значение → оранжевая
   траектория и бейдж риска → «Вернуть рекомендацию» → «Отправить» → через несколько тактов сера идёт вниз.
2. `S1` → «Включить автомат» → 3–4 шага с обратным отсчётом; «Пропустить шаг» один раз; лента заполняется.
3. Не выходя из автомата: `demo/fault {tag:"HT_Q21", fault_type:"nan", lims_age_hours:26}` → автовыход, баннер, жёлтый блок.
4. «Сдать смену» → отчёт.
Запиши тайминг каждого акта в `STATUS.md`. Цель — ≤ 5 минут суммарно.
