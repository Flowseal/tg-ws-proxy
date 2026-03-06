#!/bin/bash
# Установка TG WS Proxy в Fedora (GNOME)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="tg-ws-proxy"
APP_DIR="$HOME/.local/share/$APP_NAME"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor"

echo "🔧 Установка TG WS Proxy..."

# Создаём директории
mkdir -p "$APP_DIR"
mkdir -p "$BIN_DIR"
mkdir -p "$DESKTOP_DIR"
mkdir -p "$ICON_DIR/scalable/apps"
mkdir -p "$ICON_DIR/512x512/apps"
mkdir -p "$ICON_DIR/256x256/apps"
mkdir -p "$ICON_DIR/128x128/apps"
mkdir -p "$ICON_DIR/64x64/apps"
mkdir -p "$ICON_DIR/32x32/apps"

# Копируем файлы приложения
echo "📁 Копирование файлов..."
cp "$SCRIPT_DIR/tg_ws_proxy.py" "$APP_DIR/"
cp "$SCRIPT_DIR/tg_ws_tray.py" "$APP_DIR/"
cp "$SCRIPT_DIR/icon.ico" "$APP_DIR/" 2>/dev/null || true

# Создаём скрипт запуска
echo "📝 Создание скрипта запуска..."
cat > "$BIN_DIR/tg-ws-proxy" << 'EOF'
#!/bin/bash
exec python3 "$HOME/.local/share/tg-ws-proxy/tg_ws_tray.py" "$@"
EOF
chmod +x "$BIN_DIR/tg-ws-proxy"

# Создаём .desktop файл
echo "🖥️ Создание .desktop файла..."
cat > "$DESKTOP_DIR/tg-ws-proxy.desktop" << EOF
[Desktop Entry]
Version=1.0
Name=TG WS Proxy
Name[ru]=TG WS Proxy
Comment=Telegram WebSocket Proxy for Desktop
Comment[ru]=Telegram WebSocket Proxy для рабочего стола
Exec=$BIN_DIR/tg-ws-proxy
Icon=tg-ws-proxy
Terminal=false
Type=Application
Categories=Network;Proxy;
Keywords=telegram;proxy;socks;
StartupNotify=false
EOF

# Создаём иконки из icon.ico или генерируем SVG
echo "🎨 Установка иконок..."

# Создаём SVG иконку (универсальная)
cat > "$ICON_DIR/scalable/apps/tg-ws-proxy.svg" << 'EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <circle cx="64" cy="64" r="60" fill="#3390ec"/>
  <text x="64" y="88" font-family="Arial, sans-serif" font-size="72" font-weight="bold" fill="white" text-anchor="middle">T</text>
</svg>
EOF

# Генерируем PNG иконки из SVG с помощью convert (если есть) или создаём заглушки
if command -v convert &> /dev/null; then
    for size in 512 256 128 64 32; do
        convert -background none \
            "$ICON_DIR/scalable/apps/tg-ws-proxy.svg" \
            -resize "${size}x${size}" \
            "$ICON_DIR/${size}x${size}/apps/tg-ws-proxy.png" 2>/dev/null || true
    done
fi

# Если PNG не создались, создадим символические ссылки на SVG
for size in 512 256 128 64 32; do
    if [ ! -f "$ICON_DIR/${size}x${size}/apps/tg-ws-proxy.png" ]; then
        ln -sf ../../scalable/apps/tg-ws-proxy.svg \
            "$ICON_DIR/${size}x${size}/apps/tg-ws-proxy.png" 2>/dev/null || true
    fi
done

# Обновляем кэш иконок
if command -v gtk-update-icon-cache &> /dev/null; then
    gtk-update-icon-cache -f "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

# Обновляем базу данных desktop файлов
if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

# Устанавливаем зависимости Python
echo "📦 Установка Python зависимостей..."
if command -v pip3 &> /dev/null; then
    pip3 install --user -r "$SCRIPT_DIR/requirements.txt"
elif command -v pip &> /dev/null; then
    pip install --user -r "$SCRIPT_DIR/requirements.txt"
else
    echo "⚠️ pip не найден. Установите зависимости вручную:"
    echo "   pip install -r requirements.txt"
fi

echo ""
echo "✅ Установка завершена!"
echo ""
echo "📍 Приложение установлено в: $APP_DIR"
echo "🚀 Запустить можно через меню приложений или командой: tg-ws-proxy"
echo ""
echo "⚠️ Для работы в GNOME может потребзоваться AppIndicator:"
echo "   sudo dnf install libappindicator-gtk3"
echo ""
