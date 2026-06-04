# -*- coding: utf-8 -*-
"""设置对话框：配置 Blender / UE 路径、贴图根目录、默认单位等。"""

from pathlib import Path
from typing import Dict, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.keyword_classifier import (
    TEXTURE_KEYWORD_ROLES,
    default_texture_keyword_tokens,
    format_config_tokens,
    load_texture_keyword_tokens_from_script,
    split_config_tokens,
    texture_keyword_tokens_from_config,
)


def _path_picker(parent: QWidget, line: QLineEdit, mode: str, caption: str, filt: str = ""):
    def pick():
        if mode == "file":
            path, _ = QFileDialog.getOpenFileName(parent, caption, line.text(), filt)
        elif mode == "dir":
            path = QFileDialog.getExistingDirectory(parent, caption, line.text())
        else:
            return
        if path:
            line.setText(path)

    btn = QPushButton("浏览…")
    btn.clicked.connect(pick)
    return btn


class SettingsDialog(QDialog):
    def __init__(self, config: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setModal(True)
        self.resize(900, 760)
        self._config = dict(config)

        root = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        form = QFormLayout(content)
        form.setLabelAlignment(Qt.AlignRight)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # Blender
        self.blender_edit = QLineEdit(self._config.get("blender_exe", ""))
        form.addRow("Blender 可执行文件", self._row_with_picker(
            self.blender_edit, "file", "选择 blender.exe", "Blender (blender.exe);;所有文件 (*.*)"
        ))

        # UE Editor
        self.ue_edit = QLineEdit(self._config.get("ue_editor_exe", ""))
        form.addRow("UE Editor (-Cmd)", self._row_with_picker(
            self.ue_edit, "file", "选择 UnrealEditor-Cmd.exe", "UE Editor (UnrealEditor-Cmd.exe UnrealEditor.exe);;所有文件 (*.*)"
        ))

        # UE Project
        self.ue_proj_edit = QLineEdit(self._config.get("ue_project", ""))
        form.addRow("UE 项目 (.uproject)", self._row_with_picker(
            self.ue_proj_edit, "file", "选择 .uproject", "UE 项目 (*.uproject);;所有文件 (*.*)"
        ))

        # UE Content root
        self.ue_content_edit = QLineEdit(self._config.get("ue_content_root", "/Game/Imports"))
        form.addRow("UE 导入目标 (Content 相对路径)", self.ue_content_edit)

        # Pipeline script
        self.pipeline_edit = QLineEdit(self._config.get("pipeline_script", ""))
        form.addRow("Blender 流水线脚本", self._row_with_picker(
            self.pipeline_edit, "file", "选择 ue_obj_to_glb_pipeline.py", "Python (*.py)"
        ))

        # 材质 / 贴图根目录（可选）
        self.material_root_edit = QLineEdit(self._config.get("material_search_root", ""))
        form.addRow("材质 JSON 根目录（可选）", self._row_with_picker(
            self.material_root_edit, "dir", "选择材质根目录"
        ))

        self.texture_root_edit = QLineEdit(self._config.get("texture_search_root", ""))
        form.addRow("贴图根目录（可选）", self._row_with_picker(
            self.texture_root_edit, "dir", "选择贴图根目录"
        ))

        # 预览图目录（可选）：原 Blender 流水线渲染出的同名 PNG 所在目录
        self.preview_dir_edit = QLineEdit(self._config.get("preview_dir", ""))
        form.addRow("预览图目录（可选，预览图视图用）", self._row_with_picker(
            self.preview_dir_edit, "dir", "选择预览图目录"
        ))

        form.addRow(self._build_keyword_panel())

        # Unity
        self.unity_exe_edit = QLineEdit(self._config.get("unity_exe", ""))
        form.addRow("Unity.exe", self._row_with_picker(
            self.unity_exe_edit, "file", "选择 Unity.exe",
            "Unity (Unity.exe);;所有文件 (*.*)"
        ))

        self.unity_proj_edit = QLineEdit(self._config.get("unity_project", ""))
        form.addRow("Unity 项目目录", self._row_with_picker(
            self.unity_proj_edit, "dir", "选择 Unity 项目（含 Assets/ProjectSettings）"
        ))

        self.unity_subdir_edit = QLineEdit(self._config.get("unity_assets_subdir", "Imports"))
        form.addRow("Unity 导入子目录 (Assets 下)", self.unity_subdir_edit)

        # 默认单位
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["centimeter", "meter", "millimeter", "inch", "foot"])
        cur = self._config.get("obj_assumed_unit", "centimeter")
        if cur in [self.unit_combo.itemText(i) for i in range(self.unit_combo.count())]:
            self.unit_combo.setCurrentText(cur)
        form.addRow("OBJ 假定单位（OBJ 无单位元数据）", self.unit_combo)

        # OBJ 朝向修正（OBJ 无坐标系元数据，导入 UE 常躺倒/颠倒，按需选预设）
        self.orient_combo = QComboBox()
        self._orient_options = [
            ("绕X轴 -90°（竖直·推荐）", "x_m90"),
            ("绕X轴 +90°（竖直·反向）", "x_p90"),
            ("绕X轴 180°（上下翻转）", "x_180"),
            ("绕Z轴 180°（水平转身）", "z_180"),
            ("自动检测（最长轴朝上）", "auto"),
            ("不修正（保持原样）", "none"),
        ]
        for label, val in self._orient_options:
            self.orient_combo.addItem(label, val)
        cur_orient = self._config.get("obj_orientation", "x_m90")
        for i, (_, val) in enumerate(self._orient_options):
            if val == cur_orient:
                self.orient_combo.setCurrentIndex(i)
                break
        form.addRow("OBJ 朝向修正（躺倒/颠倒时换一个）", self.orient_combo)

        tip = QLabel(
            "提示：仅 OBJ 没有单位/坐标系信息。模型躺倒或上下颠倒时，换一个朝向预设重新导入即可"
            "（同一批模型来源一致，选对一个就全部正确）。FBX / GLB 自带单位和朝向。"
        )
        tip.setStyleSheet("color: #808080; font-size: 12px;")
        tip.setWordWrap(True)
        root.addWidget(tip)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _script_default_keyword_tokens(self) -> Dict[str, str]:
        script_tokens = load_texture_keyword_tokens_from_script(self.pipeline_edit.text().strip())
        defaults = default_texture_keyword_tokens()
        if script_tokens:
            defaults.update(script_tokens)
        return defaults

    def _build_keyword_panel(self) -> QGroupBox:
        label_map = {
            "basecolor": "基础色 BaseColor",
            "metallic": "金属度 Metallic",
            "roughness": "粗糙度 Roughness",
            "metallic_roughness": "混合贴图 MR/ORM/AORM",
            "normal": "法线 Normal",
            "roughness_specular_metallic": "RSM 混合贴图",
            "flexible": "万能匹配 Flexible",
            "bad": "排除/非基础色 Bad",
            "best_match_basecolor": "优先基础色 token",
        }

        defaults = self._script_default_keyword_tokens()
        values = texture_keyword_tokens_from_config(self._config, defaults)
        self.keyword_token_edits: Dict[str, QTextEdit] = {}

        box = QGroupBox("贴图匹配关键词 token")
        outer = QVBoxLayout(box)

        tip = QLabel("每行、逗号或分号分隔一个 token。保存后会覆盖脚本默认表；点“重载脚本默认关键词”可重新从流水线脚本读取。")
        tip.setStyleSheet("color: #707070; font-size: 12px;")
        tip.setWordWrap(True)
        outer.addWidget(tip)

        keyword_form = QFormLayout()
        keyword_form.setLabelAlignment(Qt.AlignRight)
        outer.addLayout(keyword_form)

        for role, _, _ in TEXTURE_KEYWORD_ROLES:
            edit = QTextEdit()
            edit.setAcceptRichText(False)
            edit.setLineWrapMode(QTextEdit.NoWrap)
            edit.setPlainText(values.get(role, ""))
            edit.setFixedHeight(74 if role != "bad" else 118)
            self.keyword_token_edits[role] = edit
            keyword_form.addRow(label_map.get(role, role), edit)

        reset_btn = QPushButton("重载脚本默认关键词")
        reset_btn.clicked.connect(self._reload_keyword_defaults)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(reset_btn)
        outer.addLayout(btn_row)

        return box

    def _reload_keyword_defaults(self):
        defaults = self._script_default_keyword_tokens()
        for role, edit in self.keyword_token_edits.items():
            edit.setPlainText(defaults.get(role, ""))

    def _collect_keyword_tokens(self) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for role, edit in self.keyword_token_edits.items():
            result[role] = format_config_tokens(split_config_tokens(edit.toPlainText()))
        return result

    def _row_with_picker(self, line: QLineEdit, mode: str, caption: str, filt: str = "") -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line, 1)
        layout.addWidget(_path_picker(self, line, mode, caption, filt))
        return w

    def result_config(self) -> Dict[str, Any]:
        cfg = dict(self._config)
        cfg["blender_exe"] = self.blender_edit.text().strip()
        cfg["ue_editor_exe"] = self.ue_edit.text().strip()
        cfg["ue_project"] = self.ue_proj_edit.text().strip()
        cfg["ue_content_root"] = self.ue_content_edit.text().strip() or "/Game/Imports"
        cfg["pipeline_script"] = self.pipeline_edit.text().strip()
        cfg["material_search_root"] = self.material_root_edit.text().strip()
        cfg["texture_search_root"] = self.texture_root_edit.text().strip()
        cfg["preview_dir"] = self.preview_dir_edit.text().strip()
        keyword_tokens = self._collect_keyword_tokens()
        cfg["texture_keyword_tokens"] = keyword_tokens
        cfg["texture_keywords_override"] = True
        # 同步旧字段，兼容旧的外部脚本/旧打包版本。
        cfg["basecolor_tokens"] = keyword_tokens.get("basecolor", "")
        cfg["metallic_tokens"] = keyword_tokens.get("metallic", "")
        cfg["roughness_tokens"] = keyword_tokens.get("roughness", "")
        cfg["normal_tokens"] = keyword_tokens.get("normal", "")
        cfg["metallic_roughness_tokens"] = keyword_tokens.get("metallic_roughness", "")
        cfg["roughness_specular_metallic_tokens"] = keyword_tokens.get("roughness_specular_metallic", "")
        cfg["flexible_tokens"] = keyword_tokens.get("flexible", "")
        cfg["bad_texture_tokens"] = keyword_tokens.get("bad", "")
        cfg["best_match_basecolor_tokens"] = keyword_tokens.get("best_match_basecolor", "")
        cfg["unity_exe"] = self.unity_exe_edit.text().strip()
        cfg["unity_project"] = self.unity_proj_edit.text().strip()
        cfg["unity_assets_subdir"] = self.unity_subdir_edit.text().strip() or "Imports"
        cfg["obj_assumed_unit"] = self.unit_combo.currentText()
        cfg["obj_orientation"] = self.orient_combo.currentData()
        return cfg
