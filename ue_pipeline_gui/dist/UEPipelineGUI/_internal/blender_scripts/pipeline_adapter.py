# -*- coding: utf-8 -*-
"""
完整流水线适配器：在 Blender 里加载原 ue_obj_to_glb_pipeline.py，
覆盖关键的全局配置（材质/贴图根目录、输出目录），然后对 GUI 选中的文件
逐个调用 process_single_glb() 处理。

不修改原脚本本身。
"""

import importlib.util
import json
import sys
import time
from pathlib import Path

import bpy  # type: ignore


def _argv_after_dashdash():
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1:]


def _load_pipeline_module(pipeline_script: Path):
    # 把脚本所在目录加进 sys.path，避免相对依赖问题
    sys.path.insert(0, str(pipeline_script.parent))

    spec = importlib.util.spec_from_file_location("ue_pipeline_original", str(pipeline_script))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载脚本: {pipeline_script}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["ue_pipeline_original"] = module
    # 注意：原脚本顶部有 `if "bpy" not in sys.modules` 的自启 Blender 逻辑，
    # 在 Blender 里 bpy 已存在，会跳过自启进入正常分支。
    spec.loader.exec_module(module)
    return module


def _merge_file_indexer(base_indexer, extra_indexer):
    if getattr(base_indexer, "enable_folder_build", False):
        for folder_name, folder_data in getattr(extra_indexer, "folder_index", {}).items():
            target = base_indexer.folder_index.setdefault(
                folder_name,
                {"glb_paths": [], "json_paths": [], "texture_paths": []},
            )
            for key in ("glb_paths", "json_paths", "texture_paths"):
                seen = {str(p).lower() for p in target.get(key, [])}
                for p in folder_data.get(key, []):
                    if str(p).lower() not in seen:
                        target[key].append(p)
                        seen.add(str(p).lower())
        return

    for attr in ("json_index", "texture_index"):
        target = getattr(base_indexer, attr, {})
        for k, v in getattr(extra_indexer, attr, {}).items():
            target.setdefault(k, v)
        setattr(base_indexer, attr, target)
    base_indexer._json_keys = list(getattr(base_indexer, "json_index", {}).keys())
    base_indexer._texture_keys = list(getattr(base_indexer, "texture_index", {}).keys())


def main():
    args = _argv_after_dashdash()
    if not args:
        print("[Adapter] 缺少 JSON 配置参数")
        sys.exit(1)

    cfg = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    pipeline_script = Path(cfg["pipeline_script"])
    material_root = Path(cfg["material_root"])
    texture_root = Path(cfg["texture_root"])
    dst_root = Path(cfg["dst_root"])
    selected_model_root = Path(cfg["model_root"]) if cfg.get("model_root") else None
    files = [Path(f) for f in cfg.get("files", [])]

    if not files:
        print("[Adapter] 文件列表为空")
        return

    print(f"[Adapter] 加载原流水线脚本: {pipeline_script}")
    module = _load_pipeline_module(pipeline_script)

    token_roles = {
        "basecolor": ("BASECOLOR_KEYS", "basecolor_tokens"),
        "metallic": ("METALLIC_KEYS", "metallic_tokens"),
        "roughness": ("ROUGHNESS_KEYS", "roughness_tokens"),
        "metallic_roughness": ("METALLIC_ROUGHNESS_KEYS", "metallic_roughness_tokens"),
        "normal": ("NORMAL_KEYS", "normal_tokens"),
        "roughness_specular_metallic": ("ROUGHNESS_SPECULAR_METALLIC_KEYS", "roughness_specular_metallic_tokens"),
        "flexible": ("FLEXIBLE_KEYS", "flexible_tokens"),
        "bad": ("BAD_KEYS", "bad_texture_tokens"),
        "best_match_basecolor": ("BEST_MATCH_BASECOLOR_TOKENS", "best_match_basecolor_tokens"),
    }

    def _token_set(value):
        if not value:
            return set()
        import re
        return {t.strip().lower() for t in re.split(r"[,，;；\r\n\t]+", str(value)) if t.strip()}

    def _extend_tokens(attr_name, value):
        extra = _token_set(value)
        if not extra or not hasattr(module, attr_name):
            return
        current = getattr(module, attr_name)
        setattr(module, attr_name, frozenset(set(current) | extra))
        print(f"[Adapter] 已追加 {attr_name}: {sorted(extra)}")

    token_cfg = cfg.get("texture_keyword_tokens", {})
    if isinstance(token_cfg, dict) and token_cfg:
        for role, (attr_name, _) in token_roles.items():
            if not hasattr(module, attr_name):
                continue
            tokens = _token_set(token_cfg.get(role, ""))
            setattr(module, attr_name, frozenset(tokens))
            print(f"[Adapter] 已设置 {attr_name}: {len(tokens)} 个 token")
    else:
        for _, (attr_name, legacy_key) in token_roles.items():
            _extend_tokens(attr_name, cfg.get(legacy_key, ""))

    if hasattr(module, "NON_BASECOLOR_KEYS"):
        module.NON_BASECOLOR_KEYS = (
            module.METALLIC_KEYS
            | module.ROUGHNESS_KEYS
            | module.METALLIC_ROUGHNESS_KEYS
            | module.NORMAL_KEYS
            | module.ROUGHNESS_SPECULAR_METALLIC_KEYS
            | module.FLEXIBLE_KEYS
            | module.BAD_KEYS
        )

    # 覆盖关键全局配置
    module.MATERIAL_TEXTURE_ROOT = material_root
    module.DST_ROOT = dst_root
    if hasattr(module, "OBJ_ROOT"):
        module.OBJ_ROOT = material_root  # 仅占位，process_single_glb 直接吃路径
    if hasattr(module, "GLB_ROOT"):
        module.GLB_ROOT = material_root
    if hasattr(module, "SRC_ROOT"):
        module.SRC_ROOT = material_root

    dst_root.mkdir(parents=True, exist_ok=True)

    # 重定向日志到 dst_root。原脚本在 import 时就用硬编码 DST_ROOT 固化了 logger 路径，
    # 所以日志一直写在旧位置、且 append 从不清空（已涨到几百 MB）。这里改指向 dst_root
    # 并清空旧内容，避免无限增长，也方便美术在输出目录里找到日志。
    try:
        log_file = dst_root / "detailed_processing.log"
        if log_file.exists():
            log_file.unlink()
        if hasattr(module, "DETAILED_LOG_FILE"):
            module.DETAILED_LOG_FILE = log_file
        if hasattr(module, "ProcessLogger"):
            module.logger = module.ProcessLogger(log_file)
        print(f"[Adapter] 处理日志输出到: {log_file}")
    except Exception as e:
        print(f"[Adapter] 日志重定向失败: {e}")

    # 给导出的 GLB 内材质加 M_ 前缀，避免 UE 导入时材质和同名网格冲突。
    # 做法：包装原脚本实际调用的导出函数 export_glb_with_fallback，导出前给当前
    # 场景里所有材质补 M_ 前缀（process_single_glb 每次都清空重导，所以只影响当前模型）。
    try:
        if hasattr(module, "export_glb_with_fallback"):
            _orig_export = module.export_glb_with_fallback

            def _export_with_prefix(output_path, _orig=_orig_export):
                try:
                    for m in module.bpy.data.materials:
                        if m is not None and not m.name.startswith("M_"):
                            m.name = "M_" + m.name
                except Exception as e:
                    print(f"[Adapter] 材质加前缀失败: {e}")
                return _orig(output_path)

            module.export_glb_with_fallback = _export_with_prefix
            print("[Adapter] 已启用 GLB 材质 M_ 前缀（避免 UE 同名冲突）")
    except Exception as e:
        print(f"[Adapter] 安装材质前缀包装失败: {e}")

    # 初始化 file_indexer（原 main() 里做的事，这里手动做一遍）
    print(f"[Adapter] 构建文件索引: {material_root}")
    try:
        indexer = module.FileIndexer(
            material_root,
            module.TEXTURE_EXTENSIONS,
            enable_folder_build=getattr(module, "ENABLE_SINGLE_GLB_FOLDER_BUILD", True),
        )
        json_idx_csv = dst_root / "file_index_json.csv"
        tex_idx_csv = dst_root / "file_index.csv"
        # 不再复用旧缓存。输出目录固定为 _GUI_PipelineOutput 后，用户会频繁切换
        # 贴图/资源根目录；旧 folder_index 会让完整流水线继续用上一批错误路径。
        indexer.build_index()
        if texture_root != material_root and texture_root.exists():
            extra_indexer = module.FileIndexer(
                texture_root,
                module.TEXTURE_EXTENSIONS,
                enable_folder_build=getattr(module, "ENABLE_SINGLE_GLB_FOLDER_BUILD", True),
            )
            extra_indexer.build_index()
            _merge_file_indexer(indexer, extra_indexer)
            print(f"[Adapter] 已合并贴图索引: {texture_root}")
        indexer.save_index(json_idx_csv, tex_idx_csv)
        module.file_indexer = indexer
    except Exception as e:
        print(f"[Adapter] 索引构建失败: {e}")

    success = 0
    fail = 0
    start = time.time()
    for i, fp in enumerate(files, 1):
        print(f"\n[Adapter] [{i}/{len(files)}] 处理: {fp.name}")
        try:
            # process_single_glb 的最后一个参数是“待处理模型所在根目录”。
            # 原脚本用它确认当前 OBJ 属于处理后模型目录，再用 OBJ 文件名去原始资源根目录
            # 匹配同名 JSON/贴图文件夹。这里不能传 material_root，否则模型目录和资源目录分离时会退化为全局搜索。
            model_root = selected_model_root or fp.parent
            print(f"[Adapter] 模型匹配根目录: {model_root}")
            result = module.process_single_glb(
                fp,
                material_root,
                texture_root,
                dst_root,
                model_root,
            )
            if result.get("success"):
                success += 1
                print(f"[Adapter] ✅ {fp.name} 完成 — 材质:{result.get('materials_processed',0)} 贴图:{result.get('textures_connected',0)}")
            else:
                fail += 1
                print(f"[Adapter] ❌ {fp.name} 失败 — {result.get('error')}")
        except Exception as e:
            fail += 1
            print(f"[Adapter] ❌ {fp.name} 异常 — {e}")

    duration = time.time() - start
    print("\n" + "=" * 60)
    print(f"[Adapter] 处理完成。成功 {success}，失败 {fail}，耗时 {duration:.1f}s")

    # 渲染预览图：复用原脚本 render_main()，PNG 输出到 GLB 同级目录、与 GLB 同名。
    # （之前漏了这一步，所以只有 GLB 没有预览图）
    try:
        print("[Adapter] 开始渲染预览图…")
        # 重定向并清空渲染进度文件，强制重渲所有 GLB 的预览图。
        # 否则原脚本 render_main 会跳过 status==completed 的 GLB，导致重导后预览图残留旧图；
        # 且 RENDER_PROGRESS_FILE 在 import 时固化为旧硬编码路径，这里改指向当前 dst_root。
        try:
            rp = dst_root / ".render_progress.json"
            if hasattr(module, "RENDER_PROGRESS_FILE"):
                module.RENDER_PROGRESS_FILE = rp
            if rp.exists():
                rp.unlink()
                print("[Adapter] 已清渲染进度，强制重渲所有预览图")
        except Exception as e:
            print(f"[Adapter] 清渲染进度失败: {e}")
        module.render_main()
        print("[Adapter] 预览图渲染完成")
    except Exception as e:
        import traceback
        print(f"[Adapter] 渲染预览图失败: {e}")
        print(traceback.format_exc())

    print(f"[Adapter] 输出目录: {dst_root}")
    print("=" * 60)


if __name__ == "__main__":
    main()
