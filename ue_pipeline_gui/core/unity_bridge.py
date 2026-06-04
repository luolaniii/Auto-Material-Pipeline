# -*- coding: utf-8 -*-
"""
Unity 桥接：subprocess 调用 Unity 的 batchmode + -executeMethod 批量导入模型。
模式和 blender_bridge / ue_bridge 一致：写临时 JSON 配置 -> 启动外部程序读它。
Unity 端逻辑在 unity_scripts/BatchImporter.cs（首次运行由本模块部署到项目 Assets/Editor/）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

from gui.log_widget import emit_log

# OBJ 无单位元数据。Unity 1 单位 = 1 米。把“假定单位”换算成 ModelImporter.globalScale。
# （和 UE 不同：UE 是厘米，meter -> 100；Unity 是米，meter -> 1）
_UNITY_UNIT_SCALE = {
    "meter": 1.0,
    "centimeter": 0.01,
    "millimeter": 0.001,
    "inch": 0.0254,
    "foot": 0.3048,
}

GLTFAST_PKG = "com.unity.cloud.gltfast"
GLTFAST_VERSION = "6.9.0"  # 兼容 Unity 2021.3+ / 2023.2；如解析失败可在 manifest 改版本号


def _gui_root() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).resolve().parent.parent


def _scripts_dir() -> Path:
    return _gui_root() / "unity_scripts"


def _check_unity(config: Dict[str, Any]) -> tuple[Path, Path]:
    unity_exe = (config.get("unity_exe", "") or "").strip()
    proj = (config.get("unity_project", "") or "").strip()
    if not unity_exe:
        raise RuntimeError(
            "未填写 Unity.exe 路径，请在“设置”中填写。\n"
            "通常在 Unity Hub 安装目录，如 C:\\Program Files\\Unity\\Hub\\Editor\\<版本>\\Editor\\Unity.exe"
        )
    if not Path(unity_exe).exists():
        raise RuntimeError(f"Unity.exe 找不到这个文件：\n{unity_exe}\n请在“设置”里检查路径。")
    if not proj:
        raise RuntimeError("未填写 Unity 项目路径，请在“设置”中填写。")
    p = Path(proj)
    if not (p / "Assets").exists() or not (p / "ProjectSettings").exists():
        raise RuntimeError(f"这不是有效的 Unity 项目（缺 Assets/ 或 ProjectSettings/）：\n{proj}")
    return Path(unity_exe), p


def _project_locked(proj: Path) -> bool:
    """Unity 编辑器打开项目时会生成 Temp/UnityLockfile。batchmode 不能和它并发。"""
    return (proj / "Temp" / "UnityLockfile").exists()


def _project_pending_config(proj: Path) -> Path:
    return proj / "ProjectSettings" / "UEPipelineGUI_import.json"


def _write_project_pending_config(proj: Path, payload: Dict[str, Any]) -> Path:
    cfg_path = _project_pending_config(proj)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg_path


def _touch_csharp(proj: Path) -> None:
    script = proj / "Assets" / "Editor" / "BatchImporter.cs"
    if script.exists():
        now = time.time()
        os.utime(script, (now, now))


def _manifest_path(proj: Path) -> Path:
    return proj / "Packages" / "manifest.json"


def _gltfast_in_manifest(proj: Path) -> bool:
    try:
        mf = _manifest_path(proj)
        return mf.exists() and GLTFAST_PKG in mf.read_text(encoding="utf-8")
    except Exception:
        return False


def _ensure_gltfast(proj: Path) -> bool:
    """往 manifest.json 的 dependencies 加 gltfast 包（Unity 启动时自动下载）。"""
    mf = _manifest_path(proj)
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
        deps = data.setdefault("dependencies", {})
        if GLTFAST_PKG not in deps:
            deps[GLTFAST_PKG] = GLTFAST_VERSION
            mf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            emit_log(f"已往 Unity 项目 manifest.json 加入 {GLTFAST_PKG} {GLTFAST_VERSION}（首次启动会联网下载）", "OK")
        return True
    except Exception as e:
        emit_log(f"自动添加 gltfast 失败，请手动在 Package Manager 装 {GLTFAST_PKG}: {e}", "WARN")
        return False


def _deploy_csharp(proj: Path) -> None:
    """把 BatchImporter.cs 复制到 项目 Assets/Editor/（内容有变才覆盖）。"""
    src = _scripts_dir() / "BatchImporter.cs"
    if not src.exists():
        raise RuntimeError(f"Unity 端脚本不存在: {src}")
    editor_dir = proj / "Assets" / "Editor"
    editor_dir.mkdir(parents=True, exist_ok=True)
    dst = editor_dir / "BatchImporter.cs"
    try:
        need = (not dst.exists()) or (dst.read_text(encoding="utf-8") != src.read_text(encoding="utf-8"))
        if need:
            shutil.copyfile(src, dst)
            emit_log(f"已部署 Unity 导入脚本: {dst}", "DEBUG")
    except Exception as e:
        raise RuntimeError(f"部署 BatchImporter.cs 失败: {e}")


def _write_temp_json(payload: Dict[str, Any]) -> Path:
    fd, path = tempfile.mkstemp(prefix="ue_pipeline_gui_unity_", suffix=".json")
    os.close(fd)
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return Path(path)


def _unity_import_log_file() -> Path:
    log_file = Path(tempfile.gettempdir()) / "UEPipelineGUI" / "unity_import.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    return log_file


def _tail_log(log_file: Path, proc: subprocess.Popen, timeout: float = 1800.0) -> None:
    """轮询 Unity 日志，转发含 [Unity-Pipeline] 或 error 的行，直到进程结束。"""
    seen = 0
    start = time.time()
    while proc.poll() is None and (time.time() - start) < timeout:
        try:
            if log_file.exists():
                lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                for ln in lines[seen:]:
                    low = ln.lower()
                    if "[unity-pipeline]" in low:
                        emit_log(ln.strip(), "INFO")
                    elif "error" in low and "0 error" not in low:
                        emit_log(ln.strip(), "WARN")
                seen = len(lines)
        except Exception:
            pass
        time.sleep(1.0)


def batch_import(paths: List[str], config: Dict[str, Any], material_mode: str = "embedded") -> None:
    """
    批量导入选中模型到 Unity 项目。material_mode 同 UE：
      none / embedded（用文件内嵌材质）。
    """
    material_mode = material_mode if material_mode in ("none", "embedded") else "embedded"
    unity_exe, proj = _check_unity(config)
    project_locked = _project_locked(proj)
    if project_locked:
        emit_log("检测到 Unity 项目已打开，将通过项目内自动执行脚本导入，不启动 batchmode。", "INFO")

    # GLB 需要 gltfast 包
    has_glb = any(Path(p).suffix.lower() in (".glb", ".gltf") for p in paths)
    glb_supported = _gltfast_in_manifest(proj)
    if has_glb and not glb_supported:
        if _ensure_gltfast(proj):
            glb_supported = True
            emit_log("Unity 首次启动会下载 gltfast 包，需联网且耗时较长。", "INFO")
        else:
            emit_log("没有 gltfast，GLB 本次会被跳过；FBX/OBJ 正常导入。", "WARN")

    try:
        _deploy_csharp(proj)
    except Exception as e:
        emit_log(str(e), "ERROR")
        return

    obj_scale = _UNITY_UNIT_SCALE.get(config.get("obj_assumed_unit", "meter"), 1.0)

    log_file = _unity_import_log_file()
    payload = {
        "files": paths,
        "dest_subdir": config.get("unity_assets_subdir", "Imports") or "Imports",
        "material_mode": material_mode,
        "obj_scale": obj_scale,
        "glb_supported": bool(glb_supported),
        "log_file": str(log_file),
    }
    cfg_path = _write_temp_json(payload)

    if project_locked:
        try:
            pending = _write_project_pending_config(proj, payload)
            _touch_csharp(proj)
            emit_log(f"已写入 Unity 打开状态导入配置: {pending}", "OK")
            emit_log(f"Unity 导入日志: {log_file}", "INFO")
            emit_log("Unity 会在脚本刷新/编译后自动导入；如未触发，请在 Unity 中点一下窗口或执行 Assets > Refresh。", "INFO")
        except Exception as e:
            emit_log(f"写入 Unity 打开状态导入配置失败: {e}", "ERROR")
        return

    env = os.environ.copy()
    env["UE_PIPELINE_UNITY_CONFIG"] = str(cfg_path)

    cmd = [
        str(unity_exe),
        "-batchmode", "-quit",
        "-projectPath", str(proj),
        "-executeMethod", "BatchImporter.Run",
        "-logFile", str(log_file),
    ]

    emit_log(f"启动 Unity 批处理导入（材质模式: {material_mode}, OBJ 缩放 x{obj_scale}）…", "INFO")
    emit_log(f"Unity 导入日志: {log_file}", "INFO")
    emit_log("Unity batchmode 冷启动较慢（首次还要编译脚本/下载包），进度看日志，请耐心等。", "INFO")

    def _worker():
        try:
            proc = subprocess.Popen(cmd, env=env)
            _tail_log(log_file, proc)
            rc = proc.wait()
            if rc == 0:
                emit_log("Unity 导入完成（返回码 0）。资产在项目 Assets/" + payload["dest_subdir"] + " 下。", "OK")
                try:
                    subprocess.Popen([str(unity_exe), "-projectPath", str(proj)])
                    emit_log("已打开 Unity 项目供检查导入结果。", "OK")
                except Exception as e:
                    emit_log(f"导入完成，但打开 Unity 编辑器失败: {e}", "WARN")
            else:
                emit_log(f"Unity 导入进程返回码 {rc}（可能是脚本编译错误或导入异常）。日志: {log_file}", "ERROR")
        except Exception as e:
            emit_log(f"启动 Unity 失败: {e}", "ERROR")

    threading.Thread(target=_worker, daemon=True).start()
