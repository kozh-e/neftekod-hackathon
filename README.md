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

# Запуск всех 42 модульных тестов
pytest -v tests/
```

---

## 📁 Структура проекта

```text
├── Dockerfile          # Сборка контейнера на python:3.12-slim
├── docker-compose.yml  # Оркестрация сервисов API, UI и тестов
├── .dockerignore       # Оптимизация контекста сборки Docker
├── start.sh            # Скрипт запуска в один клик
├── requirements.txt    # Зависимости проекта
├── main.py             # FastAPI микросервис
├── streamlit_app.py    # Точка входа веб-консоли диспетчера
├── initial_data/       # Начальные технологические данные и схемы
├── artifacts/          # Системный дизайн, дорожная карта, ТЗ
├── notebooks/          # Исследовательские ноутбуки
├── data/               # Данные и датасеты
├── src/                # Исходный код системы
│   ├── twin/           # Физический цифровой двойник (FOPDT, ВАК)
│   ├── agents/         # Мультиагентный граф (Safety, Quality, Arbitration, LIMS)
│   ├── xai/            # Генератор диспетчерских отчетов XAI
│   └── ui/             # Диспетчерская консоль оператора (Streamlit)
└── tests/              # Набор из 42 модульных тестов
```
