"""Вспомогательные утилиты (проверка релизов и т.п.)."""

from utils.update_check import RELEASES_PAGE_URL, get_status, run_check

__all__ = ["RELEASES_PAGE_URL", "get_status", "run_check"]
