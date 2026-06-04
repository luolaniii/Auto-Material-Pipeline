# -*- coding: utf-8 -*-
"""Blender 桥接：subprocess 调用 blender，两种模式 —— 快速打开、完整流水线。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from gui.log_widget import emit_log
from core.thumbnails import thumb_path_for


def _check_blender_exe(blender_exe: str) -> Path:
    if not blender_exe:
        raise RuntimeError("未配置 Blender 路径，请先在“设置”中填写 blender.exe。")
    p = Path(blender_exe)
    if not p.exists():
        raise RuntimeError(f"Blender 不存在: {blender_exe}")
    return p


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "blender_scripts"


def _write_temp_json(payload: Dict[str, Any]) -> Path:
    fd, path = tempfile.mkstemp(prefix="ue_pipeline_gui_", suffix=".json")
    os.close(fd)
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return Path(path)


def _keyword_payload(config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "texture_keyword_tokens": config.get("texture_keyword_tokens", {}),
        "texture_keywords_override": config.get("texture_keywords_override", False),
        "basecolor_tokens": config.get("basecolor_tokens", ""),
        "metallic_tokens": config.get("metallic_tokens", ""),
        "roughness_tokens": config.get("roughness_tokens", ""),
        "normal_tokens": config.get("normal_tokens", ""),
        "metallic_roughness_tokens": config.get("metallic_roughness_tokens", ""),
        "roughness_specular_metallic_tokens": config.get("roughness_specular_metallic_tokens", ""),
        "flexible_tokens": config.get("flexible_tokens", ""),
        "bad_texture_tokens": config.get("bad_texture_tokens", ""),
        "best_match_basecolor_tokens": config.get("best_match_basecolor_tokens", ""),
    }


def _spawn_blender(blender_exe: Path, args: List[str], cwd: Path = None) -> subprocess.Popen:
    cmd = [str(blender_exe), *args]
    emit_log(f"启动 Blender: {' '.join(cmd)}", "DEBUG")
    return subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
    )


def _output_dir_for(paths: List[str], fallback_root: str, selected_root: str = "") -> Path:
    """流水线输出目录：优先使用用户添加目录时选择的根目录。"""
    if selected_root:
        p = Path(selected_root)
        if p.exists():
            return p / "_GUI_PipelineOutput"

    try:
        parents = [str(Path(p).resolve().parent) for p in paths]
        if not parents:
            base = fallback_root
        elif len(set(parents)) == 1:
            base = parents[0]
        else:
            base = os.path.commonpath(parents)
    except Exception:
        base = fallback_root
    return Path(base) / "_GUI_PipelineOutput"


def open_in_blender(paths: List[str], config: Dict[str, Any], material_mode: str = "none") -> None:
    """GUI 模式启动 Blender，加载选中的模型让美术查看。"""
    blender = _check_blender_exe(config.get("blender_exe", ""))

    payload: Dict[str, Any] = {
        "files": paths,
        "material_mode": material_mode,
    }

    if material_mode == "rebuild":
        pipeline_script = config.get("pipeline_script", "")
        if not pipeline_script or not Path(pipeline_script).exists():
            raise RuntimeError("自建匹配需要原 Blender 流水线脚本（ue_obj_to_glb_pipeline.py）。请在“设置”中指定。")

        material_root = (config.get("material_search_root", "") or "").strip()
        texture_root = (config.get("texture_search_root", "") or "").strip()
        selected_root = (config.get("model_root", "") or config.get("pipeline_output_root", "") or "").strip()
        resource_root = selected_root or texture_root or material_root
        if not resource_root:
            raise RuntimeError("自建匹配至少需要“贴图根目录”。请在“设置”中填写贴图根目录。")
        if not Path(resource_root).exists():
            raise RuntimeError(f"资源目录不存在: {resource_root}")

        effective_material_root = selected_root or material_root or resource_root
        effective_texture_root = selected_root or texture_root or resource_root
        payload.update({
            "pipeline_script": str(Path(pipeline_script).resolve()),
            "material_root": str(Path(effective_material_root).resolve()),
            "texture_root": str(Path(effective_texture_root).resolve()),
            "model_root": str(Path(selected_root).resolve()) if selected_root else "",
        })
        payload.update(_keyword_payload(config))

    cfg_path = _write_temp_json(payload)

    script = _scripts_dir() / "open_models.py"
    if not script.exists():
        raise RuntimeError(f"快速打开脚本不存在: {script}")

    _spawn_blender(blender, ["--python", str(script), "--", str(cfg_path)])


def run_full_pipeline(paths: List[str], config: Dict[str, Any]) -> None:
    """
    headless 模式调用 Blender 跑完整流水线。
    payload 包含：选中文件 + 资源根目录 + 流水线脚本路径 + 输出目录。
    """
    blender = _check_blender_exe(config.get("blender_exe", ""))

    pipeline_script = config.get("pipeline_script", "")
    if not pipeline_script or not Path(pipeline_script).exists():
        raise RuntimeError("未配置或找不到原 Blender 流水线脚本（ue_obj_to_glb_pipeline.py）。请在“设置”中指定。")

    material_root = (config.get("material_search_root", "") or "").strip()
    texture_root = (config.get("texture_search_root", "") or "").strip()

    # JSON 可选：没有 JSON 时，原脚本会自动按贴图文件名匹配。
    # 所以只要求“至少有一个目录用来定位贴图”（贴图根目录 或 材质 JSON 根目录）。
    selected_root = (config.get("pipeline_output_root", "") or config.get("model_root", "") or "").strip()
    resource_root = selected_root or texture_root or material_root
    if not resource_root:
        raise RuntimeError(
            "完整流水线至少需要“贴图根目录”（没有 JSON 也能按贴图文件名匹配）。请在“设置”中填写贴图根目录。"
        )
    if not Path(resource_root).exists():
        raise RuntimeError(f"资源目录不存在: {resource_root}")

    # material_root 为空时用 resource_root：原脚本在其中找 JSON，找不到就走贴图文件名匹配。
    effective_material_root = selected_root or material_root or resource_root
    effective_texture_root = selected_root or texture_root or resource_root

    dst_root = _output_dir_for(paths, resource_root, selected_root)

    payload = {
        "pipeline_script": str(Path(pipeline_script).resolve()),
        "material_root": str(Path(effective_material_root).resolve()),
        "texture_root": str(Path(effective_texture_root).resolve()),
        "dst_root": str(dst_root.resolve()),
        "model_root": str(Path(selected_root).resolve()) if selected_root else "",
        "files": paths,
    }
    payload.update(_keyword_payload(config))
    emit_log(f"流水线输出目录: {dst_root}", "INFO")
    cfg_path = _write_temp_json(payload)

    adapter = _scripts_dir() / "pipeline_adapter.py"
    if not adapter.exists():
        raise RuntimeError(f"流水线适配器脚本不存在: {adapter}")

    _spawn_blender(blender, ["-b", "--python", str(adapter), "--", str(cfg_path)])


def render_white_previews(paths: List[str], config: Dict[str, Any]) -> None:
    """Headless Blender 生成原始模型白模缩略图，输出到 GUI 缩略图缓存。"""
    blender = _check_blender_exe(config.get("blender_exe", ""))
    jobs = []
    for p in paths:
        try:
            thumb = thumb_path_for(p)
            jobs.append({"model": str(Path(p).resolve()), "thumbnail": str(thumb.resolve())})
        except Exception:
            continue
    if not jobs:
        raise RuntimeError("没有可生成预览的模型路径。")

    log_file = Path(tempfile.gettempdir()) / "UEPipelineGUI" / "thumbnail_render.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        log_file.write_text("", encoding="utf-8")
    except Exception:
        pass

    payload = {"jobs": jobs, "log_file": str(log_file)}
    cfg_path = _write_temp_json(payload)
    script = _scripts_dir() / "render_thumbnails.py"
    if not script.exists():
        raise RuntimeError(f"白模预览脚本不存在: {script}")

    emit_log(f"白模预览日志: {log_file}", "INFO")
    _spawn_blender(blender, ["-b", "--python", str(script), "--", str(cfg_path)])
