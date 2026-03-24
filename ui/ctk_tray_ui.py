"""
Общая разметка CustomTkinter для tray (Windows / Linux): настройки и первый запуск.
Логика сохранения и колбэки остаются в платформенных модулях.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import proxy.tg_ws_proxy as tg_ws_proxy

from ui.ctk_theme import (
    FIRST_RUN_FRAME_PAD,
    CtkTheme,
    main_content_frame,
)
from ui.ctk_tooltip import attach_ctk_tooltip, attach_tooltip_to_widgets

# Подсказки для формы настроек (новые пользователи)
_TIP_HOST = (
    "Адрес, на котором прокси принимает SOCKS5-подключения.\n"
    "Обычно 127.0.0.1 — только этот компьютер. Другой IP нужен, "
    "если к прокси подключаются по локальной сети."
)
_TIP_PORT = (
    "Порт SOCKS5. В Telegram Desktop в настройках прокси должен быть "
    "указан тот же порт (часто 1080)."
)
_TIP_DC = (
    "Соответствие номера датацентра Telegram (DC) и IP-адреса сервера.\n"
    "Каждая строка: «номер:IP», например 2:149.154.167.220. "
    "Прокси по этим правилам направляет трафик к нужным серверам Telegram."
)
_TIP_VERBOSE = (
    "Если включено, в файл логов пишется больше подробностей — "
    "удобно при поиске неполадок."
)
_TIP_BUF_KB = (
    "Размер буфера приёма/передачи в килобайтах.\n"
    "Больше значение — обычно стабильнее на быстрых каналах, выше расход памяти."
)
_TIP_POOL = (
    "Сколько параллельных WebSocket-сессий к одному датацентру можно держать.\n"
    "Увеличение может помочь при высокой нагрузке."
)
_TIP_LOG_MB = (
    "Максимальный размер файла лога; при достижении лимита файл перезаписывается."
)
_TIP_AUTOSTART = (
    "Запускать TG WS Proxy при входе в Windows. "
    "Если вы переместите программу в другую папку, запись автозапуска может сброситься."
)
_TIP_SAVE = "Сохранить настройки в файл. После сохранения можно перезапустить прокси."
_TIP_CANCEL = "Закрыть окно без сохранения изменений."


@dataclass
class TrayConfigFormWidgets:
    host_var: Any
    port_var: Any
    dc_textbox: Any
    verbose_var: Any
    adv_entries: List[Any]
    adv_keys: Tuple[str, ...]
    autostart_var: Optional[Any]


def install_tray_config_form(
    ctk: Any,
    frame: Any,
    theme: CtkTheme,
    cfg: dict,
    default_config: dict,
    *,
    show_autostart: bool = False,
    autostart_value: bool = False,
) -> TrayConfigFormWidgets:
    """Поля настроек прокси внутри уже созданного `frame`."""
    host_lbl = ctk.CTkLabel(frame, text="IP-адрес прокси",
                            font=(theme.ui_font_family, 13),
                            text_color=theme.text_primary, anchor="w")
    host_lbl.pack(anchor="w", pady=(0, 4))
    host_var = ctk.StringVar(value=cfg.get("host", default_config["host"]))
    host_entry = ctk.CTkEntry(
        frame, textvariable=host_var, width=200, height=36,
        font=(theme.ui_font_family, 13), corner_radius=10,
        fg_color=theme.field_bg, border_color=theme.field_border,
        border_width=1, text_color=theme.text_primary)
    host_entry.pack(anchor="w", pady=(0, 12))
    attach_tooltip_to_widgets([host_lbl, host_entry], _TIP_HOST)

    port_lbl = ctk.CTkLabel(frame, text="Порт прокси",
                            font=(theme.ui_font_family, 13),
                            text_color=theme.text_primary, anchor="w")
    port_lbl.pack(anchor="w", pady=(0, 4))
    port_var = ctk.StringVar(value=str(cfg.get("port", default_config["port"])))
    port_entry = ctk.CTkEntry(
        frame, textvariable=port_var, width=120, height=36,
        font=(theme.ui_font_family, 13), corner_radius=10,
        fg_color=theme.field_bg, border_color=theme.field_border,
        border_width=1, text_color=theme.text_primary)
    port_entry.pack(anchor="w", pady=(0, 12))
    attach_tooltip_to_widgets([port_lbl, port_entry], _TIP_PORT)

    dc_lbl = ctk.CTkLabel(
        frame, text="DC → IP маппинги (по одному на строку, формат DC:IP)",
        font=(theme.ui_font_family, 13), text_color=theme.text_primary,
        anchor="w")
    dc_lbl.pack(anchor="w", pady=(0, 4))
    dc_textbox = ctk.CTkTextbox(
        frame, width=370, height=120,
        font=(theme.mono_font_family, 12), corner_radius=10,
        fg_color=theme.field_bg, border_color=theme.field_border,
        border_width=1, text_color=theme.text_primary)
    dc_textbox.pack(anchor="w", pady=(0, 12))
    dc_textbox.insert("1.0", "\n".join(cfg.get("dc_ip", default_config["dc_ip"])))
    attach_tooltip_to_widgets([dc_lbl, dc_textbox], _TIP_DC)

    verbose_var = ctk.BooleanVar(value=cfg.get("verbose", False))
    verbose_cb = ctk.CTkCheckBox(
        frame, text="Подробное логирование (verbose)",
        variable=verbose_var, font=(theme.ui_font_family, 13),
        text_color=theme.text_primary,
        fg_color=theme.tg_blue, hover_color=theme.tg_blue_hover,
        corner_radius=6, border_width=2,
        border_color=theme.field_border)
    verbose_cb.pack(anchor="w", pady=(0, 8))
    attach_ctk_tooltip(verbose_cb, _TIP_VERBOSE)

    adv_frame = ctk.CTkFrame(frame, fg_color="transparent")
    adv_frame.pack(anchor="w", fill="x", pady=(4, 8))

    adv_rows = [
        ("Буфер (KB, 256 default)", "buf_kb", 120, _TIP_BUF_KB),
        ("WS пулов (4 default)", "pool_size", 120, _TIP_POOL),
        ("Log size (MB, 5 def)", "log_max_mb", 120, _TIP_LOG_MB),
    ]
    for lbl, key, w_, tip in adv_rows:
        col_frame = ctk.CTkFrame(adv_frame, fg_color="transparent")
        col_frame.pack(side="left", padx=(0, 10))
        adv_l = ctk.CTkLabel(col_frame, text=lbl, font=(theme.ui_font_family, 11),
                             text_color=theme.text_secondary, anchor="w")
        adv_l.pack(anchor="w")
        adv_e = ctk.CTkEntry(
            col_frame, width=w_, height=30, font=(theme.ui_font_family, 12),
            corner_radius=8, fg_color=theme.field_bg,
            border_color=theme.field_border, border_width=1,
            text_color=theme.text_primary,
            textvariable=ctk.StringVar(
                value=str(cfg.get(key, default_config[key]))
            ))
        adv_e.pack(anchor="w")
        attach_tooltip_to_widgets([adv_l, adv_e, col_frame], tip)

    adv_entries = list(adv_frame.winfo_children())
    adv_keys = ("buf_kb", "pool_size", "log_max_mb")

    autostart_var = None
    if show_autostart:
        autostart_var = ctk.BooleanVar(value=autostart_value)
        as_cb = ctk.CTkCheckBox(
            frame, text="Автозапуск при включении Windows",
            variable=autostart_var, font=(theme.ui_font_family, 13),
            text_color=theme.text_primary,
            fg_color=theme.tg_blue, hover_color=theme.tg_blue_hover,
            corner_radius=6, border_width=2,
            border_color=theme.field_border)
        as_cb.pack(anchor="w", pady=(0, 8))
        as_hint = ctk.CTkLabel(
            frame, text="При перемещении файла или открытии из другой папки\n"
            "автозапуск будет сброшен",
            font=(theme.ui_font_family, 13), text_color=theme.text_secondary,
            anchor="w", justify="left")
        as_hint.pack(anchor="w", pady=(0, 8))
        attach_tooltip_to_widgets([as_cb, as_hint], _TIP_AUTOSTART)

    return TrayConfigFormWidgets(
        host_var=host_var,
        port_var=port_var,
        dc_textbox=dc_textbox,
        verbose_var=verbose_var,
        adv_entries=adv_entries,
        adv_keys=adv_keys,
        autostart_var=autostart_var,
    )


def merge_adv_from_form(
    widgets: TrayConfigFormWidgets,
    base: Dict[str, Any],
    default_config: dict,
) -> None:
    """Дополняет base значениями buf_kb / pool_size / log_max_mb (in-place)."""
    for i, key in enumerate(widgets.adv_keys):
        col_frame = widgets.adv_entries[i]
        entry = col_frame.winfo_children()[1]
        try:
            val = float(entry.get().strip())
            if key in ("buf_kb", "pool_size"):
                val = int(val)
            base[key] = val
        except ValueError:
            base[key] = default_config[key]


def validate_config_form(
    widgets: TrayConfigFormWidgets,
    default_config: dict,
    *,
    include_autostart: bool,
) -> Union[dict, str]:
    """
    Возвращает словарь полей конфига или строку ошибки для показа пользователю.
    """
    import socket as _sock

    host_val = widgets.host_var.get().strip()
    try:
        _sock.inet_aton(host_val)
    except OSError:
        return "Некорректный IP-адрес."

    try:
        port_val = int(widgets.port_var.get().strip())
        if not (1 <= port_val <= 65535):
            raise ValueError
    except ValueError:
        return "Порт должен быть числом 1-65535"

    lines = [
        l.strip()
        for l in widgets.dc_textbox.get("1.0", "end").strip().splitlines()
        if l.strip()
    ]
    try:
        tg_ws_proxy.parse_dc_ip_list(lines)
    except ValueError as e:
        return str(e)

    new_cfg: Dict[str, Any] = {
        "host": host_val,
        "port": port_val,
        "dc_ip": lines,
        "verbose": widgets.verbose_var.get(),
    }
    if include_autostart:
        new_cfg["autostart"] = (
            widgets.autostart_var.get()
            if widgets.autostart_var is not None
            else False
        )

    merge_adv_from_form(widgets, new_cfg, default_config)
    return new_cfg


def install_tray_config_buttons(
    ctk: Any,
    frame: Any,
    theme: CtkTheme,
    *,
    on_save: Callable[[], None],
    on_cancel: Callable[[], None],
) -> None:
    btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
    btn_frame.pack(fill="x", pady=(20, 0))
    save_btn = ctk.CTkButton(
        btn_frame, text="Сохранить", height=38,
        font=(theme.ui_font_family, 14, "bold"), corner_radius=10,
        fg_color=theme.tg_blue, hover_color=theme.tg_blue_hover,
        text_color="#ffffff",
        command=on_save)
    save_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
    attach_ctk_tooltip(save_btn, _TIP_SAVE)
    cancel_btn = ctk.CTkButton(
        btn_frame, text="Отмена", height=38,
        font=(theme.ui_font_family, 14), corner_radius=10,
        fg_color=theme.field_bg, hover_color=theme.field_border,
        text_color=theme.text_primary, border_width=1,
        border_color=theme.field_border,
        command=on_cancel)
    cancel_btn.pack(side="right", fill="x", expand=True)
    attach_ctk_tooltip(cancel_btn, _TIP_CANCEL)


def populate_first_run_window(
    ctk: Any,
    root: Any,
    theme: CtkTheme,
    *,
    host: str,
    port: int,
    on_done: Callable[[bool], None],
) -> None:
    """
    Содержимое окна первого запуска. on_done(open_in_telegram) — по «Начать» и по закрытию окна.
    """
    tg_url = f"tg://socks?server={host}&port={port}"
    fpx, fpy = FIRST_RUN_FRAME_PAD
    frame = main_content_frame(ctk, root, theme, padx=fpx, pady=fpy)

    title_frame = ctk.CTkFrame(frame, fg_color="transparent")
    title_frame.pack(anchor="w", pady=(0, 16), fill="x")

    accent_bar = ctk.CTkFrame(title_frame, fg_color=theme.tg_blue,
                              width=4, height=32, corner_radius=2)
    accent_bar.pack(side="left", padx=(0, 12))

    ctk.CTkLabel(title_frame, text="Прокси запущен и работает в системном трее",
                 font=(theme.ui_font_family, 17, "bold"),
                 text_color=theme.text_primary).pack(side="left")

    sections = [
        ("Как подключить Telegram Desktop:", True),
        ("  Автоматически:", True),
        ("  ПКМ по иконке в трее → «Открыть в Telegram»", False),
        (f"  Или ссылка: {tg_url}", False),
        ("\n  Вручную:", True),
        ("  Настройки → Продвинутые → Тип подключения → Прокси", False),
        (f"  SOCKS5 → {host} : {port} (без логина/пароля)", False),
    ]

    for text, bold in sections:
        weight = "bold" if bold else "normal"
        ctk.CTkLabel(frame, text=text,
                     font=(theme.ui_font_family, 13, weight),
                     text_color=theme.text_primary,
                     anchor="w", justify="left").pack(anchor="w", pady=1)

    ctk.CTkFrame(frame, fg_color="transparent", height=16).pack()

    ctk.CTkFrame(frame, fg_color=theme.field_border, height=1,
                 corner_radius=0).pack(fill="x", pady=(0, 12))

    auto_var = ctk.BooleanVar(value=True)
    ctk.CTkCheckBox(frame, text="Открыть прокси в Telegram сейчас",
                    variable=auto_var, font=(theme.ui_font_family, 13),
                    text_color=theme.text_primary,
                    fg_color=theme.tg_blue, hover_color=theme.tg_blue_hover,
                    corner_radius=6, border_width=2,
                    border_color=theme.field_border).pack(anchor="w", pady=(0, 16))

    def on_ok():
        on_done(auto_var.get())

    ctk.CTkButton(frame, text="Начать", width=180, height=42,
                  font=(theme.ui_font_family, 15, "bold"), corner_radius=10,
                  fg_color=theme.tg_blue, hover_color=theme.tg_blue_hover,
                  text_color="#ffffff",
                  command=on_ok).pack(pady=(0, 0))

    root.protocol("WM_DELETE_WINDOW", on_ok)
