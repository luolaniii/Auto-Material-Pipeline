# -*- coding: utf-8 -*-
"""
UE 桥接：把批量导入任务送进 Unreal。两条路线：
  A. UE 已运行 + 启用了 Python Remote Execution -> 通过 UDP 直接发任务（秒级，不重启）
  B. UE 没运行 -> 启动完整编辑器 + -ExecCmds 执行导入并保持打开
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gui.log_widget import emit_log


def _check_ue(config: Dict[str, Any]) -> tuple[Path, Path]:
    ue_exe = (config.get("ue_editor_exe", "") or "").strip()
    uproj = (config.get("ue_project", "") or "").strip()

    if not ue_exe:
        raise RuntimeError("未填写 UnrealEditor-Cmd.exe 路径，请在“设置”中填写。")
    if not Path(ue_exe).exists():
        raise RuntimeError(
            f"UnrealEditor-Cmd.exe 找不到这个文件：\n{ue_exe}\n"
            "请在“设置”里把路径改成你 UE 的真实安装位置（通常在 引擎目录\\Engine\\Binaries\\Win64\\）。"
        )

    if not uproj:
        raise RuntimeError("未填写 .uproject 文件路径，请在“设置”中填写。")
    if not Path(uproj).exists():
        raise RuntimeError(f".uproject 找不到这个文件：\n{uproj}\n请在“设置”里检查路径。")

    return Path(ue_exe), Path(uproj)


def _full_editor_exe(cmd_exe: Path) -> Path:
    """
    把 UnrealEditor-Cmd.exe 换成同目录的 UnrealEditor.exe（完整编辑器）。
    完整编辑器执行完 -ExecutePythonScript 后会保持打开，便于美术查看结果。
    若同目录没有完整编辑器，则回退用传入的可执行文件。
    """
    if cmd_exe.name.lower() == "unrealeditor-cmd.exe":
        full = cmd_exe.with_name("UnrealEditor.exe")
        if full.exists():
            return full
    return cmd_exe


def _ue_running() -> bool:
    """检测是否有 UnrealEditor.exe 正在运行。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq UnrealEditor.exe", "/NH"],
            capture_output=True, text=True, timeout=10,
        ).stdout or ""
        return "UnrealEditor.exe" in out
    except Exception:
        return False


def _find_remote_execution_module(ue_exe: Path) -> Optional[Path]:
    """从 UE 可执行文件往上找到 Engine 目录，定位官方 remote_execution.py。"""
    try:
        for parent in ue_exe.resolve().parents:
            if parent.name == "Engine":
                cand = (parent / "Plugins" / "Experimental" / "PythonScriptPlugin"
                        / "Content" / "Python" / "remote_execution.py")
                return cand if cand.exists() else None
    except Exception:
        pass
    return None


def _send_via_remote(ue_exe: Path, script: Path, cfg_path: Path, wait_seconds: float = 8.0) -> Tuple[bool, str]:
    """
    用 UE 官方 remote_execution.py 把导入脚本发给正在运行的编辑器执行。
    需要 UE 端已启用 Python Remote Execution（项目设置 > 插件 > Python）。
    wait_seconds: 等待发现 UE 节点的时长（刚启动 UE 时给长一点，等它加载完）。
    返回 (是否成功, 说明)。
    """
    mod_path = _find_remote_execution_module(ue_exe)
    if not mod_path:
        return False, "找不到 remote_execution.py（无法定位 UE 引擎目录）"

    try:
        spec = importlib.util.spec_from_file_location("ue_remote_execution", str(mod_path))
        rexec = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rexec)
    except Exception as e:
        return False, f"加载 remote_execution 失败: {e}"

    remote = rexec.RemoteExecution()
    try:
        remote.start()
        nodes = []
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            nodes = remote.remote_nodes
            if nodes:
                break
            time.sleep(0.5)
        if not nodes:
            return False, "在限定时间内未发现 UE 节点（未启用 Remote Execution 或编辑器未就绪）"

        node_id = nodes[0]["node_id"]
        remote.open_command_connection(node_id)
        # 在 UE 进程里设置环境变量并以 __main__ 方式运行导入脚本
        py_code = (
            "import os, runpy\n"
            f'os.environ["UE_PIPELINE_CONFIG"] = r"{cfg_path}"\n'
            f'runpy.run_path(r"{script}", run_name="__main__")\n'
        )
        result = remote.run_command(
            py_code, unattended=True,
            exec_mode=rexec.MODE_EXEC_FILE, raise_on_failure=False,
        )
        remote.close_command_connection()
        if result and result.get("success"):
            return True, "ok"
        return False, f"远程执行返回失败: {result}"
    except Exception as e:
        return False, f"远程执行异常: {e}"
    finally:
        try:
            remote.stop()
        except Exception:
            pass


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "ue_scripts"


def _gui_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _write_temp_json(payload: Dict[str, Any]) -> Path:
    fd, path = tempfile.mkstemp(prefix="ue_pipeline_gui_ue_", suffix=".json")
    os.close(fd)
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return Path(path)


def _ue_import_log_file() -> Path:
    log_file = Path(tempfile.gettempdir()) / "UEPipelineGUI" / "ue_import.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    return log_file


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


def batch_import(paths: List[str], config: Dict[str, Any], material_mode: str = "embedded") -> None:
    """
    批量导入选中模型到 UE。material_mode:
      "none"     - 只导网格，删掉导入器顺带产生的材质/贴图
      "embedded" - 用文件自带的内嵌材质（GLB / 带材质 FBX），导入后整理到子文件夹
    """
    material_mode = material_mode if material_mode in ("none", "embedded") else "embedded"
    ue_exe, uproj = _check_ue(config)

    # OBJ 没有单位元数据，UE 默认按 1 单位=1cm 导入。若模型实际是米，需 ×100。
    # 这个倍数只作用于 .obj（FBX/GLB 自带单位，UE 会正确读取）。
    unit_scale = {
        "meter": 100.0,
        "centimeter": 1.0,
        "millimeter": 0.1,
        "inch": 2.54,
        "foot": 30.48,
    }
    obj_scale = unit_scale.get(config.get("obj_assumed_unit", "centimeter"), 1.0)

    log_file = _ue_import_log_file()
    try:
        log_file.write_text(
            f"=== UE Pipeline import {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n",
            encoding="utf-8",
        )
    except Exception:
        pass

    payload = {
        "files": paths,
        "dest": config.get("ue_content_root", "/Game/Imports") or "/Game/Imports",
        "material_mode": material_mode,
        "gui_root": str(_gui_root()),
        "obj_scale": obj_scale,
        "obj_orientation": config.get("obj_orientation", "x_m90"),
        "log_file": str(log_file),
    }
    payload.update(_keyword_payload(config))
    if obj_scale != 1.0:
        emit_log(f"OBJ 单位={config.get('obj_assumed_unit')}，导入时 OBJ 将缩放 x{obj_scale}", "INFO")
    emit_log(f"材质模式: {material_mode}", "INFO")
    emit_log(f"UE 导入日志: {log_file}", "INFO")
    cfg_path = _write_temp_json(payload)

    script = _scripts_dir() / "ue_batch_import.py"
    if not script.exists():
        raise RuntimeError(f"UE 端脚本不存在: {script}")

    env = os.environ.copy()
    env["UE_PIPELINE_CONFIG"] = str(cfg_path)

    remote_hint = (
        "需在 UE 启用：编辑 > 项目设置 > 插件 > Python > 勾选 “Enable Remote Execution”，"
        "重启 UE 后生效（只需配置一次）。"
    )

    # 统一走 Remote Execution（在编辑器“完全就绪”后才执行导入，避免启动早期执行导致不刷新）。
    if _ue_running():
        # 已运行：直接发，短等待
        emit_log("检测到 UE 正在运行，通过 Remote Execution 发送导入任务（不重启）…", "INFO")

        def _worker_running():
            ok, msg = _send_via_remote(ue_exe, script, cfg_path, wait_seconds=8.0)
            if ok:
                emit_log("UE 导入任务已执行完成或已由 UE 接收。进度见“日志 > UE 导入”和 Output Log（搜 UE-Pipeline）。", "OK")
            else:
                emit_log(f"Remote Execution 失败：{msg}", "ERROR")
                emit_log(remote_hint, "WARN")

        threading.Thread(target=_worker_running, daemon=True).start()
        return

    # 没运行：先正常启动编辑器（不带导入脚本，避免启动早期执行），等它完全就绪后再 Remote 发送。
    editor_exe = _full_editor_exe(ue_exe)
    emit_log("UE 未运行，正在启动编辑器（约1-3分钟）。就绪后会自动发送导入，无需手动重开。", "INFO")
    subprocess.Popen([str(editor_exe), str(uproj)], env=env)

    def _worker_launch():
        # 给足冷启动 + 加载项目的时间，轮询直到 Remote 节点出现
        ok, msg = _send_via_remote(ue_exe, script, cfg_path, wait_seconds=300.0)
        if ok:
            emit_log("UE 已就绪，导入任务已执行完成或已由 UE 接收。进度见“日志 > UE 导入”和 Output Log。", "OK")
        else:
            emit_log(f"等待 UE 就绪或远程执行失败：{msg}", "ERROR")
            emit_log("最可能是没启用 Remote Execution。" + remote_hint, "WARN")
            emit_log(
                f"临时手动方案：UE 里 工具>执行Python脚本 选择：{script}"
                f"（并先设环境变量 UE_PIPELINE_CONFIG={cfg_path}）", "WARN",
            )

    threading.Thread(target=_worker_launch, daemon=True).start()
