from __future__ import annotations

import os
import threading
import webbrowser
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Union

from proxy import __version__, coerce_domain_list, parse_dc_ip_list
from proxy.balancer import balancer
from utils.update_check import RELEASES_PAGE_URL, get_status, run_check
from ui.background_task import BackgroundTask
from ui.connectivity import (
    run_cfproxy_multi_test, run_cfproxy_auto_test, run_cfworker_multi_test,
    show_connectivity_results, show_multi_connectivity_results,
)
from ui.ctk_controls import (
    create_button, create_checkbox, create_config_section, create_entry, create_icon_button,
    create_label, create_labeled_entry, create_segmented, icon, on_relabel, relabel, spin,
)
from ui.ctk_theme import CtkTheme, crossfade
from ui.ctk_tooltip import attach_ctk_tooltip, attach_tooltip_to_widgets
from ui.settings import validate_settings
from ui.i18n import (
    label_from_language, language_from_label, language_option_labels,
    set_language, get_language, t,
)

_APPEARANCE_KEYS = ("auto", "light", "dark")
_APPEARANCE_TO_CTK = {"auto": "system", "light": "Light", "dark": "Dark"}


def _get_doc_url(doc_name: str) -> str:
    lang = get_language().value
    lang_folder = "EN" if lang == "en" else "RU"
    return f"https://github.com/Flowseal/tg-ws-proxy/blob/main/docs/{lang_folder}/{doc_name}.md"


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


@dataclass
class TrayConfigFormWidgets:
    host_var: Any
    port_var: Any
    secret_var: Any
    dc_textbox: Any
    verbose_var: Any
    no_secure_var: Any
    advanced_vars: Dict[str, Any]
    autostart_var: Optional[Any]
    check_updates_var: Optional[Any]
    cfproxy_var: Optional[Any] = None
    h2_var: Optional[Any] = None
    cfproxy_user_domain_enabled_var: Optional[Any] = None
    cfproxy_user_domain_var: Optional[Any] = None
    cfproxy_worker_enabled_var: Optional[Any] = None
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
    on_update_click: Optional[Callable[[], None]] = None,
) -> TrayConfigFormWidgets:
    set_language(cfg.get("language", default_config["language"]))
    no_secure_var = ctk.BooleanVar(master=frame, value=cfg.get("no_secure", False))

    _create_header(ctk, frame, theme)
    appearance_var, language_var = _create_interface(ctk, frame, theme, cfg, default_config)
    host_var, port_var, secret_var = _create_connection(ctk, frame, theme, cfg, default_config)
    dc_textbox = _create_dc(ctk, frame, theme, cfg, default_config)
    cfproxy_var, h2_var, cf_custom_cb_var, cfproxy_user_domain_var = _create_cfproxy(
        ctk, frame, theme, cfg, default_config, no_secure_var,
    )
    cfproxy_worker_enabled_var, cfproxy_worker_domain_var = _create_cfworker(
        ctk, frame, theme, cfg, default_config, no_secure_var,
    )
    verbose_var, advanced_vars = _create_logging(
        ctk, frame, theme, cfg, default_config, no_secure_var,
    )
    check_updates_var = _create_updates(ctk, frame, theme, cfg, default_config, on_update_click)
    autostart_var = _create_autostart(ctk, frame, theme, show_autostart, autostart_value)
    ctk.CTkFrame(frame, fg_color="transparent", height=10).pack()

    return TrayConfigFormWidgets(
        host_var=host_var, port_var=port_var, secret_var=secret_var,
        dc_textbox=dc_textbox, verbose_var=verbose_var, no_secure_var=no_secure_var,
        advanced_vars=advanced_vars,
        autostart_var=autostart_var, check_updates_var=check_updates_var,
        cfproxy_var=cfproxy_var,
        h2_var=h2_var,
        cfproxy_user_domain_enabled_var=cf_custom_cb_var,
        cfproxy_user_domain_var=cfproxy_user_domain_var,
        cfproxy_worker_enabled_var=cfproxy_worker_enabled_var,
        cfproxy_worker_domain_var=cfproxy_worker_domain_var,
        appearance_var=appearance_var,
        language_var=language_var,
    )


def _create_header(ctk, frame, theme):
    header = ctk.CTkFrame(frame, fg_color="transparent")
    header.pack(fill="x", padx=6, pady=(4, 0))
    create_label(ctk, header, theme, "settings.title", size=20, bold=True, secondary=False).pack(side="left")
    ctk.CTkLabel(
        header, text=f"v{__version__}", font=(theme.ui_font_family, 12),
        text_color=theme.text_secondary,
    ).pack(side="left", padx=(8, 0), pady=(6, 0))
    create_button(
        ctk, header, theme, "button.donate", "donate", width=96, height=28,
        image=icon("heart", 13, ("#ffffff", "#ffffff")),
        command=lambda: webbrowser.open(_get_doc_url("Funding")),
    ).pack(side="right")


def _setting_row(ctk, parent, theme, key):
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=3)
    create_label(ctk, row, theme, key, size=13, secondary=False).pack(side="left")
    return row


def _create_interface(ctk, frame, theme, cfg, default_config):
    top = frame.winfo_toplevel()
    inner = create_config_section(ctk, frame, theme, "section.interface")
    appearance = {"key": cfg.get("appearance", "auto")}
    if appearance["key"] not in _APPEARANCE_KEYS:
        appearance["key"] = "auto"
    language_var = ctk.StringVar(
        master=frame, value=label_from_language(cfg.get("language", default_config["language"]))
    )
    appearance_var = ctk.StringVar(master=frame, value=_appearance_from_cfg(appearance["key"]))

    def on_language(label: str) -> None:
        crossfade(top, lambda: (set_language(language_from_label(label)), relabel(top)))

    def on_appearance(label: str) -> None:
        appearance["key"] = _appearance_to_cfg(label)
        crossfade(top, lambda: (ctk.set_appearance_mode(_APPEARANCE_TO_CTK[appearance["key"]]), relabel(top)))

    create_segmented(
        ctk, _setting_row(ctk, inner, theme, "settings.language"), theme, variable=language_var,
        values=[label for _, label in language_option_labels()], command=on_language,
    ).pack(side="right")
    theme_seg = create_segmented(
        ctk, _setting_row(ctk, inner, theme, "settings.theme"), theme, variable=appearance_var,
        values=_appearance_options(), command=on_appearance,
    )
    theme_seg.pack(side="right")
    on_relabel(theme_seg, lambda: (
        theme_seg.configure(values=_appearance_options()),
        appearance_var.set(t(f"appearance.{appearance['key']}")),
    ))
    return appearance_var, language_var


def _create_connection(ctk, frame, theme, cfg, default_config):
    conn = create_config_section(ctk, frame, theme, "section.mtproto")
    conn.grid_columnconfigure(0, weight=1)

    host_lbl = create_label(ctk, conn, theme, "label.host")
    host_lbl.grid(row=0, column=0, sticky="w")
    port_lbl = create_label(ctk, conn, theme, "label.port")
    port_lbl.grid(row=0, column=1, sticky="w", padx=(10, 0))
    host_var = ctk.StringVar(master=frame, value=str(cfg.get("host", default_config["host"])))
    host_entry = create_entry(ctk, conn, theme, var=host_var)
    host_entry.grid(row=1, column=0, sticky="ew")
    port_var = ctk.StringVar(master=frame, value=str(cfg.get("port", default_config["port"])))
    port_entry = create_entry(ctk, conn, theme, var=port_var, width=96)
    port_entry.grid(row=1, column=1, padx=(10, 0))
    attach_tooltip_to_widgets([host_lbl, host_entry], "tip.host")
    attach_tooltip_to_widgets([port_lbl, port_entry], "tip.port")

    secret_lbl = create_label(ctk, conn, theme, "label.secret")
    secret_lbl.grid(row=2, column=0, sticky="w", pady=(8, 0))
    secret_var = ctk.StringVar(master=frame, value=str(cfg.get("secret", default_config["secret"])))
    secret_entry = create_entry(ctk, conn, theme, var=secret_var)
    secret_entry.grid(row=3, column=0, sticky="ew")
    attach_tooltip_to_widgets([secret_lbl, secret_entry], "tip.secret")

    regen = create_button(
        ctk, conn, theme, "button.new_secret", "secondary", width=96,
        image=icon("refresh", 14, theme.text_primary),
        command=lambda: secret_var.set(os.urandom(16).hex()),
    )
    regen.grid(row=3, column=1, padx=(10, 0))
    attach_ctk_tooltip(regen, "tip.new_secret")

    note = create_label(ctk, conn, theme, "label.secret_changed")
    note.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
    saved_secret = secret_var.get().strip()

    def sync_note(*_):
        if secret_var.get().strip() != saved_secret:
            note.grid()
        else:
            note.grid_remove()

    secret_var.trace_add("write", sync_note)
    sync_note()
    return host_var, port_var, secret_var


def _create_dc(ctk, frame, theme, cfg, default_config):
    dc_inner = create_config_section(ctk, frame, theme, "section.dc")
    dc_lbl = create_label(ctk, dc_inner, theme, "label.dc_hint")
    dc_lbl.pack(fill="x", pady=(0, 4))
    dc_textbox = ctk.CTkTextbox(
        dc_inner, height=84, font=(theme.mono_font_family, 12), corner_radius=8,
        fg_color=theme.field_bg, border_color=theme.field_bg,
        border_width=1, text_color=theme.text_primary,
    )
    dc_textbox.pack(fill="x")
    dc_textbox.insert("1.0", "\n".join(cfg.get("dc_ip", default_config["dc_ip"])))
    hint = ctk.CTkLabel(dc_inner, text="", anchor="w", justify="left", wraplength=360,
                        font=(theme.ui_font_family, 12))
    hint.pack(fill="x", pady=(4, 0))
    attach_tooltip_to_widgets([dc_lbl, dc_textbox], "tip.dc")

    def check(*_):
        box = dc_textbox._textbox
        box.tag_remove("bad", "1.0", "end")
        bad = []
        for n, line in enumerate(dc_textbox.get("1.0", "end").splitlines(), 1):
            if not line.strip():
                continue
            try:
                parse_dc_ip_list([line.strip()])
            except ValueError:
                bad.append(n)
                box.tag_add("bad", f"{n}.0", f"{n}.end")
        box.tag_configure("bad", background=("#fde7e7", "#4a2626")[ctk.get_appearance_mode() == "Dark"])
        if bad:
            hint.configure(text=t("dc.bad_line", n=bad[0]), text_color=theme.error)
        else:
            hint.configure(text=t("dc.example"), text_color=theme.text_secondary)

    dc_textbox.bind("<KeyRelease>", check, add="+")
    on_relabel(dc_textbox, check)
    check()
    return dc_textbox


def _test_button(ctk, parent, theme):
    return create_button(ctk, parent, theme, "button.test", "secondary", width=72, height=28)


def _run_test(ctk, button, theme, task, work, complete, finish=None):
    if task.running:
        return
    button.configure(state="disabled")
    task.start(work, complete, finish or (lambda: button.configure(state="normal")))
    spin(button, theme, lambda: task.running, lambda: None)


def _create_cfproxy(ctk, frame, theme, cfg, default_config, no_secure_var):
    cf_inner = create_config_section(ctk, frame, theme, "section.cfproxy")

    cf_row = ctk.CTkFrame(cf_inner, fg_color="transparent")
    cf_row.pack(fill="x", pady=(0, 4))
    cfproxy_var = ctk.BooleanVar(
        master=frame, value=cfg.get("cfproxy", default_config.get("cfproxy", True))
    )
    cf_cb = create_checkbox(ctk, cf_row, theme, "label.cf_enable", cfproxy_var)
    cf_cb.pack(side="left")
    attach_ctk_tooltip(cf_cb, "tip.cfproxy")

    cf_test = BackgroundTask(frame.winfo_toplevel())
    cf_test_btn = _test_button(ctk, cf_row, theme)
    cf_test_btn.pack(side="right")

    def on_cf_test():
        secure = not no_secure_var.get()
        user_domains = (
            coerce_domain_list(cfproxy_user_domain_var.get())
            if cf_custom_cb_var.get() else []
        )
        if user_domains:
            _run_test(
                ctk, cf_test_btn, theme, cf_test,
                lambda: run_cfproxy_multi_test(user_domains, secure=secure),
                lambda per: show_multi_connectivity_results(
                    t("connectivity.cfproxy_title"), per, label_prefix="kws",
                ),
            )
        else:
            domains = list(balancer.domains)
            _run_test(
                ctk, cf_test_btn, theme, cf_test,
                lambda: run_cfproxy_auto_test(domains, secure=secure),
                lambda result: show_connectivity_results(
                    t("connectivity.cfproxy_title"), result[1],
                    domain=result[0] or "", auto_mode=True,
                    unavailable_message=t("connectivity.cf_auto_fail"),
                ),
            )

    cf_test_btn.configure(command=on_cf_test)

    h2_var = ctk.BooleanVar(master=frame, value=cfg.get("h2", default_config.get("h2", True)))
    h2_cb = create_checkbox(ctk, cf_inner, theme, "label.h2_enable", h2_var)
    h2_cb.pack(anchor="w", pady=(2, 6))
    attach_ctk_tooltip(h2_cb, "tip.h2")

    def sync_h2(*_):
        enabled = cfproxy_var.get() and not no_secure_var.get() and not cfg.get("force_test_dc", False)
        h2_cb.configure(state="normal" if enabled else "disabled")

    cfproxy_var.trace_add("write", sync_h2)
    no_secure_var.trace_add("write", sync_h2)
    sync_h2()

    saved_user_domains = coerce_domain_list(
        cfg.get("cfproxy_user_domain", default_config.get("cfproxy_user_domain", ""))
    )
    cf_custom_cb_var = ctk.BooleanVar(
        master=frame, value=cfg.get("cfproxy_user_domain_enabled", bool(saved_user_domains))
    )
    cfproxy_user_domain_var = ctk.StringVar(master=frame, value=", ".join(saved_user_domains))
    _domain_row(
        ctk, cf_inner, theme, "label.cf_custom_domain", cf_custom_cb_var, cfproxy_user_domain_var,
        "tip.cfproxy_user_domain_cb", "tip.cfproxy_domain", "CfProxy",
    )
    return cfproxy_var, h2_var, cf_custom_cb_var, cfproxy_user_domain_var


def _domain_row(ctk, parent, theme, cb_key, enabled_var, domain_var, cb_tip, entry_tip, doc):
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x")
    cb = create_checkbox(ctk, row, theme, cb_key, enabled_var)
    cb.pack(side="left")
    attach_ctk_tooltip(cb, cb_tip)
    create_icon_button(
        ctk, row, theme, "help", "tip.open_docs", command=lambda: webbrowser.open(_get_doc_url(doc)),
    ).pack(side="right")
    entry = create_entry(ctk, parent, theme, var=domain_var, height=32)
    entry.pack(fill="x", pady=(4, 0))
    attach_ctk_tooltip(entry, entry_tip)

    def sync(*_):
        on = enabled_var.get()
        entry.configure(state="normal" if on else "disabled",
                        text_color=theme.text_primary if on else theme.text_secondary)

    enabled_var.trace_add("write", sync)
    sync()


def _create_cfworker(ctk, frame, theme, cfg, default_config, no_secure_var):
    inner = create_config_section(ctk, frame, theme, "section.cfworker")

    head = ctk.CTkFrame(inner, fg_color="transparent")
    head.pack(fill="x", pady=(0, 4))
    head_lbl = create_label(ctk, head, theme, "label.cfworker_domains")
    head_lbl.pack(side="left")
    attach_ctk_tooltip(head_lbl, "tip.cfworker_domain")
    test_btn = _test_button(ctk, head, theme)
    test_btn.pack(side="right")

    saved_worker_domains = coerce_domain_list(
        cfg.get("cfproxy_worker_domain", default_config.get("cfproxy_worker_domain", ""))
    )
    enabled_var = ctk.BooleanVar(
        master=frame, value=cfg.get("cfproxy_worker_enabled", bool(saved_worker_domains))
    )
    domain_var = ctk.StringVar(master=frame, value=", ".join(saved_worker_domains))
    _domain_row(
        ctk, inner, theme, "label.cf_custom_domain", enabled_var, domain_var,
        "tip.cfworker_domain", "tip.cfworker_domain", "CfWorker",
    )

    worker_test = BackgroundTask(frame.winfo_toplevel())

    def sync_test(*_):
        ready = enabled_var.get() and bool(coerce_domain_list(domain_var.get()))
        if not worker_test.running:
            test_btn.configure(state="normal" if ready else "disabled")

    def on_test():
        domains = coerce_domain_list(domain_var.get())
        if not enabled_var.get() or not domains:
            return
        secure = not no_secure_var.get()
        _run_test(
            ctk, test_btn, theme, worker_test,
            lambda: run_cfworker_multi_test(domains, secure=secure),
            lambda per: show_multi_connectivity_results(
                t("connectivity.cfworker_title"), per, label_prefix="DC",
            ),
            sync_test,
        )

    test_btn.configure(command=on_test)
    enabled_var.trace_add("write", sync_test)
    domain_var.trace_add("write", sync_test)
    sync_test()
    return enabled_var, domain_var


def _create_logging(ctk, frame, theme, cfg, default_config, no_secure_var):
    log_inner = create_config_section(ctk, frame, theme, "section.logs")

    verbose_var = ctk.BooleanVar(master=frame, value=cfg.get("verbose", False))
    verbose_cb = create_checkbox(ctk, log_inner, theme, "label.verbose", verbose_var)
    verbose_cb.pack(anchor="w", pady=(0, 4))
    attach_ctk_tooltip(verbose_cb, "tip.verbose")

    no_secure_cb = create_checkbox(ctk, log_inner, theme, "label.no_secure", no_secure_var)
    no_secure_cb.pack(anchor="w", pady=(0, 6))
    attach_ctk_tooltip(no_secure_cb, "tip.no_secure")

    advanced_vars = {}
    for label_key, key, tip_key in (
        ("label.buf_kb", "buf_kb", "tip.buf_kb"),
        ("label.pool_size", "pool_size", "tip.pool"),
        ("label.log_max_mb", "log_max_mb", "tip.log_mb"),
    ):
        col, advanced_vars[key] = create_labeled_entry(
            ctk, log_inner, theme, label_key, cfg.get(key, default_config[key]), tip_key=tip_key,
        )
        col.pack(fill="x", pady=(0, 0 if key == "log_max_mb" else 6))
    return verbose_var, advanced_vars


def _create_updates(ctk, frame, theme, cfg, default_config, on_update_click):
    upd_inner = create_config_section(ctk, frame, theme, "section.updates")
    check_updates_var = ctk.BooleanVar(
        master=frame, value=bool(cfg.get("check_updates", default_config.get("check_updates", True)))
    )
    upd_cb = create_checkbox(ctk, upd_inner, theme, "label.check_updates", check_updates_var)
    upd_cb.pack(anchor="w", pady=(0, 6))
    attach_ctk_tooltip(upd_cb, "tip.check_updates")

    row = ctk.CTkFrame(upd_inner, fg_color="transparent")
    row.pack(fill="x")
    check_btn = create_button(ctk, row, theme, "button.check_updates", "secondary", width=110, height=30)
    check_btn.pack(side="right")
    status = ctk.CTkLabel(row, text="", anchor="w", justify="left", compound="left",
                          wraplength=240, font=(theme.ui_font_family, 12))
    status.pack(side="left", fill="x", expand=True, padx=(0, 8))

    def open_update():
        if on_update_click is not None:
            on_update_click()
        else:
            webbrowser.open((get_status().get("html_url") or "").strip() or RELEASES_PAGE_URL)

    action = create_button(
        ctk, upd_inner, theme, "button.update" if on_update_click else "button.open_release",
        height=32, command=open_update,
    )

    def render():
        st = get_status()
        has_update = bool(st.get("has_update") and st.get("latest"))
        if st.get("checking"):
            text, color, image = t("updates.status_checking"), theme.text_secondary, None
        elif st.get("error"):
            text, color, image = t("updates.status_error"), theme.error, icon("warning", 14, theme.error)
        elif not st.get("checked"):
            text, color, image = t("updates.status_pending"), theme.text_secondary, None
        elif has_update:
            text = t("updates.status_available", latest=st["latest"], current=__version__)
            color, image = theme.tg_blue, icon("download", 14, theme.tg_blue)
        elif st.get("ahead_of_release") and st.get("latest"):
            text = t("updates.status_ahead", current=__version__, latest=st["latest"])
            color, image = theme.text_secondary, None
        else:
            text, color, image = t("updates.status_latest"), theme.text_secondary, icon("check", 14, theme.ok)
        status.configure(text=(" " if image else "") + text, text_color=color, image=image or "")
        if has_update:
            action.pack(fill="x", pady=(8, 0))
        else:
            action.pack_forget()

    def poll():
        if status.winfo_exists():
            render()
            status.after(500, poll)

    def check_now():
        worker = threading.Thread(target=lambda: run_check(__version__, force=True),
                                  daemon=True, name="update-check-manual")
        worker.start()
        check_btn.configure(state="disabled")
        spin(check_btn, theme, worker.is_alive, lambda: (check_btn.configure(state="normal"), render()))

    check_btn.configure(command=check_now)
    on_relabel(status, render)
    poll()
    return check_updates_var


def _create_autostart(ctk, frame, theme, show_autostart, autostart_value):
    if not show_autostart:
        return None
    sys_inner = create_config_section(ctk, frame, theme, "section.windows_startup")
    autostart_var = ctk.BooleanVar(master=frame, value=autostart_value)
    as_cb = create_checkbox(ctk, sys_inner, theme, "label.autostart", autostart_var)
    as_cb.pack(anchor="w", pady=(0, 4))
    as_hint = create_label(ctk, sys_inner, theme, "label.autostart_hint", justify="left", wraplength=360)
    as_hint.pack(fill="x")
    attach_tooltip_to_widgets([as_cb, as_hint], "tip.autostart")
    return autostart_var


def validate_config_form(
    widgets: TrayConfigFormWidgets,
    default_config: dict,
    *,
    include_autostart: bool,
) -> Union[dict, str]:
    values = {
        "host": widgets.host_var.get(),
        "port": widgets.port_var.get(),
        "secret": widgets.secret_var.get(),
        "dc_ip": widgets.dc_textbox.get("1.0", "end"),
        "verbose": bool(widgets.verbose_var.get()),
    }
    for key, var in widgets.advanced_vars.items():
        values[key] = var.get()
    for key in (
        "check_updates", "cfproxy", "h2", "cfproxy_user_domain_enabled",
        "cfproxy_worker_enabled", "no_secure",
    ):
        var = getattr(widgets, f"{key}_var")
        if var is not None:
            values[key] = bool(var.get())
    for key in ("cfproxy_user_domain", "cfproxy_worker_domain"):
        var = getattr(widgets, f"{key}_var")
        if var is not None:
            values[key] = var.get()
    if include_autostart:
        values["autostart"] = (
            bool(widgets.autostart_var.get())
            if widgets.autostart_var is not None else False
        )
    if widgets.appearance_var is not None:
        values["appearance"] = _appearance_to_cfg(widgets.appearance_var.get())
    if widgets.language_var is not None:
        values["language"] = language_from_label(widgets.language_var.get()).value
    return validate_settings(values, default_config)
