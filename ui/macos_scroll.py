"""Tk 9 precise trackpad scrolling for CustomTkinter settings frames."""
import tkinter


def install_touchpad_scroll(scroll):
    canvas = scroll._parent_canvas
    if (canvas.tk.call('tk', 'windowingsystem') != 'aqua'
            or not canvas.tk.call('info', 'commands', '::tk::PreciseScrollDeltas')):
        return
    canvas.configure(yscrollincrement=1)
    top = canvas.winfo_toplevel()

    def on_scroll(event):
        widget = event.widget
        # Editable multi-line fields have their own native scrolling.
        if widget.winfo_class() in ('Text', 'Listbox', 'Treeview'):
            return
        while widget is not None and widget != canvas:
            widget = getattr(widget, 'master', None)
        if widget != canvas:
            return
        _, delta_y = canvas.tk.splitlist(
            canvas.tk.call('::tk::PreciseScrollDeltas', event.delta))
        if int(delta_y):
            pixels = int(canvas.tk.call('::tk::ScaleNum', -int(delta_y)))
            canvas.yview_scroll(pixels, 'units')
            return 'break'

    binding = top.bind('<TouchpadScroll>', on_scroll, add='+')

    def cleanup(event):
        if event.widget == canvas:
            try:
                top.unbind('<TouchpadScroll>', binding)
            except tkinter.TclError:
                pass

    canvas.bind('<Destroy>', cleanup, add='+')
