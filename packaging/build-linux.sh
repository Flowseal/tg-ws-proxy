#!/bin/bash
# Сборка TG WS Proxy в standalone-бинарник (PyInstaller)
# Результат: dist/tg-ws-proxy

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

cd "$ROOT"

echo "=== TG WS Proxy — сборка для Linux ==="

# venv
if [ ! -d .venv ]; then
    echo "Создание .venv..."
    python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt
.venv/bin/pip install -q pyinstaller

# Системные зависимости для tray (нужны при сборке и на целевой системе)
echo ""
echo "Для tray-иконки на целевой системе потребуются:"
echo "  sudo apt install libappindicator3-1 gir1.2-appindicator3-0.1 libgtk-3-0"
echo ""

# Сборка
echo "Запуск PyInstaller..."
.venv/bin/pyinstaller packaging/linux.spec --noconfirm

echo ""
echo "=== Сборка завершена ==="
echo "Бинарник: dist/tg-ws-proxy"
echo ""
echo "Запуск: ./dist/tg-ws-proxy"
echo "Или скопируйте в /usr/local/bin для глобальной установки"
echo ""
