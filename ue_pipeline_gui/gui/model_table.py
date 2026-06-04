# -*- coding: utf-8 -*-
"""模型信息表格。每行一个文件，列展示 UV 套数 / 单位 / 顶点 / 面数。"""

from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

from core.model_inspector import ModelInfo


COLUMNS = [
    ("file", "文件名", 240),
    ("format", "格式", 60),
    ("uv_count", "UV 套数", 80),
    ("uv_names", "UV 名称", 200),
    ("unit", "单位", 100),
    ("unit_source", "单位来源", 220),
    ("vertices", "顶点数", 90),
    ("faces", "面数", 90),
    ("status", "状态", 140),
    ("path", "路径", 360),
]

COL_INDEX = {key: i for i, (key, _, _) in enumerate(COLUMNS)}


class NumericItem(QTableWidgetItem):
    """按数值排序的单元格（显示千分位文本，排序用真实数值）。"""

    def __init__(self, value: float, text: str):
        super().__init__(text)
        self._value = value

    def __lt__(self, other):
        try:
            return self._value < other._value
        except AttributeError:
            return super().__lt__(other)


class ModelTable(QTableWidget):
    selection_changed = Signal(int)  # 当前选中行数

    def __init__(self, parent=None):
        super().__init__(0, len(COLUMNS), parent)
        self.setHorizontalHeaderLabels([c[1] for c in COLUMNS])

        for i, (_, _, w) in enumerate(COLUMNS):
            self.setColumnWidth(i, w)

        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(True)

        self._infos_by_path: Dict[str, ModelInfo] = {}

        self.itemSelectionChanged.connect(self._on_selection_changed)

    # ----- 数据接口 -----
    def has_path(self, path: str) -> bool:
        return path in self._infos_by_path

    def begin_update(self) -> None:
        """批量插入前调用：关排序 + 暂停重绘，结束后调 end_update。"""
        self._sort_was_enabled = self.isSortingEnabled()
        self.setSortingEnabled(False)
        self.setUpdatesEnabled(False)

    def end_update(self) -> None:
        self.setUpdatesEnabled(True)
        self.setSortingEnabled(getattr(self, "_sort_was_enabled", True))

    def add_or_update(self, info: ModelInfo) -> None:
        # 单次调用时自己管排序开关；批量时 begin_update 已关闭，这里检测到就不重复开关
        was_sorting = self.isSortingEnabled()
        if was_sorting:
            self.setSortingEnabled(False)
        try:
            if info.path in self._infos_by_path:
                row = self._find_row(info.path)
                if row >= 0:
                    self._fill_row(row, info)
            else:
                row = self.rowCount()
                self.insertRow(row)
                self._fill_row(row, info)
            self._infos_by_path[info.path] = info
        finally:
            if was_sorting:
                self.setSortingEnabled(True)

    def remove_selected(self) -> List[str]:
        rows = sorted({i.row() for i in self.selectedIndexes()}, reverse=True)
        removed_paths: List[str] = []
        for r in rows:
            path_item = self.item(r, COL_INDEX["path"])
            if path_item:
                removed_paths.append(path_item.text())
            self.removeRow(r)
        for p in removed_paths:
            self._infos_by_path.pop(p, None)
        return removed_paths

    def remove_paths(self, paths: List[str]) -> None:
        pathset = set(paths)
        for r in range(self.rowCount() - 1, -1, -1):
            it = self.item(r, COL_INDEX["path"])
            if it and it.text() in pathset:
                self.removeRow(r)
        for p in paths:
            self._infos_by_path.pop(p, None)

    def clear_all(self) -> None:
        self.setRowCount(0)
        self._infos_by_path.clear()

    def apply_filter(self, text: str) -> int:
        """按关键词过滤行（匹配 文件名/格式/单位/UV名/路径），返回可见行数。"""
        text = (text or "").lower().strip()
        visible = 0
        for r in range(self.rowCount()):
            if not text:
                self.setRowHidden(r, False)
                visible += 1
                continue
            parts = []
            for key in ("file", "format", "unit", "uv_names", "path"):
                it = self.item(r, COL_INDEX[key])
                if it:
                    parts.append(it.text().lower())
            match = text in " ".join(parts)
            self.setRowHidden(r, not match)
            if match:
                visible += 1
        return visible

    def all_infos(self) -> List[ModelInfo]:
        return list(self._infos_by_path.values())

    def selected_infos(self) -> List[ModelInfo]:
        infos: List[ModelInfo] = []
        seen_rows = sorted({i.row() for i in self.selectedIndexes()})
        for r in seen_rows:
            path_item = self.item(r, COL_INDEX["path"])
            if not path_item:
                continue
            info = self._infos_by_path.get(path_item.text())
            if info:
                infos.append(info)
        return infos

    # ----- 内部 -----
    def _find_row(self, path: str) -> int:
        col = COL_INDEX["path"]
        for r in range(self.rowCount()):
            it = self.item(r, col)
            if it and it.text() == path:
                return r
        return -1

    def _fill_row(self, row: int, info: ModelInfo) -> None:
        p = Path(info.path)
        uv_names = ", ".join(s.name for s in info.uv_sets) if info.uv_sets else "—"
        status = "OK" if info.ok else f"❌ {info.error}"

        values = {
            "file": p.name,
            "format": info.file_format.upper(),
            "uv_count": str(info.uv_set_count),
            "uv_names": uv_names,
            "unit": info.unit_display,
            "unit_source": info.unit_source,
            "vertices": f"{info.vertex_count:,}",
            "faces": f"{info.face_count:,}",
            "status": status,
            "path": info.path,
        }

        numeric_values = {
            "uv_count": info.uv_set_count,
            "vertices": info.vertex_count,
            "faces": info.face_count,
        }
        for key, value in values.items():
            col = COL_INDEX[key]
            if key in numeric_values:
                item = NumericItem(numeric_values[key], value)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            else:
                item = QTableWidgetItem(value)
            self._apply_row_style(item, info, key)
            self.setItem(row, col, item)

    def _apply_row_style(self, item: QTableWidgetItem, info: ModelInfo, key: str) -> None:
        if not info.ok:
            item.setForeground(QBrush(QColor("#C5221F")))
            return
        if key == "uv_count":
            if info.uv_set_count == 0:
                item.setForeground(QBrush(QColor("#C5221F")))
            elif info.uv_set_count >= 2:
                item.setForeground(QBrush(QColor("#1E8E3E")))
        elif key == "unit":
            if info.unit == "unknown":
                item.setForeground(QBrush(QColor("#E37400")))

    def _on_selection_changed(self) -> None:
        rows = {i.row() for i in self.selectedIndexes()}
        self.selection_changed.emit(len(rows))
