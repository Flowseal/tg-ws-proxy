import io
import base64
import tkinter as tk
from enum import Enum
from typing import Any, Optional, Sequence, Tuple

from ui.ctk_theme import CtkTheme, center_ctk_geometry, resolve_ctk_color

class DialogKind(str, Enum):
    INFO = "info"
    ERROR = "error"
    QUESTION = "question"

def _clear_low_alpha(image: Any, threshold: int = 72) -> Any:
    alpha = image.getchannel("A")
    mask = alpha.point(lambda value: 0 if value < threshold else value)
    image.putalpha(mask)
    return image

def _modern_dialog_icon(kind: DialogKind, size: int = 40):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None

    scale = 4
    canvas = size * scale
    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fill = (217, 52, 52, 255) if kind == DialogKind.ERROR else (51, 144, 236, 255)
    pad = max(4, int(canvas * 0.08))
    draw.ellipse((pad, pad, canvas - pad - 1, canvas - pad - 1), fill=fill)
    text = "?" if kind == DialogKind.QUESTION else "i" if kind == DialogKind.INFO else "!"
    try:
        font_name = "segoeuib.ttf" if kind == DialogKind.ERROR else "segoeui.ttf"
        font = ImageFont.truetype(font_name, int(canvas * 0.68))
    except Exception:
        font = ImageFont.load_default()
        
    if kind == DialogKind.ERROR:
        line = max(6, int(canvas * 0.13))
        inset = int(canvas * 0.31)
        draw.line((inset, inset, canvas - inset, canvas - inset), fill=(255, 255, 255, 255), width=line)
        draw.line((canvas - inset, inset, inset, canvas - inset), fill=(255, 255, 255, 255), width=line)
    else:
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (canvas - (bbox[2] - bbox[0])) / 2 - bbox[0]
        y = (canvas - (bbox[3] - bbox[1])) / 2 - bbox[1] + canvas * 0.01
        draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
        
    return _clear_low_alpha(img.resize((size, size), Image.Resampling.LANCZOS), 48)

def _photo_from_image(image: Any) -> Any:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = base64.b64encode(buffer.getvalue()).decode("ascii")
    return tk.PhotoImage(data=data)


def _build_dialog_base(
    ctk: Any,
    parent: Optional[Any],
    theme: CtkTheme,
    title: str,
    message: str,
    kind: DialogKind,
    icon_path: Optional[str]
) -> Tuple[Any, Any, Any, float]:
    body_bg = resolve_ctk_color(ctk, theme.bg)
    text_color = theme.text_primary

    root = ctk.CTkToplevel(parent)
    root.withdraw()
    root.title(title)
    root.configure(fg_color=body_bg)
    
    if parent is not None:
        try:
            root.transient(parent)
        except Exception:
            pass
            
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    if icon_path:
        try:
            root.after(300, lambda: root.iconbitmap(icon_path))
        except Exception:
            pass

    body = ctk.CTkFrame(root, fg_color=body_bg, corner_radius=0)
    body.pack(fill="both", expand=True, padx=20, pady=16)
    
    try:
        scaling = ctk.ScalingTracker.get_window_scaling(root)
    except Exception:
        scaling = root._get_window_scaling() if hasattr(root, "_get_window_scaling") else 1.0
    icon_size_scaled = int(40 * scaling)

    pil_icon = _modern_dialog_icon(kind, size=icon_size_scaled)
    if pil_icon is not None:
        tk_icon = _photo_from_image(pil_icon)
        icon_label = tk.Label(
            body,
            text="",
            image=tk_icon,
            width=icon_size_scaled,
            height=icon_size_scaled,
            bg=body_bg,
            bd=0,
            highlightthickness=0
        )
        icon_label.image = tk_icon # keep reference
        icon_label.pack(side="left", anchor="n", padx=(0, int(14 * scaling)))
    else:
        icon_label = None
    
    content_frame = ctk.CTkFrame(body, fg_color="transparent")
    content_frame.pack(side="left", fill="both", expand=True)

    text_label = ctk.CTkLabel(
        content_frame,
        text=message,
        justify="left",
        anchor="nw",
        font=(theme.ui_font_family, 13),
        text_color=text_color,
        wraplength=int(350 * scaling),
    )
    text_label.pack(fill="x")
    
    # Dynamic Alignment Logic
    dummy = ctk.CTkLabel(content_frame, text="A", font=(theme.ui_font_family, 13))
    dummy.pack()
    root.update_idletasks()
    
    line_h = dummy.winfo_reqheight()
    dummy.destroy()
    root.update_idletasks()
    
    text_h = text_label.winfo_reqheight()
    icon_h = icon_size_scaled
    
    num_lines = max(1, round(text_h / line_h))
    
    icon_top_pad = 0
    text_top_pad = 0
    
    if num_lines <= 2:
        if icon_h > text_h:
            text_top_pad = (icon_h - text_h) // 2
        else:
            icon_top_pad = (text_h - icon_h) // 2
    else:
        two_lines_h = line_h * 2
        if icon_h > two_lines_h:
            text_top_pad = (icon_h - two_lines_h) // 2
        else:
            icon_top_pad = (two_lines_h - icon_h) // 2

    if icon_label is not None and icon_top_pad > 0:
        icon_label.pack_configure(pady=(icon_top_pad, 0))
    if text_top_pad > 0:
        text_label.pack_configure(pady=(text_top_pad, 0))
    
    return root, body, content_frame, scaling

def show_ctk_dialog(
    ctk: Any,
    *,
    parent: Optional[Any],
    theme: CtkTheme,
    title: str,
    message: str,
    kind: DialogKind = DialogKind.INFO,
    buttons: Sequence[Tuple[str, str, bool]] = (("OK", "ok", True),),
    default: str = "ok",
    icon_path: Optional[str] = None,
) -> str:
    root, body, content_frame, scaling = _build_dialog_base(ctk, parent, theme, title, message, kind, icon_path)
    
    footer_bg = resolve_ctk_color(ctk, theme.field_bg)

    footer = ctk.CTkFrame(root, fg_color=footer_bg, corner_radius=0)
    footer.pack(fill="x", side="bottom")
    
    btn_frame = ctk.CTkFrame(footer, fg_color="transparent")
    btn_frame.pack(side="right", padx=16, pady=10)
    
    result = {"value": default}
    
    def close(value: str) -> None:
        result["value"] = value
        try:
            root.grab_release()
        except Exception:
            pass
        root.destroy()

    for i, (text, value, is_primary) in enumerate(buttons):
        btn = ctk.CTkButton(
            btn_frame,
            text=text,
            width=84,
            height=28,
            font=(theme.ui_font_family, 13),
            corner_radius=8,
            fg_color=theme.tg_blue if is_primary else theme.field_bg,
            hover_color=theme.tg_blue_hover if is_primary else theme.field_border,
            text_color="#ffffff" if is_primary else theme.text_primary,
            border_width=0 if is_primary else 1,
            border_color=theme.field_border,
            command=lambda v=value: close(v)
        )
        btn.pack(side="left", padx=(0 if i == 0 else 8, 0))
        if value == default:
            try:
                btn.focus_set()
            except Exception:
                pass

    root.protocol("WM_DELETE_WINDOW", lambda: close(default))
    root.bind("<Escape>", lambda _e: close(default))
    root.bind("<Return>", lambda _e: close(default))
    
    root.update_idletasks()
    width = max(260, int(root.winfo_reqwidth() / scaling))
    height = int(root.winfo_reqheight() / scaling)
    center_ctk_geometry(root, width, height)
    root.resizable(False, False)
    root.deiconify()
    
    try:
        root.grab_set()
    except Exception:
        pass
        
    root.wait_window()
    return result["value"]

def ask_yes_no(
    ctk: Any,
    *,
    parent: Optional[Any],
    theme: CtkTheme,
    title: str,
    message: str,
    icon_path: Optional[str] = None,
) -> bool:
    return show_ctk_dialog(
        ctk,
        parent=parent,
        theme=theme,
        title=title,
        message=message,
        kind=DialogKind.QUESTION,
        buttons=(("Да", "yes", True), ("Нет", "no", False)),
        default="no",
        icon_path=icon_path,
    ) == "yes"

def show_error(
    ctk: Any,
    *,
    parent: Optional[Any],
    theme: CtkTheme,
    title: str,
    message: str,
    icon_path: Optional[str] = None,
) -> bool:
    show_ctk_dialog(
        ctk,
        parent=parent,
        theme=theme,
        title=title,
        message=message,
        kind=DialogKind.ERROR,
        buttons=(("OK", "ok", True),),
        icon_path=icon_path,
    )
    return True

def show_info(
    ctk: Any,
    *,
    parent: Optional[Any],
    theme: CtkTheme,
    title: str,
    message: str,
    icon_path: Optional[str] = None,
) -> bool:
    show_ctk_dialog(
        ctk,
        parent=parent,
        theme=theme,
        title=title,
        message=message,
        kind=DialogKind.INFO,
        buttons=(("OK", "ok", True),),
        icon_path=icon_path,
    )
    return True

def run_with_progress(
    ctk: Any,
    *,
    parent: Optional[Any],
    theme: CtkTheme,
    title: str,
    message: str,
    icon_path: Optional[str] = None,
    task: Any,
) -> str:
    import queue as _queue
    import threading as _threading

    root, body, content_frame, scaling = _build_dialog_base(ctk, parent, theme, title, message, DialogKind.INFO, icon_path)
    
    body.pack_configure(pady=(16, 26))

    status_label = ctk.CTkLabel(
        content_frame, text="",
        justify="left", anchor="w",
        font=(theme.ui_font_family, 11),
        text_color=theme.text_secondary,
        wraplength=int(350 * scaling),
    )
    status_label.pack(fill="x", pady=(6, 0))

    footer = ctk.CTkFrame(root, fg_color=theme.field_bg, corner_radius=0)
    footer.pack(fill="x", side="bottom")

    btn_frame = ctk.CTkFrame(footer, fg_color="transparent")
    btn_frame.pack(side="right", padx=16, pady=10)

    result = {"value": "close"}
    running = {"value": False}
    buttons = []
    task_events = _queue.Queue()

    def close(value: str) -> None:
        if running["value"]:
            return
        result["value"] = value
        try:
            root.grab_release()
        except Exception:
            pass
        root.destroy()

    def _set_running(value: bool) -> None:
        running["value"] = value
        state = "disabled" if value else "normal"
        for btn in buttons:
            btn.configure(state=state)
        root.protocol("WM_DELETE_WINDOW", lambda: None if value else close("close"))

    def _finish_task() -> None:
        _set_running(False)

    def _set_status(msg: str) -> None:
        task_events.put(("status", msg))

    def _poll_task_events() -> None:
        while True:
            try:
                event, value = task_events.get_nowait()
            except _queue.Empty:
                break
            if event == "status":
                status_label.configure(text=value)
            elif event == "finish":
                _finish_task()

        if running["value"]:
            root.after(50, _poll_task_events)

    def _run_task() -> None:
        try:
            task(_set_status)
        except Exception as exc:
            _set_status(f"Ошибка: {exc}")
        finally:
            task_events.put(("finish", ""))

    def _on_update() -> None:
        _set_running(True)
        _poll_task_events()
        _threading.Thread(target=_run_task, daemon=True).start()

    btn_update = ctk.CTkButton(
        btn_frame,
        text="Обновить",
        width=88,
        height=34,
        font=(theme.ui_font_family, 13),
        command=_on_update,
    )
    btn_update.pack(side="left", padx=(0, 6))
    buttons.append(btn_update)

    btn_page = ctk.CTkButton(
        btn_frame,
        text="Страница",
        width=88,
        height=34,
        font=(theme.ui_font_family, 13),
        command=lambda: close("open"),
    )
    btn_page.pack(side="left", padx=(0, 6))
    buttons.append(btn_page)

    btn_close = ctk.CTkButton(
        btn_frame,
        text="Закрыть",
        width=88,
        height=34,
        font=(theme.ui_font_family, 13),
        fg_color=theme.field_bg,
        hover_color=theme.field_border,
        text_color=theme.text_primary,
        border_width=1,
        border_color=theme.field_border,
        command=lambda: close("close"),
    )
    btn_close.pack(side="left")
    buttons.append(btn_close)

    root.protocol("WM_DELETE_WINDOW", lambda: close("close"))

    root.update_idletasks()
    width = max(320, int(root.winfo_reqwidth() / scaling))
    height = int(root.winfo_reqheight() / scaling)
    center_ctk_geometry(root, width, height)
    root.resizable(False, False)
    root.deiconify()

    try:
        root.grab_set()
    except Exception:
        pass

    root.wait_window()
    return result["value"]
