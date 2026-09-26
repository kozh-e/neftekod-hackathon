#!/usr/bin/env bash
set -e

echo "================================================================="
echo "🛢️  НЕФТЕКОД: Автономный комплекс MES/APC (Евро-5)"
echo "   Запуск мультиагентной системы и консоли диспетчера в Docker"
echo "================================================================="

# Проверка доступности Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Ошибка: Docker не установлен или не найден в PATH."
    exit 1
fi

if ! docker info &> /dev/null; then
    echo "⚠️  Внимание: Docker daemon не запущен. Пожалуйста, запустите Docker Desktop / Daemon."
    exit 1
fi

echo "🚀 Сборка образов и запуск сервисов в Docker Compose..."
echo ""
echo "Сервисы будут доступны по адресам:"
echo "  • 🛢️ Диспетчерская консоль (Streamlit): http://localhost:8501"
echo "  • 🚀 Промышленный REST API (FastAPI):  http://localhost:8000"
echo "  • 📖 Интерактивная документация (API):  http://localhost:8000/docs"
echo "  • 🩺 Healthcheck API:                   http://localhost:8000/api/v1/health"
echo ""
echo "💡 Для запуска тестов в контейнере выполните:"
echo "   docker compose run --rm tests"
echo ""
echo "-----------------------------------------------------------------"

# Запуск docker compose
docker compose up --build
