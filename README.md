/initial_data - начальные данные
/artifacts - отчеты полученные на основе начальных данных
/notebooks - jupyter ноутбуки для анализа

/data - обработанные данные
/data/processed - очищенные датасеты

/src - исходный кодд
/src/twin - цифровой двойник системы
/src/agents
/src/xai - explainable ai
/src/ui

/tests

Запуск приложения: .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
Запуск веб-интерфейса приложения: .venv/bin/streamlit run src/ui/app.py

TODO:
