from __future__ import annotations

import logging
import threading
from copy import deepcopy

from tkinter import messagebox

from ui.settings_form import validate_config_form
from ui.i18n import set_language, t
from ui.settings import prepare_settings

log = logging.getLogger("tg-ws-tray")


class SettingsDialog:
    def __init__(
        self, *, ctk, root, widgets, config, defaults, persist,
        refresh_menu, finish, restart, include_autostart=False,
        apply_autostart=None, read_autostart=None,
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
        self.read_autostart = read_autostart
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
            messagebox.showerror(t("app.error_title"), values, parent=self.root)
            return
        change = prepare_settings(self.config, values, self.defaults)
        autostart_changed = (
            self.include_autostart
            and self.read_autostart is not None
            and bool(values["autostart"]) != self.read_autostart()
        )
        if not change.changed_keys and not autostart_changed:
            self._finish()
            return
        try:
            self.persist(change.config)
        except (OSError, ValueError, TypeError) as exc:
            log.exception("Failed to save settings")
            messagebox.showerror(t("app.error_title"), str(exc), parent=self.root)
            return
        self.config.update(deepcopy(change.config))
        set_language(change.config["language"])
        changed_keys = change.changed_keys | ({"autostart"} if autostart_changed else set())
        log.info("Settings saved: %s", ", ".join(sorted(changed_keys)))
        if self.apply_autostart is not None:
            self.apply_autostart(bool(change.config.get("autostart", False)))
        self.refresh_menu()
        do_restart = change.requires_restart and messagebox.askyesno(
            t("dialog.restart_title"), t("dialog.restart_body"), parent=self.root,
        )
        self._finish()
        if do_restart:
            threading.Thread(
                target=self.restart, args=(deepcopy(change.config),),
                daemon=True, name="proxy-restart",
            ).start()
