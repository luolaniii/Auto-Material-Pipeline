# -*- coding: utf-8 -*-
"""预览图（缩略图）视图：用图标网格展示模型，复用原脚本渲染出的同名 PNG。"""

from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem

from core.model_inspector import ModelInfo, SUPPORTED_EXTS
from core.thumbnails import clear_preview_index, find_preview_for

PATH_ROLE = Qt.UserRole + 1


class ThumbnailView(QListWidget):
    selection_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(160, 160))
        self.setGridSize(QSize(190, 215))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSpacing(8)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)

        self._search_dirs: List[str] = []
        self._items_by_path: Dict[str, QListWidgetItem] = {}
        self._placeholder = self._make_placeholder()

        self.itemSelectionChanged.connect(
            lambda: self.selection_changed.emit(len(self.selectedItems()))
        )

    def _make_placeholder(self) -> QIcon:
        pm = QPixmap(160, 160)
        pm.fill(QColor("#ECECEC"))
        return QIcon(pm)

    def set_search_dirs(self, dirs: List[str]) -> None:
        self._search_dirs = [d for d in (dirs or []) if d]
        clear_preview_index(self._search_dirs)

    def add_or_update(self, info: ModelInfo) -> None:
        preview = find_preview_for(info.path, self._search_dirs)
        icon = QIcon(str(preview)) if preview else self._placeholder
        name = Path(info.path).name

        if info.path in self._items_by_path:
            item = self._items_by_path[info.path]
            item.setIcon(icon)
        else:
            item = QListWidgetItem(icon, name)
            item.setData(PATH_ROLE, info.path)
            item.setSizeHint(QSize(185, 210))
            self.addItem(item)
            self._items_by_path[info.path] = item

        item.setToolTip(
            f"{info.path}\nUV:{info.uv_set_count}  顶点:{info.vertex_count:,}  面:{info.face_count:,}"
            + ("" if preview else "\n(无预览图：跑一次 Blender 完整流水线即可生成)")
        )

    def add_output_model(self, model_path: str | Path) -> None:
        path = str(Path(model_path).resolve())
        preview = find_preview_for(path, self._search_dirs)
        icon = QIcon(str(preview)) if preview else self._placeholder
        name = Path(path).name

        if path in self._items_by_path:
            item = self._items_by_path[path]
            item.setIcon(icon)
        else:
            item = QListWidgetItem(icon, name)
            item.setData(PATH_ROLE, path)
            item.setSizeHint(QSize(185, 210))
            self.addItem(item)
            self._items_by_path[path] = item

        item.setToolTip(
            f"{path}\n来源: _GUI_PipelineOutput"
            + ("" if preview else "\n(未找到同名预览图)")
        )

    def sync_output_models(self) -> int:
        """递归读取 _GUI_PipelineOutput 下所有模型，让预览图视图显示流水线结果。"""
        added = 0
        seen = set(self._items_by_path.keys())
        for d in self._search_dirs:
            if not d:
                continue
            root = Path(d)
            if not root.exists():
                continue
            try:
                candidates = sorted(
                    p for p in root.rglob("*")
                    if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
                )
            except OSError:
                candidates = []
            for p in candidates:
                key = str(p.resolve())
                if key not in seen:
                    added += 1
                    seen.add(key)
                self.add_output_model(p)
        return added

    def clear_all(self) -> None:
        self.clear()
        self._items_by_path.clear()

    def remove_paths(self, paths: List[str]) -> None:
        for p in paths:
            it = self._items_by_path.pop(p, None)
            if it is not None:
                self.takeItem(self.row(it))

    def refresh_previews(self) -> None:
        clear_preview_index(self._search_dirs)
        for path, item in self._items_by_path.items():
            preview = find_preview_for(path, self._search_dirs)
            if preview:
                item.setIcon(QIcon(str(preview)))

    def selected_paths(self) -> List[str]:
        return [it.data(PATH_ROLE) for it in self.selectedItems()]

    def apply_filter(self, text: str) -> None:
        text = (text or "").lower().strip()
        for path, item in self._items_by_path.items():
            item.setHidden(bool(text) and text not in path.lower())
