from __future__ import annotations

import sys
from typing import Any, Callable, Optional, Tuple

from proxy import get_link_host
from ui.ctk_controls import create_button, create_checkbox, create_label, icon
from ui.ctk_theme import (
    FIRST_RUN_FRAME_PAD, CtkTheme, create_ctk_toplevel, fade_destroy, main_content_frame, toast,
)
from ui.ctk_tooltip import attach_ctk_tooltip
from ui.i18n import t
from ui.settings_form import (
    TrayConfigFormWidgets as TrayConfigFormWidgets,
    install_tray_config_form as install_tray_config_form,
    validate_config_form as validate_config_form,
)


def tray_settings_scroll_and_footer(
    ctk: Any,
    content_parent: Any,
    theme: CtkTheme,
) -> Tuple[Any, Any]:
    footer = ctk.CTkFrame(content_parent, fg_color=theme.bg)
    footer.pack(side="bottom", fill="x", padx=6, pady=(6, 6))
    scroll = ctk.CTkScrollableFrame(
        content_parent,
        fg_color=theme.bg,
        corner_radius=0,
        scrollbar_button_color=theme.field_border,
        scrollbar_button_hover_color=theme.text_secondary,
    )
    scroll.pack(fill="both", expand=True)
    if sys.platform == "win32":
        scroll._parent_canvas.configure(yscrollincrement=round(3 * scroll._get_widget_scaling()))
    return scroll, footer


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
    ).pack(fill="x", pady=(0, 12))
    btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
    btn_frame.pack(fill="x")
    save_btn = create_button(ctk, btn_frame, theme, "button.save", height=38, command=on_save)
    save_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
    attach_ctk_tooltip(save_btn, "tip.save")
    cancel_btn = create_button(ctk, btn_frame, theme, "button.cancel", "secondary", height=38, command=on_cancel)
    cancel_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))
    attach_ctk_tooltip(cancel_btn, "tip.cancel")


def show_relink_dialog(
    ctk: Any,
    theme: CtkTheme,
    link: str,
    *,
    on_open: Callable[[], None],
    icon_path: Optional[str] = None,
    after_create: Optional[Callable[[Any], None]] = None,
) -> Any:
    root = create_ctk_toplevel(
        ctk, title=t("relink.title"), width=440, height=250, theme=theme,
        icon=icon_path, after_create=after_create,
    )
    box = ctk.CTkFrame(root, fg_color="transparent")
    box.pack(fill="both", expand=True, padx=20, pady=16)
    create_label(ctk, box, theme, "relink.title", size=16, bold=True, secondary=False).pack(fill="x")
    create_label(ctk, box, theme, "relink.body", justify="left", wraplength=390).pack(fill="x", pady=(4, 12))

    def copy() -> None:
        root.clipboard_clear()
        root.clipboard_append(link)
        toast(root, t("relink.copied"), theme, image=icon("check", 14, ("#ffffff", "#ffffff")), bottom=14)

    for key, hint, kind, command in (
        ("relink.open", "relink.open_hint", "primary", on_open),
        ("relink.copy", "relink.copy_hint", "secondary", copy),
    ):
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", pady=3)
        create_button(ctk, row, theme, key, kind, width=180, command=command).pack(side="left")
        create_label(ctk, row, theme, hint).pack(side="left", padx=12)

    create_button(ctk, box, theme, "button.done", "secondary", width=100,
                  command=lambda: fade_destroy(root)).pack(side="right", pady=(10, 0))
    root.protocol("WM_DELETE_WINDOW", lambda: fade_destroy(root))
    return root


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

    create_label(ctk, title_frame, theme, "first_run.title", size=17, bold=True,
                 secondary=False).pack(side="left")

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
    create_checkbox(ctk, frame, theme, "first_run.open_now", auto_var).pack(anchor="w", pady=(0, 16))

    def on_ok():
        on_done(auto_var.get())

    create_button(ctk, frame, theme, "button.start", width=180, height=42, command=on_ok).pack()

    root.protocol("WM_DELETE_WINDOW", on_ok)
