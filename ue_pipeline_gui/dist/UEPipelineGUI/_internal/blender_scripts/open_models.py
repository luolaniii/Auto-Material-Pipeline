# -*- coding: utf-8 -*-
"""
Open selected models in Blender GUI for quick preview.
Reads a JSON config (passed after `--`) with a "files" list, imports each model,
then frames the viewport. Writes a diagnostic log to TEMP/UEPipelineGUI/blender_open.log
so issues can be inspected even when the GUI console shows garbled text.
"""

import json
import importlib.util
import re
import sys
import tempfile
import traceback
from pathlib import Path

import bpy  # type: ignore

_LOG_PATH = Path(tempfile.gettempdir()) / "UEPipelineGUI" / "blender_open.log"
_LOGF = None


def _open_log():
    global _LOGF
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LOGF = open(_LOG_PATH, "w", encoding="utf-8")
    except Exception:
        _LOGF = None


def log(msg: str):
    # English only to avoid console codepage garbling on Windows
    print("[GUI] " + msg)
    try:
        if _LOGF:
            _LOGF.write(msg + "\n")
            _LOGF.flush()
    except Exception:
        pass


def _argv_after_dashdash():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def _import_one(path: Path) -> bool:
    ext = path.suffix.lower()
    if ext == ".obj":
        try:
            bpy.ops.wm.obj_import(filepath=str(path))
        except Exception:
            bpy.ops.import_scene.obj(filepath=str(path))
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    else:
        return False
    return True


def _load_pipeline_module(pipeline_script: Path):
    sys.path.insert(0, str(pipeline_script.parent))

    spec = importlib.util.spec_from_file_location("ue_pipeline_original_open", str(pipeline_script))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load pipeline script: %s" % pipeline_script)

    module = importlib.util.module_from_spec(spec)
    sys.modules["ue_pipeline_original_open"] = module
    spec.loader.exec_module(module)
    return module


def _token_set(value):
    if not value:
        return set()
    return {t.strip().lower() for t in re.split(r"[,，;；\r\n\t]+", str(value)) if t.strip()}


_TOKEN_ROLES = {
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


def _extend_tokens(module, attr_name: str, value):
    extra = _token_set(value)
    if not extra or not hasattr(module, attr_name):
        return
    current = getattr(module, attr_name)
    setattr(module, attr_name, frozenset(set(current) | extra))
    log("extended %s: %s" % (attr_name, sorted(extra)))


def _apply_token_config(module, cfg):
    token_cfg = cfg.get("texture_keyword_tokens", {})
    if isinstance(token_cfg, dict) and token_cfg:
        for role, (attr_name, _) in _TOKEN_ROLES.items():
            if not hasattr(module, attr_name):
                continue
            tokens = _token_set(token_cfg.get(role, ""))
            setattr(module, attr_name, frozenset(tokens))
            log("set %s: %d tokens" % (attr_name, len(tokens)))
    else:
        for _, (attr_name, legacy_key) in _TOKEN_ROLES.items():
            _extend_tokens(module, attr_name, cfg.get(legacy_key, ""))
    _refresh_non_basecolor_tokens(module)


def _refresh_non_basecolor_tokens(module):
    if not hasattr(module, "NON_BASECOLOR_KEYS"):
        return
    module.NON_BASECOLOR_KEYS = (
        module.METALLIC_KEYS
        | module.ROUGHNESS_KEYS
        | module.METALLIC_ROUGHNESS_KEYS
        | module.NORMAL_KEYS
        | module.ROUGHNESS_SPECULAR_METALLIC_KEYS
        | module.FLEXIBLE_KEYS
        | module.BAD_KEYS
    )


def _setup_rebuild_matcher(cfg):
    if cfg.get("material_mode") != "rebuild":
        return None

    pipeline_script = Path(cfg.get("pipeline_script", ""))
    material_root = Path(cfg.get("material_root", ""))
    texture_root = Path(cfg.get("texture_root", ""))
    model_root = Path(cfg["model_root"]) if cfg.get("model_root") else None

    if not pipeline_script.exists():
        log("rebuild disabled: pipeline script missing: %s" % pipeline_script)
        return None
    if not material_root.exists():
        log("rebuild disabled: material root missing: %s" % material_root)
        return None
    if not texture_root.exists():
        log("rebuild disabled: texture root missing: %s" % texture_root)
        return None

    log("loading material matcher: %s" % pipeline_script)
    module = _load_pipeline_module(pipeline_script)

    _apply_token_config(module, cfg)

    module.MATERIAL_TEXTURE_ROOT = material_root
    if hasattr(module, "OBJ_ROOT"):
        module.OBJ_ROOT = material_root
    if hasattr(module, "GLB_ROOT"):
        module.GLB_ROOT = material_root
    if hasattr(module, "SRC_ROOT"):
        module.SRC_ROOT = material_root

    try:
        indexer = module.FileIndexer(
            material_root,
            module.TEXTURE_EXTENSIONS,
            enable_folder_build=getattr(module, "ENABLE_SINGLE_GLB_FOLDER_BUILD", True),
        )
        indexer.build_index()
        if texture_root != material_root and texture_root.exists():
            extra_indexer = module.FileIndexer(
                texture_root,
                module.TEXTURE_EXTENSIONS,
                enable_folder_build=getattr(module, "ENABLE_SINGLE_GLB_FOLDER_BUILD", True),
            )
            extra_indexer.build_index()
            _merge_file_indexer(indexer, extra_indexer)
            log("texture index merged from: %s" % texture_root)
        module.file_indexer = indexer
        log("material index built from: %s" % material_root)
    except Exception as e:
        log("material index build failed: %s" % e)

    return {
        "module": module,
        "material_root": material_root,
        "texture_root": texture_root,
        "model_root": model_root,
    }


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


def _materials_from_objects(objects, model_path: Path):
    materials = []
    seen = set()
    mesh_objects = [obj for obj in objects if getattr(obj, "type", "") == "MESH"]

    for obj in mesh_objects:
        for slot in getattr(obj, "material_slots", []):
            mat = getattr(slot, "material", None)
            if mat and mat.name not in seen:
                seen.add(mat.name)
                materials.append(mat)

    if not materials and mesh_objects:
        mat = bpy.data.materials.new(model_path.stem)
        mat.use_nodes = True
        for obj in mesh_objects:
            try:
                obj.data.materials.append(mat)
            except Exception:
                pass
        materials.append(mat)

    for mat in materials:
        try:
            if not mat.use_nodes:
                mat.use_nodes = True
        except Exception:
            pass

    return materials


def _material_has_image_textures(module, material) -> bool:
    if hasattr(module, "material_has_image_textures"):
        return module.material_has_image_textures(material)
    try:
        if not material or not material.use_nodes or not material.node_tree:
            return False
        return any(
            node.type == "TEX_IMAGE" and getattr(node, "image", None) is not None
            for node in material.node_tree.nodes
        )
    except Exception:
        return False


def _resolve_folder_name(module, model_path: Path, model_root):
    if not (
        getattr(module, "ENABLE_SINGLE_GLB_FOLDER_BUILD", False)
        and getattr(module, "file_indexer", None)
        and module.file_indexer.enable_folder_build
    ):
        return None
    try:
        return module.file_indexer.get_folder_for_glb(model_path, model_root or model_path.parent)
    except Exception as e:
        log("folder resolve failed for %s: %s" % (model_path.name, e))
        return None


def _match_one_material(module, material, model_path: Path, material_root: Path, texture_root: Path, folder_name):
    texture_files = {}
    textures = {}

    material_json_path = module.find_material_json(material.name, material_root, folder_name)
    if not material_json_path and material.name != model_path.stem:
        material_json_path = module.find_material_json(model_path.stem, material_root, folder_name)

    if material_json_path:
        with open(material_json_path, "r", encoding="utf-8") as f:
            textures = json.load(f).get("Textures", {})

        valid_textures = {}
        has_basecolor = False
        for key, value in textures.items():
            texture_type, _, _ = module.classify_texture_type(key, value)
            if texture_type != "unknown":
                valid_textures[key] = value
                if texture_type == "basecolor":
                    has_basecolor = True

        if textures and (has_basecolor or valid_textures):
            for key, value in textures.items():
                tex_file = module.find_texture_file(value, texture_root, folder_name)
                if tex_file:
                    texture_files[key] = tex_file
    else:
        log("no material json for %s; using filename match" % material.name)

    if not texture_files:
        candidates = module.collect_candidate_textures_for_material(
            material.name,
            texture_root,
            folder_name,
            model_path,
        )
        if not candidates and material.name != model_path.stem:
            candidates = module.collect_candidate_textures_for_material(
                model_path.stem,
                texture_root,
                folder_name,
                model_path,
            )
        if candidates:
            texture_files, textures = module.build_texture_files_from_filenames(candidates)

    if not texture_files:
        if model_path.suffix.lower() != ".obj" and _material_has_image_textures(module, material):
            log("kept embedded textures: %s" % material.name)
            return False
        log("no textures matched: %s" % material.name)
        return False

    connected, _ = module.connect_textures_to_material(material, texture_files, material.name, textures, model_path.stem)
    if connected:
        log("matched material %s with %d textures" % (material.name, len(texture_files)))
    else:
        log("texture candidates found but not connected: %s" % material.name)
    return connected


def _match_imported_materials(matcher, model_path: Path, objects) -> int:
    if not matcher:
        return 0

    module = matcher["module"]
    material_root = matcher["material_root"]
    texture_root = matcher["texture_root"]
    model_root = matcher["model_root"]

    materials = _materials_from_objects(objects, model_path)
    if not materials:
        log("no mesh/materials for matching: %s" % model_path.name)
        return 0

    folder_name = _resolve_folder_name(module, model_path, model_root)
    if folder_name:
        log("resource folder for %s: %s" % (model_path.name, folder_name))
    else:
        log("resource folder not resolved for %s; using global search" % model_path.name)

    matched = 0
    for material in materials:
        try:
            if _match_one_material(module, material, model_path, material_root, texture_root, folder_name):
                matched += 1
        except Exception as e:
            log("material match failed %s/%s: %s" % (model_path.name, material.name, e))
            log(traceback.format_exc())

    try:
        module.set_default_material_values()
        module.cleanup_color_attributes()
    except Exception as e:
        log("post material cleanup failed: %s" % e)

    return matched


def _clear_default_objects():
    """Remove the default startup cube/camera/light without read_factory_settings
    (which can behave oddly in GUI --python startup)."""
    try:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    try:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
    except Exception as e:
        log("clear default failed: %s" % e)


def _normalize_armature_display(objects=None) -> int:
    changed = 0
    # glTF/FBX importers may create nested armatures or rename imported objects
    # after the import call. Normalize every armature in the scene so the user
    # sees normal stick bones instead of envelope/custom-shape "balls".
    pool = list(bpy.data.objects)
    for obj in pool:
        if obj is None or getattr(obj, "type", None) != "ARMATURE":
            continue
        try:
            arm = getattr(obj, "data", None)
            if arm is not None and getattr(arm, "display_type", None) != "STICK":
                arm.display_type = "STICK"
                changed += 1
            if arm is not None and hasattr(arm, "show_bone_custom_shapes") and arm.show_bone_custom_shapes:
                arm.show_bone_custom_shapes = False
                changed += 1
            obj.show_in_front = False
            try:
                was_visible = not obj.hide_get()
            except Exception:
                was_visible = not getattr(obj, "hide_viewport", False)
            try:
                obj.hide_set(True)
            except Exception:
                pass
            obj.hide_viewport = True
            obj.hide_render = True
            if was_visible:
                changed += 1
        except Exception as e:
            log("normalize armature display failed %s: %s" % (getattr(obj, "name", "<unnamed>"), e))
    return changed


_HELPER_COLLECTION_TOKENS = (
    "gltf_not_exported",
    "rigidbody",
    "rigid_body",
    "rigid body",
    "rigid",
    "joint",
    "collision",
    "collider",
    "capsule",
    "sphere",
    "剛体",
    "刚体",
    "ジョイント",
)

_HELPER_OBJECT_NAME_TOKENS = (
    "rigidbody",
    "rigid_body",
    "rigid body",
    "collision",
    "collider",
    "剛体",
    "刚体",
    "ジョイント",
)


def _is_helper_collection_name(name: str) -> bool:
    lowered = (name or "").lower()
    return any(token in lowered for token in _HELPER_COLLECTION_TOKENS)


def _is_helper_object_name(name: str) -> bool:
    lowered = (name or "").lower()
    return any(token in lowered for token in _HELPER_OBJECT_NAME_TOKENS)


def _is_helper_display_object(obj) -> bool:
    if obj is None:
        return False

    try:
        if any(_is_helper_collection_name(coll.name) for coll in obj.users_collection):
            return True
    except Exception:
        pass

    try:
        name_blob = "%s %s" % (getattr(obj, "name", ""), getattr(getattr(obj, "data", None), "name", ""))
        if _is_helper_object_name(name_blob):
            return True
    except Exception:
        pass

    try:
        if getattr(obj, "rigid_body", None) is not None:
            return True
    except Exception:
        pass

    try:
        mmd_type = getattr(obj, "mmd_type", "") or obj.get("mmd_type", "")
        if str(mmd_type).lower() in {"rigid_body", "rigidbody", "joint", "spring"}:
            return True
    except Exception:
        pass

    return False


def _hide_helper_display_objects() -> int:
    hidden = 0

    for coll in list(bpy.data.collections):
        if not _is_helper_collection_name(coll.name):
            continue
        try:
            coll.hide_viewport = True
            coll.hide_render = True
        except Exception:
            pass

    for obj in list(bpy.data.objects):
        if not _is_helper_display_object(obj):
            continue
        try:
            was_visible = not obj.hide_get()
        except Exception:
            was_visible = not getattr(obj, "hide_viewport", False)
        try:
            obj.hide_set(True)
        except Exception:
            pass
        try:
            obj.hide_viewport = True
            obj.hide_render = True
        except Exception:
            pass
        if was_visible:
            hidden += 1

    return hidden


def _normalize_scene_display() -> None:
    changed_armatures = _normalize_armature_display()
    hidden_helpers = _hide_helper_display_objects()
    if changed_armatures:
        log("normalized armature display: %d" % changed_armatures)
    if hidden_helpers:
        log("hidden helper display objects: %d" % hidden_helpers)


def _group_imported_objects_by_file(model_path: Path, objects) -> str:
    """Put imported objects into a source-file collection for readable Outliner names."""
    collection_name = model_path.stem or "ImportedModel"
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        collection = bpy.data.collections.new(collection_name)
        bpy.context.scene.collection.children.link(collection)

    linked = 0
    for obj in list(objects):
        try:
            if obj.name not in collection.objects:
                collection.objects.link(obj)
                linked += 1
            obj["source_model_file"] = str(model_path)
        except Exception:
            pass

    if linked:
        log("grouped %d objects under collection: %s" % (linked, collection.name))
    return collection.name


def _frame_all_views():
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region is None:
                    continue
                try:
                    with bpy.context.temp_override(window=window, area=area, region=region):
                        bpy.ops.object.select_all(action="SELECT")
                        bpy.ops.view3d.view_all(center=False)
                except Exception as e:
                    log("frame failed: %s" % e)
    except Exception as e:
        log("iterate views failed: %s" % e)


def _deferred_frame():
    _normalize_scene_display()
    _frame_all_views()
    return None


def main():
    _open_log()
    try:
        log("blender %s" % bpy.app.version_string)
    except Exception:
        pass

    args = _argv_after_dashdash()
    log("argv after --: %r" % args)
    if not args:
        log("ERROR: no config arg")
        return

    cfg_path = args[0]
    try:
        cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    except Exception as e:
        log("ERROR read config %s: %s" % (cfg_path, e))
        return

    files = cfg.get("files", [])
    log("files: %d" % len(files))
    if not files:
        log("ERROR: empty file list")
        return

    _clear_default_objects()
    matcher = _setup_rebuild_matcher(cfg)
    if matcher:
        log("material rebuild matching is enabled")
    else:
        log("material rebuild matching is disabled")

    ok = 0
    matched = 0
    for f in files:
        try:
            model_path = Path(f)
            before_names = {obj.name for obj in bpy.data.objects}
            if _import_one(model_path):
                ok += 1
                log("imported OK: %s" % f)
                new_objects = [obj for obj in bpy.data.objects if obj.name not in before_names]
                _group_imported_objects_by_file(model_path, new_objects)
                _normalize_scene_display()
                matched += _match_imported_materials(matcher, model_path, new_objects)
            else:
                log("skip unsupported: %s" % f)
        except Exception as e:
            log("import FAILED %s: %s" % (f, e))
            log(traceback.format_exc())

    _normalize_scene_display()

    log("import done %d/%d ; matched materials=%d ; scene objects=%d" % (ok, len(files), matched, len(bpy.data.objects)))

    try:
        bpy.app.timers.register(_deferred_frame, first_interval=0.6)
    except Exception as e:
        log("timer register failed: %s" % e)
        _frame_all_views()


main()
