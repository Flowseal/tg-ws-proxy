"""
updater.py — автообновление proxy/tg_ws_proxy.py с GitHub main ветки.

Логика:
  1. Скачивает актуальный файл с GitHub raw
  2. Сравнивает SHA-256 с локальной копией
  3. Если отличается — сохраняет новую версию, возвращает True
  4. Вся работа синхронная (вызывается из фонового потока)
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger("tg-ws-updater")

RAW_URL = (
    "https://raw.githubusercontent.com/"
    "Flowseal/tg-ws-proxy/main/proxy/tg_ws_proxy.py"
)

# Локальный путь к файлу ядра (рядом с этим скриптом)
_HERE = Path(__file__).parent
PROXY_CORE = _HERE / "proxy" / "tg_ws_proxy.py"

TIMEOUT = 15  # секунд на скачивание


def _sha256(path: Path) -> Optional[str]:
    """SHA-256 файла или None если файл не существует."""
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch(url: str) -> bytes:
    """Скачать URL, вернуть содержимое как bytes."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "tg-ws-proxy-macos-updater/1.0"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def check_and_update() -> bool:
    """
    Проверить обновление proxy core.

    Возвращает True если файл был обновлён, False если уже актуален
    или произошла ошибка (ошибки логируются, не бросаются).
    """
    log.info("Checking for proxy core update: %s", RAW_URL)
    try:
        new_content = _fetch(RAW_URL)
    except Exception as exc:
        log.warning("Update check failed (network): %s", exc)
        return False

    new_hash = hashlib.sha256(new_content).hexdigest()
    old_hash = _sha256(PROXY_CORE)

    if new_hash == old_hash:
        log.info("Proxy core is up to date (sha256: %s…)", new_hash[:12])
        return False

    log.info(
        "Proxy core update detected: %s… → %s…",
        (old_hash or "none")[:12],
        new_hash[:12],
    )

    # Атомарная замена: пишем во временный файл, потом переименовываем
    tmp = PROXY_CORE.with_suffix(".py.tmp")
    try:
        PROXY_CORE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(new_content)
        shutil.move(str(tmp), str(PROXY_CORE))
    except Exception as exc:
        log.error("Failed to write updated proxy core: %s", exc)
        tmp.unlink(missing_ok=True)
        return False

    # Выгрузить старый модуль из sys.modules, чтобы следующий import
    # подхватил новый файл с диска
    for key in list(sys.modules.keys()):
        if "tg_ws_proxy" in key:
            del sys.modules[key]

    log.info("Proxy core updated successfully")
    return True
