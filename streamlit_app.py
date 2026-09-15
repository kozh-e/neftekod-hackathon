"""Точка входа Streamlit приложения 'Нефтекод' (Диспетчерская консоль).

Позволяет запускать веб-интерфейс как через:
    streamlit run streamlit_app.py
так и через:
    streamlit run src/ui/app.py
"""

from __future__ import annotations

from pathlib import Path
import runpy
import sys

# Обеспечиваем доступность корня проекта в sys.path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Запуск консоли оператора
runpy.run_path(str(ROOT_DIR / "src" / "ui" / "app.py"), run_name="__main__")
