# -*- coding: utf-8 -*-
"""预览图缓存：为每个模型文件生成稳定的缩略图缓存路径。"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_PREVIEW_INDEX = {}


def cache_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    d = base / "thumbnails"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def thumb_path_for(model_path: str | Path) -> Path:
    """模型文件 -> 唯一缩略图缓存路径（基于绝对路径 hash，避免重名冲突）。"""
    key = str(Path(model_path).resolve()).lower()
    h = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
    return cache_dir() / f"{h}.png"


def clear_preview_index(search_dirs=None) -> None:
    if search_dirs is None:
        _PREVIEW_INDEX.clear()
        return
    for d in search_dirs:
        try:
            key = str(Path(d).resolve()).lower()
            _PREVIEW_INDEX.pop(key, None)
        except Exception:
            pass


def _preview_index_for(root: Path):
    key = str(root.resolve()).lower()
    cached = _PREVIEW_INDEX.get(key)
    if cached is not None:
        return cached

    index = {}
    try:
        for png in root.rglob("*.png"):
            if png.is_file():
                index.setdefault(png.stem.lower(), png)
    except OSError:
        pass
    _PREVIEW_INDEX[key] = index
    return index


def find_preview_for(model_path: str | Path, search_dirs=None):
    """
    找模型对应的预览图 PNG（复用原脚本流水线 render_glb 渲染出的同名 PNG）。
    查找顺序：① 各搜索目录里的同名 PNG；② 模型同级/模型旁流水线输出；③ 缩略图缓存。
    这些 PNG 通常只有跑完 Blender 完整流水线后才会生成。
    找不到返回 None。
    """
    mp = Path(model_path)
    stem = mp.stem

    # 在搜索目录里找同名 PNG。_GUI_PipelineOutput 会按分类目录/uvfixed 等子目录
    # 输出模型和预览图，所以这里建立一次递归索引，再按模型名匹配。
    for d in (search_dirs or []):
        if not d:
            continue
        d = Path(d)
        if not d.exists():
            continue
        direct = d / f"{stem}.png"
        if direct.exists():
            return direct
        sub = d / "_GUI_PipelineOutput" / f"{stem}.png"
        if sub.exists():
            return sub
        indexed = _preview_index_for(d).get(stem.lower())
        if indexed:
            return indexed

    same = mp.with_suffix(".png")
    if same.exists():
        return same

    # 模型旁边的流水线输出目录（GLB/预览图都在这），与模型同名的 PNG
    pipeline_png = mp.parent / "_GUI_PipelineOutput" / f"{stem}.png"
    if pipeline_png.exists():
        return pipeline_png

    cached = thumb_path_for(model_path)
    if cached.exists():
        return cached
    return None
