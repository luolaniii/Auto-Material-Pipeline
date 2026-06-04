# -*- coding: utf-8 -*-
"""
UE 端入口（在 UnrealEditor-Cmd 的 Python 解释器里运行）。
从环境变量 UE_PIPELINE_CONFIG 读取 GUI 传过来的 JSON 配置：
  - files: 选中的模型文件路径列表
  - dest: UE Content 下的目标路径，例如 /Game/Imports
  - material_mode: none(只导网格) / embedded(用文件内嵌材质)
  - gui_root: GUI 项目根目录（用于把共享 keyword_classifier 加进 sys.path）
"""

import json
import os
import sys
import time
from pathlib import Path

import unreal  # type: ignore


_LOG_FILE: Path | None = None


def _set_log_file(path: str | None) -> None:
    global _LOG_FILE
    if not path:
        _LOG_FILE = None
        return
    try:
        _LOG_FILE = Path(path)
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        _LOG_FILE = None


def _log(msg: str, level: str = "INFO") -> None:
    line = f"[UE-Pipeline][{level}] {msg}"
    print(line)
    if _LOG_FILE:
        try:
            with _LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def _load_config() -> dict:
    cfg_path = os.environ.get("UE_PIPELINE_CONFIG", "")
    if not cfg_path or not Path(cfg_path).exists():
        raise RuntimeError("缺少环境变量 UE_PIPELINE_CONFIG 或文件不存在")
    return json.loads(Path(cfg_path).read_text(encoding="utf-8"))


def _ensure_dir(path: str) -> str:
    if not unreal.EditorAssetLibrary.does_directory_exist(path):
        unreal.EditorAssetLibrary.make_directory(path)
    return path


def _split_tokens(value: str) -> set[str]:
    import re
    return {t.strip().lower() for t in re.split(r"[,，;；\r\n\t]+", str(value or "")) if t.strip()}


def _extend_classifier_tokens(cfg: dict) -> None:
    """把 GUI 设置里的贴图关键词注入共享 keyword_classifier。"""
    try:
        import keyword_classifier as kc

        roles = {
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

        def extend(attr: str, cfg_key: str) -> None:
            extra = _split_tokens(cfg.get(cfg_key, ""))
            if not extra or not hasattr(kc, attr):
                return
            setattr(kc, attr, frozenset(set(getattr(kc, attr)) | extra))
            _log(f"追加贴图关键词 {attr}: {sorted(extra)}")

        token_cfg = cfg.get("texture_keyword_tokens", {})
        if isinstance(token_cfg, dict) and token_cfg:
            for role, (attr, _) in roles.items():
                if not hasattr(kc, attr):
                    continue
                tokens = _split_tokens(token_cfg.get(role, ""))
                setattr(kc, attr, frozenset(tokens))
                _log(f"设置贴图关键词 {attr}: {len(tokens)} 个 token")
        else:
            for _, (attr, legacy_key) in roles.items():
                extend(attr, legacy_key)

        kc.NON_BASECOLOR_KEYS = (
            kc.METALLIC_KEYS
            | kc.ROUGHNESS_KEYS
            | kc.METALLIC_ROUGHNESS_KEYS
            | kc.NORMAL_KEYS
            | kc.ROUGHNESS_SPECULAR_METALLIC_KEYS
            | kc.FLEXIBLE_KEYS
            | kc.BAD_KEYS
        )
    except Exception as e:
        _log(f"追加贴图关键词失败: {e}", "WARN")


def _build_static_mesh_import_options(file_ext: str, material_mode: str) -> unreal.FbxImportUI | None:
    """统一构造 FBX 导入选项；OBJ/GLB 走默认 Interchange/插件路径，返回 None 即可。
    material_mode='embedded' 时导入 FBX 内嵌材质/贴图；否则不导。"""
    if file_ext != ".fbx":
        return None
    want_embedded = (material_mode == "embedded")
    options = unreal.FbxImportUI()
    options.set_editor_property("import_mesh", True)
    options.set_editor_property("import_materials", want_embedded)
    options.set_editor_property("import_textures", want_embedded)
    # 让 UE 自动识别 Static/Skeletal，避免把带骨骼 FBX 强制导成 StaticMesh。
    try:
        options.set_editor_property("automated_import_should_detect_type", True)
    except Exception:
        pass
    options.set_editor_property("import_animations", True)
    options.set_editor_property("create_physics_asset", False)
    try:
        sm_data: unreal.FbxStaticMeshImportData = options.static_mesh_import_data
        sm_data.set_editor_property("combine_meshes", True)
        sm_data.set_editor_property("generate_lightmap_u_vs", True)
        sm_data.set_editor_property("auto_generate_collision", True)
    except Exception:
        pass
    return options


def _make_task(filepath: str, dest: str, material_mode: str = "embedded") -> unreal.AssetImportTask:
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", filepath)
    task.set_editor_property("destination_path", dest)
    task.set_editor_property("automated", True)
    task.set_editor_property("save", True)
    task.set_editor_property("replace_existing", True)

    ext = Path(filepath).suffix.lower()
    options = _build_static_mesh_import_options(ext, material_mode)
    if options is not None:
        task.set_editor_property("options", options)
    return task


def import_models(files: list[str], dest: str, material_mode: str = "embedded") -> list[tuple[str, unreal.Object]]:
    """返回 [(源文件路径, 导入的资产)]，保留源文件信息以便就近找贴图。"""
    _ensure_dir(dest)
    total = len(files)
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    results: list[tuple[str, unreal.Object]] = []
    for idx, fp in enumerate(files, start=1):
        src_path = Path(fp)
        try:
            size_mb = src_path.stat().st_size / 1024 / 1024
            size_desc = f"{size_mb:.1f} MB"
        except Exception:
            size_desc = "unknown size"
        _log(f"[{idx}/{total}] 导入开始: {src_path.name} ({size_desc})")

        t = _make_task(fp, dest, material_mode)
        try:
            asset_tools.import_asset_tasks([t])
        except Exception as e:
            _log(f"[{idx}/{total}] 导入异常: {src_path.name}: {e}", "ERROR")
            continue

        imported_count = 0
        for path in t.imported_object_paths:
            obj = unreal.EditorAssetLibrary.load_asset(path)
            if obj is not None:
                results.append((fp, obj))
                imported_count += 1
                _log(f"  导入资产: {path} ({obj.get_class().get_name()})")
        _log(f"[{idx}/{total}] 导入完成: {src_path.name} -> {imported_count} 个资产")
        try:
            unreal.SystemLibrary.collect_garbage()
        except Exception:
            pass

    _log(f"模型导入完成，共 {len(results)} 个资产")
    return results


# 朝向预设：把一个顶点坐标按指定模式重排（纯旋转，行列式=+1，不镜像）
#   none   : 不变
#   x_p90  : 绕 X +90°  (x, y, z) -> (x, -z, y)
#   x_m90  : 绕 X -90°  (x, y, z) -> (x,  z, -y)
#   x_180  : 绕 X 180°  (x, y, z) -> (x, -y, -z)
#   z_180  : 绕 Z 180°  (水平转身) (x, y, z) -> (-x, -y, z)
def _orient_vertex(px: float, py: float, pz: float, mode: str):
    if mode == "x_p90":
        return px, -pz, py
    if mode == "x_m90":
        return px, pz, -py
    if mode == "x_180":
        return px, -py, -pz
    if mode == "z_180":
        return -px, -py, pz
    return px, py, pz


def _bounds_longest_axis(md, count: int) -> str:
    """扫描顶点求包围盒，返回最长的轴 'x'/'y'/'z'（用于 auto 朝向：把最长轴当作‘高’）。"""
    mn = [1e30, 1e30, 1e30]
    mx = [-1e30, -1e30, -1e30]
    step = max(1, count // 2000)  # 大网格抽样，避免太慢
    for i in range(0, count, step):
        p = md.get_vertex_position(unreal.VertexID(i))
        for k, v in enumerate((p.x, p.y, p.z)):
            if v < mn[k]:
                mn[k] = v
            if v > mx[k]:
                mx[k] = v
    ext = [mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]]
    return "xyz"[ext.index(max(ext))]


def transform_static_mesh(static_mesh: unreal.StaticMesh,
                          scale: float = 1.0,
                          orientation: str = "none") -> bool:
    """
    在 LOD0 顶点上一次性应用 朝向修正 + 均匀缩放，再 build_from_static_mesh_descriptions 重建。
    orientation: none / x_p90 / x_m90 / x_180 / z_180 / auto。
      auto = 自动把包围盒最长轴转成 UE 的 Z(竖直)，解决“躺倒”（但不区分头朝上/下）。
    缩放与旋转合并成一次重建。注意：重建会重置材质槽，材质绑定必须在本步之后。
    """
    has_scale = abs(scale - 1.0) > 1e-6
    if not has_scale and orientation in ("none", "", None):
        return False
    try:
        md = static_mesh.get_static_mesh_description(0)
        count = md.get_vertex_count()

        mode = orientation
        if orientation == "auto":
            axis = _bounds_longest_axis(md, count)
            # 最长轴若已是 Z 则不转；是 Y 则绕 X 转；是 X 则绕 Y 转(用 z_180 近似不行，这里用 x 处理)
            mode = {"z": "none", "y": "x_m90", "x": "x_m90"}.get(axis, "none")
            _log(f"  auto朝向: 最长轴={axis} -> {mode}")

        for i in range(count):
            vid = unreal.VertexID(i)
            p = md.get_vertex_position(vid)
            x, y, z = _orient_vertex(p.x, p.y, p.z, mode)
            md.set_vertex_position(vid, unreal.Vector(x * scale, y * scale, z * scale))
        static_mesh.build_from_static_mesh_descriptions([md])
        unreal.EditorAssetLibrary.save_loaded_asset(static_mesh)
        return True
    except Exception as e:
        _log(f"  变换网格失败 {static_mesh.get_name()}: {e}", "WARN")
        return False


def _configure_texture(tex: unreal.Texture2D, role: str) -> None:
    """
    按用途设置贴图的 sRGB / 压缩格式，否则材质里的 TextureSample 采样类型与贴图不符会报 error。
      - normal: 关 sRGB + TC_Normalmap（否则法线采样报 "should be Color"）
      - mask 类(MR/粗糙/金属): 关 sRGB + TC_Masks（线性）
      - basecolor: 开 sRGB
    """
    try:
        if role == "normal":
            tex.set_editor_property("srgb", False)
            tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP)
        elif role in ("metallic_roughness", "roughness", "metallic",
                      "roughness_specular_metallic", "flexible"):
            tex.set_editor_property("srgb", False)
            tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_MASKS)
        else:  # basecolor
            tex.set_editor_property("srgb", True)
        unreal.EditorAssetLibrary.save_loaded_asset(tex)
    except Exception as e:
        _log(f"  配置贴图压缩失败 {tex.get_name()}: {e}", "WARN")


def import_texture(texture_path: Path, dest: str, role: str = "basecolor") -> unreal.Texture2D | None:
    target_pkg = f"{dest}/{texture_path.stem}"
    existing = unreal.EditorAssetLibrary.load_asset(target_pkg)
    if isinstance(existing, unreal.Texture2D):
        _configure_texture(existing, role)
        return existing

    task = unreal.AssetImportTask()
    task.set_editor_property("filename", str(texture_path))
    task.set_editor_property("destination_path", dest)
    task.set_editor_property("automated", True)
    task.set_editor_property("save", True)
    task.set_editor_property("replace_existing", True)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

    for p in task.imported_object_paths:
        obj = unreal.EditorAssetLibrary.load_asset(p)
        if isinstance(obj, unreal.Texture2D):
            _configure_texture(obj, role)
            return obj
    return None


def _create_material(name: str, dest: str) -> unreal.Material:
    full_path = f"{dest}/{name}"
    existing = unreal.EditorAssetLibrary.load_asset(full_path)
    if isinstance(existing, unreal.Material):
        return existing

    factory = unreal.MaterialFactoryNew()
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    new_mat = asset_tools.create_asset(name, dest, unreal.Material, factory)
    return new_mat


def _add_tex_sample(material: unreal.Material, texture: unreal.Texture2D, x: int, y: int,
                    sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_COLOR) -> unreal.MaterialExpressionTextureSample:
    node = unreal.MaterialEditingLibrary.create_material_expression(
        material, unreal.MaterialExpressionTextureSample, x, y
    )
    node.texture = texture
    node.sampler_type = sampler_type
    return node


def _connect_node_to_property(material: unreal.Material,
                              node: unreal.MaterialExpression,
                              output: str,
                              prop) -> None:
    try:
        unreal.MaterialEditingLibrary.connect_material_property(node, output, prop)
    except Exception as e:
        _log(f"连接节点失败 ({output} -> {prop}): {e}", "WARN")


def build_material(material_name: str,
                   textures_by_role: dict[str, unreal.Texture2D],
                   dest: str) -> unreal.Material | None:
    """
    根据角色 -> Texture2D 的字典创建并连线材质：
      basecolor -> Base Color
      normal    -> Normal
      metallic_roughness / rma / orm -> 拆分到 Roughness/Metallic
    """
    if not textures_by_role:
        return None

    material = _create_material(material_name, dest)
    if material is None:
        return None

    P = unreal.MaterialProperty

    y_cursor = -300
    if "basecolor" in textures_by_role:
        n = _add_tex_sample(material, textures_by_role["basecolor"], -400, y_cursor)
        _connect_node_to_property(material, n, "RGB", P.MP_BASE_COLOR)
        y_cursor += 280

    if "normal" in textures_by_role:
        n = _add_tex_sample(material, textures_by_role["normal"], -400, y_cursor,
                            sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
        _connect_node_to_property(material, n, "RGB", P.MP_NORMAL)
        y_cursor += 280

    if "metallic_roughness" in textures_by_role:
        # glTF/UE 风格：G=Roughness, B=Metallic
        n = _add_tex_sample(material, textures_by_role["metallic_roughness"], -400, y_cursor,
                            sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
        _connect_node_to_property(material, n, "G", P.MP_ROUGHNESS)
        _connect_node_to_property(material, n, "B", P.MP_METALLIC)
        y_cursor += 280
    else:
        if "metallic" in textures_by_role:
            n = _add_tex_sample(material, textures_by_role["metallic"], -400, y_cursor,
                                sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_LINEARGRAYSCALE)
            _connect_node_to_property(material, n, "R", P.MP_METALLIC)
            y_cursor += 280
        if "roughness" in textures_by_role:
            n = _add_tex_sample(material, textures_by_role["roughness"], -400, y_cursor,
                                sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_LINEARGRAYSCALE)
            _connect_node_to_property(material, n, "R", P.MP_ROUGHNESS)
            y_cursor += 280

    try:
        unreal.MaterialEditingLibrary.layout_material_expressions(material)
    except Exception:
        pass
    # 必须 recompile，否则材质连了节点但没编译，编辑器里显示为默认灰材质，
    # 直到手动“保存”才触发编译。recompile 后即时生效。
    unreal.MaterialEditingLibrary.recompile_material(material)
    unreal.EditorAssetLibrary.save_loaded_asset(material)
    return material


def attach_materials_to_meshes(imported_pairs: list[tuple[str, unreal.Object]],
                               material_root,
                               texture_root,
                               tex_dest: str,
                               mat_dest: str) -> None:
    """
    为每个导入的 StaticMesh 解析贴图并创建/绑定材质。
    解析策略（与 Blender 流水线一致）：
      1) 若配置了材质根目录且能找到同名 JSON -> 用 JSON 里的贴图；
      2) 否则回退到“按贴图文件名匹配”——在模型旁边（或配置的贴图根目录）
         扫描贴图，用材质名/模型名打分 + 关键词分类。
    """
    from keyword_classifier import resolve_textures_for_material

    for src_file, asset in imported_pairs:
        if not isinstance(asset, unreal.StaticMesh):
            continue
        src = Path(src_file)
        model_name = src.stem
        sm_name = asset.get_name()

        # 贴图搜索目录：优先配置的贴图根目录，否则用模型文件旁边的目录
        if texture_root and texture_root.exists():
            tex_dir = texture_root
        else:
            tex_dir = src.parent

        role_paths, source = resolve_textures_for_material(
            model_name, material_root, tex_dir, model_name
        )
        _log(f"处理 {sm_name}（源 {src.name}） 贴图来源: {source}")
        if not role_paths:
            _log(f"  未匹配到任何贴图，跳过材质", "WARN")
            continue

        textures_by_role: dict[str, unreal.Texture2D] = {}
        for role, p in role_paths.items():
            tex = import_texture(p, tex_dest, role)
            if tex:
                textures_by_role[role] = tex
                _log(f"  {role:<22} <- {p.name}")

        if not textures_by_role:
            continue

        mat = build_material(f"M_{sm_name}", textures_by_role, mat_dest)
        if mat is None:
            continue

        try:
            slots = []
            try:
                slots = list(asset.get_editor_property("static_materials") or [])
            except Exception:
                slots = []
            slot_count = max(1, len(slots))
            for slot_idx in range(slot_count):
                asset.set_material(slot_idx, mat)
            unreal.EditorAssetLibrary.save_loaded_asset(asset)
            _log(f"  材质已绑定到 {slot_count} 个槽: {mat.get_name()}", "INFO")
        except Exception as e:
            _log(f"  绑定材质失败: {e}", "ERROR")


def _is_ue_type(obj, type_name: str) -> bool:
    cls = getattr(unreal, type_name, None)
    return cls is not None and isinstance(obj, cls)


def organize_meshes_folder(mesh_dest: str, tex_dest: str, mat_dest: str,
                           skel_dest: str, anim_dest: str, physics_dest: str,
                           delete_extras: bool) -> None:
    """
    Interchange 导入 OBJ/GLB 时会把材质/贴图也导进 Meshes 目录。
    本函数让 Meshes 只保留 StaticMesh：
      delete_extras=True：删除这些顺带导入的材质/贴图（rebuild/none）。
      delete_extras=False：移动材质到 Materials、贴图到 Textures（embedded，保留引用）。
    """
    try:
        paths = unreal.EditorAssetLibrary.list_assets(mesh_dest, recursive=True, include_folder=False)
    except Exception as e:
        _log(f"整理 Meshes 目录失败(列举): {e}", "WARN")
        return

    moved = 0
    deleted = 0
    for path in paths:
        obj = unreal.EditorAssetLibrary.load_asset(path)
        if obj is None or isinstance(obj, unreal.StaticMesh) or _is_ue_type(obj, "SkeletalMesh"):
            continue
        if isinstance(obj, unreal.Texture):
            target_dir = tex_dest
        elif isinstance(obj, unreal.MaterialInterface):
            target_dir = mat_dest
        elif _is_ue_type(obj, "Skeleton"):
            target_dir = skel_dest
        elif _is_ue_type(obj, "AnimSequence"):
            target_dir = anim_dest
        elif _is_ue_type(obj, "PhysicsAsset"):
            target_dir = physics_dest
        else:
            continue

        preserve_rig_asset = target_dir in (skel_dest, anim_dest, physics_dest)
        if delete_extras and not preserve_rig_asset:
            try:
                unreal.EditorAssetLibrary.delete_asset(path)
                deleted += 1
            except Exception as e:
                _log(f"  删除 {path} 失败: {e}", "WARN")
        else:
            dst = f"{target_dir}/{obj.get_name()}"
            if not unreal.EditorAssetLibrary.does_asset_exist(dst):
                try:
                    unreal.EditorAssetLibrary.rename_asset(path, dst)
                    moved += 1
                except Exception as e:
                    _log(f"  移动 {path} 失败: {e}", "WARN")

    if delete_extras:
        _log(f"已整理 Meshes：删除 {deleted} 个非 StaticMesh 资产")
    else:
        _log(f"已整理 Meshes：移动 {moved} 个材质/贴图到 Materials/Textures")


def main():
    try:
        cfg = _load_config()
    except Exception as e:
        _log(f"配置加载失败: {e}", "ERROR")
        return

    files: list[str] = cfg.get("files", [])
    if not files:
        _log("文件列表为空", "WARN")
        return

    dest_root = cfg.get("dest", "/Game/Imports")
    mesh_dest = f"{dest_root}/Meshes"
    tex_dest = f"{dest_root}/Textures"
    mat_dest = f"{dest_root}/Materials"
    skel_dest = f"{dest_root}/Skeletons"
    anim_dest = f"{dest_root}/Animations"
    physics_dest = f"{dest_root}/Physics"
    for d in (mesh_dest, tex_dest, mat_dest, skel_dest, anim_dest, physics_dest):
        _ensure_dir(d)

    material_mode = cfg.get("material_mode", "embedded")
    if material_mode not in ("none", "embedded"):
        material_mode = "embedded"
    _set_log_file(cfg.get("log_file"))

    start = time.time()
    _log(f"开始导入 {len(files)} 个模型 -> {mesh_dest}（材质模式: {material_mode}）")
    imported = import_models(files, mesh_dest, material_mode)

    # OBJ 专属：缩放(米->厘米) + 朝向修正，一次性烘进几何（必须在材质绑定之前）。
    # FBX/GLB 自带单位和朝向，UE 会正确处理，不动它们。
    obj_scale = float(cfg.get("obj_scale", 1.0))
    orientation = cfg.get("obj_orientation", "x_m90")
    if abs(obj_scale - 1.0) > 1e-6 or orientation not in ("none", "", None):
        n = 0
        for src, asset in imported:
            if Path(src).suffix.lower() == ".obj" and isinstance(asset, unreal.StaticMesh):
                if transform_static_mesh(asset, obj_scale, orientation):
                    n += 1
        _log(f"已对 {n} 个 OBJ 应用 缩放x{obj_scale} + 朝向({orientation})")

    try:
        organize_meshes_folder(
            mesh_dest, tex_dest, mat_dest, skel_dest, anim_dest, physics_dest,
            delete_extras=(material_mode != "embedded"),
        )
    except Exception as e:
        _log(f"整理 Meshes 目录出错: {e}", "WARN")

    duration = time.time() - start
    _log(f"全部完成。耗时 {duration:.1f}s，导入 {len(imported)} 个资产。材质模式: {material_mode}")


if __name__ == "__main__":
    main()
