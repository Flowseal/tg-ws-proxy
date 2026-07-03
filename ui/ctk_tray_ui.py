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
from ui.i18n import (
    label_from_language,
    language_from_label,
    language_option_labels,
    set_language,
    t,
)

log = logging.getLogger('tg-mtproto-proxy')

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
                        results[dc] = first or t("connectivity.no_response")
                    ssock.close()
                raw.close()
        except _socket.timeout:
            results[dc] = t("connectivity.timeout")
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
    total = len(_CFPROXY_TEST_DCS)
    if auto_mode:
        if domain:
            title = t("connectivity.available", title=title_base)
            msg = t("connectivity.auto_ok", title=title_base, ok=len(ok), total=total)
        else:
            title = t("connectivity.unavailable", title=title_base)
            msg = unavailable_message
    else:
        fail = [(dc, v) for dc, v in results.items() if v is not True]
        if len(ok) == total:
            title = t("connectivity.all_ok", title=title_base)
            msg = t("connectivity.all_ok_domain", total=total, domain=domain)
        elif not ok:
            title = t("connectivity.unavailable", title=title_base)
            errors = "\n".join(
                t("connectivity.error_line", prefix=label_prefix, dc=dc, error=v)
                for dc, v in fail
            )
            msg = t("connectivity.none_ok", domain=domain, errors=errors)
        else:
            title = t("connectivity.partial", title=title_base)
            ok_list = ", ".join(f"{label_prefix}{dc}" for dc in ok)
            fail_list = "\n".join(
                t("connectivity.error_line", prefix=label_prefix, dc=dc, error=v)
                for dc, v in fail
            )
            msg = t("connectivity.partial_detail", domain=domain, ok_list=ok_list, fail_list=fail_list)

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
            blocks.append(t("connectivity.multi_all_ok", domain=domain, total=total))
        elif not ok:
            all_ok = False
            blocks.append(t("connectivity.multi_fail", domain=domain))
        else:
            all_ok = False
            any_ok = True
            ok_list = ", ".join(f"{label_prefix}{dc}" for dc in ok)
            fail_list = ", ".join(f"{label_prefix}{dc}" for dc, _ in fail)
            blocks.append(
                t("connectivity.multi_partial", domain=domain, ok_list=ok_list, fail_list=fail_list)
            )

    if all_ok:
        title = t("connectivity.all_ok", title=title_base)
    elif any_ok:
        title = t("connectivity.partial", title=title_base)
    else:
        title = t("connectivity.unavailable", title=title_base)
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

_APPEARANCE_KEYS = ("auto", "light", "dark")
_APPEARANCE_TO_CTK = {"auto": "system", "light": "Light", "dark": "Dark"}


def _appearance_options() -> List[str]:
    return [t(f"appearance.{key}") for key in _APPEARANCE_KEYS]


def _appearance_from_cfg(value: str) -> str:
    if value in _APPEARANCE_KEYS:
        return t(f"appearance.{value}")
    return t("appearance.auto")


def _appearance_to_cfg(label: str) -> str:
    for key in _APPEARANCE_KEYS:
        if t(f"appearance.{key}") == label:
            return key
    return "auto"


def _sync_language_combobox(combo: Any, var: Any, cfg_value: str) -> None:
    combo.configure(values=[label for _, label in language_option_labels()])
    var.set(label_from_language(cfg_value))


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
    try:
        scroll._parent_canvas.configure(yscrollincrement=4)
    except Exception:
        pass
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
    language_var: Optional[Any] = None


def install_tray_config_form(
    ctk: Any,
    frame: Any,
    theme: CtkTheme,
    cfg: dict,
    default_config: dict,
    *,
    show_autostart: bool = False,
    autostart_value: bool = False,
    on_language_change: Optional[Callable[[], None]] = None,
) -> TrayConfigFormWidgets:
    lang_cfg = cfg.get("language", default_config["language"])
    set_language(lang_cfg)

    header = ctk.CTkFrame(frame, fg_color="transparent")
    header.pack(fill="x", pady=(0, 2))
    ctk.CTkLabel(
        header, text=t("settings.title"),
        font=(theme.ui_font_family, 17, "bold"),
        text_color=theme.text_primary, anchor="w",
    ).pack(side="left")
    ctk.CTkLabel(
        header, text=f"v{__version__}",
        font=(theme.ui_font_family, 12),
        text_color=theme.text_secondary, anchor="e",
    ).pack(side="right", padx=(4, 0))

    appearance_var = ctk.StringVar(
        value=_appearance_from_cfg(cfg.get("appearance", "auto"))
    )

    def _on_appearance_change(choice: str) -> None:
        cfg_val = _appearance_to_cfg(choice)
        ctk.set_appearance_mode(_APPEARANCE_TO_CTK[cfg_val])
        cfg["appearance"] = cfg_val

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

    ui_inner = _config_section(ctk, frame, theme, t("section.interface"))
    ui_row = ctk.CTkFrame(ui_inner, fg_color="transparent")
    ui_row.pack(fill="x")

    lang_col = ctk.CTkFrame(ui_row, fg_color="transparent")
    lang_col.pack(side="left", fill="x", expand=True, padx=(0, 8))

    theme_col = ctk.CTkFrame(ui_row, fg_color="transparent")
    theme_col.pack(side="left", fill="x", expand=True, padx=(8, 0))

    language_var = ctk.StringVar(value=label_from_language(lang_cfg))
    _label(ctk, lang_col, theme, t("settings.language"), size=11).pack(
        anchor="w", pady=(0, 2)
    )
    language_combo = ctk.CTkComboBox(
        lang_col,
        values=[label for _, label in language_option_labels()],
        variable=language_var,
        height=32,
        font=(theme.ui_font_family, 12),
        text_color=theme.text_primary,
        fg_color=theme.bg,
        border_color=theme.field_border,
        button_color=theme.field_border,
        button_hover_color=theme.text_secondary,
        dropdown_fg_color=theme.field_bg,
        dropdown_text_color=theme.text_primary,
        dropdown_hover_color=theme.field_border,
        corner_radius=8,
        state="readonly",
    )
    language_combo.pack(fill="x")
    _sync_language_combobox(language_combo, language_var, lang_cfg)

    _label(ctk, theme_col, theme, t("settings.theme"), size=11).pack(
        anchor="w", pady=(0, 2)
    )
    theme_combo = ctk.CTkComboBox(
        theme_col,
        values=_appearance_options(),
        variable=appearance_var,
        height=32,
        font=(theme.ui_font_family, 12),
        text_color=theme.text_primary,
        fg_color=theme.bg,
        border_color=theme.field_border,
        button_color=theme.field_border,
        button_hover_color=theme.text_secondary,
        dropdown_fg_color=theme.field_bg,
        dropdown_text_color=theme.text_primary,
        dropdown_hover_color=theme.field_border,
        corner_radius=8,
        state="readonly",
        command=_on_appearance_change,
    )
    theme_combo.pack(fill="x")

    conn = _config_section(ctk, frame, theme, t("section.mtproto"))

    host_row = ctk.CTkFrame(conn, fg_color="transparent")
    host_row.pack(fill="x")

    host_col, host_var = _labeled_entry(
        ctk, host_row, theme, t("label.host"),
        cfg.get("host", default_config["host"]),
        tip=t("tip.host"), width=160, pack_fill=True,
    )
    host_col.pack(side="left", fill="x", expand=True, padx=(0, 10))

    port_col, port_var = _labeled_entry(
        ctk, host_row, theme, t("label.port"),
        cfg.get("port", default_config["port"]),
        tip=t("tip.port"), width=100,
    )
    port_col.pack(side="left")

    secret_row = ctk.CTkFrame(conn, fg_color="transparent")
    secret_row.pack(fill="x")

    secret_col, secret_var = _labeled_entry(
        ctk, secret_row, theme, t("label.secret"),
        cfg.get("secret", default_config["secret"]),
        tip=t("tip.secret"), width=160, pack_fill=True,
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

    dc_inner = _config_section(ctk, frame, theme, t("section.dc"))
    dc_lbl = _label(ctk, dc_inner, theme, t("label.dc_hint"), size=11)
    dc_lbl.pack(anchor="w", pady=(0, 4))
    dc_textbox = ctk.CTkTextbox(
        dc_inner, width=_INNER_W, height=88,
        font=(theme.mono_font_family, 12), corner_radius=10,
        fg_color=theme.bg, border_color=theme.field_border,
        border_width=1, text_color=theme.text_primary,
    )
    dc_textbox.pack(fill="x")
    dc_textbox.insert("1.0", "\n".join(cfg.get("dc_ip", default_config["dc_ip"])))
    attach_tooltip_to_widgets([dc_lbl, dc_textbox], t("tip.dc"))

    cf_inner = _config_section(ctk, frame, theme, t("section.cfproxy"))

    cf_row = ctk.CTkFrame(cf_inner, fg_color="transparent")
    cf_row.pack(fill="x", pady=(0, 4))

    cfproxy_var = ctk.BooleanVar(
        value=cfg.get("cfproxy", default_config.get("cfproxy", True))
    )
    cf_cb = _checkbox(ctk, cf_row, theme, t("label.cf_enable"), cfproxy_var)
    cf_cb.pack(side="left", padx=(0, 16))
    attach_ctk_tooltip(cf_cb, t("tip.cfproxy"))

    _cf_test_btn = [None]

    def _on_cf_test():
        user_domains = (
            coerce_domain_list(cfproxy_user_domain_var.get())
            if cf_custom_cb_var.get() else []
        )
        btn = _cf_test_btn[0]
        if btn:
            btn.configure(text=t("button.test_loading"), state="disabled")
        import threading as _threading
        if user_domains:
            def _worker():
                try:
                    per = _run_cfproxy_multi_test(user_domains)
                    if btn:
                        btn.after(
                            0,
                            lambda: _show_multi_connectivity_results(
                                t("connectivity.cfproxy_title"), per, label_prefix='kws',
                            ),
                        )
                except Exception as exc:
                    log.error("CF proxy test failed: %s", exc)
                finally:
                    if btn:
                        btn.after(0, lambda: btn.configure(text=t("button.test"), state="normal"))
            _threading.Thread(target=_worker, daemon=True).start()
        else:
            def _worker_auto():
                try:
                    ok_domain, res = _run_cfproxy_auto_test(balancer.domains)
                    if btn:
                        btn.after(
                            0,
                            lambda: _show_connectivity_results(
                                t("connectivity.cfproxy_title"), res,
                                domain=ok_domain or '',
                                auto_mode=True,
                                unavailable_message=t("connectivity.cf_auto_fail"),
                            ),
                        )
                except Exception as exc:
                    log.error("CF proxy auto-test failed: %s", exc)
                finally:
                    if btn:
                        btn.after(0, lambda: btn.configure(text=t("button.test"), state="normal"))
            _threading.Thread(target=_worker_auto, daemon=True).start()

    _cf_test_widget = ctk.CTkButton(
        cf_row, text=t("button.test"), width=56, height=28,
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
    cf_custom_cb = _checkbox(ctk, cf_custom_row, theme, t("label.cf_custom_domain"), cf_custom_cb_var)
    cf_custom_cb.pack(side="left", padx=(0, 10))
    attach_ctk_tooltip(cf_custom_cb, t("tip.cfproxy_user_domain_cb"))

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
    attach_ctk_tooltip(cf_domain_entry, t("tip.cfproxy_domain"))

    def _sync_domain_entry(*_):
        state = "normal" if cf_custom_cb_var.get() else "disabled"
        cf_domain_entry.configure(state=state)
        if not cf_custom_cb_var.get():
            cfproxy_user_domain_var.set("")

    cf_custom_cb_var.trace_add("write", _sync_domain_entry)
    _sync_domain_entry()

    cf_worker_inner = _config_section(ctk, frame, theme, t("section.cfworker"))

    cf_worker_row = ctk.CTkFrame(cf_worker_inner, fg_color="transparent")
    cf_worker_row.pack(fill="x", pady=(0, 4))
    cf_worker_lbl = _label(ctk, cf_worker_row, theme, t("label.cfworker_domains"), size=11)
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
    attach_tooltip_to_widgets([cf_worker_lbl, cf_worker_entry], t("tip.cfworker_domain"))

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
        btn.configure(text=t("button.test_loading"), state="disabled")
        import threading as _threading

        def _worker():
            try:
                per = _run_cfworker_multi_test(domains)
                btn.after(
                    0,
                    lambda: _show_multi_connectivity_results(
                        t("connectivity.cfworker_title"), per, label_prefix='DC',
                    ),
                )
            except Exception as exc:
                log.error("CF worker test failed: %s", exc)
            finally:
                btn.after(0, lambda: btn.configure(text=t("button.test")))
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
        cf_worker_input, text=t("button.test"), width=56, height=32,
        font=(theme.ui_font_family, 13), corner_radius=8,
        fg_color=theme.tg_blue, hover_color=theme.tg_blue_hover,
        text_color="#ffffff", border_width=1, border_color=theme.field_border,
        command=_on_cfworker_test,
    )
    _cfworker_test_widget.pack(side="right", padx=(0, 6))
    _cfworker_test_btn[0] = _cfworker_test_widget
    cfproxy_worker_domain_var.trace_add("write", _sync_cfworker_test_button)
    _sync_cfworker_test_button()

    log_inner = _config_section(ctk, frame, theme, t("section.logs"))

    verbose_var = ctk.BooleanVar(value=cfg.get("verbose", False))
    verbose_cb = _checkbox(ctk, log_inner, theme, t("label.verbose"), verbose_var)
    verbose_cb.pack(anchor="w", pady=(0, 6))
    attach_ctk_tooltip(verbose_cb, t("tip.verbose"))

    adv_frame = ctk.CTkFrame(log_inner, fg_color="transparent")
    adv_frame.pack(fill="x")

    adv_rows = [
        (t("label.buf_kb"), "buf_kb", t("tip.buf_kb")),
        (t("label.pool_size"), "pool_size", t("tip.pool")),
        (t("label.log_max_mb"), "log_max_mb", t("tip.log_mb")),
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

    upd_inner = _config_section(ctk, frame, theme, t("section.updates"))
    st = get_status()
    check_updates_var = ctk.BooleanVar(
        value=bool(cfg.get("check_updates", default_config.get("check_updates", True)))
    )
    upd_cb = _checkbox(ctk, upd_inner, theme, t("label.check_updates"), check_updates_var)
    upd_cb.pack(anchor="w", pady=(0, 6))
    attach_ctk_tooltip(upd_cb, t("tip.check_updates"))

    if st.get("error"):
        upd_status = t("updates.status_error")
    elif not st.get("checked"):
        upd_status = t("updates.status_pending")
    elif st.get("has_update") and st.get("latest"):
        upd_status = t("updates.status_available", latest=st["latest"], current=__version__)
    elif st.get("ahead_of_release") and st.get("latest"):
        upd_status = t("updates.status_ahead", current=__version__, latest=st["latest"])
    else:
        upd_status = t("updates.status_latest")

    _label(ctk, upd_inner, theme, upd_status, size=11,
           justify="left", wraplength=_INNER_W).pack(anchor="w", pady=(0, 8))

    rel_url = (st.get("html_url") or "").strip() or RELEASES_PAGE_URL
    ctk.CTkButton(
        upd_inner, text=t("button.open_release"), height=32,
        font=(theme.ui_font_family, 13), corner_radius=8,
        fg_color=theme.field_bg, hover_color=theme.field_border,
        text_color=theme.text_primary, border_width=1,
        border_color=theme.field_border,
        command=lambda u=rel_url: webbrowser.open(u),
    ).pack(anchor="w")

    autostart_var = None
    if show_autostart:
        sys_inner = _config_section(ctk, frame, theme, t("section.windows_startup"), bottom_spacer=4)
        autostart_var = ctk.BooleanVar(value=autostart_value)
        as_cb = _checkbox(ctk, sys_inner, theme, t("label.autostart"), autostart_var)
        as_cb.pack(anchor="w", pady=(0, 4))
        as_hint = _label(
            ctk, sys_inner, theme,
            t("label.autostart_hint"),
            size=11, justify="left", wraplength=_INNER_W,
        )
        as_hint.pack(anchor="w")
        attach_tooltip_to_widgets([as_cb, as_hint], t("tip.autostart"))

    return TrayConfigFormWidgets(
        host_var=host_var, port_var=port_var, secret_var=secret_var,
        dc_textbox=dc_textbox, verbose_var=verbose_var,
        adv_entries=adv_entries, adv_keys=adv_keys,
        autostart_var=autostart_var, check_updates_var=check_updates_var,
        cfproxy_var=cfproxy_var,
        cfproxy_user_domain_var=cfproxy_user_domain_var,
        cfproxy_worker_domain_var=cfproxy_worker_domain_var,
        appearance_var=appearance_var,
        language_var=language_var,
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


def _dc_validation_message(error: ValueError) -> str:
    exc_entry = getattr(error, "entry", None)
    if exc_entry is None:
        return str(error)
    kind = getattr(error, "kind", "invalid")
    if kind == "format":
        return t("validation.dc_format", entry=exc_entry)
    return t("validation.dc_invalid", entry=exc_entry)


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
        return t("validation.bad_host")

    try:
        port_val = int(widgets.port_var.get().strip())
        if not (1 <= port_val <= 65535):
            raise ValueError
    except ValueError:
        return t("validation.bad_port")

    lines = [
        line.strip()
        for line in widgets.dc_textbox.get("1.0", "end").strip().splitlines()
        if line.strip()
    ]
    try:
        parse_dc_ip_list(lines)
    except ValueError as e:
        return _dc_validation_message(e)

    secret_val = widgets.secret_var.get().strip()
    if len(secret_val) != 32:
        return t("validation.bad_secret_len")
    try:
        bytes.fromhex(secret_val)
    except ValueError:
        return t("validation.bad_secret_hex")

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
        new_cfg["appearance"] = _appearance_to_cfg(widgets.appearance_var.get())
    if widgets.language_var is not None:
        new_cfg["language"] = language_from_label(widgets.language_var.get()).value
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
        btn_frame, text=t("button.save"), height=38,
        font=(theme.ui_font_family, 14, "bold"), corner_radius=10,
        fg_color=theme.tg_blue, hover_color=theme.tg_blue_hover,
        text_color="#ffffff",
        command=on_save)
    save_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
    attach_ctk_tooltip(save_btn, t("tip.save"))
    cancel_btn = ctk.CTkButton(
        btn_frame, text=t("button.cancel"), height=38,
        font=(theme.ui_font_family, 14), corner_radius=10,
        fg_color=theme.field_bg, hover_color=theme.field_border,
        text_color=theme.text_primary, border_width=1,
        border_color=theme.field_border,
        command=on_cancel)
    cancel_btn.pack(side="right", fill="x", expand=True)
    attach_ctk_tooltip(cancel_btn, t("tip.cancel"))


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

    ctk.CTkLabel(title_frame, text=t("first_run.title"),
                 font=(theme.ui_font_family, 17, "bold"),
                 text_color=theme.text_primary).pack(side="left")

    sections = [
        (t("first_run.how_to"), True),
        (t("first_run.auto"), True),
        (t("first_run.auto_hint"), False),
        (t("first_run.auto_link", url=tg_url), False),
        ("\n" + t("first_run.manual"), True),
        (t("first_run.manual_path"), False),
        (t("first_run.manual_mtproto", host=link_host, port=port), False),
        (t("first_run.manual_secret", secret=secret), False),
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
    _checkbox(ctk, frame, theme, t("first_run.open_now"),
              auto_var).pack(anchor="w", pady=(0, 16))

    def on_ok():
        on_done(auto_var.get())

    ctk.CTkButton(frame, text=t("button.start"), width=180, height=42,
                  font=(theme.ui_font_family, 15, "bold"), corner_radius=10,
                  fg_color=theme.tg_blue, hover_color=theme.tg_blue_hover,
                  text_color="#ffffff",
                  command=on_ok).pack(pady=(0, 0))

    root.protocol("WM_DELETE_WINDOW", on_ok)
