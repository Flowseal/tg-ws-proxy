import math
import unittest
from copy import deepcopy
from types import SimpleNamespace

try:
    from ui.ctk_tray_ui import merge_adv_from_form
except ImportError:
    merge_adv_from_form = None


@unittest.skipIf(merge_adv_from_form is None, 'Tk is required to import the settings form')
class AdvancedFormTest(unittest.TestCase):
    def merge(self, key, value):
        entry = SimpleNamespace(get=lambda: value)
        frame = SimpleNamespace(winfo_children=lambda: [None, entry])
        widgets = SimpleNamespace(adv_keys=(key,), adv_entries=[frame])
        result = {}
        merge_adv_from_form(widgets, result, {'buf_kb': 256, 'pool_size': 4, 'log_max_mb': 5})
        return result[key]

    def test_non_finite_values_use_defaults(self):
        for key, default in (('buf_kb', 256), ('pool_size', 4), ('log_max_mb', 5)):
            for value in ('nan', 'NaN', 'inf', '-inf', '1e309', '-1e309', 'abc', ''):
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.merge(key, value), default)

    def test_log_size_that_overflows_bytes_uses_default(self):
        self.assertEqual(self.merge('log_max_mb', '1e308'), 5)

    def test_valid_values_keep_existing_conversion(self):
        self.assertEqual(self.merge('buf_kb', '512'), 512)
        self.assertEqual(self.merge('pool_size', '0'), 0)
        self.assertEqual(self.merge('pool_size', '4.9'), 4)
        self.assertEqual(self.merge('log_max_mb', '2.5'), 2.5)
        self.assertTrue(math.isfinite(self.merge('log_max_mb', 'nan')))


@unittest.skipIf(merge_adv_from_form is None, 'Tk is required to import the settings form')
class SecretValidationTest(unittest.TestCase):
    def test_requires_16_decoded_bytes_without_internal_whitespace(self):
        from ui.ctk_tray_ui import TrayConfigFormWidgets, validate_config_form
        from utils.default_config import default_tray_config

        def var(value):
            return SimpleNamespace(get=lambda *args: value)

        for secret, valid in [('a' * 32, True), ('A' * 32, True),
                              ('z' * 32, False), ('a' * 31, False),
                              ('aa ' * 10 + 'aa', False)]:
            with self.subTest(secret=secret):
                form = TrayConfigFormWidgets(
                    host_var=var('127.0.0.1'), port_var=var('1443'),
                    secret_var=var(secret), dc_textbox=var('2:149.154.167.220'),
                    verbose_var=var(False), adv_keys=(), adv_entries=[],
                    autostart_var=None, check_updates_var=None,
                )
                result = validate_config_form(form, default_tray_config(), include_autostart=False)
                self.assertEqual(isinstance(result, dict), valid)


class ThemePreviewTest(unittest.TestCase):
    def test_theme_preview_preserves_config_snapshot_and_validates_selection(self):
        try:
            import tkinter
            import customtkinter as ctk
        except ImportError:
            self.skipTest('Tk and customtkinter are required for the GUI regression')
        from ui.ctk_theme import ctk_theme_for_platform
        from ui.ctk_tray_ui import install_tray_config_form, validate_config_form
        from ui.i18n import get_language, set_language, t
        from utils.default_config import default_tray_config

        previous_language = get_language()
        previous_appearance = ctk.get_appearance_mode()
        try:
            root = ctk.CTk()
        except tkinter.TclError as exc:
            self.skipTest(f'Tk display unavailable: {exc}')
        root.withdraw()
        try:
            cfg = dict(default_tray_config(), appearance='light', language='ru')
            snapshot = deepcopy(cfg)
            form = install_tray_config_form(ctk, root, ctk_theme_for_platform(), cfg, cfg)

            def find_theme(widget):
                if isinstance(widget, ctk.CTkComboBox) and t('appearance.dark') in widget.cget('values'):
                    return widget
                for child in widget.winfo_children():
                    found = find_theme(child)
                    if found is not None:
                        return found
                return None

            combo = find_theme(root)
            self.assertIsNotNone(combo)
            combo.set(t('appearance.dark'))
            combo.cget('command')(t('appearance.dark'))
            self.assertEqual(ctk.get_appearance_mode(), 'Dark')
            self.assertEqual(cfg, snapshot)
            merged = validate_config_form(form, cfg, include_autostart=False)
            self.assertIsInstance(merged, dict)
            self.assertEqual(merged['appearance'], 'dark')
        finally:
            root.destroy()
            ctk.set_appearance_mode(previous_appearance)
            set_language(previous_language)
