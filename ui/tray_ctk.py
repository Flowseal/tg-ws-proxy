"""Общие операции с окнами CustomTkinter / tkinter."""
from __future__ import annotations


def destroy_root_safely(root) -> None:
    import tkinter as tk

    try:
        if root.winfo_exists():
            root.destroy()
    except tk.TclError:
        pass
