from __future__ import annotations

import logging
import os
import webbrowser
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from proxy import __version__, get_link_host, parse_dc_ip_list, coerce_domain_list
from proxy.balancer import balancer
from utils.update_check import RELEASES_PAGE_URL, get_status


from ui.ctk_theme import (
    FIRST_RUN_FRAME_PAD,
    CtkTheme,
    main_content_frame,
)
from ui.ctk_tooltip import attach_ctk_tooltip, attach_tooltip_to_widgets

log = logging.getLogger('tg-mtproto-proxy')

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
    "Каждая строка: «номер:IP», например 4:149.154.167.220. "
    "Прокси по этим правилам направляет трафик к нужным серверам Telegram\n\n"
    "Если у вас не работают медиа и работает CF-прокси, то попробуйте убрать строку 2:149.154.167.220"
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
_TIP_KEEPALIVE = (
    "Держит соединение с Telegram «тёплым», чтобы NAT/файрвол не рвал "
    "простаивающие сессии.\n"
    "TCP — на уровне ОС, незаметно для Telegram (рекомендуется).\n"
    "WebSocket — старый режим через PING-кадры; часть серверов Telegram "
    "из-за него рвёт соединение.\n"
    "Выключен — keepalive не используется."
)
_TIP_KEEPALIVE_INTERVAL = (
    "Интервал keepalive в секундах.\n"
    "Для TCP — время простоя до проб, для WebSocket — период между PING"
)
_TIP_AUTOSTART = (
    "Запускать TG WS Proxy при входе в Windows. "
    "Если вы переместите программу в другую папку, автозапуск сбросится"
)
_TIP_CHECK_UPDATES = "При запуске проверять наличие обновлений"
_TIP_CFPROXY = (
    "Использовать Cloudflare прокси для недоступных датацентров"
)
_TIP_CFPROXY_DOMAIN = (
    "Ваши собственные домены, проксируемые через Cloudflare, для WS-подключения.\n"
    "Несколько доменов указывайте через запятую.\n"
    "Если не указаны — выбираются автоматически из поддерживаемых доменов"
)
_TIP_CFPROXY_USER_DOMAIN_CB = (
    "Указать свои домены вместо автоматического выбора"
)
_TIP_CFWORKER_DOMAIN = (
    "Домены Cloudflare Worker (например, name.account.workers.dev).\n"
    "Несколько доменов указывайте через запятую.\n"
    "Прокси передает через них подключение к Telegram DC по IP"
)
_TIP_SAVE = "Сохранить настройки"
_TIP_CANCEL = "Закрыть окно без сохранения изменений"

_CFPROXY_HELP_URL = "https://github.com/Flowseal/tg-ws-proxy/blob/main/docs/CfProxy.md"
_CFWORKER_HELP_URL = "https://github.com/Flowseal/tg-ws-proxy/blob/main/docs/CfWorker.md"
_CFPROXY_TEST_DCS = [1, 2, 3, 4, 5, 203]
_CFWORKER_TEST_DST = {
    1: '149.154.175.50',
    2: '149.154.167.51',
    3: '149.154.175.100',
    4: '149.154.167.91',
    5: '149.154.171.5',
    203: '91.105.192.100',
}


def _run_connectivity_test(cases: list) -> dict:
    import base64
    import ssl
    import socket as _socket

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    results = {}
    for dc, connect_host, sni_host, req_host, path in cases:
        try:
            with _socket.create_connection((connect_host, 443), timeout=5) as raw:
                with ctx.wrap_socket(raw, server_hostname=sni_host) as ssock:
                    ws_key = base64.b64encode(os.urandom(16)).decode()
                    req = (
                        f"GET {path} HTTP/1.1\r\n"
                        f"Host: {req_host}\r\n"
                        f"Upgrade: websocket\r\n"
                        f"Connection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {ws_key}\r\n"
                        f"Sec-WebSocket-Version: 13\r\n"
                        f"Sec-WebSocket-Protocol: binary\r\n"
                        f"\r\n"
                    ).encode()
                    ssock.sendall(req)
                    ssock.settimeout(5)
                    buf = b""
                    while b"\r\n\r\n" not in buf:
                        chunk = ssock.recv(512)
                        if not chunk:
                            break
                        buf += chunk
                    first = buf.decode("utf-8", errors="replace").split("\r\n")[0]
                    if "101" in first:
                        results[dc] = True
                    else:
                        results[dc] = first or "нет ответа"
                    ssock.close()
                raw.close()
        except _socket.timeout:
            results[dc] = "таймаут"
        except OSError as exc:
            msg = str(exc)
            results[dc] = msg[:60] if len(msg) > 60 else msg
    return results


def _run_cfproxy_connectivity_test(domain: str) -> dict:
    cases = []
    for dc in _CFPROXY_TEST_DCS:
        host = f"kws{dc}.{domain}"
        cases.append((dc, host, host, host, "/apiws"))
    return _run_connectivity_test(cases)


def _run_cfworker_connectivity_test(domain: str) -> dict:
    cases = []
    for dc in _CFPROXY_TEST_DCS:
        dst = _CFWORKER_TEST_DST[dc]
        path = f"/apiws?dst={dst}&dc={dc}&media=0"
        cases.append((dc, domain, domain, domain, path))
    return _run_connectivity_test(cases)


def _run_cfproxy_multi_test(domains: list) -> dict:
    return {domain: _run_cfproxy_connectivity_test(domain) for domain in domains}


def _run_cfworker_multi_test(domains: list) -> dict:
    return {domain: _run_cfworker_connectivity_test(domain) for domain in domains}


def _run_cfproxy_auto_test(domains: list) -> tuple:
    merged: dict = {}
    best_domain = None
    for domain in reversed(domains):
        res = _run_cfproxy_connectivity_test(domain)
        if all(v is True for v in res.values()):
            return domain, res
        for dc, v in res.items():
            if v is True:
                merged[dc] = True
                best_domain = domain
            elif dc not in merged:
                merged[dc] = v
    return best_domain, merged


def _show_connectivity_results(title_base: str, results: dict,
                               domain: str = '', label_prefix: str = 'DC',
                               auto_mode: bool = False,
                               unavailable_message: str = '') -> None:
    import tkinter as _tk
    from tkinter import messagebox as _mb

    ok = [dc for dc, v in results.items() if v is True]
    if auto_mode:
        if domain:
            title = f"{title_base}: доступен"
            msg = f"\u2713 {title_base} работает. {len(ok)} из {len(_CFPROXY_TEST_DCS)} серверов доступны."
        else:
            title = f"{title_base}: недоступен"
            msg = unavailable_message
    else:
        fail = [(dc, v) for dc, v in results.items() if v is not True]
        if len(ok) == len(_CFPROXY_TEST_DCS):
            title = f"{title_base}: всё работает"
            msg = f"\u2713 Все {len(_CFPROXY_TEST_DCS)} серверов доступны через {domain}."
        elif not ok:
            title = f"{title_base}: недоступен"
            msg = f"\u2717 Ни один сервер не отвечает через {domain}.\n\nОшибки:\n"
            msg += "\n".join(f"  {label_prefix}{dc}: {v}" for dc, v in fail)
        else:
            title = f"{title_base}: частично работает"
            msg = (
                f"Домен: {domain}\n\n"
                f"\u2713 Работают: {', '.join(f'{label_prefix}{dc}' for dc in ok)}\n\n"
                f"\u2717 Недоступны:\n"
                + "\n".join(f"  {label_prefix}{dc}: {v}" for dc, v in fail)
            )

    root = _tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    _mb.showinfo(title, msg, parent=root)
    root.destroy()


def _show_multi_connectivity_results(title_base: str, per_domain: dict,
                                     label_prefix: str = 'DC') -> None:
    import tkinter as _tk
    from tkinter import messagebox as _mb

    total = len(_CFPROXY_TEST_DCS)
    all_ok = True
    any_ok = False
    blocks = []
    for domain, results in per_domain.items():
        ok = [dc for dc, v in results.items() if v is True]
        fail = [(dc, v) for dc, v in results.items() if v is not True]
        if len(ok) == total:
            any_ok = True
            blocks.append(f"\u2713 {domain}: все {total} серверов доступны")
        elif not ok:
            all_ok = False
            blocks.append(f"\u2717 {domain}: недоступен")
        else:
            all_ok = False
            any_ok = True
            blocks.append(
                f"~ {domain}: работают "
                f"{', '.join(f'{label_prefix}{dc}' for dc in ok)}; "
                f"недоступны "
                f"{', '.join(f'{label_prefix}{dc}' for dc, _ in fail)}"
            )

    if all_ok:
        title = f"{title_base}: всё работает"
    elif any_ok:
        title = f"{title_base}: частично работает"
    else:
        title = f"{title_base}: недоступен"
    msg = "\n\n".join(blocks)

    root = _tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    _mb.showinfo(title, msg, parent=root)
    root.destroy()

_INNER_W = 396

_APPEARANCE_OPTIONS = ["Авто", "Светлая", "Тёмная"]
_APPEARANCE_FROM_CFG = {"auto": "Авто", "light": "Светлая", "dark": "Тёмная"}
_APPEARANCE_TO_CFG = {"Авто": "auto", "Светлая": "light", "Тёмная": "dark"}
_APPEARANCE_TO_CTK = {"auto": "system", "light": "Light", "dark": "Dark"}

_KEEPALIVE_OPTIONS = ["Выключен", "TCP (рекомендуется)", "WebSocket"]
_KEEPALIVE_FROM_CFG = {
    "off": "Выключен", "tcp": "TCP (рекомендуется)", "ws": "WebSocket",
}
_KEEPALIVE_TO_CFG = {
    "Выключен": "off", "TCP (рекомендуется)": "tcp", "WebSocket": "ws",
}


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
    cfproxy_var: Optional[Any] = None
    cfproxy_user_domain_var: Optional[Any] = None
    cfproxy_worker_domain_var: Optional[Any] = None
    appearance_var: Optional[Any] = None
    keepalive_mode_var: Optional[Any] = None
    keepalive_interval_var: Optional[Any] = None


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
        header, text="Настройки",
        font=(theme.ui_font_family, 17, "bold"),
        text_color=theme.text_primary, anchor="w",
    ).pack(side="left")
    ctk.CTkLabel(
        header, text=f"v{__version__}",
        font=(theme.ui_font_family, 12),
        text_color=theme.text_secondary, anchor="e",
    ).pack(side="right", padx=(4, 0))
    appearance_var = ctk.StringVar(
        value=_APPEARANCE_FROM_CFG.get(cfg.get("appearance", "auto"), "Авто")
    )

    def _on_appearance_change(choice: str) -> None:
        cfg_val = _APPEARANCE_TO_CFG.get(choice, "auto")
        ctk.set_appearance_mode(_APPEARANCE_TO_CTK[cfg_val])
        cfg["appearance"] = cfg_val

    ctk.CTkComboBox(
        header,
        values=_APPEARANCE_OPTIONS,
        variable=appearance_var,
        width=102,
        height=28,
        font=(theme.ui_font_family, 12),
        text_color=theme.text_secondary,
        fg_color=theme.field_bg,
        border_color=theme.field_border,
        button_color=theme.field_border,
        button_hover_color=theme.text_secondary,
        dropdown_fg_color=theme.field_bg,
        dropdown_text_color=theme.text_primary,
        dropdown_hover_color=theme.field_border,
        corner_radius=8,
        state="readonly",
        command=_on_appearance_change,
    ).pack(side="right")

    ctk.CTkButton(
        header, text="Donate ♥", width=90, height=28,
        font=(theme.ui_font_family, 13, "bold"), corner_radius=8,
        fg_color="#22c55e", hover_color="#16a34a",
        text_color="#ffffff", border_width=0,
        command=lambda: (
            header.winfo_toplevel().iconify(),
            webbrowser.open("https://github.com/Flowseal/tg-ws-proxy/blob/main/docs/Funding.md"),
        ),
    ).pack(side="right", padx=(0, 6))

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

    cf_inner = _config_section(ctk, frame, theme, "Cloudflare Proxy")

    cf_row = ctk.CTkFrame(cf_inner, fg_color="transparent")
    cf_row.pack(fill="x", pady=(0, 4))

    cfproxy_var = ctk.BooleanVar(
        value=cfg.get("cfproxy", default_config.get("cfproxy", True))
    )
    cf_cb = _checkbox(ctk, cf_row, theme, "Включить CF-прокси", cfproxy_var)
    cf_cb.pack(side="left", padx=(0, 16))
    attach_ctk_tooltip(cf_cb, _TIP_CFPROXY)

    _cf_test_btn = [None]

    def _on_cf_test():
        user_domains = (
            coerce_domain_list(cfproxy_user_domain_var.get())
            if cf_custom_cb_var.get() else []
        )
        btn = _cf_test_btn[0]
        if btn:
            btn.configure(text="...", state="disabled")
        import threading as _threading
        if user_domains:
            def _worker():
                try:
                    per = _run_cfproxy_multi_test(user_domains)
                    if btn:
                        btn.after(
                            0,
                            lambda: _show_multi_connectivity_results(
                                "CF-прокси", per, label_prefix='kws',
                            ),
                        )
                except Exception as exc:
                    log.error("CF proxy test failed: %s", exc)
                finally:
                    if btn:
                        btn.after(0, lambda: btn.configure(text="Тест", state="normal"))
            _threading.Thread(target=_worker, daemon=True).start()
        else:
            def _worker_auto():
                try:
                    ok_domain, res = _run_cfproxy_auto_test(balancer.domains)
                    if btn:
                        btn.after(
                            0,
                            lambda: _show_connectivity_results(
                                "CF-прокси", res,
                                domain=ok_domain or '',
                                auto_mode=True,
                                unavailable_message=(
                                    "\u2717 Ни один из автоматических CF-доменов не отвечает."
                                ),
                            ),
                        )
                except Exception as exc:
                    log.error("CF proxy auto-test failed: %s", exc)
                finally:
                    if btn:
                        btn.after(0, lambda: btn.configure(text="Тест", state="normal"))
            _threading.Thread(target=_worker_auto, daemon=True).start()

    _cf_test_widget = ctk.CTkButton(
        cf_row, text="Тест", width=56, height=28,
        font=(theme.ui_font_family, 13), corner_radius=8,
        fg_color=theme.tg_blue, hover_color=theme.tg_blue_hover,
        text_color="#ffffff", border_width=1, border_color=theme.field_border,
        command=_on_cf_test,
    )
    _cf_test_widget.pack(side="right")
    _cf_test_btn[0] = _cf_test_widget

    cf_custom_row = ctk.CTkFrame(cf_inner, fg_color="transparent")
    cf_custom_row.pack(fill="x")

    saved_user_domains = coerce_domain_list(
        cfg.get("cfproxy_user_domain", default_config.get("cfproxy_user_domain", ""))
    )
    cf_custom_cb_var = ctk.BooleanVar(value=bool(saved_user_domains))
    cf_custom_cb = _checkbox(ctk, cf_custom_row, theme, "Свой домен", cf_custom_cb_var)
    cf_custom_cb.pack(side="left", padx=(0, 10))
    attach_ctk_tooltip(cf_custom_cb, _TIP_CFPROXY_USER_DOMAIN_CB)

    ctk.CTkButton(
        cf_custom_row, text="?", width=28, height=32,
        font=(theme.ui_font_family, 14), corner_radius=8,
        fg_color=theme.tg_blue, hover_color=theme.tg_blue_hover,
        text_color="#ffffff", border_width=1, border_color=theme.field_border,
        command=lambda: webbrowser.open(_CFPROXY_HELP_URL),
    ).pack(side="right")

    cfproxy_user_domain_var = ctk.StringVar(value=", ".join(saved_user_domains))
    cf_domain_entry = _entry(
        ctk, cf_custom_row, theme, var=cfproxy_user_domain_var,
        height=32, radius=8,
    )
    cf_domain_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
    attach_ctk_tooltip(cf_domain_entry, _TIP_CFPROXY_DOMAIN)

    def _sync_domain_entry(*_):
        state = "normal" if cf_custom_cb_var.get() else "disabled"
        cf_domain_entry.configure(state=state)
        if not cf_custom_cb_var.get():
            cfproxy_user_domain_var.set("")

    cf_custom_cb_var.trace_add("write", _sync_domain_entry)
    _sync_domain_entry()

    cf_worker_inner = _config_section(ctk, frame, theme, "Cloudflare Worker")

    cf_worker_row = ctk.CTkFrame(cf_worker_inner, fg_color="transparent")
    cf_worker_row.pack(fill="x", pady=(0, 4))
    cf_worker_lbl = _label(ctk, cf_worker_row, theme, "Cloudflare Worker домены (через запятую)", size=11)
    cf_worker_lbl.pack(anchor="w", pady=(0, 2))

    cf_worker_input = ctk.CTkFrame(cf_worker_inner, fg_color="transparent")
    cf_worker_input.pack(fill="x")

    cfproxy_worker_domain_var = ctk.StringVar(
        value=", ".join(coerce_domain_list(
            cfg.get("cfproxy_worker_domain", default_config.get("cfproxy_worker_domain", ""))
        ))
    )
    cf_worker_entry = _entry(
        ctk, cf_worker_input, theme, var=cfproxy_worker_domain_var,
        height=32, radius=8,
    )
    cf_worker_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
    attach_tooltip_to_widgets([cf_worker_lbl, cf_worker_entry], _TIP_CFWORKER_DOMAIN)

    _cfworker_test_btn = [None]

    def _sync_cfworker_test_button(*_):
        btn = _cfworker_test_btn[0]
        if btn is None:
            return
        enabled = bool(coerce_domain_list(cfproxy_worker_domain_var.get()))
        btn.configure(state="normal" if enabled else "disabled")

    def _on_cfworker_test():
        domains = coerce_domain_list(cfproxy_worker_domain_var.get())
        btn = _cfworker_test_btn[0]
        if not domains or btn is None:
            return
        btn.configure(text="...", state="disabled")
        import threading as _threading

        def _worker():
            try:
                per = _run_cfworker_multi_test(domains)
                btn.after(
                    0,
                    lambda: _show_multi_connectivity_results(
                        "CF Worker", per, label_prefix='DC',
                    ),
                )
            except Exception as exc:
                log.error("CF worker test failed: %s", exc)
            finally:
                btn.after(0, lambda: btn.configure(text="Тест"))
                btn.after(0, _sync_cfworker_test_button)

        _threading.Thread(target=_worker, daemon=True).start()

    ctk.CTkButton(
        cf_worker_input, text="?", width=28, height=32,
        font=(theme.ui_font_family, 14), corner_radius=8,
        fg_color=theme.tg_blue, hover_color=theme.tg_blue_hover,
        text_color="#ffffff", border_width=1, border_color=theme.field_border,
        command=lambda: webbrowser.open(_CFWORKER_HELP_URL),
    ).pack(side="right")

    _cfworker_test_widget = ctk.CTkButton(
        cf_worker_input, text="Тест", width=56, height=32,
        font=(theme.ui_font_family, 13), corner_radius=8,
        fg_color=theme.tg_blue, hover_color=theme.tg_blue_hover,
        text_color="#ffffff", border_width=1, border_color=theme.field_border,
        command=_on_cfworker_test,
    )
    _cfworker_test_widget.pack(side="right", padx=(0, 6))
    _cfworker_test_btn[0] = _cfworker_test_widget
    cfproxy_worker_domain_var.trace_add("write", _sync_cfworker_test_button)
    _sync_cfworker_test_button()

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

    ka_frame = ctk.CTkFrame(log_inner, fg_color="transparent")
    ka_frame.pack(fill="x", pady=(8, 0))
    ka_l = _label(ctk, ka_frame, theme, "Keepalive соединений", size=11)
    ka_l.pack(anchor="w", pady=(0, 2))
    keepalive_mode_var = ctk.StringVar(
        value=_KEEPALIVE_FROM_CFG.get(
            str(cfg.get("keepalive_mode",
                        default_config.get("keepalive_mode", "tcp"))).lower(),
            "TCP (рекомендуется)")
    )
    ka_combo = ctk.CTkComboBox(
        ka_frame, values=_KEEPALIVE_OPTIONS, variable=keepalive_mode_var,
        width=_INNER_W, height=32, font=(theme.ui_font_family, 12),
        text_color=theme.text_primary, fg_color=theme.field_bg,
        border_color=theme.field_border, button_color=theme.field_border,
        button_hover_color=theme.text_secondary,
        dropdown_fg_color=theme.field_bg, dropdown_text_color=theme.text_primary,
        dropdown_hover_color=theme.field_border, corner_radius=8,
        state="readonly",
    )
    ka_combo.pack(fill="x")
    attach_tooltip_to_widgets([ka_l, ka_combo, ka_frame], _TIP_KEEPALIVE)

    ka_int_col = ctk.CTkFrame(log_inner, fg_color="transparent")
    ka_int_col.pack(fill="x", pady=(5, 0))
    ka_int_l = _label(ctk, ka_int_col, theme,
                      "Keepalive: интервал, сек (по умолчанию 30)", size=11)
    ka_int_l.pack(anchor="w", pady=(0, 2))
    keepalive_interval_var = ctk.StringVar(
        value=str(cfg.get("keepalive_interval",
                          default_config.get("keepalive_interval", 30)))
    )
    ka_int_e = _entry(
        ctk, ka_int_col, theme, width=_INNER_W, height=32, radius=8,
        textvariable=keepalive_interval_var,
    )
    ka_int_e.pack(fill="x")
    attach_tooltip_to_widgets([ka_int_l, ka_int_e, ka_int_col],
                              _TIP_KEEPALIVE_INTERVAL)

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
        cfproxy_var=cfproxy_var,
        cfproxy_user_domain_var=cfproxy_user_domain_var,
        cfproxy_worker_domain_var=cfproxy_worker_domain_var,
        appearance_var=appearance_var,
        keepalive_mode_var=keepalive_mode_var,
        keepalive_interval_var=keepalive_interval_var,
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
        line.strip()
        for line in widgets.dc_textbox.get("1.0", "end").strip().splitlines()
        if line.strip()
    ]
    try:
        parse_dc_ip_list(lines)
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
    if widgets.cfproxy_var is not None:
        new_cfg["cfproxy"] = bool(widgets.cfproxy_var.get())
    if widgets.cfproxy_user_domain_var is not None:
        new_cfg["cfproxy_user_domain"] = coerce_domain_list(widgets.cfproxy_user_domain_var.get())
    if widgets.cfproxy_worker_domain_var is not None:
        new_cfg["cfproxy_worker_domain"] = coerce_domain_list(widgets.cfproxy_worker_domain_var.get())
    if widgets.appearance_var is not None:
        new_cfg["appearance"] = _APPEARANCE_TO_CFG.get(widgets.appearance_var.get(), "auto")
    if widgets.keepalive_mode_var is not None:
        new_cfg["keepalive_mode"] = _KEEPALIVE_TO_CFG.get(
            widgets.keepalive_mode_var.get(), "tcp")
    if widgets.keepalive_interval_var is not None:
        try:
            new_cfg["keepalive_interval"] = max(
                1.0, float(widgets.keepalive_interval_var.get().strip()))
        except (ValueError, AttributeError):
            new_cfg["keepalive_interval"] = default_config.get(
                "keepalive_interval", 30)
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
    link_host = get_link_host(host)
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
