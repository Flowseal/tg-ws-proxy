from __future__ import annotations

import logging
import queue
import threading

log = logging.getLogger("tg-ws-tray")


class BackgroundTask:
    def __init__(self, owner):
        self.owner = owner
        self.running = False
        self.closed = False
        self.pending = None
        owner.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, event):
        if event.widget is self.owner:
            self.closed = True
            if self.pending is not None:
                self.owner.after_cancel(self.pending)
                self.pending = None

    def start(self, work, complete, finish):
        if self.running or self.closed:
            return
        self.running = True
        results = queue.Queue()

        def run():
            try:
                results.put((True, work()))
            except Exception:
                log.exception("Background task failed")
                results.put((False, None))

        def poll():
            self.pending = None
            if self.closed:
                return
            try:
                success, result = results.get_nowait()
            except queue.Empty:
                self.pending = self.owner.after(50, poll)
                return
            self.running = False
            try:
                if success:
                    complete(result)
            finally:
                if not self.closed:
                    finish()

        threading.Thread(target=run, daemon=True, name="ui-background-task").start()
        self.pending = self.owner.after(50, poll)
