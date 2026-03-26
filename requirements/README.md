# Зависимости по ОС (разработка из исходников)

Версии совпадают с [`pyproject.toml`](../pyproject.toml). Для установки **пакета проекта** после зависимостей выполните из корня репозитория:

```bash
pip install -e .
```

Скрипты точек входа: `tg-ws-proxy`, `tg-ws-proxy-tray-win` / `tg-ws-proxy-tray-linux` / `tg-ws-proxy-tray-macos`.

---

## Windows

| Файл | Условие |
|------|---------|
| [`windows-py39plus.txt`](windows-py39plus.txt) | Python **3.9+** (рекомендуется) |
| [`windows-py38.txt`](windows-py38.txt) | Python **3.8** |

**Системно:** для сборки/запуска из исходников — установленный [Python](https://www.python.org/downloads/) с опцией *tcl/tk* (обычно включена). Отдельный Tcl/Tk не требуется.

```powershell
pip install -r requirements\windows-py39plus.txt
pip install -e .
tg-ws-proxy-tray-win
```

---

## Linux

Файл: [`linux.txt`](linux.txt) (Python **3.9+**).

**Системно:** модуль `tkinter` (нужен CustomTkinter для трея):

- Debian/Ubuntu: `sudo apt install python3-tk`
- Fedora: `sudo dnf install python3-tkinter`
- Arch: `sudo pacman -S tk`

Также нужны заголовки Python при сборке расширений (если pip собирает колёса): `python3-dev` / `gcc` — по сообщениям pip.

```bash
pip install -r requirements/linux.txt
pip install -e .
tg-ws-proxy-tray-linux
```

---

## macOS

Файл: [`macos.txt`](macos.txt).

**Системно:** Python с официального установщика или Homebrew; Tcl/Tk обычно уже в комплекте. Для графики трея используется **rumps** (нативная строка меню), не CustomTkinter.

```bash
pip install -r requirements/macos.txt
pip install -e .
tg-ws-proxy-tray-macos
```

---

## Примечание

Готовые бинарники для пользователей — на [странице релизов](https://github.com/Flowseal/tg-ws-proxy/releases); отдельная установка Python и `pip` для них не требуется.
