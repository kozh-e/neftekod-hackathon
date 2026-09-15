FROM python:3.12-slim

# Установка рабочей директории
WORKDIR /app

# Системные зависимости (curl для docker healthcheck, build-essential при сборке зависимостей)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Настройка переменных окружения Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Установка зависимостей Python (кэширование слоя)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода приложения
COPY . .

# Открытие портов: 8000 (FastAPI REST API) и 8501 (Streamlit Консоль диспетчера)
EXPOSE 8000 8501

# Команда по умолчанию
CMD ["streamlit", "run", "streamlit_app.py", "--server.port", "8501", "--server.address", "0.0.0.0", "--server.headless", "true"]
