from __future__ import annotations

import os
import webbrowser
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import proxy.tg_ws_proxy as tg_ws_proxy
from proxy import __version__
from utils.update_check import RELEASES_PAGE_URL, get_status

from ui.ctk_theme import (
    FIRST_RUN_FRAME_PAD,
    CtkTheme,
    main_content_frame,
)
from ui.ctk_tooltip import attach_ctk_tooltip, attach_tooltip_to_widgets

_TIP_HOST = (
    "Адрес, на котором прокси принимает подключения.\n"
    "Обычно 127.0.0.1 — локальная сеть, 0.0.0.0 - все интерфейсы"
)
_TIP_PORT = (
    "Порт прокси. В Telegram Desktop в настройках прокси должен быть "
    "указан тот же порт"
)
_TIP_SECRET = "Секретный ключ для авторизации клиентов"
_TIP_DC = (
    "Соответствие номера датацентра Telegram (DC) и IP-адреса сервера.\n"
    "Каждая строка: «номер:IP», например 2:149.154.167.220. "
    "Прокси по этим правилам направляет трафик к нужным серверам Telegram"
)
_TIP_VERBOSE = (
    "Если включено, в файл логов пишется больше подробностей — "
    "необходимо при поиске неполадок"
)
_TIP_BUF_KB = (
    "Размер буфера приёма/передачи в килобайтах.\n"
    "Больше значение — больше выделение памяти на сокет"
)
_TIP_POOL = (
    "Сколько параллельных WebSocket-сессий к одному датацентру можно держать.\n"
    "Увеличение может помочь при высокой нагрузке"
)
_TIP_LOG_MB = (
    "Максимальный размер файла лога; при достижении лимита файл перезаписывается"
)
_TIP_AUTOSTART = (
    "Запускать TG WS Proxy при входе в Windows. "
    "Если вы переместите программу в другую папку, автозапуск сбросится"
)
_TIP_CHECK_UPDATES = "При запуске проверять наличие обновлений"
_TIP_SAVE = "Сохранить настройки"
_TIP_CANCEL = "Закрыть окно без сохранения изменений"

_INNER_W = 396


def _entry(ctk, parent, theme, *, var=None, width=0, height=36, radius=10, **kw):
    opts = dict(
        font=(theme.ui_font_family, 13), corner_radius=radius,
        fg_color=theme.bg, border_color=theme.field_border,
        border_width=1, text_color=theme.text_primary,
    )
    if var is not None:
        opts["textvariable"] = var
    if width:
        opts["width"] = width
    opts["height"] = height
    opts.update(kw)
    return ctk.CTkEntry(parent, **opts)


def _checkbox(ctk, parent, theme, text, variable):
    return ctk.CTkCheckBox(
        parent, text=text, variable=variable,
        font=(theme.ui_font_family, 13), text_color=theme.text_primary,
        fg_color=theme.tg_blue, hover_color=theme.tg_blue_hover,
        corner_radius=6, border_width=2, border_color=theme.field_border,
    )


def _label(ctk, parent, theme, text, *, size=12, bold=False, secondary=True, **kw):
    weight = "bold" if bold else "normal"
    return ctk.CTkLabel(
        parent, text=text,
        font=(theme.ui_font_family, size, weight),
        text_color=theme.text_secondary if secondary else theme.text_primary,
        anchor="w", **kw,
    )


def _labeled_entry(ctk, parent, theme, label_text, value, *, tip="", width=0, pack_fill=False):
    col = ctk.CTkFrame(parent, fg_color="transparent")
    lbl = _label(ctk, col, theme, label_text)
    lbl.pack(anchor="w", pady=(0, 2))
    var = ctk.StringVar(value=str(value))
    ent = _entry(ctk, col, theme, var=var, width=width)
    if pack_fill:
        ent.pack(fill="x")
    else:
        ent.pack(anchor="w")
    if tip:
        attach_tooltip_to_widgets([lbl, ent, col], tip)
    return col, var


def tray_settings_scroll_and_footer(
    ctk: Any,
    content_parent: Any,
    theme: CtkTheme,
) -> Tuple[Any, Any]:
    footer = ctk.CTkFrame(content_parent, fg_color=theme.bg)
    footer.pack(side="bottom", fill="x")
    scroll = ctk.CTkScrollableFrame(
        content_parent,
        fg_color=theme.bg,
        corner_radius=0,
        scrollbar_button_color=theme.field_border,
        scrollbar_button_hover_color=theme.text_secondary,
    )
    scroll.pack(fill="both", expand=True)
    return scroll, footer


def _config_section(
    ctk: Any,
    parent: Any,
    theme: CtkTheme,
    title: str,
    *,
    bottom_spacer: int = 6,
) -> Any:
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    wrap.pack(fill="x", pady=(0, bottom_spacer))
    _label(ctk, wrap, theme, title, secondary=False, bold=True).pack(anchor="w", pady=(0, 2))
    card = ctk.CTkFrame(
        wrap, fg_color=theme.field_bg, corner_radius=10,
        border_width=1, border_color=theme.field_border,
    )
    card.pack(fill="x")
    inner = ctk.CTkFrame(card, fg_color="transparent")
    inner.pack(fill="x", padx=10, pady=8)
    return inner


@dataclass
class TrayConfigFormWidgets:
    host_var: Any
    port_var: Any
    secret_var: Any
    dc_textbox: Any
    verbose_var: Any
    adv_entries: List[Any]
    adv_keys: Tuple[str, ...]
    autostart_var: Optional[Any]
    check_updates_var: Optional[Any]


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
    header = ctk.CTkFrame(frame, fg_color="transparent")
    header.pack(fill="x", pady=(0, 2))
    ctk.CTkLabel(
        header, text="Настройки прокси",
        font=(theme.ui_font_family, 17, "bold"),
        text_color=theme.text_primary, anchor="w",
    ).pack(side="left")
    ctk.CTkLabel(
        header, text=f"v{__version__}",
        font=(theme.ui_font_family, 12),
        text_color=theme.text_secondary, anchor="e",
    ).pack(side="right")

    conn = _config_section(ctk, frame, theme, "Подключение MTProto")

    host_row = ctk.CTkFrame(conn, fg_color="transparent")
    host_row.pack(fill="x")

    host_col, host_var = _labeled_entry(
        ctk, host_row, theme, "IP-адрес",
        cfg.get("host", default_config["host"]),
        tip=_TIP_HOST, width=160, pack_fill=True,
    )
    host_col.pack(side="left", fill="x", expand=True, padx=(0, 10))

    port_col, port_var = _labeled_entry(
        ctk, host_row, theme, "Порт",
        cfg.get("port", default_config["port"]),
        tip=_TIP_PORT, width=100,
    )
    port_col.pack(side="left")

    secret_row = ctk.CTkFrame(conn, fg_color="transparent")
    secret_row.pack(fill="x")

    secret_col, secret_var = _labeled_entry(
        ctk, secret_row, theme, "Secret",
        cfg.get("secret", default_config["secret"]),
        tip=_TIP_SECRET, width=160, pack_fill=True,
    )
    secret_col.pack(side="left", fill="x", expand=True, padx=(0, 10))

    regen_col = ctk.CTkFrame(secret_row, fg_color="transparent")
    regen_col.pack(side="left", anchor="s")
    ctk.CTkLabel(regen_col, text="", font=(theme.ui_font_family, 12)).pack(pady=(0, 2))
    ctk.CTkButton(
        regen_col, text="↺", width=36, height=36,
        font=(theme.ui_font_family, 18), corner_radius=10,
        fg_color=theme.tg_blue, hover_color=theme.tg_blue_hover,
        text_color="#ffffff", border_width=1, border_color=theme.field_border,
        command=lambda: secret_var.set(os.urandom(16).hex()),
    ).pack()

    dc_inner = _config_section(ctk, frame, theme, "Датацентры Telegram (DC → IP)")
    dc_lbl = _label(ctk, dc_inner, theme, "По одному правилу на строку, формат: номер:IP", size=11)
    dc_lbl.pack(anchor="w", pady=(0, 4))
    dc_textbox = ctk.CTkTextbox(
        dc_inner, width=_INNER_W, height=88,
        font=(theme.mono_font_family, 12), corner_radius=10,
        fg_color=theme.bg, border_color=theme.field_border,
        border_width=1, text_color=theme.text_primary,
    )
    dc_textbox.pack(fill="x")
    dc_textbox.insert("1.0", "\n".join(cfg.get("dc_ip", default_config["dc_ip"])))
    attach_tooltip_to_widgets([dc_lbl, dc_textbox], _TIP_DC)

    log_inner = _config_section(ctk, frame, theme, "Логи и производительность")

    verbose_var = ctk.BooleanVar(value=cfg.get("verbose", False))
    verbose_cb = _checkbox(ctk, log_inner, theme, "Подробное логирование (verbose)", verbose_var)
    verbose_cb.pack(anchor="w", pady=(0, 6))
    attach_ctk_tooltip(verbose_cb, _TIP_VERBOSE)

    adv_frame = ctk.CTkFrame(log_inner, fg_color="transparent")
    adv_frame.pack(fill="x")

    adv_rows = [
        ("Буфер, КБ (по умолчанию 256)", "buf_kb", _TIP_BUF_KB),
        ("Пул WebSocket-сессий (по умолчанию 4)", "pool_size", _TIP_POOL),
        ("Макс. размер лога, МБ (по умолчанию 5)", "log_max_mb", _TIP_LOG_MB),
    ]
    for label_text, key, tip in adv_rows:
        col = ctk.CTkFrame(adv_frame, fg_color="transparent")
        col.pack(fill="x", pady=(0, 0 if key == "log_max_mb" else 5))
        adv_l = _label(ctk, col, theme, label_text, size=11)
        adv_l.pack(anchor="w", pady=(0, 2))
        adv_e = _entry(
            ctk, col, theme, width=_INNER_W, height=32, radius=8,
            textvariable=ctk.StringVar(value=str(cfg.get(key, default_config[key]))),
        )
        adv_e.pack(fill="x")
        attach_tooltip_to_widgets([adv_l, adv_e, col], tip)

    adv_entries = list(adv_frame.winfo_children())
    adv_keys = ("buf_kb", "pool_size", "log_max_mb")

    upd_inner = _config_section(ctk, frame, theme, "Обновления")
    st = get_status()
    check_updates_var = ctk.BooleanVar(
        value=bool(cfg.get("check_updates", default_config.get("check_updates", True)))
    )
    upd_cb = _checkbox(ctk, upd_inner, theme, "Проверять обновления при запуске", check_updates_var)
    upd_cb.pack(anchor="w", pady=(0, 6))
    attach_ctk_tooltip(upd_cb, _TIP_CHECK_UPDATES)

    if st.get("error"):
        upd_status = "Не удалось связаться с GitHub. Проверьте сеть."
    elif not st.get("checked"):
        upd_status = "Статус появится после фоновой проверки при запуске."
    elif st.get("has_update") and st.get("latest"):
        upd_status = (
            f"На GitHub доступна версия {st['latest']} "
            f"(у вас {__version__})."
        )
    elif st.get("ahead_of_release") and st.get("latest"):
        upd_status = (
            f"У вас {__version__} — новее последнего релиза на GitHub "
            f"({st['latest']})."
        )
    else:
        upd_status = "Установлена последняя известная версия с GitHub."

    _label(ctk, upd_inner, theme, upd_status, size=11,
           justify="left", wraplength=_INNER_W).pack(anchor="w", pady=(0, 8))

    rel_url = (st.get("html_url") or "").strip() or RELEASES_PAGE_URL
    ctk.CTkButton(
        upd_inner, text="Открыть страницу релиза", height=32,
        font=(theme.ui_font_family, 13), corner_radius=8,
        fg_color=theme.field_bg, hover_color=theme.field_border,
        text_color=theme.text_primary, border_width=1,
        border_color=theme.field_border,
        command=lambda u=rel_url: webbrowser.open(u),
    ).pack(anchor="w")

    autostart_var = None
    if show_autostart:
        sys_inner = _config_section(ctk, frame, theme, "Запуск Windows", bottom_spacer=4)
        autostart_var = ctk.BooleanVar(value=autostart_value)
        as_cb = _checkbox(ctk, sys_inner, theme, "Автозапуск при включении компьютера", autostart_var)
        as_cb.pack(anchor="w", pady=(0, 4))
        as_hint = _label(
            ctk, sys_inner, theme,
            "Если переместить программу в другую папку, запись автозапуска может сброситься.",
            size=11, justify="left", wraplength=_INNER_W,
        )
        as_hint.pack(anchor="w")
        attach_tooltip_to_widgets([as_cb, as_hint], _TIP_AUTOSTART)

    return TrayConfigFormWidgets(
        host_var=host_var, port_var=port_var, secret_var=secret_var,
        dc_textbox=dc_textbox, verbose_var=verbose_var,
        adv_entries=adv_entries, adv_keys=adv_keys,
        autostart_var=autostart_var, check_updates_var=check_updates_var,
    )


def merge_adv_from_form(
    widgets: TrayConfigFormWidgets,
    base: Dict[str, Any],
    default_config: dict,
) -> None:
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

    secret_val = widgets.secret_var.get().strip()
    if len(secret_val) != 32:
        return "Secret должен содержать ровно 32 hex-символа (16 байт)."
    try:
        bytes.fromhex(secret_val)
    except ValueError:
        return "Secret должен состоять только из hex-символов (0-9, a-f)."

    new_cfg: Dict[str, Any] = {
        "host": host_val,
        "port": port_val,
        "secret": secret_val,
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
    if widgets.check_updates_var is not None:
        new_cfg["check_updates"] = bool(widgets.check_updates_var.get())
    return new_cfg


def install_tray_config_buttons(
    ctk: Any,
    frame: Any,
    theme: CtkTheme,
    *,
    on_save: Callable[[], None],
    on_cancel: Callable[[], None],
) -> None:
    ctk.CTkFrame(
        frame,
        fg_color=theme.field_border,
        height=1,
        corner_radius=0,
    ).pack(fill="x", pady=(4, 10))
    btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
    btn_frame.pack(fill="x", pady=(0, 0))
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
    secret: str,
    on_done: Callable[[bool], None],
) -> None:
    link_host = tg_ws_proxy.get_link_host(host)
    tg_url = f"tg://proxy?server={link_host}&port={port}&secret=dd{secret}"
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
        (f"  Или скопировать ссылку, отправить её себе в TG и нажать по ней: {tg_url}", False),
        ("\n  Вручную:", True),
        ("  Настройки → Продвинутые → Тип подключения → Прокси", False),
        (f"  MTProto → {link_host} : {port}", False),
        (f"  Secret: dd{secret}", False),
    ]

    textbox = ctk.CTkTextbox(
        frame,
        font=(theme.ui_font_family, 13),
        fg_color=theme.bg,
        border_width=0,
        text_color=theme.text_primary,
        activate_scrollbars=False,
        wrap="word",
        height=275,
    )
    textbox._textbox.tag_configure("bold", font=(theme.ui_font_family, 13, "bold"))
    textbox._textbox.configure(spacing1=1, spacing3=1)
    for text, bold in sections:
        if text.startswith("\n"):
            textbox.insert("end", "\n")
            text = text[1:]
        if bold:
            textbox.insert("end", text + "\n", "bold")
        else:
            textbox.insert("end", text + "\n")
    textbox.configure(state="disabled")
    textbox.pack(anchor="w", fill="x")

    ctk.CTkFrame(frame, fg_color="transparent", height=16).pack()

    ctk.CTkFrame(frame, fg_color=theme.field_border, height=1,
                 corner_radius=0).pack(fill="x", pady=(0, 12))

    auto_var = ctk.BooleanVar(value=True)
    _checkbox(ctk, frame, theme, "Открыть прокси в Telegram сейчас",
              auto_var).pack(anchor="w", pady=(0, 16))

    def on_ok():
        on_done(auto_var.get())

    ctk.CTkButton(frame, text="Начать", width=180, height=42,
                  font=(theme.ui_font_family, 15, "bold"), corner_radius=10,
                  fg_color=theme.tg_blue, hover_color=theme.tg_blue_hover,
                  text_color="#ffffff",
                  command=on_ok).pack(pady=(0, 0))

    root.protocol("WM_DELETE_WINDOW", on_ok)
