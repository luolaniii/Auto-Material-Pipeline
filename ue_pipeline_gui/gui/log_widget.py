# -*- coding: utf-8 -*-
"""日志输出窗口，支持彩色级别。"""

from datetime import datetime

from PySide6.QtCore import Signal, QObject
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit


LEVEL_COLORS = {
    "DEBUG": QColor("#808080"),
    "INFO": QColor("#202020"),
    "OK": QColor("#1E8E3E"),
    "WARN": QColor("#E37400"),
    "ERROR": QColor("#C5221F"),
}


class LogBus(QObject):
    log = Signal(str, str)  # (level, message)


log_bus = LogBus()


def emit_log(message: str, level: str = "INFO") -> None:
    log_bus.log.emit(level, message)


class LogWidget(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(5000)
        self.setStyleSheet(
            "QPlainTextEdit { background: #FAFAFA; font-family: Consolas, 'Microsoft YaHei', monospace; font-size: 12px; }"
        )
        log_bus.log.connect(self.append_message)

    def append_message(self, level: str, message: str) -> None:
        color = LEVEL_COLORS.get(level, LEVEL_COLORS["INFO"])
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = f"[{ts}] [{level}] "

        self.moveCursor(QTextCursor.End)
        cursor = self.textCursor()
        fmt = cursor.charFormat()
        fmt.setForeground(color)
        cursor.setCharFormat(fmt)
        cursor.insertText(prefix + message + "\n")
        self.moveCursor(QTextCursor.End)
