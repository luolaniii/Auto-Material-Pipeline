# -*- coding: utf-8 -*-
"""配置加载/保存，集中管理用户偏好。"""

import json
import sys
from pathlib import Path
from typing import Any, Dict

CONFIG_FILENAME = "config.json"
EXAMPLE_FILENAME = "config.example.json"
DEFAULT_UNITY_EXE = "D:/Unity/Unity 2023.2.20f1/Editor/Unity.exe"


def _writable_root() -> Path:
    """配置读写目录：打包后用 exe 同级（持久保存），开发时用项目根目录。
    注意：绝不能用 __file__，因为打包后它指向 _MEIPASS 临时解压目录，重启即丢。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _bundle_root() -> Path:
    """打包内置只读资源目录（config.example.json 被 --add-data 解压到这里）。"""
    return Path(getattr(sys, "_MEIPASS", _writable_root()))


def _config_path() -> Path:
    return _writable_root() / CONFIG_FILENAME


def _example_path() -> Path:
    cand = _writable_root() / EXAMPLE_FILENAME
    if cand.exists():
        return cand
    return _bundle_root() / EXAMPLE_FILENAME


DEFAULT_CONFIG: Dict[str, Any] = {
    "blender_exe": "",
    "ue_editor_exe": "",
    "ue_project": "",
    "ue_content_root": "/Game/Imports",
    "pipeline_script": "",
    "default_unit": "centimeter",
    "obj_assumed_unit": "centimeter",
    "obj_orientation": "x_m90",
    "material_search_root": "",
    "texture_search_root": "",
    "preview_dir": "",
    "texture_keyword_tokens": {},
    "texture_keywords_override": False,
    "basecolor_tokens": "",
    "metallic_tokens": "",
    "roughness_tokens": "",
    "normal_tokens": "",
    "metallic_roughness_tokens": "",
    "roughness_specular_metallic_tokens": "",
    "flexible_tokens": "",
    "bad_texture_tokens": "",
    "best_match_basecolor_tokens": "",
    "unity_exe": DEFAULT_UNITY_EXE,
    "unity_project": "E:/Projects/Unity/Test_Python",
    "unity_assets_subdir": "Imports",
}


def load_config() -> Dict[str, Any]:
    cfg_path = _config_path()
    if not cfg_path.exists():
        example = _example_path()
        if example.exists():
            try:
                data = json.loads(example.read_text(encoding="utf-8"))
                merged = {**DEFAULT_CONFIG, **data}
                if not merged.get("unity_exe"):
                    merged["unity_exe"] = DEFAULT_UNITY_EXE
                save_config(merged)
                return merged
            except Exception:
                pass
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)

    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        merged = {**DEFAULT_CONFIG, **data}
        if not merged.get("unity_exe"):
            merged["unity_exe"] = DEFAULT_UNITY_EXE
        return merged
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_config(cfg: Dict[str, Any]) -> None:
    _config_path().write_text(
        json.dumps(cfg, ensure_ascii=False, indent=4),
        encoding="utf-8",
    )
