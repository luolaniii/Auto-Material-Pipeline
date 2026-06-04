# -*- coding: utf-8 -*-
"""
模型文件解析：提取 UV 套数、单位、面数、顶点数。

支持 OBJ / FBX (ASCII + Binary 头部) / GLB / glTF。
不依赖 Blender 或 UE，运行在主 GUI 进程里。
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Dict, Any

SUPPORTED_EXTS = {".obj", ".fbx", ".gltf", ".glb"}


UNIT_LABEL = {
    "meter": "米 (m)",
    "centimeter": "厘米 (cm)",
    "millimeter": "毫米 (mm)",
    "inch": "英寸 (in)",
    "foot": "英尺 (ft)",
    "unknown": "未知",
}


@dataclass
class UVSet:
    name: str
    coords_count: int = 0  # 数量；OBJ 用 vt 行数，glTF 用 accessor count


@dataclass
class ModelInfo:
    path: str
    file_format: str  # obj / fbx / glb / gltf
    ok: bool = True
    error: str = ""
    vertex_count: int = 0
    face_count: int = 0
    uv_sets: List[UVSet] = field(default_factory=list)
    unit: str = "unknown"       # meter / centimeter / millimeter / inch / foot / unknown
    unit_source: str = ""        # 解释来源（"glTF 规范"/"FBX GlobalSettings"/"OBJ 无单位元数据，按设置假定"）
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def uv_set_count(self) -> int:
        return len(self.uv_sets)

    @property
    def unit_display(self) -> str:
        return UNIT_LABEL.get(self.unit, self.unit)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["uv_set_count"] = self.uv_set_count
        d["unit_display"] = self.unit_display
        return d


def inspect_model(file_path: str | Path, obj_assumed_unit: str = "centimeter") -> ModelInfo:
    """解析单个模型，返回 ModelInfo。绝不抛异常，错误记录到 error 字段。"""
    path = Path(file_path)
    fmt = path.suffix.lower().lstrip(".")
    info = ModelInfo(path=str(path), file_format=fmt)

    if not path.exists():
        info.ok = False
        info.error = "文件不存在"
        return info

    try:
        if fmt == "obj":
            _inspect_obj(path, info, obj_assumed_unit)
        elif fmt == "fbx":
            _inspect_fbx(path, info)
        elif fmt in ("glb", "gltf"):
            _inspect_gltf(path, info)
        else:
            info.ok = False
            info.error = f"不支持的格式: .{fmt}"
    except Exception as e:
        info.ok = False
        info.error = f"解析异常: {e}"

    return info


# ============================================================
# OBJ
# ============================================================
def _inspect_obj(path: Path, info: ModelInfo, assumed_unit: str) -> None:
    """
    OBJ 格式：
      v  x y z        顶点
      vt u v          UV
      vn x y z        法线
      f  v/vt/vn ...  面
    OBJ 规范只支持单套 UV。
    """
    v_count = 0
    vt_count = 0
    f_count = 0

    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not line:
                continue
            head = line[:3]
            if head.startswith("v "):
                v_count += 1
            elif head.startswith("vt"):
                vt_count += 1
            elif head.startswith("f "):
                f_count += 1

    info.vertex_count = v_count
    info.face_count = f_count
    if vt_count > 0:
        info.uv_sets.append(UVSet(name="UVMap", coords_count=vt_count))

    info.unit = assumed_unit
    info.unit_source = "OBJ 无单位元数据，按设置假定"


# ============================================================
# glTF / GLB
# ============================================================
def _inspect_gltf(path: Path, info: ModelInfo) -> None:
    """只读 glTF/GLB 的 JSON 块来取 UV/顶点/单位（不碰二进制几何块）。

    刻意不用 pygltflib：它的 load_binary 在解析某些 GLB 的二进制块时会触发
    Windows access violation（C 层段错误），直接崩掉整个进程，Python 的
    try/except 根本拦不住。而 glTF 的网格/UV/accessor 元数据全在 JSON 块里，
    只读 JSON 块就足够，且永远不会因为二进制数据畸形而崩溃。"""
    _parse_gltf_raw(path, info)


def _parse_gltf_object(gltf, info: ModelInfo) -> None:
    accessors = gltf.accessors or []
    meshes = gltf.meshes or []

    vertex_total = 0
    face_total = 0
    uv_set_indices: Dict[int, int] = {}  # texcoord_index -> 累计坐标数

    for mesh in meshes:
        for prim in mesh.primitives or []:
            attrs = prim.attributes
            if attrs is None:
                continue
            attrs_dict = {k: v for k, v in attrs.__dict__.items() if not k.startswith("_")}

            pos_idx = attrs_dict.get("POSITION")
            if pos_idx is not None and pos_idx < len(accessors):
                vertex_total += accessors[pos_idx].count or 0

            if prim.indices is not None and prim.indices < len(accessors):
                face_total += (accessors[prim.indices].count or 0) // 3
            elif pos_idx is not None and pos_idx < len(accessors):
                face_total += (accessors[pos_idx].count or 0) // 3

            for key, acc_idx in attrs_dict.items():
                if key.startswith("TEXCOORD_") and acc_idx is not None:
                    try:
                        n = int(key.split("_")[1])
                    except (IndexError, ValueError):
                        continue
                    cnt = accessors[acc_idx].count if acc_idx < len(accessors) else 0
                    uv_set_indices[n] = uv_set_indices.get(n, 0) + (cnt or 0)

    info.vertex_count = vertex_total
    info.face_count = face_total
    for idx in sorted(uv_set_indices.keys()):
        info.uv_sets.append(UVSet(name=f"TEXCOORD_{idx}", coords_count=uv_set_indices[idx]))

    info.unit = "meter"
    info.unit_source = "glTF 规范固定为米"


def _parse_gltf_raw(path: Path, info: ModelInfo) -> None:
    """只读 GLB 头部 + JSON chunk（不读二进制几何）。对畸形数据全部抛 Python 异常，
    由上层 inspect_model 的 try/except 兜住，绝不触发进程级崩溃。"""
    if path.suffix.lower() == ".glb":
        file_size = path.stat().st_size
        with path.open("rb") as f:
            header = f.read(12)
            if len(header) < 12:
                raise ValueError("GLB 文件过短")
            magic, version, length = struct.unpack("<4sII", header)
            if magic != b"glTF":
                raise ValueError("非法 GLB 文件（magic 不是 glTF）")
            chunk_header = f.read(8)
            if len(chunk_header) < 8:
                raise ValueError("GLB chunk 头过短")
            chunk_len, chunk_type = struct.unpack("<II", chunk_header)
            # 保护：JSON chunk 长度不该超过文件大小，避免畸形值导致超大内存读取
            if chunk_len <= 0 or chunk_len > file_size:
                raise ValueError(f"GLB JSON chunk 长度异常: {chunk_len} (文件 {file_size})")
            json_bytes = f.read(chunk_len)
            data = json.loads(json_bytes.decode("utf-8", errors="ignore"))
    else:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))

    accessors = data.get("accessors", [])
    meshes = data.get("meshes", [])

    vertex_total = 0
    face_total = 0
    uv_set_indices: Dict[int, int] = {}

    for mesh in meshes:
        for prim in mesh.get("primitives", []):
            attrs = prim.get("attributes", {}) or {}
            pos_idx = attrs.get("POSITION")
            if pos_idx is not None and pos_idx < len(accessors):
                vertex_total += accessors[pos_idx].get("count", 0)

            indices_idx = prim.get("indices")
            if indices_idx is not None and indices_idx < len(accessors):
                face_total += accessors[indices_idx].get("count", 0) // 3
            elif pos_idx is not None and pos_idx < len(accessors):
                face_total += accessors[pos_idx].get("count", 0) // 3

            for key, acc_idx in attrs.items():
                if key.startswith("TEXCOORD_"):
                    try:
                        n = int(key.split("_")[1])
                    except (IndexError, ValueError):
                        continue
                    cnt = accessors[acc_idx].get("count", 0) if acc_idx < len(accessors) else 0
                    uv_set_indices[n] = uv_set_indices.get(n, 0) + cnt

    info.vertex_count = vertex_total
    info.face_count = face_total
    for idx in sorted(uv_set_indices.keys()):
        info.uv_sets.append(UVSet(name=f"TEXCOORD_{idx}", coords_count=uv_set_indices[idx]))

    info.unit = "meter"
    info.unit_source = "glTF 规范固定为米"


# ============================================================
# FBX
# ============================================================
_FBX_UNIT_SCALE_RE = re.compile(
    r'"UnitScaleFactor"\s*,\s*"[^"]*"\s*,\s*"",\s*"A\+",\s*([\d.]+)',
    re.IGNORECASE,
)
_FBX_UV_NAME_RE = re.compile(r'LayerElementUV:\s*(\d+)\s*\{[^}]*?Name:\s*"([^"]*)"', re.DOTALL)


# 二进制 FBX 一次性读入的上限（200MB）。超过则只读这么多，单位通常在文件头部，
# UV 套数可能不完整，但避免几 GB 文件吃爆内存导致进程崩溃。
_FBX_MAX_READ = 200 * 1024 * 1024
_FBX_WALK_MAX_DEPTH = 200


def _inspect_fbx(path: Path, info: ModelInfo) -> None:
    try:
        with path.open("rb") as f:
            head = f.read(32)
    except Exception as e:
        info.ok = False
        info.error = f"FBX 打开失败: {e}"
        return

    if head.startswith(b"Kaydara FBX Binary"):
        _inspect_fbx_binary(path, info)
    else:
        _inspect_fbx_ascii(path, info)


def _inspect_fbx_ascii(path: Path, info: ModelInfo) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        info.ok = False
        info.error = f"FBX ASCII 读取失败: {e}"
        return

    unit_scale = None
    m = re.search(r'UnitScaleFactor[^\d-]+([\d.]+)', text)
    if m:
        try:
            unit_scale = float(m.group(1))
        except ValueError:
            pass

    if unit_scale is not None:
        info.unit = _fbx_scale_to_unit(unit_scale)
        info.unit_source = f"FBX GlobalSettings.UnitScaleFactor={unit_scale}"
    else:
        info.unit = "unknown"
        info.unit_source = "FBX ASCII 未找到 UnitScaleFactor"

    uv_names: List[str] = []
    for m in _FBX_UV_NAME_RE.finditer(text):
        uv_names.append(m.group(2) or f"UVSet_{m.group(1)}")

    seen = set()
    for name in uv_names:
        if name in seen:
            continue
        seen.add(name)
        info.uv_sets.append(UVSet(name=name))

    info.vertex_count = len(re.findall(r'Vertices:\s*\*\d+', text))
    info.face_count = 0
    info.extra["note"] = "ASCII FBX 顶点/面数只提供占位，精确值请用 Blender 打开"


def _inspect_fbx_binary(path: Path, info: ModelInfo) -> None:
    """
    Binary FBX 解析（轻量级）：
    顶层 Node 头：endOffset(u32/u64) + numProps(u32/u64) + propListLen(u32/u64) + nameLen(u8) + name
    7.5+ 起所有 offset/count 字段变为 u64。
    我们只扫描顶层节点和 GlobalSettings 节点下的 P "UnitScaleFactor" 属性。
    UV 套数走 LayerElementUV 节点计数。
    """
    try:
        file_size = path.stat().st_size
        with path.open("rb") as f:
            data = f.read(_FBX_MAX_READ)
        truncated = file_size > _FBX_MAX_READ
        if truncated:
            info.extra["note"] = f"FBX 较大({file_size//1024//1024}MB)，仅解析前 {_FBX_MAX_READ//1024//1024}MB"
    except MemoryError:
        info.ok = False
        info.error = "FBX 文件过大，内存不足"
        return
    except Exception as e:
        info.ok = False
        info.error = f"FBX 二进制读取失败: {e}"
        return

    if len(data) < 27:
        info.ok = False
        info.error = "FBX 文件过短"
        return

    version = struct.unpack("<I", data[23:27])[0]
    use_64bit = version >= 7500

    info.extra["fbx_version"] = version

    unit_scale = _fbx_binary_extract_unit_scale(data, use_64bit)
    if unit_scale is not None:
        info.unit = _fbx_scale_to_unit(unit_scale)
        info.unit_source = f"FBX GlobalSettings.UnitScaleFactor={unit_scale}"
    else:
        info.unit = "unknown"
        info.unit_source = "FBX 未找到 UnitScaleFactor"

    uv_names = _fbx_binary_collect_uv_names(data, use_64bit)
    seen = set()
    for n in uv_names:
        if n in seen:
            continue
        seen.add(n)
        info.uv_sets.append(UVSet(name=n))

    vertex_total, face_total = _fbx_binary_vertex_face_estimate(data, use_64bit)
    info.vertex_count = vertex_total
    info.face_count = face_total


def _fbx_binary_iter_nodes(data: bytes, start: int, end: int, use_64bit: bool):
    """yield (node_name, props_raw, children_start, children_end, next_node_start)。"""
    offset_size = 8 if use_64bit else 4
    header_size = 13 if use_64bit else 13  # 3*offset_size + 1
    header_size = offset_size * 3 + 1

    cursor = start
    while cursor + header_size <= end:
        if use_64bit:
            end_offset = struct.unpack_from("<Q", data, cursor)[0]
            num_props = struct.unpack_from("<Q", data, cursor + 8)[0]
            prop_list_len = struct.unpack_from("<Q", data, cursor + 16)[0]
            name_len = data[cursor + 24]
            name_start = cursor + 25
        else:
            end_offset = struct.unpack_from("<I", data, cursor)[0]
            num_props = struct.unpack_from("<I", data, cursor + 4)[0]
            prop_list_len = struct.unpack_from("<I", data, cursor + 8)[0]
            name_len = data[cursor + 12]
            name_start = cursor + 13

        if end_offset == 0 or end_offset <= cursor:
            break
        if end_offset > end or name_start + name_len > end:
            break

        name = data[name_start:name_start + name_len].decode("ascii", errors="ignore")
        props_start = name_start + name_len
        props_end = props_start + prop_list_len
        children_start = props_end
        children_end = end_offset

        yield name, data[props_start:props_end], children_start, children_end, end_offset
        cursor = end_offset


def _fbx_binary_extract_unit_scale(data: bytes, use_64bit: bool) -> Optional[float]:
    """走顶层 -> GlobalSettings -> Properties70 -> P，找 UnitScaleFactor。"""
    top_start = 27
    file_size = len(data)
    for name, _, ch_start, ch_end, _ in _fbx_binary_iter_nodes(data, top_start, file_size, use_64bit):
        if name != "GlobalSettings":
            continue
        for sub_name, _, sub_ch_start, sub_ch_end, _ in _fbx_binary_iter_nodes(data, ch_start, ch_end, use_64bit):
            if sub_name not in ("Properties70", "Properties60"):
                continue
            for p_name, p_props, _, _, _ in _fbx_binary_iter_nodes(data, sub_ch_start, sub_ch_end, use_64bit):
                if p_name != "P":
                    continue
                values = _fbx_parse_properties(p_props)
                if not values:
                    continue
                prop_name = values[0] if isinstance(values[0], str) else ""
                if prop_name == "UnitScaleFactor":
                    for v in values:
                        if isinstance(v, (int, float)):
                            return float(v)
    return None


def _fbx_binary_collect_uv_names(data: bytes, use_64bit: bool) -> List[str]:
    """递归扫描所有 LayerElementUV 节点，提取 Name 子节点。"""
    found: List[str] = []
    top_start = 27
    file_size = len(data)

    def walk(start: int, end: int, depth: int = 0):
        if depth > _FBX_WALK_MAX_DEPTH:
            return
        for name, _, ch_start, ch_end, _ in _fbx_binary_iter_nodes(data, start, end, use_64bit):
            if name == "LayerElementUV":
                for sub_name, sub_props, _, _, _ in _fbx_binary_iter_nodes(data, ch_start, ch_end, use_64bit):
                    if sub_name == "Name":
                        vals = _fbx_parse_properties(sub_props)
                        if vals and isinstance(vals[0], str):
                            found.append(vals[0] or "UVSet")
                            break
                else:
                    found.append("UVSet")
            else:
                walk(ch_start, ch_end, depth + 1)

    walk(top_start, file_size, 0)
    return found


def _fbx_binary_vertex_face_estimate(data: bytes, use_64bit: bool):
    """累加所有 Geometry / Vertices + PolygonVertexIndex 的长度做估算。"""
    vertex_total = 0
    face_total = 0
    top_start = 27
    file_size = len(data)

    def walk(start: int, end: int, depth: int = 0):
        nonlocal vertex_total, face_total
        if depth > _FBX_WALK_MAX_DEPTH:
            return
        for name, _, ch_start, ch_end, _ in _fbx_binary_iter_nodes(data, start, end, use_64bit):
            if name == "Geometry":
                for sub_name, sub_props, _, _, _ in _fbx_binary_iter_nodes(data, ch_start, ch_end, use_64bit):
                    if sub_name == "Vertices":
                        vals = _fbx_parse_properties(sub_props)
                        if vals and isinstance(vals[0], (list, tuple)):
                            vertex_total += len(vals[0]) // 3
                    elif sub_name == "PolygonVertexIndex":
                        vals = _fbx_parse_properties(sub_props)
                        if vals and isinstance(vals[0], (list, tuple)):
                            arr = vals[0]
                            face_total += sum(1 for x in arr if x < 0)
            walk(ch_start, ch_end, depth + 1)

    walk(top_start, file_size, 0)
    return vertex_total, face_total


def _fbx_parse_properties(props_raw: bytes) -> list:
    """解析 FBX 二进制属性流，只返回我们关心的字符串/数字/数组。失败回退到空 list。"""
    result = []
    cursor = 0
    n = len(props_raw)
    try:
        while cursor < n:
            type_code = props_raw[cursor:cursor + 1]
            cursor += 1
            if not type_code:
                break
            tc = type_code.decode("ascii", errors="ignore")
            if tc == "Y":
                result.append(struct.unpack_from("<h", props_raw, cursor)[0]); cursor += 2
            elif tc == "C":
                result.append(bool(props_raw[cursor])); cursor += 1
            elif tc == "I":
                result.append(struct.unpack_from("<i", props_raw, cursor)[0]); cursor += 4
            elif tc == "F":
                result.append(struct.unpack_from("<f", props_raw, cursor)[0]); cursor += 4
            elif tc == "D":
                result.append(struct.unpack_from("<d", props_raw, cursor)[0]); cursor += 8
            elif tc == "L":
                result.append(struct.unpack_from("<q", props_raw, cursor)[0]); cursor += 8
            elif tc in ("f", "d", "l", "i", "b"):
                array_length = struct.unpack_from("<I", props_raw, cursor)[0]; cursor += 4
                encoding = struct.unpack_from("<I", props_raw, cursor)[0]; cursor += 4
                compressed_length = struct.unpack_from("<I", props_raw, cursor)[0]; cursor += 4
                payload = props_raw[cursor:cursor + compressed_length]
                cursor += compressed_length
                if encoding == 1:
                    import zlib
                    try:
                        payload = zlib.decompress(payload)
                    except zlib.error:
                        result.append([])
                        continue
                fmt_map = {"f": ("<f", 4), "d": ("<d", 8), "l": ("<q", 8), "i": ("<i", 4), "b": ("<b", 1)}
                fmt, size = fmt_map[tc]
                arr = list(struct.iter_unpack(fmt, payload))
                result.append([x[0] for x in arr])
            elif tc == "S" or tc == "R":
                length = struct.unpack_from("<I", props_raw, cursor)[0]; cursor += 4
                raw = props_raw[cursor:cursor + length]; cursor += length
                if tc == "S":
                    decoded = raw.decode("utf-8", errors="ignore")
                    if "\x00\x01" in decoded:
                        decoded = decoded.split("\x00\x01")[-1]
                    result.append(decoded)
                else:
                    result.append(raw)
            else:
                break
    except Exception:
        pass
    return result


def _fbx_scale_to_unit(scale: float) -> str:
    """FBX UnitScaleFactor 是 cm 到目标单位的换算倍数：1.0=cm, 100.0=m, 0.1=mm, 2.54=in, 30.48=ft。"""
    table = [
        (0.1, "millimeter"),
        (1.0, "centimeter"),
        (2.54, "inch"),
        (30.48, "foot"),
        (100.0, "meter"),
    ]
    best = min(table, key=lambda kv: abs(kv[0] - scale))
    if abs(best[0] - scale) / max(best[0], 1e-6) < 0.05:
        return best[1]
    return "unknown"
