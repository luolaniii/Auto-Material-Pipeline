# -*- coding: utf-8 -*-
"""后台线程：批量解析模型，避免阻塞 GUI。"""

from typing import List

from PySide6.QtCore import QObject, QThread, Signal

from core.model_inspector import inspect_model, ModelInfo


class InspectWorker(QObject):
    progress = Signal(int, int)        # done, total
    one_done = Signal(object)          # ModelInfo
    finished = Signal()

    def __init__(self, paths: List[str], obj_assumed_unit: str):
        super().__init__()
        self._paths = list(paths)
        self._obj_unit = obj_assumed_unit
        self._abort = False

    def abort(self):
        self._abort = True

    def run(self):
        total = len(self._paths)
        for i, p in enumerate(self._paths):
            if self._abort:
                break
            info = inspect_model(p, obj_assumed_unit=self._obj_unit)
            self.one_done.emit(info)
            self.progress.emit(i + 1, total)
        self.finished.emit()


def run_inspect_async(parent, paths: List[str], obj_assumed_unit: str,
                      on_one, on_progress=None, on_finished=None) -> QThread:
    thread = QThread(parent)
    worker = InspectWorker(paths, obj_assumed_unit)
    worker.moveToThread(thread)
    thread.worker = worker  # type: ignore[attr-defined]  保持引用

    thread.started.connect(worker.run)
    worker.one_done.connect(on_one)
    if on_progress:
        worker.progress.connect(on_progress)
    if on_finished:
        worker.finished.connect(on_finished)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    thread.start()
    return thread
