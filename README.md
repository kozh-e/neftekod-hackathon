# 🛢️ Нефтекод: Автономный комплекс MES/APC (Евро-5)

Мультиагентная система замкнутого контура для оптимизации производства дизельного топлива стандарта ГОСТ 32511-2013 (Евро-5) в технологической цепочке:
**ЭЛОУ-АВТ-6 $\to$ Гидроочистка 24-2000 $\to$ Поточный инлайн-блендинг**.

---

## 🚀 Быстрый запуск в Docker (One-Click Launch)

Для запуска всего приложения достаточно запустить Docker:

```bash
# Вариант 1: через скрипт быстрого запуска
./start.sh

# Вариант 2: стандартная команда docker compose
docker compose up --build
```

После запуска сервисы доступны по адресам:
- 🛢️ **Консоль диспетчера (Streamlit HITL)**: [http://localhost:8501](http://localhost:8501)
- 🚀 **REST API (FastAPI MES/APC)**: [http://localhost:8000](http://localhost:8000)
- 📖 **Интерактивная документация API (Swagger UI)**: [http://localhost:8000/docs](http://localhost:8000/docs)
- 🩺 **Проверка здоровья сервиса (Healthcheck)**: [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)

### 🧪 Запуск тестов в Docker:
```bash
docker compose run --rm tests
```

---

## 💻 Локальный запуск без Docker

Если требуется запустить проект локально в виртуальном окружении Python:

```bash
# Активация окружения и установка зависимостей
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Запуск REST API сервиса (порт 8000)
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Запуск веб-консоли диспетчера (порт 8501)
streamlit run streamlit_app.py
# (или streamlit run src/ui/app.py)

# Запуск всех 104 модульных и интеграционных тестов
pytest -v tests/

# Запуск реплея 4 технологических сценариев (Норма, Риск качества, Отказ КИП, Конфликт ПАЗ)
python scripts/replay_scenarios.py

# Офлайн-калибровка параметров цифрового двойника на архивах телеметрии
python scripts/calibrate_twin.py
```

---

## 📁 Структура проекта

```text
├── Dockerfile          # Сборка контейнера на python:3.12-slim
├── docker-compose.yml  # Оркестрация сервисов API, UI и тестов
├── .dockerignore       # Оптимизация контекста сборки Docker
├── start.sh            # Скрипт запуска в один клик
├── requirements.txt    # Зависимости проекта
├── main.py             # FastAPI микросервис (/api/v1/optimize с поддержкой economics)
├── streamlit_app.py    # Точка входа веб-консоли диспетчера
├── config/             # Зафиксированные конфигурации (config/twin_params.json)
├── initial_data/       # Исходные технологические регламенты и выгрузки архивов
├── new_data/           # Официальные реестры тегов и 17 моделей формул ВАК
├── artifacts/          # Системный дизайн, дорожная карта,whats_done.md
├── agents/             # Системные промты, DOMAIN_KNOWLEDGE.md, ASSUMPTIONS.md
├── scripts/            # Скрипты калибровки (calibrate_twin.py) и реплея сценариев (replay_scenarios.py)
├── data/               # Калибровочные отчеты и аудит-журналы решений (decisions.jsonl)
├── src/                # Исходный код системы
│   ├── twin/           # Физический цифровой двойник (FeedLink, Kinetics, Stabilizer, Product, FullChainTwin)
│   ├── agents/         # Мультиагентный граф (Optimization, Safety, Quality, Arbitration, Tank Blending)
│   ├── xai/            # Генератор диспетчерских отчетов XAI на русском языке
│   └── ui/             # Диспетчерская консоль оператора (Streamlit с блоком управления экономикой)
└── tests/              # Набор из 104 тестов (unit, e2e, rollout, arbitration, blending, VAK, latency p95)
```
