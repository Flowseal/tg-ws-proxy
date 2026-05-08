# preview script: shows 3 tray icons simultaneously (idle / active / stopped)
import threading
import sys
from pathlib import Path
from PIL import Image
import pystray

sys.path.insert(0, str(Path(__file__).parent))

from utils.tray_common import load_icon, add_status_dot

STATUSES = [
    ("idle",  "TG WS Proxy — Ожидание"),
    ("ok",    "TG WS Proxy — Активно"),
    ("error", "TG WS Proxy — Остановлен"),
]

icons: list[pystray.Icon] = []


def _run_icon(status: str, title: str) -> None:
    base = load_icon()
    img = add_status_dot(base, status)
    icon = pystray.Icon(f"preview_{status}", img, title)
    icons.append(icon)
    icon.run()


threads = []
for st, tt in STATUSES:
    t = threading.Thread(target=_run_icon, args=(st, tt), daemon=True)
    t.start()
    threads.append(t)

print("Three tray icons running. Press Enter to quit.")
input()
for ic in icons:
    try:
        ic.stop()
    except Exception:
        pass
