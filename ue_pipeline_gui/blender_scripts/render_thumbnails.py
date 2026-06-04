# -*- coding: utf-8 -*-
"""Render simple white-model thumbnails for source assets."""

from __future__ import annotations

import json
import math
import sys
import traceback
from pathlib import Path

import bpy
from mathutils import Vector


_LOG_PATH = None


def log(message: str) -> None:
    line = str(message)
    print("[ThumbnailRender] " + line)
    if _LOG_PATH:
        try:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _LOG_PATH.open("a", encoding="utf-8") as f:
                f.write("[ThumbnailRender] " + line + "\n")
        except Exception:
            pass


def load_payload() -> dict:
    if "--" not in sys.argv:
        raise RuntimeError("missing payload path")
    cfg = Path(sys.argv[sys.argv.index("--") + 1])
    return json.loads(cfg.read_text(encoding="utf-8"))


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for data in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.cameras, bpy.data.lights):
        for item in list(data):
            try:
                data.remove(item)
            except Exception:
                pass


def import_model(model_path: Path) -> None:
    ext = model_path.suffix.lower()
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(model_path))
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(model_path))
    elif ext == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=str(model_path))
        else:
            bpy.ops.import_scene.obj(filepath=str(model_path))
    else:
        raise RuntimeError(f"unsupported model format: {ext}")


def apply_white_material() -> None:
    mat = bpy.data.materials.new("WhitePreview")
    mat.diffuse_color = (0.82, 0.82, 0.82, 1.0)
    mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.82, 0.82, 0.82, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.65

    for obj in bpy.context.scene.objects:
        if obj.type == "MESH":
            obj.data.materials.clear()
            obj.data.materials.append(mat)


def mesh_bounds():
    coords = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        for corner in obj.bound_box:
            coords.append(obj.matrix_world @ Vector(corner))
    if not coords:
        return None
    mn = Vector((min(v.x for v in coords), min(v.y for v in coords), min(v.z for v in coords)))
    mx = Vector((max(v.x for v in coords), max(v.y for v in coords), max(v.z for v in coords)))
    return mn, mx


def setup_render() -> bool:
    bounds = mesh_bounds()
    if not bounds:
        return False
    mn, mx = bounds
    center = (mn + mx) * 0.5
    extent = mx - mn
    size = max(extent.x, extent.y, extent.z, 0.01)

    cam_data = bpy.data.cameras.new("PreviewCamera")
    cam = bpy.data.objects.new("PreviewCamera", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = center + Vector((size * 1.8, -size * 2.4, size * 1.35))
    direction = center - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = size * 1.35
    bpy.context.scene.camera = cam

    light_data = bpy.data.lights.new("PreviewKey", "AREA")
    light = bpy.data.objects.new("PreviewKey", light_data)
    bpy.context.collection.objects.link(light)
    light.location = center + Vector((size * 1.2, -size * 1.2, size * 2.0))
    light_data.energy = 500
    light_data.size = max(size * 1.4, 1.0)

    scene = bpy.context.scene
    scene.render.resolution_x = 512
    scene.render.resolution_y = 512
    scene.render.film_transparent = False
    scene.world.color = (0.78, 0.80, 0.84)
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        try:
            scene.render.engine = "BLENDER_EEVEE"
        except Exception:
            pass
    return True


def render_job(model: Path, thumbnail: Path) -> bool:
    clear_scene()
    import_model(model)
    apply_white_material()
    if not setup_render():
        log(f"skip no mesh: {model}")
        return False
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    bpy.context.scene.render.filepath = str(thumbnail)
    bpy.ops.render.render(write_still=True)
    log(f"rendered: {model.name} -> {thumbnail}")
    return True


def main() -> None:
    global _LOG_PATH
    payload = load_payload()
    if payload.get("log_file"):
        _LOG_PATH = Path(payload["log_file"])
        try:
            _LOG_PATH.write_text("", encoding="utf-8")
        except Exception:
            pass

    jobs = payload.get("jobs", [])
    ok = 0
    for idx, job in enumerate(jobs, start=1):
        model = Path(job.get("model", ""))
        thumb = Path(job.get("thumbnail", ""))
        log(f"[{idx}/{len(jobs)}] start: {model}")
        try:
            if render_job(model, thumb):
                ok += 1
        except Exception as e:
            log(f"failed: {model}: {e}")
            log(traceback.format_exc())
    log(f"done: {ok}/{len(jobs)}")


if __name__ == "__main__":
    main()
