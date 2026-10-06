from __future__ import annotations

import logging
import threading
from copy import deepcopy

from ui.ctk_dialogs import show_message
from ui.settings_form import validate_config_form
from ui.i18n import set_language, t
from ui.settings import prepare_settings

log = logging.getLogger("tg-ws-tray")
LINK_KEYS = frozenset({"host", "port", "secret"})


class SettingsDialog:
    def __init__(
        self, *, ctk, root, widgets, config, defaults, persist,
        refresh_menu, finish, restart, include_autostart=False,
        apply_autostart=None, on_link_changed=None,
    ):
        self.ctk = ctk
        self.root = root
        self.widgets = widgets
        self.config = config
        self.defaults = defaults
        self.persist = persist
        self.refresh_menu = refresh_menu
        self.finish = finish
        self.restart = restart
        self.include_autostart = include_autostart
        self.apply_autostart = apply_autostart
        self.on_link_changed = on_link_changed
        self.closed = False

    def cancel(self):
        if self.closed:
            return
        mode = self.config.get("appearance", "auto")
        self.ctk.set_appearance_mode("system" if mode == "auto" else mode)
        set_language(self.config.get("language", self.defaults["language"]))
        self.refresh_menu()
        self._finish()

    def _finish(self):
        self.closed = True
        self.finish()

    def save(self):
        if self.closed:
            return
        values = validate_config_form(
            self.widgets, self.defaults, include_autostart=self.include_autostart,
        )
        if isinstance(values, str):
            show_message(values, title=t("app.error_title"), kind="error")
            return
        change = prepare_settings(self.config, values, self.defaults)
        if not change.changed_keys:
            self._finish()
            return
        try:
            self.persist(change.config)
        except (OSError, ValueError, TypeError) as exc:
            log.exception("Failed to save settings")
            show_message(str(exc), title=t("app.error_title"), kind="error")
            return
        self.config.update(deepcopy(change.config))
        set_language(change.config["language"])
        log.info("Settings saved: %s", ", ".join(sorted(change.changed_keys)))
        if self.apply_autostart is not None:
            self.apply_autostart(bool(change.config.get("autostart", False)))
        self.refresh_menu()
        self._finish()
        if change.requires_restart:
            threading.Thread(
                target=self.restart, args=(deepcopy(change.config),),
                daemon=True, name="proxy-restart",
            ).start()
        if self.on_link_changed is not None and change.changed_keys & LINK_KEYS:
            self.on_link_changed(deepcopy(change.config))
