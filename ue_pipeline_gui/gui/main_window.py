# -*- coding: utf-8 -*-
"""主窗口。"""

import os
import sys
import tempfile
from pathlib import Path
from typing import List

from PySide6.QtCore import Qt, Slot, QTimer
from PySide6.QtGui import QAction, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core import crash_log
from core.config import load_config, save_config
from core.model_inspector import SUPPORTED_EXTS
from gui.inspect_worker import run_inspect_async
from gui.log_widget import LogWidget, emit_log
from gui.model_table import ModelTable
from gui.settings_dialog import SettingsDialog
from gui.thumbnail_view import ThumbnailView


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UE Pipeline GUI - 美术批量处理工具")
        self.resize(1480, 880)
        self.setAcceptDrops(True)

        self.config = load_config()
        self._threads = []  # 持有活跃解析线程的强引用，防止被 GC
        self._source_roots = {}  # path -> 用户添加/拖入时选择的目录根，用于完整流水线输出位置

        self._build_ui()
        self._build_toolbar()
        self._build_status_bar()

        emit_log("启动完成。把模型拖进窗口，或点上方“添加文件 / 添加目录”。", "INFO")
        self._refresh_action_states()

    # ---------------- UI 构造 ----------------
    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Vertical, self)
        root.addWidget(splitter, 1)

        # 上：用 QStackedWidget 在 [详细信息表格] 和 [预览图网格] 间切换
        self.table = ModelTable(self)
        self.table.selection_changed.connect(self._on_view_selection)

        self.thumb_view = ThumbnailView(self)
        self.thumb_view.set_search_dirs(self._preview_dirs())
        self.thumb_view.selection_changed.connect(self._on_view_selection)

        self.view_stack = QStackedWidget(self)
        self.view_stack.addWidget(self.table)        # index 0 = 详细信息
        self.view_stack.addWidget(self.thumb_view)   # index 1 = 预览图
        splitter.addWidget(self.view_stack)

        # 下：日志
        self.log = LogWidget(self)
        splitter.addWidget(self.log)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        # 底部：材质模式 + 平台按钮
        bottom = QHBoxLayout()

        bottom.addWidget(QLabel("材质模式:"))
        self.mat_mode_combo = QComboBox()
        self.mat_mode_combo.addItem("仅模型", "none")
        self.mat_mode_combo.addItem("文件内嵌（流水线 GLB）", "embedded")
        self.mat_mode_combo.setCurrentIndex(1)
        self.mat_mode_combo.setToolTip(
            "材质处理方式：\n"
            "· 仅模型：只导网格\n"
            "· 文件内嵌：UE / Unity 直接导入 Blender 流水线输出的 GLB"
        )
        bottom.addWidget(self.mat_mode_combo)
        bottom.addStretch(1)

        self.btn_blender_quick = QPushButton("  在 Blender 中打开  ")
        self.btn_blender_full = QPushButton("  Blender 完整流水线  ")
        self.btn_ue_import = QPushButton("  导入 UE  ")
        self.btn_unity_import = QPushButton("  导入 Unity  ")
        self.btn_settings = QPushButton("  设置  ")

        for b in (self.btn_blender_quick, self.btn_blender_full,
                  self.btn_ue_import, self.btn_unity_import, self.btn_settings):
            b.setMinimumHeight(34)
            bottom.addWidget(b)

        self.btn_blender_quick.clicked.connect(self._action_blender_quick)
        self.btn_blender_full.clicked.connect(self._action_blender_full)
        self.btn_ue_import.clicked.connect(self._action_ue_import)
        self.btn_unity_import.clicked.connect(self._action_unity_import)
        self.btn_settings.clicked.connect(self._action_settings)

        root.addLayout(bottom)

    def _material_mode(self) -> str:
        return self.mat_mode_combo.currentData() or "embedded"

    def _engine_material_mode(self) -> str:
        mode = self._material_mode()
        return "none" if mode == "none" else "embedded"

    def _build_toolbar(self):
        toolbar = QToolBar("主工具栏", self)
        toolbar.setMovable(False)
        toolbar.setIconSize(toolbar.iconSize())
        self.addToolBar(toolbar)

        self.act_add_files = QAction("添加文件", self)
        self.act_add_files.triggered.connect(self._action_add_files)
        toolbar.addAction(self.act_add_files)

        self.act_add_dir = QAction("添加目录", self)
        self.act_add_dir.triggered.connect(self._action_add_dir)
        toolbar.addAction(self.act_add_dir)

        toolbar.addSeparator()

        self.act_remove = QAction("移除选中", self)
        self.act_remove.triggered.connect(self._action_remove_selected)
        toolbar.addAction(self.act_remove)

        self.act_clear = QAction("清空列表", self)
        self.act_clear.triggered.connect(self._action_clear)
        toolbar.addAction(self.act_clear)

        toolbar.addSeparator()

        self.act_reinspect = QAction("重新解析选中", self)
        self.act_reinspect.triggered.connect(self._action_reinspect)
        toolbar.addAction(self.act_reinspect)

        toolbar.addSeparator()

        self.act_view_toggle = QAction("切换到预览图", self)
        self.act_view_toggle.setCheckable(True)
        self.act_view_toggle.triggered.connect(self._toggle_view)
        toolbar.addAction(self.act_view_toggle)

        self.act_render_previews = QAction("生成白模预览", self)
        self.act_render_previews.setToolTip("用 Blender 为原始模型生成白模缩略图，不需要先跑完整流水线")
        self.act_render_previews.triggered.connect(self._action_render_previews)
        toolbar.addAction(self.act_render_previews)

        self.act_show_logs = QAction("日志", self)
        self.act_show_logs.setToolTip("查看当前窗口、Blender、UE、Unity、完整流水线和崩溃日志")
        self.act_show_logs.triggered.connect(self._action_show_logs)
        toolbar.addAction(self.act_show_logs)

        # 右侧搜索框
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        toolbar.addWidget(QLabel("搜索 "))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("按 文件名/格式/单位/路径 过滤…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMaximumWidth(340)
        self.search_edit.textChanged.connect(self._on_search)
        toolbar.addWidget(self.search_edit)

    def _build_status_bar(self):
        bar = QStatusBar(self)
        self.setStatusBar(bar)

        self.status_label = QLabel("就绪")
        bar.addWidget(self.status_label, 1)

        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.setVisible(False)
        bar.addPermanentWidget(self.progress)

    # ---------------- 拖拽 ----------------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        try:
            paths: List[str] = []
            source_roots: List[str] = []
            for url in event.mimeData().urls():
                local = url.toLocalFile()
                if not local:
                    continue
                p = Path(local)
                try:
                    is_dir = p.is_dir()
                except OSError:
                    is_dir = False
                if is_dir:
                    source_roots.append(str(p))
                    paths.extend(self._collect_dir(p))
                elif p.suffix.lower() in SUPPORTED_EXTS:
                    paths.append(str(p))
            if paths:
                self._enqueue_inspect(paths, source_root=source_roots[0] if len(source_roots) == 1 else "")
            else:
                emit_log("拖入的内容里没有支持的模型文件（.obj/.fbx/.glb/.gltf）", "WARN")
        except Exception as e:
            emit_log(f"处理拖入文件出错: {e}", "ERROR")

    # ---------------- 工具栏动作 ----------------
    def _action_add_files(self):
        filt = "支持的模型 (*.obj *.fbx *.glb *.gltf);;所有文件 (*.*)"
        paths, _ = QFileDialog.getOpenFileNames(self, "选择模型文件", "", filt)
        if paths:
            self._enqueue_inspect(paths)

    def _action_add_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择包含模型的目录")
        if d:
            paths = self._collect_dir(Path(d))
            if paths:
                self._enqueue_inspect(paths, source_root=d)
            else:
                emit_log(f"目录 {d} 下未找到支持的模型文件", "WARN")

    def _action_remove_selected(self):
        paths = self._selected_paths()
        if not paths:
            return
        self.table.remove_paths(paths)
        self.thumb_view.remove_paths(paths)
        emit_log(f"已移除 {len(paths)} 个文件", "INFO")
        self._refresh_action_states()

    def _action_clear(self):
        self.table.clear_all()
        self.thumb_view.clear_all()
        self.search_edit.clear()
        emit_log("已清空列表", "INFO")
        self._refresh_action_states()

    def _action_reinspect(self):
        paths = self._selected_paths()
        if not paths:
            return
        self._enqueue_inspect(paths)

    def _action_show_logs(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("日志")
        dialog.resize(1120, 720)

        layout = QVBoxLayout(dialog)
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("搜索"))
        search_edit = QLineEdit(dialog)
        search_edit.setPlaceholderText("输入关键词后回车查找当前日志")
        search_edit.setClearButtonEnabled(True)
        search_row.addWidget(search_edit, 1)
        btn_find_next = QPushButton("下一个", dialog)
        btn_refresh = QPushButton("刷新", dialog)
        search_row.addWidget(btn_find_next)
        search_row.addWidget(btn_refresh)
        layout.addLayout(search_row)

        tabs = QTabWidget(dialog)
        layout.addWidget(tabs, 1)

        editors = []

        def populate_tabs():
            current_index = tabs.currentIndex()
            tabs.clear()
            editors.clear()
            for title, text in self._collect_log_tabs():
                editor = QPlainTextEdit(dialog)
                editor.setReadOnly(True)
                editor.setLineWrapMode(QPlainTextEdit.NoWrap)
                editor.setPlainText(text)
                editor.moveCursor(QTextCursor.End)
                tabs.addTab(editor, title)
                editors.append(editor)
            if tabs.count() and current_index >= 0:
                tabs.setCurrentIndex(min(current_index, tabs.count() - 1))

        def find_next():
            text = search_edit.text()
            if not text or not editors:
                return
            editor = editors[tabs.currentIndex()]
            if not editor.find(text):
                cursor = editor.textCursor()
                cursor.movePosition(QTextCursor.Start)
                editor.setTextCursor(cursor)
                editor.find(text)

        populate_tabs()
        search_edit.returnPressed.connect(find_next)
        btn_find_next.clicked.connect(find_next)
        btn_refresh.clicked.connect(populate_tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        dialog.exec()

    def _make_log_tab(self, title: str, log_path: Path):
        name = title
        if log_path.exists():
            name = f"{title} ({log_path.parent.name})"
        return name, self._read_log_text(log_path)

    def _pick_existing_or_first(self, paths: List[Path]) -> "Path | None":
        if not paths:
            return None
        existing = []
        for p in paths:
            try:
                if p.exists():
                    existing.append(p)
            except Exception:
                pass
        if existing:
            return max(existing, key=lambda p: p.stat().st_mtime)
        return paths[0]

    def _collect_log_tabs(self):
        tabs = [("当前窗口", self.log.toPlainText() or "当前窗口暂无日志。")]

        for title, log_path in self._candidate_log_files():
            tabs.append(self._make_log_tab(title, log_path))

        return tabs

    def _candidate_log_files(self):
        candidates = []
        seen = set()

        def add(title: str, p: Path):
            try:
                resolved = p.resolve()
            except Exception:
                resolved = p
            key = str(resolved).lower()
            if key in seen:
                return
            seen.add(key)
            candidates.append((title, resolved))

        add("Blender 打开", Path(tempfile.gettempdir()) / "UEPipelineGUI" / "blender_open.log")
        add("Blender 白模预览", Path(tempfile.gettempdir()) / "UEPipelineGUI" / "thumbnail_render.log")
        add("UE 导入", Path(tempfile.gettempdir()) / "UEPipelineGUI" / "ue_import.log")
        add("Unity 导入", Path(tempfile.gettempdir()) / "UEPipelineGUI" / "unity_import.log")

        output_dirs = self._candidate_output_dirs()
        detailed_log = self._pick_existing_or_first([d / "detailed_processing.log" for d in output_dirs])
        render_log = self._pick_existing_or_first([d / ".render_progress.json" for d in output_dirs])
        if detailed_log:
            add("流水线详细", detailed_log)
        if render_log:
            add("渲染进度", render_log)

        session_log = crash_log.log_path()
        if session_log:
            add("会话日志", Path(session_log))

        log_dirs = []
        if getattr(sys, "frozen", False):
            log_dirs.append(Path(sys.executable).resolve().parent / "logs")
        log_dirs.append(Path(__file__).resolve().parent.parent / "logs")
        log_dirs.append(Path(os.environ.get("TEMP", tempfile.gettempdir())) / "UEPipelineGUI" / "logs")
        for log_dir in log_dirs:
            try:
                logs = sorted(log_dir.glob("session_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            except Exception:
                logs = []
            for log_path in logs[:3]:
                add("会话日志", log_path)

        return candidates

    def _candidate_output_dirs(self) -> List[Path]:
        dirs: List[Path] = []

        for d in self._preview_dirs():
            if d:
                dirs.append(Path(d))

        for root in self._source_roots.values():
            if root:
                dirs.append(Path(root) / "_GUI_PipelineOutput")

        for info in self.table.all_infos():
            p = Path(info.path)
            for parent in [p.parent, *p.parents]:
                if parent.name == "_GUI_PipelineOutput":
                    dirs.append(parent)
                    break

        deduped: List[Path] = []
        seen = set()
        for d in dirs:
            try:
                resolved = d.resolve()
            except Exception:
                resolved = d
            key = str(resolved).lower()
            if key not in seen:
                seen.add(key)
                deduped.append(resolved)
        return deduped

    def _read_log_text(self, log_path: Path, max_bytes: int = 2_000_000) -> str:
        if not log_path.exists():
            return f"未找到日志文件：\n{log_path}"
        try:
            data = log_path.read_bytes()
            truncated = len(data) > max_bytes
            if truncated:
                data = data[-max_bytes:]
            text = data.decode("utf-8", errors="replace")
            prefix = f"路径: {log_path}\n"
            if truncated:
                prefix += f"只显示最后 {max_bytes:,} 字节。\n"
            return prefix + "\n" + text
        except Exception as e:
            return f"读取日志失败：\n{log_path}\n\n{e}"

    # ---------------- 视图切换 / 搜索 / 选择 ----------------
    def _toggle_view(self, checked: bool):
        if checked:
            self.view_stack.setCurrentWidget(self.thumb_view)
            self.act_view_toggle.setText("切换到详细信息")
            self.thumb_view.set_search_dirs(self._preview_dirs())
            added = self.thumb_view.sync_output_models()
            self.thumb_view.refresh_previews()
            if added:
                emit_log(f"已从 _GUI_PipelineOutput 递归读取 {added} 个流水线输出模型", "INFO")
        else:
            self.view_stack.setCurrentWidget(self.table)
            self.act_view_toggle.setText("切换到预览图")
        self._on_search(self.search_edit.text())
        self._refresh_action_states()

    def _on_search(self, text: str):
        self.table.apply_filter(text)
        self.thumb_view.apply_filter(text)

    def _preview_dirs(self) -> List[str]:
        dirs: List[str] = []
        for root in sorted({r for r in self._source_roots.values() if r}):
            dirs.append(str(Path(root) / "_GUI_PipelineOutput"))
        pv = self.config.get("preview_dir", "")
        if pv:
            dirs.append(pv)
        return dirs

    def _on_view_selection(self, n: int):
        total = self.thumb_view.count() if self.view_stack.currentWidget() is self.thumb_view else self.table.rowCount()
        self.status_label.setText(
            f"共 {total} 个文件，已选中 {n} 个" if n else f"共 {total} 个文件"
        )
        self._refresh_action_states()

    def _refresh_action_states(self):
        n_selected = len(self._selected_paths())
        n_total = self.table.rowCount()
        has_sel = n_selected > 0
        has_any = n_total > 0
        for b in (self.btn_blender_quick, self.btn_blender_full,
                  self.btn_ue_import, self.btn_unity_import):
            b.setEnabled(has_sel)
        self.act_remove.setEnabled(has_sel)
        self.act_clear.setEnabled(has_any)
        self.act_reinspect.setEnabled(has_sel)
        self.act_render_previews.setEnabled(has_any)

    # ---------------- 解析 ----------------
    def _collect_dir(self, d: Path) -> List[str]:
        """用 os.walk 容错遍历，跳过无权限/超长路径目录，避免 rglob 抛异常崩溃。"""
        import os
        result: List[str] = []
        try:
            for root, dirs, files in os.walk(str(d), onerror=lambda e: None):
                for name in files:
                    ext = os.path.splitext(name)[1].lower()
                    if ext in SUPPORTED_EXTS:
                        result.append(os.path.join(root, name))
        except Exception as e:
            emit_log(f"扫描目录 {d} 出错: {e}", "WARN")
        return result

    def _enqueue_inspect(self, paths: List[str], source_root: str = ""):
        new_paths = []
        resolved_source_root = ""
        if source_root:
            try:
                resolved_source_root = str(Path(source_root).resolve())
            except Exception:
                resolved_source_root = str(source_root)
        for p in paths:
            try:
                sp = str(Path(p).resolve())
            except Exception:
                sp = str(p)
            new_paths.append(sp)
            if resolved_source_root:
                self._source_roots[sp] = resolved_source_root

        if not new_paths:
            return

        total = len(new_paths)
        emit_log(f"开始解析 {total} 个文件…", "INFO")
        self.progress.setVisible(True)
        self.progress.setRange(0, total)
        self.progress.setValue(0)
        self.table.begin_update()
        # 解析批次状态。回调是本类的 @Slot 方法（绑定到主线程 QObject），
        # Qt 跨线程会自动 QueuedConnection 到主线程执行，GUI 操作才安全。
        self._inspect_ctx = {"ok": 0, "warn": 0, "err": 0, "verbose": total <= 200}

        thread = run_inspect_async(
            self,
            new_paths,
            obj_assumed_unit=self.config.get("obj_assumed_unit", "centimeter"),
            on_one=self._on_inspect_one,
            on_progress=self._on_inspect_progress,
            on_finished=self._on_inspect_finished,
        )
        self._threads.append(thread)
        # 线程结束后用 Python 身份比较安全移除（绝不调用 C++ 方法如 isRunning）
        thread.finished.connect(lambda: self._discard_thread(thread))

    @Slot(object)
    def _on_inspect_one(self, info):
        # 必须在主线程执行（GUI 控件操作）。本方法是 MainWindow(QObject) 的槽，
        # 连到子线程 worker 的信号时 Qt 自动用 QueuedConnection，保证在主线程跑。
        self.table.add_or_update(info)
        self.thumb_view.add_or_update(info)
        ctx = self._inspect_ctx
        if not info.ok:
            ctx["err"] += 1
            emit_log(f"× {Path(info.path).name}: {info.error}", "ERROR")
        elif info.uv_set_count == 0:
            ctx["warn"] += 1
            emit_log(f"⚠ {Path(info.path).name}: 没有 UV", "WARN")
        else:
            ctx["ok"] += 1
            if ctx["verbose"]:
                emit_log(
                    f"✓ {Path(info.path).name} | UV:{info.uv_set_count} | "
                    f"单位:{info.unit_display} | V:{info.vertex_count} | F:{info.face_count}",
                    "OK",
                )

    @Slot(int, int)
    def _on_inspect_progress(self, done, total):
        self.progress.setValue(done)

    @Slot()
    def _on_inspect_finished(self):
        self.table.end_update()
        self.progress.setVisible(False)
        ctx = getattr(self, "_inspect_ctx", {"ok": 0, "warn": 0, "err": 0})
        emit_log(
            f"解析完成：成功 {ctx['ok']}，无 UV {ctx['warn']}，失败 {ctx['err']}。",
            "INFO",
        )
        self._refresh_action_states()

    def _discard_thread(self, thread):
        try:
            self._threads = [t for t in self._threads if t is not thread]
        except Exception:
            pass

    # ---------------- 下方按钮 ----------------
    def _selected_paths(self) -> List[str]:
        if self.view_stack.currentWidget() is self.thumb_view:
            return self.thumb_view.selected_paths()
        return [info.path for info in self.table.selected_infos()]

    def _all_paths(self) -> List[str]:
        return [info.path for info in self.table.all_infos()]

    def _config_for_paths(self, paths: List[str]) -> dict:
        """把“添加目录”时记录的源根目录传给后端，用于匹配原始 JSON/贴图资源。"""
        cfg = dict(self.config)
        source_roots = {self._source_roots.get(p, "") for p in paths}
        source_roots.discard("")
        if len(source_roots) == 1:
            root = next(iter(source_roots))
            cfg["pipeline_output_root"] = root
            cfg["model_root"] = root
        return cfg

    def _action_blender_quick(self):
        paths = self._selected_paths()
        if not paths:
            return
        try:
            from core.blender_bridge import open_in_blender
        except Exception as e:
            emit_log(f"Blender 桥接未就绪: {e}", "ERROR")
            return

        try:
            mode = self._material_mode()
            cfg = self._config_for_paths(paths)
            open_in_blender(paths, cfg, material_mode=mode)
            emit_log(f"已请求 Blender 打开 {len(paths)} 个模型（材质模式: {mode}）", "OK")
        except Exception as e:
            emit_log(f"Blender 打开失败: {e}", "ERROR")
            QMessageBox.critical(self, "错误", str(e))

    def _action_blender_full(self):
        paths = self._selected_paths()
        if not paths:
            return
        try:
            from core.blender_bridge import run_full_pipeline
        except Exception as e:
            emit_log(f"Blender 桥接未就绪: {e}", "ERROR")
            return
        try:
            cfg = self._config_for_paths(paths)
            run_full_pipeline(paths, cfg)
            self.thumb_view.set_search_dirs(self._preview_dirs())
            self.thumb_view.sync_output_models()
            emit_log(f"已请求 Blender 完整流水线处理 {len(paths)} 个模型，请关注 Blender 输出。", "OK")
        except Exception as e:
            emit_log(f"完整流水线启动失败: {e}", "ERROR")
            QMessageBox.critical(self, "错误", str(e))

    def _action_render_previews(self):
        paths = self._selected_paths() or self._all_paths()
        if not paths:
            return
        try:
            from core.blender_bridge import render_white_previews
        except Exception as e:
            emit_log(f"Blender 桥接未就绪: {e}", "ERROR")
            return
        try:
            render_white_previews(paths, self._config_for_paths(paths))
            emit_log(f"已请求 Blender 生成 {len(paths)} 个白模预览。完成后切换/刷新预览图即可看到。", "OK")
            for delay in (5000, 15000, 30000):
                QTimer.singleShot(delay, self.thumb_view.refresh_previews)
        except Exception as e:
            emit_log(f"白模预览生成失败: {e}", "ERROR")
            QMessageBox.critical(self, "错误", str(e))

    def _action_ue_import(self):
        paths = self._selected_paths()
        if not paths:
            return
        try:
            from core.ue_bridge import batch_import
        except Exception as e:
            emit_log(f"UE 桥接未就绪: {e}", "ERROR")
            return
        mode = self._material_mode()
        try:
            engine_mode = self._engine_material_mode()
            if engine_mode == "embedded":
                emit_log("文件内嵌模式不会重新匹配贴图；请确认当前选中的是 _GUI_PipelineOutput 里的 GLB，而不是原始模型。", "INFO")
            emit_log("UE 实际导入文件: " + " | ".join(paths[:3]) + (" ..." if len(paths) > 3 else ""), "DEBUG")
            batch_import(paths, self._config_for_paths(paths), material_mode=engine_mode)
            emit_log(f"已请求 UE 导入 {len(paths)} 个模型（材质模式: {engine_mode}）", "OK")
        except Exception as e:
            emit_log(f"UE 导入失败: {e}", "ERROR")
            QMessageBox.critical(self, "错误", str(e))

    def _action_unity_import(self):
        paths = self._selected_paths()
        if not paths:
            return
        try:
            from core.unity_bridge import batch_import
        except Exception as e:
            emit_log(f"Unity 桥接未就绪: {e}", "ERROR")
            return
        mode = self._material_mode()
        try:
            engine_mode = self._engine_material_mode()
            if engine_mode == "embedded":
                emit_log("文件内嵌模式不会重新匹配贴图；请确认当前选中的是 _GUI_PipelineOutput 里的 GLB，而不是原始模型。", "INFO")
            emit_log("Unity 实际导入文件: " + " | ".join(paths[:3]) + (" ..." if len(paths) > 3 else ""), "DEBUG")
            batch_import(paths, self._config_for_paths(paths), material_mode=engine_mode)
            emit_log(f"已请求 Unity 导入 {len(paths)} 个模型（材质模式: {engine_mode}）", "OK")
        except Exception as e:
            emit_log(f"Unity 导入失败: {e}", "ERROR")
            QMessageBox.critical(self, "错误", str(e))

    def _action_settings(self):
        dlg = SettingsDialog(self.config, self)
        if dlg.exec():  # 非 0 即 Accepted；避免 PySide6 新版 dlg.Accepted 取不到的坑
            self.config = dlg.result_config()
            save_config(self.config)
            self.thumb_view.set_search_dirs(self._preview_dirs())
            self.thumb_view.sync_output_models()
            self.thumb_view.refresh_previews()
            emit_log("设置已保存", "OK")
