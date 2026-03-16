#!/bin/bash
# Установка TG WS Proxy как Linux-приложения
# Устанавливает в ~/.local/share/tg-ws-proxy и добавляет в меню приложений

set -e

INSTALL_DIR="${HOME}/.local/share/tg-ws-proxy"
BIN_DIR="${HOME}/.local/bin"
APPS_DIR="${HOME}/.local/share/applications"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== TG WS Proxy — установка для Linux ==="
echo "Каталог установки: $INSTALL_DIR"
echo ""

# Системные зависимости
echo "Проверка системных зависимостей..."
for pkg in python3 python3-venv python3-tk; do
    if ! dpkg -l "$pkg" &>/dev/null 2>/dev/null; then
        echo "  Требуется: $pkg"
        echo "  Установите: sudo apt install $pkg"
        MISSING=1
    fi
done
if [ -n "$MISSING" ]; then
    echo ""
    read -p "Продолжить без установки недостающих пакетов? (y/N) " -n 1 -r
    echo
    [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
fi

# Копирование файлов
echo ""
echo "Копирование файлов..."
mkdir -p "$INSTALL_DIR"
cp -r "$ROOT/proxy" "$INSTALL_DIR/"
cp "$ROOT/linux.py" "$INSTALL_DIR/"
cp "$ROOT/requirements.txt" "$INSTALL_DIR/"

# Создание venv
echo "Создание виртуального окружения..."
cd "$INSTALL_DIR"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# Launcher
echo "Создание launcher..."
cat > "$INSTALL_DIR/run.sh" << 'LAUNCHER'
#!/bin/bash
cd "$(dirname "$0")"
exec .venv/bin/python linux.py
LAUNCHER
chmod +x "$INSTALL_DIR/run.sh"

# .desktop файл
echo "Установка .desktop файла..."
mkdir -p "$APPS_DIR"
sed "s|__INSTALL_DIR__|$INSTALL_DIR|g" "$SCRIPT_DIR/tg-ws-proxy.desktop" \
    > "$APPS_DIR/tg-ws-proxy.desktop"

# Опционально: симлинк в ~/.local/bin
mkdir -p "$BIN_DIR"
ln -sf "$INSTALL_DIR/run.sh" "$BIN_DIR/tg-ws-proxy" 2>/dev/null || true

echo ""
echo "=== Установка завершена ==="
echo ""
echo "TG WS Proxy установлен. Запуск:"
echo "  • Из меню приложений: найдите «TG WS Proxy»"
echo "  • Из терминала: $INSTALL_DIR/run.sh"
echo "  • Или: ~/.local/bin/tg-ws-proxy (если ~/.local/bin в PATH)"
echo ""
echo "Конфигурация: ~/.config/TgWsProxy/config.json"
echo ""
