# -*- coding: utf-8 -*-
"""
贴图关键词分类（纯 Python，无 bpy / unreal 依赖）。

逻辑参照原 ue_obj_to_glb_pipeline.py 的 classify_texture_type，但去掉了 log 调用。
Blender 端和 UE 端共用同一份分类规则，保证两端识别结果一致。
"""

from __future__ import annotations

import re
import ast
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

TEXTURE_NAME_SEPARATORS = ["_", "-", ".", " ", "/", "\\"]
TEXTURE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".tga", ".bmp", ".exr", ".hdr"]


BASECOLOR_KEYS = frozenset(
    [
        "roughness-metal map", "pm_diffuse", "basecolour", "colour", "color", "albedo0", "bc",
        "basecolor", "diffuse", "albedo", "base_color", "diffusemap", "alb",
        "diff", "01 basecolor texture", "01 basecolor", "basemap", "diffusemap",
        "albedomap", "colormap", "base_texture", "base_map", "d", "a", "base color map",
        "base map", "basecolortexture", "base", "t", "hoodie", "m", "tex", "f",
        "col",
    ] + [f"col{i}" for i in range(1, 8)] + [f"d{i}" for i in range(1, 8)]
)

METALLIC_KEYS = frozenset()
ROUGHNESS_KEYS = frozenset()

METALLIC_ROUGHNESS_KEYS = frozenset([
    "metallicroughness", "mro", "mr", "mrae", "metalroughocc",
    "metallicroughnessmap", "ormap", "mrmap",
    "occlusionroughnessmetallic", "packedarm", "packeda", "naom",
    "rgh", "ormh", "msro", "aorame",
    "mrb", "mrh", "arh", "armh", "mre", "msra", "msr",
    "rma", "mra", "ram", "rmo", "orm", "mrao", "aorm", "maor", "aomr", "raom", "rmao",
])

NORMAL_KEYS = frozenset([
    "pm_normals", "normalmap", "normals", "n", "normal", "nrm", "tn",
    "norm", "basenormal", "tdn", "nml", "nrml", "nm", "tnr", "nmp", "normalgl",
] + [f"n{i}" for i in range(1, 8)])

ROUGHNESS_SPECULAR_METALLIC_KEYS = frozenset([
    "roughnessspecularmetallic", "roughnessspecularmetallicmap", "rsm",
    "roughnessspecularmetallicao",
])

FLEXIBLE_KEYS = frozenset(["Difuse"])

BAD_KEYS = frozenset([
    "rgb", "rgba", "linear", "maskmap", "blendmask",
    "emissivemap", "emiss", "opacitymap",
    "defaultblack", "defaultpurple",
    "noise", "rgbmaska", "cm",
    "msk", "id", "solidmask", "mk", "ptex",
    "rddirt", "height", "specular", "specularmap", "lut", "ramp",
    "roughnessmap", "metallicmap", "tmask", "tmsk", "rgbmask",
    "rgbmsk", "rgbrough", "rgbmetal", "rgbao",
    "metallic", "rough", "edgecavatityao",
    "sg", "roughness", "interior", "alpha",
    "reflection", "micro", "ao", "snow",
    "detailmask", "blacktexture", "mask",
    "metall", "dirtymetal", "openglnormal",
    "colormask", "index",
    "tangent", "dissolvefx", "shellfur", "voronoi",
])

NON_BASECOLOR_KEYS = (
    METALLIC_KEYS
    | ROUGHNESS_KEYS
    | METALLIC_ROUGHNESS_KEYS
    | NORMAL_KEYS
    | ROUGHNESS_SPECULAR_METALLIC_KEYS
    | FLEXIBLE_KEYS
    | BAD_KEYS
)

BEST_MATCH_BASECOLOR = True
BEST_MATCH_BASECOLOR_MODE = "value"  # "key" or "value"
BEST_MATCH_BASECOLOR_TOKENS = frozenset([
    "s", "dye", "b", "bc", "e", "f", "m", "01", "d", "di", "00",
    "clr", "eyelashes", "clra", "clrm", "difuse",
])

TEXTURE_KEYWORD_ROLES = (
    ("basecolor", "BASECOLOR_KEYS", "basecolor_tokens"),
    ("metallic", "METALLIC_KEYS", "metallic_tokens"),
    ("roughness", "ROUGHNESS_KEYS", "roughness_tokens"),
    ("metallic_roughness", "METALLIC_ROUGHNESS_KEYS", "metallic_roughness_tokens"),
    ("normal", "NORMAL_KEYS", "normal_tokens"),
    ("roughness_specular_metallic", "ROUGHNESS_SPECULAR_METALLIC_KEYS", "roughness_specular_metallic_tokens"),
    ("flexible", "FLEXIBLE_KEYS", "flexible_tokens"),
    ("bad", "BAD_KEYS", "bad_texture_tokens"),
    ("best_match_basecolor", "BEST_MATCH_BASECOLOR_TOKENS", "best_match_basecolor_tokens"),
)


def split_config_tokens(value: Any) -> Set[str]:
    """Split GUI token text. Spaces inside a line are kept for legacy phrase tokens."""
    if not value:
        return set()
    if isinstance(value, (list, tuple, set, frozenset)):
        values = value
    else:
        values = re.split(r"[,，;；\r\n\t]+", str(value))
    return {str(t).strip().lower() for t in values if str(t).strip()}


def format_config_tokens(tokens: Iterable[str]) -> str:
    return "\n".join(sorted({str(t).strip().lower() for t in tokens if str(t).strip()}))


def default_texture_keyword_tokens() -> Dict[str, str]:
    defaults: Dict[str, str] = {}
    for role, attr_name, _ in TEXTURE_KEYWORD_ROLES:
        defaults[role] = format_config_tokens(globals().get(attr_name, frozenset()))
    return defaults


def texture_keyword_tokens_from_config(
    config: Dict[str, Any],
    defaults: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    result = dict(defaults or default_texture_keyword_tokens())
    saved = config.get("texture_keyword_tokens")
    if isinstance(saved, dict) and saved:
        for role, _, _ in TEXTURE_KEYWORD_ROLES:
            if role in saved:
                result[role] = format_config_tokens(split_config_tokens(saved.get(role, "")))
        return result

    # 兼容旧版“额外 token”配置：旧配置只追加，不替换默认表。
    for role, _, legacy_key in TEXTURE_KEYWORD_ROLES:
        extra = split_config_tokens(config.get(legacy_key, ""))
        if extra:
            merged = split_config_tokens(result.get(role, "")) | extra
            result[role] = format_config_tokens(merged)
    return result


def _safe_eval_token_expr(node: ast.AST) -> List[str]:
    if isinstance(node, ast.Call) and node.args:
        return _safe_eval_token_expr(node.args[0])
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: List[str] = []
        for elt in node.elts:
            values.extend(_safe_eval_token_expr(elt))
        return values
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _safe_eval_token_expr(node.left) + _safe_eval_token_expr(node.right)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        text = ""
        for part in node.values:
            if isinstance(part, ast.Constant):
                text += str(part.value)
            else:
                return []
        return [text]
    if isinstance(node, ast.ListComp) and len(node.generators) == 1:
        gen = node.generators[0]
        if not isinstance(gen.target, ast.Name) or not isinstance(gen.iter, ast.Call):
            return []
        call = gen.iter
        if not isinstance(call.func, ast.Name) or call.func.id != "range":
            return []
        try:
            args = [ast.literal_eval(arg) for arg in call.args]
        except Exception:
            return []
        for _ in range(3 - len(args)):
            args.append(None)
        start = 0 if args[1] is None else args[0]
        stop = args[0] if args[1] is None else args[1]
        step = 1 if args[2] is None else args[2]
        values: List[str] = []
        for i in range(start, stop, step):
            if isinstance(node.elt, ast.JoinedStr):
                text = ""
                for part in node.elt.values:
                    if isinstance(part, ast.Constant):
                        text += str(part.value)
                    elif isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name) and part.value.id == gen.target.id:
                        text += str(i)
                    else:
                        text = ""
                        break
                if text:
                    values.append(text)
            elif isinstance(node.elt, ast.Constant) and isinstance(node.elt.value, str):
                values.append(node.elt.value)
        return values
    return []


def load_texture_keyword_tokens_from_script(script_path: str) -> Dict[str, str]:
    """Read hardcoded token constants from ue_obj_to_glb_pipeline.py without executing it."""
    if not script_path:
        return {}
    path = Path(script_path)
    if not path.exists():
        return {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    attr_to_role = {attr_name: role for role, attr_name, _ in TEXTURE_KEYWORD_ROLES}
    values: Dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in attr_to_role:
                tokens = _safe_eval_token_expr(node.value)
                values[attr_to_role[target.id]] = format_config_tokens(tokens)
    return values


def split_texture_name_to_tokens(name: str) -> Set[str]:
    name_lower = name.lower()
    tokens: Set[str] = set()
    current = name_lower
    for sep in TEXTURE_NAME_SEPARATORS:
        if sep in current:
            for part in current.split(sep):
                part = part.strip()
                if part:
                    tokens.add(part)
    if not tokens:
        tokens.add(name_lower)
    return tokens


def convert_ue_path_to_file_path(ue_path: str) -> str:
    """把 UE 路径 (/Game/X/Y.Y) 转换成相对文件路径（无扩展名加 .png 占位）。"""
    if ue_path.startswith("/Game/"):
        ue_path = ue_path[6:]
    parts = ue_path.split("/")
    if parts:
        filename = parts[-1]
        if "." in filename:
            name_parts = filename.split(".")
            if len(name_parts) >= 2 and name_parts[-1] == name_parts[-2]:
                parts[-1] = ".".join(name_parts[:-1])
                ue_path = "/".join(parts)
    return f"{ue_path}.png"


def classify_texture_type(texture_key: str, texture_value: str = "") -> Tuple[str, int, List[str]]:
    """
    返回 (类型, 优先级, 命中关键词)。
    类型: basecolor | normal | metallic | roughness | metallic_roughness |
          roughness_specular_metallic | flexible | unknown
    """
    tokens = split_texture_name_to_tokens(texture_key)

    if any(t in FLEXIBLE_KEYS for t in tokens):
        return ("flexible", 1, [])
    if any(t in ROUGHNESS_SPECULAR_METALLIC_KEYS for t in tokens):
        return ("roughness_specular_metallic", 1, [])
    if any(t in METALLIC_KEYS for t in tokens):
        return ("metallic", 1, [])
    if any(t in ROUGHNESS_KEYS for t in tokens):
        return ("roughness", 1, [])
    matched_mr = [t for t in tokens if t in METALLIC_ROUGHNESS_KEYS]
    if matched_mr:
        return ("metallic_roughness", 1, matched_mr)
    if any(t in NORMAL_KEYS for t in tokens):
        return ("normal", 1, [])

    key_blacklist = [t for t in tokens if t in NON_BASECOLOR_KEYS]
    if key_blacklist:
        return ("unknown", 0, [])

    if texture_value:
        converted = convert_ue_path_to_file_path(texture_value)
        value_stem = Path(converted).stem.lower()
        value_tokens = split_texture_name_to_tokens(value_stem)
        if [t for t in value_tokens if t in NON_BASECOLOR_KEYS]:
            return ("unknown", 0, [])

    if BEST_MATCH_BASECOLOR and BEST_MATCH_BASECOLOR_TOKENS:
        check_tokens: Set[str] = set()
        if BEST_MATCH_BASECOLOR_MODE == "key":
            check_tokens = tokens
        elif BEST_MATCH_BASECOLOR_MODE == "value" and texture_value:
            converted = convert_ue_path_to_file_path(texture_value)
            value_stem = Path(converted).stem.lower()
            check_tokens = split_texture_name_to_tokens(value_stem)
        if check_tokens and any(t in BEST_MATCH_BASECOLOR_TOKENS for t in check_tokens):
            return ("basecolor", 100, [])

    if any(t in BASECOLOR_KEYS for t in tokens):
        return ("basecolor", 1, [])

    return ("unknown", 0, [])


def find_material_json(material_name: str, material_root: Path) -> "Path | None":
    """简化版的材质 JSON 查找：精确匹配优先，然后模糊匹配。"""
    if not material_root or not material_root.exists():
        return None

    direct = material_root / f"{material_name}.json"
    if direct.exists():
        return direct

    for candidate in material_root.rglob(f"*{material_name}*.json"):
        if material_name.lower() in candidate.stem.lower():
            return candidate
    return None


def find_texture_file(texture_value: str, texture_root: Path) -> "Path | None":
    """简化版的贴图查找：先尝试 UE 路径直接拼接，再做模糊搜索。"""
    if not texture_root or not texture_root.exists():
        return None

    converted = convert_ue_path_to_file_path(texture_value)
    stem = Path(converted).stem

    direct = texture_root / converted
    if direct.exists():
        return direct

    for ext in TEXTURE_EXTENSIONS:
        for cand in texture_root.rglob(f"*{stem}*{ext}"):
            if stem.lower() in cand.stem.lower():
                return cand
    return None


# ============================================================
# 无 JSON 模式：按贴图文件名匹配（移植自原脚本）
# ============================================================
_MAT_PREFIXES = ["mi_", "m_", "mat_", "material_", "mtl_", "inst_", "master_"]
_IGNORED_NAME_TOKENS = {"m", "mi", "mat", "mtl", "material", "inst", "master", "tex", "texture"}
_BAD_HINT_WORDS = {"noise", "lut", "ramp", "detailmask", "cloud", "dummy", "preview"}


def normalize_material_search_name(material_name: str) -> str:
    name = material_name.lower().strip()
    name = re.sub(r"\.\d+$", "", name)  # Blender 重名后缀 .001
    for prefix in _MAT_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name.strip("_-. ")


def tokenize_name_for_matching(name: str) -> List[str]:
    if not name:
        return []
    normalized = name.lower().strip()
    normalized = re.sub(r"\.\d+$", "", normalized)
    parts = re.split(r"[\s_\-./\\]+", normalized)
    tokens: List[str] = []
    for part in parts:
        part = part.strip()
        if not part or part in _IGNORED_NAME_TOKENS:
            continue
        if len(part) <= 1 and not part.isdigit():
            continue
        tokens.append(part)
    return tokens


def score_candidate_texture_for_material(texture_path: Path, material_name: str,
                                         model_name: str = "", folder_name: str = "") -> int:
    """为无 JSON 模式下的候选贴图打分，分越高越可能属于当前材质。"""
    stem = texture_path.stem.lower()
    stem_tokens = split_texture_name_to_tokens(stem)
    score = 0

    norm = normalize_material_search_name(material_name)
    if norm:
        if norm == stem:
            score += 40
        elif norm in stem:
            score += 20

    for token in tokenize_name_for_matching(material_name):
        if token in stem_tokens:
            score += 8
        elif token in stem:
            score += 4

    for token in tokenize_name_for_matching(model_name):
        if token in stem_tokens:
            score += 3
        elif token in stem:
            score += 1

    for token in tokenize_name_for_matching(folder_name):
        if token in stem_tokens:
            score += 2

    if any(word in stem for word in _BAD_HINT_WORDS):
        score -= 8

    return score


def collect_texture_files_in_dir(texture_dir: Path, recursive: bool = False) -> List[Path]:
    if not texture_dir or not texture_dir.exists():
        return []
    files: List[Path] = []
    exts = set(TEXTURE_EXTENSIONS)
    try:
        if recursive:
            for p in texture_dir.rglob("*"):
                if p.is_file() and p.suffix.lower() in exts:
                    files.append(p)
        else:
            for p in texture_dir.iterdir():
                if p.is_file() and p.suffix.lower() in exts:
                    files.append(p)
    except OSError:
        pass
    return files


def resolve_textures_by_filename(material_name: str, texture_dir: Path,
                                 model_name: str = "", recursive: bool = False) -> Dict[str, Path]:
    """
    无 JSON 模式：在 texture_dir 收集候选贴图 -> 按材质名打分 -> 按文件名分类 ->
    每个角色 (basecolor/normal/metallic_roughness/...) 取最优一张。
    返回 {role: Path}。
    """
    candidates = collect_texture_files_in_dir(texture_dir, recursive)
    if not candidates:
        return {}

    folder_name = texture_dir.name
    scored = [
        (score_candidate_texture_for_material(p, material_name, model_name, folder_name), p)
        for p in candidates
    ]
    positive = [(s, p) for s, p in scored if s > 0]
    pool = positive if positive else scored
    pool.sort(key=lambda sp: (-sp[0], sp[1].name.lower()))

    role_to_path: Dict[str, Path] = {}
    role_meta: Dict[str, Tuple[int, int]] = {}  # role -> (classify_priority, name_score)

    for name_score, tex in pool:
        role, prio, _ = classify_texture_type(tex.stem, tex.stem)
        if role == "unknown":
            continue
        prev = role_meta.get(role)
        if prev is None or prio > prev[0] or (prio == prev[0] and name_score > prev[1]):
            role_to_path[role] = tex
            role_meta[role] = (prio, name_score)

    return role_to_path


def _find_named_folder(root: Optional[Path], name: str) -> Optional[Path]:
    if not root or not root.exists() or not name:
        return None
    candidates = {name.strip()}
    lowered = name.lower().strip()
    for prefix in ("sm_", "sk_", "s_", "m_", "mi_", "mat_"):
        if lowered.startswith(prefix):
            candidates.add(name[len(prefix):].strip("_-. "))
    candidates = {c for c in candidates if c}

    for candidate in candidates:
        direct = root / candidate
        if direct.exists() and direct.is_dir():
            return direct
    try:
        targets = {c.lower() for c in candidates}
        for child in root.iterdir():
            if child.is_dir() and child.name.lower() in targets:
                return child
        for child in root.rglob("*"):
            if child.is_dir() and child.name.lower() in targets:
                return child
    except OSError:
        return None
    return None


def resolve_textures_for_material(material_name: str,
                                  material_root: Optional[Path],
                                  texture_dir: Path,
                                  model_name: str = "") -> Tuple[Dict[str, Path], str]:
    """
    统一入口：JSON 优先，没 JSON / JSON 无有效贴图则回退文件名匹配。
    返回 ({role: Path}, 来源说明)。
    """
    import json as _json

    # 1) 尝试 JSON
    if material_root and material_root.exists():
        json_path = find_material_json(material_name, material_root)
        if json_path:
            try:
                data = _json.loads(json_path.read_text(encoding="utf-8"))
                textures = data.get("Textures", {}) or {}
            except Exception:
                textures = {}

            role_to_path: Dict[str, Path] = {}
            role_prio: Dict[str, int] = {}
            for key, value in textures.items():
                role, prio, _ = classify_texture_type(key, value)
                if role == "unknown":
                    continue
                tex_file = find_texture_file(value, texture_dir)
                if not tex_file and material_root:
                    tex_file = find_texture_file(value, material_root)
                if not tex_file:
                    continue
                if role not in role_to_path or prio > role_prio.get(role, 0):
                    role_to_path[role] = tex_file
                    role_prio[role] = prio

            if role_to_path:
                return role_to_path, f"JSON({json_path.name})"

    # 2) 优先进入同名资源文件夹。此时贴图名不一定包含模型名，只要 token 能分类即可。
    for root in (texture_dir, material_root):
        model_folder = _find_named_folder(root, model_name or material_name)
        if model_folder:
            by_name = resolve_textures_by_filename(material_name, model_folder, model_name, recursive=True)
            if by_name:
                return by_name, f"同名资源文件夹({model_folder.name})"

    # 3) 回退：文件名匹配（贴图在模型旁目录，或配置的贴图根目录）
    by_name = resolve_textures_by_filename(material_name, texture_dir, model_name, recursive=False)
    if by_name:
        return by_name, f"文件名匹配({texture_dir.name})"

    # 4) 再回退：递归搜索贴图根目录
    by_name = resolve_textures_by_filename(material_name, texture_dir, model_name, recursive=True)
    if by_name:
        return by_name, f"文件名匹配(递归 {texture_dir.name})"

    return {}, "无匹配"
