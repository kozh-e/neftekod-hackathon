### Структура проекта

```text
├── initial_data/       # начальные данные
├── artifacts/          # отчеты, полученные на основе начальных данных
├── notebooks/          # jupyter ноутбуки для анализа
├── data/               # обработанные данные
│   └── processed/      # очищенные датасеты
├── src/                # исходный код
│   ├── twin/           # цифровой двойник системы
│   ├── agents/         # мультиагентная система
│   ├── xai/            # explainable AI
│   └── ui/             # пользовательский интерфейс
└── tests/              # тесты
```

Запуск приложения: .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
Запуск веб-интерфейса приложения: .venv/bin/streamlit run src/ui/app.py

TODO:
