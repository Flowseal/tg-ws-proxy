#!/bin/bash
# Удаление TG WS Proxy из Fedora

set -e

APP_NAME="tg-ws-proxy"
APP_DIR="$HOME/.local/share/$APP_NAME"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor"

echo "🗑️ Удаление TG WS Proxy..."

# Удаляем файлы приложения
rm -rf "$APP_DIR"

# Удаляем скрипт запуска
rm -f "$BIN_DIR/tg-ws-proxy"

# Удаляем .desktop файл
rm -f "$DESKTOP_DIR/tg-ws-proxy.desktop"

# Удаляем иконки
rm -f "$ICON_DIR/scalable/apps/tg-ws-proxy.svg"
for size in 512 256 128 64 32; do
    rm -f "$ICON_DIR/${size}x${size}/apps/tg-ws-proxy.png"
done

# Обновляем кэш иконок
if command -v gtk-update-icon-cache &> /dev/null; then
    gtk-update-icon-cache -f "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

# Обновляем базу данных desktop файлов
if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

# Удаляем конфиг и логи (опционально)
if [ -d "$HOME/.config/tgwsproxy" ]; then
    read -p "📁 Удалить конфиг и логи? (~/.config/tgwsproxy) [y/N]: " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -rf "$HOME/.config/tgwsproxy"
        echo "🗑️ Конфиг и логи удалены"
    fi
fi

echo ""
echo "✅ TG WS Proxy удалён!"
