#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess
from pathlib import Path

# ===== IDE直接运行配置 =====
BLENDER_EXE_PATH = r"E:\Software\Blender\blender.exe"

# 如果不是 Blender Python，就用 Blender 重新运行当前脚本
if "bpy" not in sys.modules:
    script_path = Path(__file__).resolve()

    if not Path(BLENDER_EXE_PATH).exists():
        print(f"❌ 找不到 Blender: {BLENDER_EXE_PATH}")
        print("请把 BLENDER_EXE_PATH 改成真实的 blender.exe 路径")
        sys.exit(1)

    cmd = [BLENDER_EXE_PATH, "-b", "-P", str(script_path)]

    print("当前不是 Blender Python，正在调用 Blender 运行：")
    print(" ".join(cmd))

    sys.exit(subprocess.run(cmd).returncode)


# ===== 下面开始才是 Blender 环境里的导入 =====
import gc
import json
import time
import shutil
import csv
import tempfile
import re
import platform
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

import bpy
import bmesh
from mathutils import Vector
from math import floor, isfinite
import numpy as np

BLENDER_AVAILABLE = True

# ===== 配置区 =====
SCRIPT_ROOT = Path(__file__).resolve().parent
Game_ROOT = SCRIPT_ROOT  # GUI 运行时会覆盖为用户选择的资源根目录

# 分离模式配置
ENABLE_SEPARATE_FOLDERS = True                  # 启用OBJ和材质分离模式
OBJ_ROOT = SCRIPT_ROOT  # GUI 运行时会直接传入待处理模型路径
GLB_ROOT = OBJ_ROOT  # 兼容旧变量名：下面部分逻辑仍复用 GLB_ROOT 这个变量名
MATERIAL_TEXTURE_ROOT = SCRIPT_ROOT  # GUI 运行时会覆盖为资源根目录
DST_ROOT = OBJ_ROOT / "ExportedGLB_FromOBJ"   # 导出GLB目录

# 传统模式配置（当ENABLE_SEPARATE_FOLDERS为False时使用）
SRC_ROOT = Game_ROOT                  # 传统模式源目录（OBJ、材质、贴图都在此目录下）

PROGRESS_FILE = DST_ROOT / "export_progress.json"  # 断点续传记录
DETAILED_LOG_FILE = DST_ROOT / "detailed_processing.log"  # 详细日志
CHECKPOINT_FILE = DST_ROOT / "processing_checkpoint.json"  # 检查点文件
JSON_INDEX_CSV = DST_ROOT / "file_index_json.csv"  # JSON索引CSV
TEXTURE_INDEX_CSV = DST_ROOT / "file_index.csv"  # 纹理索引CSV

# 单glb文件夹构建模式
ENABLE_SINGLE_GLB_FOLDER_BUILD = True

# 处理配置
CHECKPOINT_INTERVAL = 5  # 每处理N个文件保存检查点

# 贴图名称分隔符数组
TEXTURE_NAME_SEPARATORS = ['_', '-', '.', ' ', '/', '\\']

#== 万能匹配 ==============================================
# 万能匹配功能：可以将匹配到的贴图分通道连接到BSDF的任意输入
FLEXIBLE_KEYS = frozenset([
   "Difuse"
])

# 分离颜色节点输出通道到BSDF的映射配置
# 可用的BSDF输入名称：
# - Base Color, Metallic, Roughness, IOR, Alpha
# - Specular IOR Level, Specular Tint (Blender 4.0+)
# - Emission Color, Emission Strength
# - Normal, Clearcoat, Clearcoat Roughness, etc.
FLEXIBLE_OUTPUT_R = "Roughness"           # R通道连接目标
FLEXIBLE_OUTPUT_G = "Metallic"            # G通道连接目标
FLEXIBLE_OUTPUT_B = "Specular IOR Level"  # B通道连接目标

#== 万能匹配结束 ==============================================
# 贴图类型关键字数组（精准匹配版）
BASECOLOR_KEYS = frozenset(
    [
        "roughness-metal map", "pm_diffuse", "basecolour", "colour", "color", "albedo0", "bc",
        "basecolor", "diffuse", "albedo", "base_color", "diffusemap", "pm_diffuse", "alb",
        "basecolor", "diff", "01 basecolor texture", "01 basecolor", "bc", "albedo", "diffuse",
        "color", "col", "basecolor", "basemap", "diffusemap", "albedomap", "colormap",
        "base_texture", "base_map", "d", "a", "base color map", "base map", "basecolortexture", "base","t","hoodie","m","tex","f","gskbn1","pattern01",
        "4","rskpntbn1","rskpntc4","zi","bd2bn1s","bd2bn1","atlas1","roof1","roof1","bwood4","0032","f","fff","roots","mothcolors",
        "texture_japanesehornetcolors02","rap","a","atlas","wood","2","mask","1","ba","dye","do","clr","eyelash","clra","clrm","0","difuse",
    ] + [f"col{i}" for i in range(1, 8)] + [f"d{i}" for i in range(1, 8)]  # 例如支持col1~col7
)

METALLIC_KEYS = frozenset([
])

ROUGHNESS_KEYS = frozenset([
])

METALLIC_ROUGHNESS_KEYS = frozenset([
    "metallicroughness","mro","mro map","mr", 
    "mrae","mr","metalroughocc",
    "metallicroughnessmap", "ormap", "mrmap","mr",
    "occlusionroughnessmetallic","packedarm","packeda","naom","aomrd",
    "rgh","ormh","msro","aorame",
    "mrb", "mrh","arh","armh","mre","msra","msr",
    "aoroughnessmetallic","metallicroughnessao","roughnessmetallicao",
    "aometallicroughness","metallicaoroughness","roughnessaometallic",
    "aoroughmet","roughmetao","metroughao","aometrough","roughaomet"
    "rma","mra","ram","r_a_m","a_r_m","m_r_a","r_m_a","a_r_m","a_m_r",
    "rmo","orm","mro",
    "mrao","aorm","maor","aomr","raom","rmao",
    "m_r_o","m_o_r","o_m_r","o_r_m","r_m_o","r_o_m",
    "m_r_ao","m_ao_r","ao_m_r","ao_r_m","ao_m_r","ao_r_m",
    "ao/rough/met","ao_met_rough","rough_ao_met","rough_met_ao","met_ao_rough","met_rough_ao","roughness",
])

NORMAL_KEYS = frozenset([
    "pm_normals", "normalmap", "normals", "n", 
    "normal", "normalmap", "nrm","tn",
    "normals", "normalmap","norm","basenormal",
    "tdn","nml","nrml","nm","tnr","nmp","normalgl",
]+[f"n{i}" for i in range(1, 8)])
ROUGHNESS_SPECULAR_METALLIC_KEYS = frozenset([
    "roughnessspecularmetallic", "roughnessspecularmetallic",
    "roughnessspecularmetallicmap", "roughnessspecularmetallicmap",
    "rsm",
    "roughnessspecularmetallicao", "roughnessspecularmetallicao",

])
BAD_KEYS = frozenset([
    "rgb", "rgba", "linear",                # 赏金猎人
    "maskmap",
    "blendmask",                            # 碧蓝幻想
    "emissivemap","emiss",
    "opacitymap",
    "defaultblack", "defaultpurple",
    "noise", "rgbmaska", "normal1", "cm", "normal", "tn","tdn",
    "t_megaatlas_bc", "t_clothpattern03_d", "t_clothpattern06_d",
    "msk", "grundnoise", "id", "solidmask", "mk", "ptex","id01","dirtmask",
    "rddirt","noise01","height","specular","specularmap","lut","ramp",
    "roughnessmap","metallicmap","tmask","tmsk","rgbmask","wavenoise","ors",
    "rgbmsk","rgbrough","rgbmetal","grundenoise","lut","ramp","macro","rgbao","cloudnoise",
    "metallic","rough","edgecavatityao","titles","papercraft_a_c","bug","nmmr",
    "pm_specularMasks","sg","roughness","layered","interior","fire","o","alpha","defaultdiffuse","cctv",
    "reflection","micro","arch49","arch40","arch","ao","snow","rma","t_ghead_jfacepainta_d","pattern01","diff1","erm","uv1","orma","ids","ids1",
    "clouds","spectrum","polygonprototype","flow","detailmask","blacktexture","noise","faceflipbookyuki","mrs",
    "underwear","tort","metall","dirtymetal","clouds","tga","rgbmasks","mrs","openglnormal","baseflattendiffusemap"
    "finalhorsemask02","finalhorsemask","atmosphericcloudnoise03","mask01","heathershieldmask08","druid","detailroughness",
    "colormask","index","noh","nom","cr","nwo","smoke","pos","nam","rot","nem","nxm","nmr","01nom","nom","nx","nor","examplecolormask",
    "ars","atmosphericcloudnoise01","pismofull","ormo","em","ach","unpleasentgrime","tilingrock","metalroughao","linear",
    "defaultdiffuse","good64x64tilingnoisehighfreq","atmosphericcloudnoise04","blank","metal02","whitesquaretexture","emask","gradient","cloudsmoke",
    "AtmosphericCloudNoise04","doi","diro","circuitry","tech","2147483647","dummywhite","metalheightemissiverough","wave",
    "lacea03","lacea04","lacea01","lacea02","lacea03","lacea05","lacea06","lacea07","detailnma","hs","t_detailnma_02_twill",
    "tangent","dissolvefx","shellfur","voronoi","n","mask02","wood01","normals2","decals","dust","normals01","normals03","normals04","normals05","normals02","no",
    "eyehl","postoutline","eyehlb","normalgl","curvatureao","rad","octp","octo","macrovariation","ambient","m","aseu","nro",
    "nrrm","eyedetail","clrm","nrro","li","noise001","mi","black","n0066","metalsteel","ordp","terrainvolumemetadataone","terrainvolumemetadatatwo","mm",
    "mask","albedo1nrm","wavetest","uniformclouds","hdr",
])
# 合并所有非基础色关键字，用于降权检查
NON_BASECOLOR_KEYS = METALLIC_KEYS | ROUGHNESS_KEYS | METALLIC_ROUGHNESS_KEYS | NORMAL_KEYS | ROUGHNESS_SPECULAR_METALLIC_KEYS | FLEXIBLE_KEYS | BAD_KEYS

# 优先基础色匹配token配置
BEST_MATCH_BASECOLOR = True                      # 是否启用优先匹配基础色token，启用后遇到该token直接root返回基础色
BEST_MATCH_BASECOLOR_MODE = "value"              # 匹配方式: "key"(从贴图键名匹配token), "value"(从贴图路径/名称匹配token)
BEST_MATCH_BASECOLOR_TOKENS = frozenset(["s","dye","b","bc","e","f","m","01","d","di","00","clr","eyelashes","clra","clrm","difuse",])  
 # 优先匹配的token列表（小写），例如["a", "albedo", "color"]   "9","6","3","12","3","6"

# 搜索配置
FUZZY_SEARCH_ENABLED = True  # 启用模糊搜索
MAX_SEARCH_DEPTH = 5  # 最大搜索深度 (此版本中未使用)
TEXTURE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.tga', '.bmp', '.exr', '.hdr']

# UV修复配置（从uvfixer.py集成）
UV_FIXER_ENABLED = False           # 启用UV修复
GUTTER = 0.002                     # 岛缩放时的边距
EPS = 1e-6                         # 浮点容差

# UV修复导出配置
UV_EXPORT_MODE = "dual"            # 导出模式: "auto"(自动判断), "dual"(双版本导出), "fixed_only"(仅导出修复版)
UVFIXED_SUBDIR = "uvfixed"         # UV修复版本的子目录名称

# RM贴图生成配置
ENABLE_RM_GENERATION = False        # 是否启用自动生成粗糙度和金属度贴图
RM_ROUGHNESS_MIN = 0.6             # 粗糙度最小值（防止过于光滑）
RM_METALLIC_MAX = 0.5              # 金属度最大值（防止过于金属化）
RM_SATURATION_THRESHOLD = 0.2      # 饱和度阈值（低于此值认为是金属）

# 金属度/粗糙度连接配置
METALLIC_ROUGHNESS_CONNECTION_MODE = "auto"  # 连接模式: "auto"(自动检测), "gb"(G→金属度,B→粗糙度), "bg"(B→金属度,G→粗糙度), "rg"(R→金属度,G→粗糙度), "rb"(R→金属度,B→粗糙度)

# 渲染配置
RENDER_PROGRESS_FILE = DST_ROOT / ".render_progress.json"

# 启用法线贴图DX转OpenGl
ENABLE_NORMAL_DX_TO_OPENGL = True
# 法线贴图分块处理配置（用于处理大尺寸贴图，避免内存溢出）
NORMAL_MAP_TILE_SIZE = 4096            # 分块大小（像素），影响内存使用和处理速度（优化后可增大）
NORMAL_MAP_MAX_SIZE = 4096             # 超过此尺寸的任意边将使用分块处理
# packed normal 的 B 通道金属度判定阈值，仅 ST 家族使用判定结果
NORMAL_B_METALLIC_MEAN_MAX = 0.6
NORMAL_B_METALLIC_LOWRATIO_MIN = 0.10
# ST 家族从法线 B 通道拆出的金属度会导成双兼容 MR 贴图：
# glTF/UE/Blender: G=Roughness, B=Metallic
# Unity Standard 兜底: R=Metallic, A=Smoothness
ST_PACKED_MR_ROUGHNESS = 0.55
ST_OBJ_REBUILT_ROUGHNESS = 0.82

# 启用添加并应用加权法线修改器
ENABLE_ADD_AND_APPLY_WEIGHTED_NORMAL_MODIFIER = True
# 使用 blender.exe 命令行导出GLB（而非脚本内直接导出）
USE_BLENDER_EXE_EXPORT = False          # 是否使用 blender.exe 导出（推荐：可解决分离通道问题）
BLENDER_EXE_PATH = r"E:\Software\Blender\blender.exe"  # Blender可执行文件路径

# RM混合贴图关键词统计（全局字典）
RM_KEYWORD_STATS: Dict[str, int] = {}  # 统计每个关键词的命中次数


def create_pbr_material_from_obj_name(glb_path: Path):
    """
    OBJ没有材质时，根据OBJ文件名创建PBR材质。
    这样后续可以继续用材质名去匹配JSON/贴图。
    """
    material_name = glb_path.stem

    mat = bpy.data.materials.get(material_name)
    if mat is None:
        mat = bpy.data.materials.new(material_name)
        mat.use_nodes = True

        nodes = mat.node_tree.nodes
        bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)

        if bsdf:
            bsdf.inputs["Base Color"].default_value = (0.8, 0.8, 0.8, 1.0)

            if "Metallic" in bsdf.inputs:
                bsdf.inputs["Metallic"].default_value = 0.0

            if "Roughness" in bsdf.inputs:
                bsdf.inputs["Roughness"].default_value = 0.5

    assigned_count = 0

    for obj in bpy.context.scene.objects:
        if obj.type == "MESH":
            if len(obj.material_slots) == 0:
                obj.data.materials.append(mat)
                assigned_count += 1

    log(f"✅ OBJ无材质，已根据文件名创建PBR材质: {material_name}，分配给 {assigned_count} 个Mesh", "INFO")

    return mat

# =========================================================
# NEW: File Indexing System (for performance)
# =========================================================
class FileIndexer:
    """
    Builds an in-memory index of files organized by folder (GLB folder).
    Each folder contains: glb_paths, json_paths, texture_paths.
    Supports saving/loading from JSON for caching.
    """
    def __init__(self, root_path: Path, texture_extensions: List[str], enable_folder_build: bool = False):
        self.root_path = root_path
        log(f"[DEBUG] self.root_path = {self.root_path}", "INFO")
        self.texture_extensions = [ext.lower() for ext in texture_extensions]
        self.enable_folder_build = enable_folder_build
        
        # 传统模式：全局索引
        self.json_index: Dict[str, Path] = {}
        self.texture_index: Dict[str, Path] = {}
        self._json_keys: List[str] = []
        self._texture_keys: List[str] = []
        
        # 单OBJ文件夹模式：按文件夹组织的索引
        # folder_index: Dict[folder_name, Dict[str, List[Path]]]
        # 每个文件夹包含: {'glb_paths': [], 'json_paths': [], 'texture_paths': []}
        self.folder_index: Dict[str, Dict[str, List[Path]]] = {}
        
        self.is_built = False

    def build_index(self):
        """Scans the root directory and builds the file index."""
        if self.enable_folder_build:
            self._build_folder_index()
        else:
            self._build_global_index()

    def _build_global_index(self):
        """构建传统模式的全局索引"""
        log("🔍 Building global file index... This may take a moment for large directories.")
        start_time = time.time()

        for file_path in self.root_path.rglob("*.*"):
            if file_path.is_file():
                file_stem_lower = file_path.stem.lower()
                file_suffix_lower = file_path.suffix.lower()

                if file_suffix_lower == '.json':
                    if file_stem_lower not in self.json_index:
                        self.json_index[file_stem_lower] = file_path
                elif file_suffix_lower in self.texture_extensions:
                    if file_stem_lower not in self.texture_index:
                        self.texture_index[file_stem_lower] = file_path
        
        self._json_keys = list(self.json_index.keys())
        self._texture_keys = list(self.texture_index.keys())
        self.is_built = True
        duration = time.time() - start_time
        log(f"✅ Global file index built in {duration:.2f}s. Indexed {len(self.json_index)} JSON files and {len(self.texture_index)} texture files.")

    def _build_folder_index(self):
        """构建单OBJ文件夹模式的索引（按文件夹组织）"""
        log("🔍 Building folder-based file index... This may take a moment for large directories.")
        start_time = time.time()
        log(f"root_path={self.root_path}","INFO")
        # 遍历root_path下的第一级子文件夹（OBJ文件夹）
        if not self.root_path.exists():
            log(f"❌ Root path does not exist: {self.root_path}", "ERROR")
            return
        
        folder_count = 0
        total_glbs = 0
        total_jsons = 0
        total_textures = 0
        
        for folder_path in self.root_path.rglob("*"):
            if not folder_path.is_dir():
                continue
            
            folder_name = folder_path.name

            # folder_name = file_indexer.get_folder_for_glb(self.glb_path, self.glb_root)
            log(f"-----------folder_name={folder_name}-----------------","INFO")
            # log(f"-----------self.glb_path={self.glb_path}-----------------","INFO")
            folder_data = {
                'glb_paths': [],
                'json_paths': [],
                'texture_paths': []
            }
            
            # 在该文件夹内搜索所有文件
            for file_path in folder_path.rglob("*.*"):
                if not file_path.is_file():
                    continue
                
                file_suffix_lower = file_path.suffix.lower()
                
                if file_suffix_lower == '.obj':
                    folder_data['glb_paths'].append(file_path)
                    total_glbs += 1
                elif file_suffix_lower == '.json':
                    folder_data['json_paths'].append(file_path)
                    total_jsons += 1
                elif file_suffix_lower in self.texture_extensions:
                    folder_data['texture_paths'].append(file_path)
                    total_textures += 1
            
            # 保存文件夹：传统模式下需要有GLB文件，分离模式下只要有JSON或贴图文件即可
            # 在分离模式下，OBJ文件在OBJ_ROOT下，所以folder_data['glb_paths']可能为空
            if folder_data['glb_paths'] or folder_data['json_paths'] or folder_data['texture_paths']:
                self.folder_index[folder_name] = folder_data
                folder_count += 1
        
        self.is_built = True
        duration = time.time() - start_time
        log(f"✅ Folder-based index built in {duration:.2f}s. Indexed {folder_count} folders, {total_glbs} GLB files, {total_jsons} JSON files, {total_textures} texture files.")

    def save_index(self, json_csv: Path, texture_csv: Path):
        """Save the indexes to files."""
        if self.enable_folder_build:
            # 单OBJ文件夹模式：保存为JSON
            index_json = json_csv.parent / "folder_index.json"
            index_json.parent.mkdir(parents=True, exist_ok=True)
            
            # 转换为可序列化的格式
            serializable_index = {}
            for folder_name, folder_data in self.folder_index.items():
                serializable_index[folder_name] = {
                    'glb_paths': [str(p) for p in folder_data['glb_paths']],
                    'json_paths': [str(p) for p in folder_data['json_paths']],
                    'texture_paths': [str(p) for p in folder_data['texture_paths']]
                }
            
            with open(index_json, 'w', encoding='utf-8') as f:
                json.dump(serializable_index, f, ensure_ascii=False, indent=2)
            
            log(f"✅ Saved folder index to {index_json}")
        else:
            # 传统模式：保存为CSV
            json_csv.parent.mkdir(parents=True, exist_ok=True)
            with open(json_csv, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['stem', 'path'])
                for stem, path in sorted(self.json_index.items()):
                    writer.writerow([stem, str(path)])

            with open(texture_csv, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['stem', 'path'])
                for stem, path in sorted(self.texture_index.items()):
                    writer.writerow([stem, str(path)])

            log(f"✅ Saved file indexes to {json_csv} and {texture_csv}")

    def load_index(self, json_csv: Path, texture_csv: Path) -> bool:
        """Load the indexes from files if they exist."""
        if self.enable_folder_build:
            # 单OBJ文件夹模式：从JSON加载
            index_json = json_csv.parent / "folder_index.json"
            if not index_json.exists():
                return False
            
            try:
                with open(index_json, 'r', encoding='utf-8') as f:
                    serializable_index = json.load(f)
                
                self.folder_index = {}
                for folder_name, folder_data in serializable_index.items():
                    self.folder_index[folder_name] = {
                        'glb_paths': [Path(p) for p in folder_data['glb_paths']],
                        'json_paths': [Path(p) for p in folder_data['json_paths']],
                        'texture_paths': [Path(p) for p in folder_data['texture_paths']]
                    }
                
                self.is_built = True
                total_glbs = sum(len(f['glb_paths']) for f in self.folder_index.values())
                total_jsons = sum(len(f['json_paths']) for f in self.folder_index.values())
                total_textures = sum(len(f['texture_paths']) for f in self.folder_index.values())
                log(f"✅ Loaded folder index from {index_json}. {len(self.folder_index)} folders, {total_glbs} GLBs, {total_jsons} JSONs, {total_textures} textures.")
                return True
            except Exception as e:
                log(f"❌ Failed to load folder index: {e}", "ERROR")
                return False
        else:
            # 传统模式：从CSV加载
            if not (json_csv.exists() and texture_csv.exists()):
                return False

            try:
                self.json_index = {}
                with open(json_csv, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    next(reader)  # Skip header
                    for stem, path_str in reader:
                        self.json_index[stem] = Path(path_str)

                self.texture_index = {}
                with open(texture_csv, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    next(reader)  # Skip header
                    for stem, path_str in reader:
                        self.texture_index[stem] = Path(path_str)

                self._json_keys = list(self.json_index.keys())
                self._texture_keys = list(self.texture_index.keys())
                self.is_built = True
                log(f"✅ Loaded file indexes from {json_csv} and {texture_csv}. Indexed {len(self.json_index)} JSON files and {len(self.texture_index)} texture files.")
                return True
            except Exception as e:
                log(f"❌ Failed to load file indexes: {e}", "ERROR")
                return False

    def get_folder_for_glb(self, glb_path: Path, glb_root: Optional[Path] = None) -> Optional[str]:
        """
        根据OBJ文件路径，找到其所在的文件夹名称
        
        参数:
            glb_path: OBJ文件路径
            glb_root: GLB根目录（分离模式下使用，用于判断是否在分离模式）
        
        返回:
            文件夹名称，如果未找到则返回None
        """
        if not self.enable_folder_build:
            return None
        
        # 分离模式下：根据GLB文件名（不含扩展名）匹配文件夹名
        if glb_root is not None:
            try:
                log(f"[DEBUG] glb_root = {glb_root}", "INFO")
                log(f"[DEBUG] glb_path = {glb_path}", "INFO")

                # 检查GLB文件是否在glb_root下
                glb_path.relative_to(glb_root)

                glb_stem = glb_path.stem
                log(f"[DEBUG] glb_stem = {glb_stem}", "INFO")

                for folder_name in self.folder_index.keys():
                    log(f"[DEBUG] checking folder_name = {folder_name}", "DEBUG")
                    if folder_name == glb_stem:
                        return folder_name
            except (ValueError, IndexError):
                pass
        
        # 传统模式：根据路径结构匹配
        try:
            relative_path = glb_path.relative_to(self.root_path)
            folder_name = relative_path.parts[0]  # 第一级子文件夹名称
            if folder_name in self.folder_index:
                return folder_name
        except (ValueError, IndexError):
            pass
        return None

    def find_json_in_folder(self, material_name: str, folder_name: str) -> Optional[Path]:
        """
        在指定文件夹内查找JSON文件
        
        参数:
            material_name: 材质名称
            folder_name: 文件夹名称
        
        返回:
            找到的JSON文件路径，未找到返回None
        """
        if not self.enable_folder_build:
            return None
        
        folder_data = self.folder_index.get(folder_name)
        if not folder_data:
            return None
        
        material_name_lower = material_name.lower()
        
        # 精确匹配
        for json_path in folder_data['json_paths']:
            if json_path.stem.lower() == material_name_lower:
                return json_path
        
        # 模糊匹配
        if FUZZY_SEARCH_ENABLED:
            for json_path in folder_data['json_paths']:
                if material_name_lower in json_path.stem.lower():
                    return json_path
        
        return None

    def find_texture_in_folder(self, texture_stem_name: str, folder_name: str) -> Optional[Path]:
        """
        在指定文件夹内查找贴图文件
        
        参数:
            texture_stem_name: 贴图文件名（不含扩展名）
            folder_name: 文件夹名称
        
        返回:
            找到的贴图文件路径，未找到返回None
        """
        if not self.enable_folder_build:
            return None
        
        folder_data = self.folder_index.get(folder_name)
        if not folder_data:
            return None
        
        texture_stem_lower = texture_stem_name.lower()
        
        # 精确匹配
        for texture_path in folder_data['texture_paths']:
            if texture_path.stem.lower() == texture_stem_lower:
                return texture_path
        
        # 模糊匹配
        if FUZZY_SEARCH_ENABLED:
            for texture_path in folder_data['texture_paths']:
                if texture_stem_lower in texture_path.stem.lower():
                    return texture_path
        
        return None

    def find_json(self, material_name: str) -> Optional[Path]:
        """Finds a material JSON file using the index (传统模式)."""
        if self.enable_folder_build:
            return None  # 单OBJ文件夹模式应使用find_json_in_folder
        
        material_name_lower = material_name.lower()
        
        path = self.json_index.get(material_name_lower)
        if path:
            return path

        if FUZZY_SEARCH_ENABLED:
            for key in self._json_keys:
                if material_name_lower in key:
                    return self.json_index[key]
        return None

def find_texture(
    self,
    texture_stem_name: str,
    obj_name: Optional[str] = None,
    material_name: Optional[str] = None,
) -> Optional[Path]:

    """
    使用贴图择优系统查找贴图
    """

    if self.enable_folder_build:
        return None

    texture_stem_lower = texture_stem_name.lower()

    candidates = []

    # =====================================================
    # 1. 精确匹配
    # =====================================================

    exact = self.texture_index.get(texture_stem_lower)

    if exact:
        candidates.append(exact)

    # =====================================================
    # 2. 模糊匹配
    # =====================================================

    if FUZZY_SEARCH_ENABLED:

        for key in self._texture_keys:

            if texture_stem_lower in key:

                path = self.texture_index[key]

                if path not in candidates:
                    candidates.append(path)

    if not candidates:
        return None

    # =====================================================
    # 3. 择优
    # =====================================================

    best_path = None
    best_score = -999999

    for candidate in candidates:

        score = score_texture_candidate(
            candidate,
            texture_stem_name,
            obj_name=obj_name,
            material_name=material_name,
        )

        log(
            f"[TextureScore] {candidate.name} -> {score}",
            "DEBUG"
        )

        if score > best_score:
            best_score = score
            best_path = candidate

    if best_path:
        log(
            f"✅ Texture Best Match: {best_path.name} (score={best_score})",
            "INFO"
        )

    return best_path

# Global file indexer instance
file_indexer: Optional[FileIndexer] = None


# =========================================================
# 日志和进度管理系统
# =========================================================
class ProcessLogger:
    """增强的日志记录器"""
    def __init__(self, log_file: Path):
        self.log_file = log_file
    
    def log(self, message: str, level: str = "INFO"):
        """记录日志"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] [{level}] {message}"
        print(log_entry)
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(log_entry + '\n')
        except Exception as e:
            print(f"❌ 日志写入失败: {e}")

logger = ProcessLogger(DETAILED_LOG_FILE)

def log(message: str, level: str = "INFO"):
    """全局日志函数"""
    logger.log(message, level)

# =========================================================
# 进度管理系统
# =========================================================
def load_progress() -> Dict[str, Any]:
    """加载处理进度"""
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log(f"加载进度文件失败: {e}", "WARNING")
    return {'processed': {}, 'stats': {'total_files': 0, 'processed_files': 0, 'success_count': 0, 'failed_count': 0}, 'start_time': time.time(), 'last_update': time.time()}

def save_progress(progress: Dict[str, Any]):
    """保存处理进度"""
    try:
        PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        progress['last_update'] = time.time()
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"保存进度文件失败: {e}", "ERROR")

# =========================================================
# 文件搜索和匹配系统
# =========================================================
def find_obj_files(obj_root: Path) -> List[Path]:
    """递归查找所有OBJ文件"""
    if not obj_root.exists():
        log(f"OBJ目录不存在: {obj_root}", "ERROR")
        return []
    return sorted(list(obj_root.rglob("*.obj")))

def find_material_json(material_name: str, material_root: Path, folder_name: Optional[str] = None) -> Optional[Path]:
    """
    [优化] 使用预构建的索引快速查找材质JSON文件
    
    参数:
        material_name: 材质名称
        material_root: 材质根目录（用于兼容旧代码）
        folder_name: 文件夹名称（单OBJ文件夹模式使用），如果为None则使用传统模式
    """
    # 单OBJ文件夹模式：使用文件夹索引
    if folder_name is not None and file_indexer and file_indexer.enable_folder_build:
        return file_indexer.find_json_in_folder(material_name, folder_name)
    
    # 传统模式：使用全局索引
    if file_indexer and not file_indexer.enable_folder_build:
        return file_indexer.find_json(material_name)
    
    # 回退到慢速搜索
    log("File indexer not available. Falling back to slow search.", "WARNING")
    json_path = material_root / f"{material_name}.json"
    if json_path.exists():
        return json_path
    if FUZZY_SEARCH_ENABLED:
        for json_file in material_root.rglob(f"*{material_name}*.json"):
            if material_name.lower() in json_file.stem.lower():
                return json_file
    return None

def convert_ue_path_to_file_path(ue_path: str) -> str:
    """转换UE路径为实际文件路径"""
    if ue_path.startswith("/Game/"):
        ue_path = ue_path[6:]
    
    path_parts = ue_path.split('/')
    if path_parts:
        filename = path_parts[-1]
        if '.' in filename:
            name_parts = filename.split('.')
            if len(name_parts) >= 2 and name_parts[-1] == name_parts[-2]:
                path_parts[-1] = '.'.join(name_parts[:-1])
                ue_path = '/'.join(path_parts)
    
    return f"{ue_path}.png"

def find_texture_file(texture_path: str, texture_root: Path, folder_name: Optional[str] = None) -> Optional[Path]:
    """
    [优化] 使用预构建的索引快速查找贴图文件
    
    参数:
        texture_path: 贴图路径（UE路径格式）
        texture_root: 贴图根目录（用于兼容旧代码）
        folder_name: 文件夹名称（单OBJ文件夹模式使用），如果为None则使用传统模式
    """
    converted_path_str = convert_ue_path_to_file_path(texture_path)
    texture_name_stem = Path(converted_path_str).stem
    
    # 单OBJ文件夹模式：使用文件夹索引
    if folder_name is not None and file_indexer and file_indexer.enable_folder_build:
        # 先尝试精确路径匹配
        folder_data = file_indexer.folder_index.get(folder_name)
        if folder_data:
            # 尝试直接路径匹配
            for texture_file in folder_data['texture_paths']:
                if texture_file.stem.lower() == texture_name_stem.lower():
                    return texture_file
            # 使用索引查找
            return file_indexer.find_texture_in_folder(texture_name_stem, folder_name)
    
    # 传统模式：先尝试直接路径匹配
    full_path = texture_root / converted_path_str
    if full_path.exists():
        return full_path
    
    # 传统模式：使用全局索引
    if file_indexer and not file_indexer.enable_folder_build:
        if FUZZY_SEARCH_ENABLED:
            return file_indexer.find_texture(
                texture_name_stem,
                obj_name=current_obj_name,
                material_name=current_material_name,
            )
    
    # 回退到慢速搜索
    if FUZZY_SEARCH_ENABLED:
        log("File indexer not available. Falling back to slow texture search.", "WARNING")
        for ext in TEXTURE_EXTENSIONS:
            for texture_file in texture_root.rglob(f"*{texture_name_stem}*{ext}"):
                if texture_name_stem.lower() in texture_file.stem.lower():
                    return texture_file
    return None

def split_texture_name_to_tokens(texture_name: str) -> set:
    """将贴图名称按分隔符分割为token集合"""
    texture_name_lower = texture_name.lower()
    tokens = set()
    current_texture = texture_name_lower
    for separator in TEXTURE_NAME_SEPARATORS:
        if separator in current_texture:
            parts = current_texture.split(separator)
            for part in parts:
                if part.strip():
                    tokens.add(part.strip())
            current_texture = separator.join(parts)
    tokens.add(texture_name_lower)
    return tokens

def score_texture_candidate(
    candidate_path: Path,
    target_name: str,
    obj_name: Optional[str] = None,
    material_name: Optional[str] = None,
) -> int:
    """
    给贴图候选打分
    分数越高越优先
    """

    score = 0

    candidate_stem = candidate_path.stem.lower()
    target_lower = target_name.lower()

    candidate_tokens = split_texture_name_to_tokens(candidate_stem)
    target_tokens = split_texture_name_to_tokens(target_lower)

    # =====================================================
    # 1. 完全同名（最高优先级）
    # =====================================================

    if candidate_stem == target_lower:
        score += 100000

    # =====================================================
    # 2. token匹配
    # =====================================================

    common_tokens = candidate_tokens & target_tokens

    score += len(common_tokens) * 500

    # =====================================================
    # 3. OBJ名命中
    # =====================================================

    if obj_name:
        obj_tokens = split_texture_name_to_tokens(obj_name.lower())

        obj_common = candidate_tokens & obj_tokens

        score += len(obj_common) * 300

    # =====================================================
    # 4. 材质名命中
    # =====================================================

    if material_name:
        mat_tokens = split_texture_name_to_tokens(material_name.lower())

        mat_common = candidate_tokens & mat_tokens

        score += len(mat_common) * 250

    # =====================================================
    # 5. 前缀匹配
    # =====================================================

    if candidate_stem.startswith(target_lower):
        score += 2000

    # =====================================================
    # 6. 包含关系
    # =====================================================

    if target_lower in candidate_stem:
        score += 1000

    # =====================================================
    # 7. 路径接近度（很重要）
    # =====================================================

    candidate_path_str = str(candidate_path).lower()

    if obj_name and obj_name.lower() in candidate_path_str:
        score += 800

    if material_name and material_name.lower() in candidate_path_str:
        score += 500

    # =====================================================
    # 8. 基础色优先防误匹配
    # =====================================================

    if any(k in candidate_tokens for k in BASECOLOR_KEYS):
        score += 200

    # =====================================================
    # 9. 黑名单降权
    # =====================================================

    bad_hit_count = sum(
        1 for k in BAD_KEYS
        if k in candidate_tokens
    )

    score -= bad_hit_count * 400

    # =====================================================
    # 10. normal / roughness / metallic 降权
    # =====================================================

    non_basecolor_hit = sum(
        1 for k in NON_BASECOLOR_KEYS
        if k in candidate_tokens
    )

    score -= non_basecolor_hit * 250

    # =====================================================
    # 11. 文件夹深度（越浅越好）
    # =====================================================

    try:
        depth = len(candidate_path.parts)
        score -= depth * 2
    except:
        pass

    return score

def classify_texture_type(texture_key: str, texture_value: str = "") -> Tuple[str, int, List[str]]:
    """
    根据贴图键名分类贴图类型（使用token精准匹配），支持优先token匹配基础色
    
    黑名单机制说明：
    - 黑名单使用 NON_BASECOLOR_KEYS（所有非基础色关键字），防止错误贴图被识别为基础色
    - 不影响其他类型贴图的正常识别（金属度、粗糙度、法线等）
    - 黑名单检查在基础色检查之前进行
    
    参数:
        texture_key: 贴图键名（来自JSON）
        texture_value: 贴图路径/名称（来自JSON）
    
    返回:
        元组 (贴图类型, 优先级, 命中的关键词列表): 
        - 贴图类型: "basecolor", "normal", "metallic", "roughness", "metallic_roughness", "roughness_specular_metallic", "flexible", "unknown"
        - 优先级: 数字越大优先级越高，优先token匹配返回100，正常匹配返回1
        - 命中的关键词列表: 匹配到的关键词列表（仅对metallic_roughness类型返回，其他类型返回空列表）
    """
    texture_tokens = split_texture_name_to_tokens(texture_key)
    log(f"贴图 '{texture_key}' 分割为tokens: {texture_tokens}", "DEBUG")

    # ========== 第一步：优先检查非基础色类型（不受黑名单影响）==========
    # 先检查特殊组合贴图
    if any(token in FLEXIBLE_KEYS for token in texture_tokens): 
        log(f"✅ 识别为flexible类型", "DEBUG")
        return ("flexible", 1, [])
    if any(token in ROUGHNESS_SPECULAR_METALLIC_KEYS for token in texture_tokens): 
        log(f"✅ 识别为roughness_specular_metallic类型", "DEBUG")
        return ("roughness_specular_metallic", 1, [])
    if any(token in METALLIC_KEYS for token in texture_tokens): 
        log(f"✅ 识别为metallic类型", "DEBUG")
        return ("metallic", 1, [])
    if any(token in ROUGHNESS_KEYS for token in texture_tokens): 
        log(f"✅ 识别为roughness类型", "DEBUG")
        return ("roughness", 1, [])
    # 对于metallic_roughness类型，返回匹配的关键词列表
    matched_mr_keywords = [t for t in texture_tokens if t in METALLIC_ROUGHNESS_KEYS]
    if matched_mr_keywords: 
        log(f"✅ 识别为metallic_roughness类型，匹配关键词: {matched_mr_keywords}", "DEBUG")
        return ("metallic_roughness", 1, matched_mr_keywords)
    if any(token in NORMAL_KEYS for token in texture_tokens): 
        log(f"✅ 识别为normal类型", "DEBUG")
        return ("normal", 1, [])

    # ========== 第二步：黑名单检查（使用所有非基础色关键字排除基础色）==========
    # 检查键名tokens中是否包含非基础色关键字
    key_blacklist_tokens = [t for t in texture_tokens if t in NON_BASECOLOR_KEYS]
    if key_blacklist_tokens:
        log(f"🚫 黑名单排除: 键名tokens中包含非基础色关键字 {key_blacklist_tokens}，排除为基础色", "INFO")
        return ("unknown", 0, [])
    
    # 检查值tokens中是否包含非基础色关键字
    if texture_value:
        converted_path = convert_ue_path_to_file_path(texture_value)
        value_stem = Path(converted_path).stem.lower()
        value_tokens = split_texture_name_to_tokens(value_stem)
        value_blacklist_tokens = [t for t in value_tokens if t in NON_BASECOLOR_KEYS]
        if value_blacklist_tokens:
            log(f"🚫 黑名单排除: 值tokens中包含非基础色关键字 {value_blacklist_tokens}，排除为基础色", "INFO")
            return ("unknown", 0, [])

    # ========== 第三步：优先基础色token匹配 ==========
    if BEST_MATCH_BASECOLOR and BEST_MATCH_BASECOLOR_TOKENS:
        check_tokens = None
        
        if BEST_MATCH_BASECOLOR_MODE == "key":
            # 从贴图键名匹配token
            check_tokens = texture_tokens
            log(f"优先基础色匹配[key模式]: 检查tokens {check_tokens}", "DEBUG")
            
        elif BEST_MATCH_BASECOLOR_MODE == "value" and texture_value:
            # 从贴图路径/名称匹配token
            converted_path = convert_ue_path_to_file_path(texture_value)
            value_stem = Path(converted_path).stem.lower()
            check_tokens = split_texture_name_to_tokens(value_stem)
            log(f"优先基础色匹配[value模式]: 值 '{value_stem}' 分割为tokens {check_tokens}", "DEBUG")
        
        # 检查是否包含优先token
        if check_tokens and any(token in BEST_MATCH_BASECOLOR_TOKENS for token in check_tokens):
            matched_tokens = [t for t in check_tokens if t in BEST_MATCH_BASECOLOR_TOKENS]
            log(f"✅ 优先基础色匹配成功: 匹配到token {matched_tokens}, 直接返回basecolor (优先级100)", "INFO")
            return ("basecolor", 100, [])  # 最高优先级

    # ========== 第四步：普通基础色匹配（不需要降权，黑名单已经排除了非基础色）==========
    if any(token in BASECOLOR_KEYS for token in texture_tokens):
        log(f"✅ 识别为basecolor类型", "DEBUG")
        return ("basecolor", 1, [])  # 普通基础色优先级为1
    
    return ("unknown", 0, [])

# =========================================================
# Blender材质处理系统
# =========================================================
def clear_color_attributes():
    """清除所有网格对象的颜色属性"""
    cleared_count = 0
    for obj in bpy.context.scene.objects:
        if obj.type == 'MESH' and obj.data and hasattr(obj.data, 'color_attributes'):
            color_attrs_to_remove = list(obj.data.color_attributes)
            for attr in color_attrs_to_remove:
                try:
                    obj.data.color_attributes.remove(attr)
                    cleared_count += 1
                except (ReferenceError, RuntimeError) as e:
                    log(f"清除颜色属性时出错: {e}", "WARNING")
    if cleared_count > 0:
        log(f"总共清除了 {cleared_count} 个颜色属性", "INFO")

def remove_color_attribute_nodes():
    """从所有材质中移除颜色属性节点"""
    removed_count = 0
    for material in bpy.data.materials:
        if material.use_nodes:
            nodes_to_remove = [n for n in material.node_tree.nodes if n.type == 'ATTRIBUTE' and 'color' in getattr(n, 'attribute_name', '').lower()]
            for node in nodes_to_remove:
                material.node_tree.nodes.remove(node)
                removed_count += 1
    if removed_count > 0:
        log(f"总共移除了 {removed_count} 个颜色属性节点", "INFO")

def cleanup_color_attributes():
    """清理颜色属性的完整流程"""
    log("🧹 开始清理颜色属性...", "INFO")
    try:
        clear_color_attributes()
        remove_color_attribute_nodes()
        log("✅ 颜色属性清理完成", "INFO")
    except Exception as e:
        log(f"⚠️ 颜色属性清理过程中出现错误: {e}", "WARNING")

def normalize_ue_texture_path(ue_path: str) -> str:
    """
    UE 贴图路径 → 文件 basename（小写）
    xxx/T_A.B → T_A
    """
    if not ue_path:
        return ""

    ue_path = ue_path.replace("\\", "/")
    last = ue_path.split("/")[-1]

    # 去掉 UE 的 .AssetName 后缀
    if "." in last:
        last = last.split(".")[0]

    return last.lower()

def normalize_material_search_name(material_name: str) -> str:
    """
    归一化材质名，用于无JSON时的贴图文件名匹配
    """
    name = material_name.lower().strip()
    name = re.sub(r"\.\d+$", "", name)  # Blender重名后缀，例如 .001

    prefixes = ["mi_", "m_", "mat_", "material_", "mtl_", "inst_", "master_"]
    for prefix in prefixes:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break

    return name.strip("_-. ")


def tokenize_name_for_matching(name: str) -> List[str]:
    """将名称拆分成更适合做匹配的token列表"""
    if not name:
        return []

    normalized = name.lower().strip()
    normalized = re.sub(r"\.\d+$", "", normalized)
    parts = re.split(r"[\s_\-./\\]+", normalized)

    ignored = {"m", "mi", "mat", "mtl", "material", "inst", "master", "tex", "texture"}
    tokens = []
    for part in parts:
        part = part.strip()
        if not part or part in ignored:
            continue
        if len(part) <= 1 and not part.isdigit():
            continue
        tokens.append(part)
    return tokens


def score_candidate_texture_for_material(texture_path: Path, material_name: str, glb_name: str = "", folder_name: str = "") -> int:
    """
    为无JSON模式下的候选贴图打分，分数越高越可能属于当前材质
    """
    stem = texture_path.stem.lower()
    stem_tokens = split_texture_name_to_tokens(stem)

    score = 0

    normalized_material_name = normalize_material_search_name(material_name)
    if normalized_material_name:
        if normalized_material_name == stem:
            score += 40
        elif normalized_material_name in stem:
            score += 20

    for token in tokenize_name_for_matching(material_name):
        if token in stem_tokens:
            score += 8
        elif token in stem:
            score += 4

    for token in tokenize_name_for_matching(glb_name):
        if token in stem_tokens:
            score += 3
        elif token in stem:
            score += 1

    for token in tokenize_name_for_matching(folder_name):
        if token in stem_tokens:
            score += 2

    bad_hint_words = {"noise", "lut", "ramp", "detailmask", "cloud", "dummy", "preview"}
    if any(word in stem for word in bad_hint_words):
        score -= 8

    return score


def collect_candidate_textures_for_material(material_name: str,
                                            texture_root: Path,
                                            folder_name: Optional[str] = None,
                                            glb_path: Optional[Path] = None) -> List[Path]:
    """
    无JSON时，为当前材质收集候选贴图：
    1. 优先当前OBJ文件夹内的贴图
    2. 优先文件名与材质名更接近的贴图
    3. 如果没有明显命中，则回退到当前文件夹全部贴图
    """
    glb_name = glb_path.stem if glb_path else ""
    folder_texture_paths: List[Path] = []

    if folder_name and file_indexer and file_indexer.enable_folder_build:
        folder_data = file_indexer.folder_index.get(folder_name)
        if folder_data:
            folder_texture_paths = list(folder_data.get('texture_paths', []))

    # 单OBJ文件夹模式：优先从当前文件夹的贴图中打分筛选
    if folder_texture_paths:
        scored = []
        for tex_path in folder_texture_paths:
            score = score_candidate_texture_for_material(tex_path, material_name, glb_name, folder_name or "")
            scored.append((score, tex_path))

        positive = [tex for score, tex in scored if score > 0]
        if positive:
            positive.sort(key=lambda p: (-score_candidate_texture_for_material(p, material_name, glb_name, folder_name or ""), p.name.lower()))
            log(f"无JSON匹配: 材质 {material_name} 在文件夹 {folder_name} 中找到 {len(positive)} 个高相关贴图候选", "INFO")
            return positive

        log(f"无JSON匹配: 材质 {material_name} 在文件夹 {folder_name} 中未找到高相关贴图，回退使用该文件夹全部贴图", "INFO")
        return sorted(folder_texture_paths, key=lambda p: p.name.lower())

    # 传统模式回退：全局按材质名检索
    candidates: List[Path] = []
    normalized_material_name = normalize_material_search_name(material_name)
    search_terms = [normalized_material_name] + tokenize_name_for_matching(material_name)
    seen = set()

    for term in search_terms:
        if not term:
            continue
        for ext in TEXTURE_EXTENSIONS:
            for tex_path in texture_root.rglob(f"*{term}*{ext}"):
                if tex_path.is_file() and tex_path not in seen:
                    candidates.append(tex_path)
                    seen.add(tex_path)

    candidates.sort(key=lambda p: (-score_candidate_texture_for_material(p, material_name, glb_name, folder_name or ""), p.name.lower()))
    return candidates


def build_texture_files_from_filenames(candidate_paths: List[Path]) -> Tuple[Dict[str, Path], Dict[str, str]]:
    """
    无JSON模式：直接用贴图文件名作为texture_key，并伪造textures映射
    返回 (texture_files, fake_textures)
    """
    texture_files: Dict[str, Path] = {}
    fake_textures: Dict[str, str] = {}

    for tex_path in candidate_paths:
        key = tex_path.stem
        if key not in texture_files:
            texture_files[key] = tex_path
            fake_textures[key] = tex_path.stem

    return texture_files, fake_textures


def detect_asset_family(material_name: str, texture_key: str = "", texture_path: Optional[Path] = None, model_name: str = "") -> str:
    """根据模型/材质/贴图命名识别项目特有的通道打包规则。"""
    parts = [material_name or "", texture_key or "", model_name or ""]
    if texture_path:
        parts.extend([texture_path.stem, texture_path.name])

    raw_blob = " ".join(parts)
    blob = raw_blob.lower()
    normalized = blob.replace("m_", "").replace("mi_", "").replace("t_", "")
    tokens = split_texture_name_to_tokens(normalized)

    if "星見雅" in raw_blob or "星见雅" in raw_blob or "xingjianya" in blob:
        return "xingjianya"

    names = []
    for value in parts:
        if not value:
            continue
        names.append(Path(str(value)).stem.lower())

    if any(name.startswith("st") for name in names) or any(token.startswith("st") for token in tokens):
        return "st"

    if any(name.startswith("sm_") or name.startswith("sm-") or name.startswith("sm") for name in names):
        return "sm"
    if "sm" in tokens:
        return "sm"

    return "default"


def is_aorm_texture(texture_key: str, texture_path: Optional[Path]) -> bool:
    name = " ".join([texture_key or "", texture_path.stem if texture_path else ""]).lower()
    tokens = split_texture_name_to_tokens(name)
    return any(token in {"aorm", "arom", "aoorm", "ao_rough_metal", "ao_roughness_metallic"} for token in tokens)


def get_socket(sockets, *names):
    for name in names:
        socket = sockets.get(name)
        if socket is not None:
            return socket
    for socket in sockets:
        if socket.name in names:
            return socket
    return sockets[0] if sockets else None


def get_channel_output(node, channel: str):
    aliases = {
        'R': ('R', 'Red'),
        'G': ('G', 'Green'),
        'B': ('B', 'Blue'),
        'A': ('A', 'Alpha'),
    }
    return get_socket(node.outputs, *aliases[channel])


def new_separate_rgb(nodes, links, tex_image):
    try:
        sep_rgb = nodes.new('ShaderNodeSeparateColor')
        if hasattr(sep_rgb, 'mode'):
            sep_rgb.mode = 'RGB'
        color_input = get_socket(sep_rgb.inputs, 'Color', 'Image')
    except Exception:
        sep_rgb = nodes.new('ShaderNodeSeparateRGB')
        color_input = get_socket(sep_rgb.inputs, 'Image', 'Color')
    links.new(tex_image.outputs['Color'], color_input)
    return sep_rgb


def _read_image_pixels_rgba(image) -> Tuple[Optional[np.ndarray], int, int]:
    width, height = image.size
    if width <= 0 or height <= 0:
        log(f"图像尺寸无效: {image.name} ({width}x{height})", "WARNING")
        return None, width, height

    pixels = np.array(image.pixels[:], dtype=np.float32)
    expected = width * height * 4
    if pixels.size != expected:
        log(f"图像像素数量异常: {image.name}, got={pixels.size}, expected={expected}", "WARNING")
        return None, width, height

    return pixels.reshape((height, width, 4)), width, height


def _pack_image_safely(image, reason: str):
    try:
        image.pack()
    except Exception as e:
        log(f"{reason}: 打包图像 {image.name} 失败: {e}", "WARNING")


def _analyze_normal_b_channel(image) -> Dict[str, Any]:
    pixel_array, width, height = _read_image_pixels_rgba(image)
    if pixel_array is None:
        return {"is_metallic": False, "mean_b": 1.0, "low_ratio": 0.0, "width": width, "height": height}

    b_channel = pixel_array[:, :, 2]
    mean_b = float(np.mean(b_channel))
    low_ratio = float(np.mean(b_channel < 0.5))
    is_metallic = (mean_b < NORMAL_B_METALLIC_MEAN_MAX) or (low_ratio > NORMAL_B_METALLIC_LOWRATIO_MIN)
    return {
        "is_metallic": bool(is_metallic),
        "mean_b": mean_b,
        "low_ratio": low_ratio,
        "width": width,
        "height": height,
    }


def _force_normal_b_to_one(image) -> bool:
    pixel_array, width, height = _read_image_pixels_rgba(image)
    if pixel_array is None:
        return False

    if width > NORMAL_MAP_MAX_SIZE or height > NORMAL_MAP_MAX_SIZE:
        log(f"packed normal B通道修正使用大图模式: {image.name} ({width}x{height})", "INFO")

    pixel_array[:, :, 2] = 1.0
    image.pixels[:] = pixel_array.flatten()
    image.update()
    _pack_image_safely(image, "packed normal B通道修正")
    return True


def _extract_b_as_st_packed_mr(normal_image, name: str):
    pixel_array, width, height = _read_image_pixels_rgba(normal_image)
    if pixel_array is None:
        return None

    b_channel = pixel_array[:, :, 2]
    packed_pixels = np.ones((height, width, 4), dtype=np.float32)
    packed_pixels[:, :, 0] = b_channel
    packed_pixels[:, :, 1] = ST_PACKED_MR_ROUGHNESS
    packed_pixels[:, :, 2] = b_channel
    packed_pixels[:, :, 3] = 1.0 - ST_PACKED_MR_ROUGHNESS

    packed_image = bpy.data.images.new(name=name, width=width, height=height, alpha=True, float_buffer=False)
    packed_image.colorspace_settings.name = 'Non-Color'
    packed_image.pixels[:] = packed_pixels.flatten()
    packed_image.update()
    _pack_image_safely(packed_image, "ST法线B通道打包MR")
    log(
        f"ST法线B通道已打包为glTF MR贴图: {packed_image.name} "
        f"(R=Metallic(Unity), G=Roughness {ST_PACKED_MR_ROUGHNESS:.2f}, "
        f"B=Metallic(glTF), A=Smoothness {1.0 - ST_PACKED_MR_ROUGHNESS:.2f}, {width}x{height})",
        "INFO",
    )
    return packed_image


def connect_sm_aorm_texture(nodes, links, tex_image, bsdf, texture_key: str) -> str:
    sep_rgb = new_separate_rgb(nodes, links, tex_image)
    links.new(get_channel_output(sep_rgb, 'G'), bsdf.inputs['Roughness'])
    links.new(get_channel_output(sep_rgb, 'B'), bsdf.inputs['Metallic'])
    log(f"SM AORM连接: G→粗糙度, B→金属度，Alpha按要求忽略", "DEBUG")
    return "G→Roughness, B→Metallic, Alpha ignored"


def connect_textures_to_material(material, texture_files: Dict[str, Path], material_name: str, textures: Dict[str, str], model_name: str = "", source_ext: str = "") -> Tuple[bool, bool]:
    """
    将贴图连接到指定材质，支持优先级机制
    
    返回:
        (是否成功连接, 是否连接了RM混合贴图)
    """
    if not material or not material.use_nodes: return False, False
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if not bsdf: return False, False
    is_obj_source = (source_ext or "").lower() == ".obj"

    # 先清除所有已有连接
    for input_name in ['Base Color', 'Normal', 'Metallic', 'Roughness']:
        if input_name in bsdf.inputs:
            for link in list(bsdf.inputs[input_name].links):
                links.remove(link)

    # 第一步：对所有贴图进行分类并记录优先级
    texture_classifications = {}
    for texture_key, texture_path in texture_files.items():
        ue_path = textures.get(texture_key, texture_path.stem if texture_path else "")
        texture_type, priority, matched_keywords = classify_texture_type(texture_key, ue_path)
        texture_classifications[texture_key] = {
            'path': texture_path,
            'type': texture_type,
            'priority': priority,
            'matched_keywords': matched_keywords
        }
    
    # 第一步.5：检查相同值不同键的去重问题
    # 如果多个键指向同一个贴图文件，应该保留优先级最高的有效键，排除其他键以避免重复连接
    # 注意：只有当所有键都无效（unknown）时才全部排除，否则保留有效键
    path_to_keys = {}
    for texture_key, info in texture_classifications.items():
        path_str = str(info['path'])
        if path_str not in path_to_keys:
            path_to_keys[path_str] = []
        path_to_keys[path_str].append(texture_key)
    
    # 对于指向同一文件的多个键，选择优先级最高的有效键，排除其他键
    for path_str, keys in path_to_keys.items():
        if len(keys) > 1:
            # 分离有效键和无效键
            valid_keys = [k for k in keys if texture_classifications[k]['type'] != 'unknown']
            invalid_keys = [k for k in keys if texture_classifications[k]['type'] == 'unknown']
            
            # 如果所有键都无效，全部保留（让后续逻辑处理）
            if not valid_keys:
                log(f"⚠️ 所有指向同一文件的键都无效: {keys} -> '{path_str}'，保留所有键让后续处理", "DEBUG")
                continue
            
            # 如果有有效键，选择优先级最高的有效键
            best_key = max(valid_keys, key=lambda k: texture_classifications[k]['priority'])
            
            # 排除其他键（包括其他有效键和所有无效键）
            excluded_keys = [k for k in keys if k != best_key]
            for key in excluded_keys:
                log(f"🚫 去重排除: 键 '{key}' 与最佳键 '{best_key}' 指向同一文件 '{path_str}'，排除该键", "DEBUG")
                texture_classifications[key]['type'] = 'unknown'
                texture_classifications[key]['priority'] = 0
    
    # 第二步：按类型分组，每组选择优先级最高的贴图
    type_to_best_texture = {}
    for texture_key, info in texture_classifications.items():
        tex_type = info['type']
        if tex_type == 'unknown':
            continue
        
        if tex_type not in type_to_best_texture or info['priority'] > type_to_best_texture[tex_type]['priority']:
            type_to_best_texture[tex_type] = {
                'key': texture_key,
                'path': info['path'],
                'priority': info['priority'],
                'matched_keywords': info['matched_keywords']
            }
    
    # 第三步：连接选中的贴图
    connected_types = []
    has_rm_connected = False  # 标记是否连接了RM混合贴图
    for tex_type, best_info in type_to_best_texture.items():
        texture_key = best_info['key']
        texture_path = best_info['path']
        priority = best_info['priority']
        matched_keywords = best_info.get('matched_keywords', [])
        
        try:
            tex_image = nodes.new('ShaderNodeTexImage')
            tex_image.image = bpy.data.images.load(str(texture_path))
            tex_image.image.pack()
            asset_family = detect_asset_family(material_name, texture_key, texture_path, model_name)

            if tex_type == "basecolor":
                tex_image.image.colorspace_settings.name = 'sRGB'
                links.new(tex_image.outputs['Color'], bsdf.inputs['Base Color'])
                priority_tag = f" [优先级{priority}]" if priority > 1 else ""
                connected_types.append(f"基础色({texture_key}){priority_tag}")
            elif tex_type == "normal":
                tex_image.image.colorspace_settings.name = 'Non-Color'
                b_analysis = _analyze_normal_b_channel(tex_image.image)
                log(
                    f"法线B通道分析: family={asset_family}, texture={texture_key}, "
                    f"mean_b={b_analysis['mean_b']:.4f}, low_ratio={b_analysis['low_ratio']:.4f}, "
                    f"is_metallic={b_analysis['is_metallic']} "
                    f"(thresholds: mean<{NORMAL_B_METALLIC_MEAN_MAX}, low_ratio>{NORMAL_B_METALLIC_LOWRATIO_MIN})",
                    "INFO"
                )

                normal_desc = f"法线({texture_key})"
                if asset_family == "st":
                    metallic_connected = False
                    roughness_connected = False
                    if is_obj_source:
                        if "Metallic" in bsdf.inputs and not bsdf.inputs["Metallic"].is_linked:
                            bsdf.inputs["Metallic"].default_value = 0.0
                        if "Roughness" in bsdf.inputs and not bsdf.inputs["Roughness"].is_linked:
                            bsdf.inputs["Roughness"].default_value = ST_OBJ_REBUILT_ROUGHNESS
                            material["st_obj_rebuilt_roughness"] = ST_OBJ_REBUILT_ROUGHNESS
                        log(
                            f"ST OBJ重建材质: 不再把法线B通道当Metallic；"
                            f"Metallic=0, Roughness={ST_OBJ_REBUILT_ROUGHNESS:.2f}",
                            "INFO",
                        )
                    elif b_analysis["is_metallic"]:
                        if "Metallic" in bsdf.inputs and not bsdf.inputs["Metallic"].is_linked:
                            packed_mr_image = _extract_b_as_st_packed_mr(tex_image.image, f"{texture_key}_PackedMR")
                            if packed_mr_image:
                                packed_mr_tex = nodes.new('ShaderNodeTexImage')
                                packed_mr_tex.image = packed_mr_image
                                packed_mr_tex.image.colorspace_settings.name = 'Non-Color'
                                sep_mr = new_separate_rgb(nodes, links, packed_mr_tex)
                                links.new(get_channel_output(sep_mr, 'B'), bsdf.inputs['Metallic'])
                                metallic_connected = True
                                if "Roughness" in bsdf.inputs and not bsdf.inputs["Roughness"].is_linked:
                                    links.new(get_channel_output(sep_mr, 'G'), bsdf.inputs['Roughness'])
                                    roughness_connected = True
                        elif "Metallic" not in bsdf.inputs:
                            log(f"ST法线B通道判定为金属度，但材质 {material_name} 没有 Metallic 输入", "WARNING")
                        else:
                            log(f"ST法线B通道判定为金属度，但材质 {material_name} 的 Metallic 已有连接，跳过连接", "WARNING")

                    if _force_normal_b_to_one(tex_image.image):
                        log(f"ST packed normal标准化: {texture_key} 的B通道已设为1.0", "INFO")
                    normal_desc = (
                        f"ST打包法线({texture_key}: PackedMR G→Roughness/{ST_PACKED_MR_ROUGHNESS:.2f}, B→Metallic, 法线B设1.0)"
                        if metallic_connected
                        else (
                            f"ST OBJ法线({texture_key}: 不连Metallic, Roughness={ST_OBJ_REBUILT_ROUGHNESS:.2f}, B设1.0后Color→Normal)"
                            if is_obj_source
                            else f"ST打包法线({texture_key}: B设1.0后Color→Normal)"
                        )
                    )
                    if metallic_connected and not roughness_connected:
                        log(f"ST PackedMR 已连接 Metallic；Roughness 已有连接或不存在，未覆盖粗糙度", "INFO")

                elif asset_family == "xingjianya":
                    if _force_normal_b_to_one(tex_image.image):
                        log(f"星见雅packed normal标准化: {texture_key} 的B通道按漫反射偏移丢弃并设为1.0", "INFO")
                    normal_desc = f"星见雅打包法线({texture_key}: B丢弃并设1.0后Color→Normal)"

                normal_map = nodes.new('ShaderNodeNormalMap')
                links.new(tex_image.outputs['Color'], normal_map.inputs['Color'])
                links.new(normal_map.outputs['Normal'], bsdf.inputs['Normal'])
                connected_types.append(normal_desc)
            elif tex_type == "metallic_roughness":
                tex_image.image.colorspace_settings.name = 'Non-Color'
                if asset_family == "sm" and is_aorm_texture(texture_key, texture_path):
                    channel_desc = connect_sm_aorm_texture(nodes, links, tex_image, bsdf, texture_key)
                    connected_types.append(f"SM AORM({texture_key}: {channel_desc})")
                else:
                    sep_rgb = nodes.new('ShaderNodeSeparateRGB')
                    links.new(tex_image.outputs['Color'], sep_rgb.inputs['Image'])
                    channel_desc = []
                    if not bsdf.inputs['Metallic'].is_linked:
                        links.new(sep_rgb.outputs['B'], bsdf.inputs['Metallic'])
                        channel_desc.append("B→金属度")
                    else:
                        log(f"跳过MR金属度连接: 材质 {material_name} 的 Metallic 已有连接", "DEBUG")
                    if not bsdf.inputs['Roughness'].is_linked:
                        links.new(sep_rgb.outputs['G'], bsdf.inputs['Roughness'])
                        channel_desc.append("G→粗糙度")
                    else:
                        log(f"跳过MR粗糙度连接: 材质 {material_name} 的 Roughness 已有连接", "DEBUG")
                    log(f"使用默认连接: {', '.join(channel_desc) if channel_desc else '无新增连接'}", "DEBUG")
                    connected_types.append(f"金属/粗糙({texture_key}: {', '.join(channel_desc) if channel_desc else '已跳过'})")
                has_rm_connected = True  # 标记已连接RM混合贴图
                # 记录关键词到全局统计字典（只记录metallic_roughness类型，不包括roughness_specular_metallic）
                for keyword in matched_keywords:
                    if keyword not in RM_KEYWORD_STATS:
                        RM_KEYWORD_STATS[keyword] = 0
                    RM_KEYWORD_STATS[keyword] += 1
                    log(f"📊 RM关键词统计: {keyword} (当前计数: {RM_KEYWORD_STATS[keyword]})", "DEBUG")
            elif tex_type == "flexible":
                # 万能匹配：根据配置将RGB通道连接到指定的BSDF输入
                tex_image.image.colorspace_settings.name = 'Non-Color'
                sep_rgb = nodes.new('ShaderNodeSeparateRGB')
                links.new(tex_image.outputs['Color'], sep_rgb.inputs['Image'])
                
                connected_channels = []
                
                # R通道连接
                if FLEXIBLE_OUTPUT_R and FLEXIBLE_OUTPUT_R in bsdf.inputs:
                    links.new(sep_rgb.outputs['R'], bsdf.inputs[FLEXIBLE_OUTPUT_R])
                    connected_channels.append(f"R→{FLEXIBLE_OUTPUT_R}")
                elif FLEXIBLE_OUTPUT_R:
                    log(f"警告: BSDF没有'{FLEXIBLE_OUTPUT_R}'输入，跳过R通道连接", "WARNING")
                
                # G通道连接
                if FLEXIBLE_OUTPUT_G and FLEXIBLE_OUTPUT_G in bsdf.inputs:
                    links.new(sep_rgb.outputs['G'], bsdf.inputs[FLEXIBLE_OUTPUT_G])
                    connected_channels.append(f"G→{FLEXIBLE_OUTPUT_G}")
                elif FLEXIBLE_OUTPUT_G:
                    log(f"警告: BSDF没有'{FLEXIBLE_OUTPUT_G}'输入，跳过G通道连接", "WARNING")
                
                # B通道连接
                if FLEXIBLE_OUTPUT_B and FLEXIBLE_OUTPUT_B in bsdf.inputs:
                    links.new(sep_rgb.outputs['B'], bsdf.inputs[FLEXIBLE_OUTPUT_B])
                    connected_channels.append(f"B→{FLEXIBLE_OUTPUT_B}")
                elif FLEXIBLE_OUTPUT_B:
                    log(f"警告: BSDF没有'{FLEXIBLE_OUTPUT_B}'输入，跳过B通道连接", "WARNING")
                
                channel_desc = ", ".join(connected_channels) if connected_channels else "无有效通道"
                log(f"万能匹配连接: {channel_desc}", "DEBUG")
                connected_types.append(f"万能匹配({texture_key}: {channel_desc})")
                
            elif tex_type == "roughness_specular_metallic":
                tex_image.image.colorspace_settings.name = 'Non-Color'
                sep_rgb = nodes.new('ShaderNodeSeparateRGB')
                links.new(tex_image.outputs['Color'], sep_rgb.inputs['Image'])
                rsm_channels = []
                if not bsdf.inputs['Roughness'].is_linked:
                    links.new(sep_rgb.outputs['R'], bsdf.inputs['Roughness'])
                    rsm_channels.append("R→粗糙度")
                else:
                    log(f"跳过RSM粗糙度连接: 材质 {material_name} 的 Roughness 已有连接", "DEBUG")
                # 检查是否有Specular IOR Level输入（Blender 4.0+）
                if 'Specular IOR Level' in bsdf.inputs:
                    links.new(sep_rgb.outputs['G'], bsdf.inputs['Specular IOR Level'])
                    rsm_channels.append("G→Specular IOR Level")
                else:
                    # 旧版本Blender使用Specular输入
                    if 'Specular' in bsdf.inputs:
                        links.new(sep_rgb.outputs['G'], bsdf.inputs['Specular'])
                        rsm_channels.append("G→Specular")
                    else:
                        log(f"RSM贴图连接: 无Specular输入，跳过G通道", "DEBUG")
                if not bsdf.inputs['Metallic'].is_linked:
                    links.new(sep_rgb.outputs['B'], bsdf.inputs['Metallic'])
                    rsm_channels.append("B→金属度")
                else:
                    log(f"跳过RSM金属度连接: 材质 {material_name} 的 Metallic 已有连接", "DEBUG")
                channel_desc = ", ".join(rsm_channels) if rsm_channels else "已跳过"
                log(f"RSM贴图连接: {channel_desc}", "DEBUG")
                connected_types.append(f"粗糙/高光/金属({texture_key}: {channel_desc})")
                has_rm_connected = True  # 标记已连接RM混合贴图（RSM也是RM混合贴图的一种）
            elif tex_type in ("metallic", "roughness"):
                tex_image.image.colorspace_settings.name = 'Non-Color'
                target_socket = 'Metallic' if tex_type == "metallic" else 'Roughness'
                if not bsdf.inputs[target_socket].is_linked:
                    links.new(tex_image.outputs['Color'], bsdf.inputs[target_socket])
                    connected_types.append(f"{target_socket}({texture_key})")
                else:
                    log(f"跳过{target_socket}连接: 材质 {material_name} 的 {target_socket} 已有连接", "DEBUG")
        except Exception as e:
            log(f"材质 {material_name} 贴图 {texture_key} 连接失败: {e}", "WARNING")
    
    if connected_types:
        log(f"材质 {material_name} 成功连接: {', '.join(connected_types)}", "INFO")
    return len(connected_types) > 0, has_rm_connected

def set_default_material_values():
    """为所有材质设置默认的金属度/粗糙度值"""
    for mat in bpy.data.materials:
        if not mat.use_nodes: continue
        bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if not bsdf: continue
        if not bsdf.inputs['Metallic'].is_linked: bsdf.inputs['Metallic'].default_value = 0.0
        if not bsdf.inputs['Roughness'].is_linked:
            roughness = mat.get("st_obj_rebuilt_roughness", 0.5)
            bsdf.inputs['Roughness'].default_value = float(roughness)

# =========================================================
# 法线贴图DX转OpenGL系统
# =========================================================
def find_image_texture_from_normal_input(normal_input, visited_nodes: Optional[set] = None):
    """
    向上递归遍历法线输入连接，查找图像纹理节点
    
    参数:
        normal_input: 法线输入socket
        visited_nodes: 已访问的节点集合，防止循环引用
    
    返回:
        找到的图像纹理节点，未找到返回None
    """
    if visited_nodes is None:
        visited_nodes = set()
    
    if not normal_input or not normal_input.is_linked:
        return None
    
    # 遍历所有连接到法线输入的节点
    for link in normal_input.links:
        source_node = link.from_node
        
        # 防止循环引用
        if source_node in visited_nodes:
            continue
        visited_nodes.add(source_node)
        
        # 如果直接找到图像纹理节点
        if source_node.type == 'TEX_IMAGE' and source_node.image:
            log(f"找到图像纹理节点: {source_node.name}, 图像: {source_node.image.name}", "DEBUG")
            return source_node
        
        # 递归检查源节点的所有输入
        for input_socket in source_node.inputs:
            if input_socket.is_linked:
                result = find_image_texture_from_normal_input(input_socket, visited_nodes)
                if result:
                    return result
    
    return None

def convert_normal_map_dx_to_opengl(image_data: np.ndarray, inplace: bool = True) -> np.ndarray:
    """
    将法线贴图从DirectX空间转换为OpenGL空间
    主要是反转G通道（Y轴）
    
    参数:
        image_data: 图像数据numpy数组，形状为(height, width, channels)
        inplace: 是否原地修改数组（True=直接修改，False=创建副本）
    
    返回:
        转换后的图像数据
    """
    try:
        # 根据参数决定是否创建副本
        converted_data = image_data if inplace else image_data.copy()
        
        # 反转G通道（索引1）
        if converted_data.shape[2] >= 2:  # 确保有G通道
            if converted_data.dtype == np.uint8 or converted_data.max() > 1.0:
                # 8位/0..255 数据
                converted_data[:, :, 1] = 255 - converted_data[:, :, 1]
            else:
                # 浮点/0..1 数据
                converted_data[:, :, 1] = 1.0 - converted_data[:, :, 1]
        else:
            log("图像通道数不足，无法进行G通道反转", "WARNING")
        
        return converted_data
    except Exception as e:
        log(f"法线贴图空间转换失败: {e}", "ERROR")
        return image_data

def process_normal_texture_dx_to_opengl(texture_node) -> bool:
    """
    处理法线贴图，进行DirectX到OpenGL空间转换（原地修改）
    支持分块处理大尺寸图像以避免内存溢出
    
    参数:
        texture_node: 图像纹理节点
    
    返回:
        是否成功处理
    """
    try:
        original_image = texture_node.image
        if not original_image:
            log("图像纹理节点没有图像数据", "WARNING")
            return False
        
        width, height = original_image.size
        
        if width == 0 or height == 0:
            log("图像尺寸为零，无法处理", "WARNING")
            return False
        
        log(f"开始处理法线贴图: {original_image.name}, 尺寸: {width}x{height}", "INFO")
        
        # 判断是否需要使用分块处理
        use_tiled_processing = width > NORMAL_MAP_MAX_SIZE or height > NORMAL_MAP_MAX_SIZE
        
        if use_tiled_processing:
            log(f"使用分块处理模式 (块大小: {NORMAL_MAP_TILE_SIZE}x{NORMAL_MAP_TILE_SIZE})", "INFO")
            return _process_normal_texture_tiled(original_image, width, height)
        else:
            log(f"使用直接处理模式", "DEBUG")
            return _process_normal_texture_direct(original_image, width, height)
        
    except Exception as e:
        log(f"处理法线贴图时出错: {e}", "ERROR")
        import traceback
        log(f"错误详情: {traceback.format_exc()}", "ERROR")
        return False

def _process_normal_texture_direct(image, width: int, height: int) -> bool:
    """
    直接处理法线贴图（适用于小尺寸图像）- 优化版本
    
    优化要点：
    - 使用numpy数组批量操作，避免转换为Python列表
    - 减少内存拷贝
    
    参数:
        image: Blender图像对象
        width, height: 图像尺寸
    
    返回:
        是否成功处理
    """
    try:
        import time
        start_time = time.time()
        
        # 直接获取numpy数组（避免创建Python列表）
        pixel_array = np.array(image.pixels[:], dtype=np.float32).reshape((height, width, 4))
        
        # 转换为0-255范围的RGB数组
        rgb_array = (pixel_array[:, :, :3] * 255).astype(np.uint8)

        # Blender image.pixels 按自下而上行序（左下为原点）
        # 需要垂直翻转以对齐标准图像行序
        rgb_array = np.flipud(rgb_array)
        
        # 执行DirectX到OpenGL转换（原地修改）
        convert_normal_map_dx_to_opengl(rgb_array, inplace=True)
        
        # 转换回0-1范围的浮点数据
        converted_float = rgb_array.astype(np.float32) / 255.0
        
        # 垂直翻转回Blender坐标系
        converted_float = np.flipud(converted_float)
        
        # 更新原数组的RGB通道
        pixel_array[:, :, :3] = converted_float
        
        # 批量写回图像
        image.pixels[:] = pixel_array.flatten()
        image.update()
        
        elapsed = time.time() - start_time
        log(f"✅ 法线贴图转换完成: {image.name} (DirectX → OpenGL, 耗时: {elapsed:.2f}秒)", "INFO")
        return True
        
    except Exception as e:
        log(f"直接处理法线贴图时出错: {e}", "ERROR")
        return False

def _process_normal_texture_tiled(image, width: int, height: int) -> bool:
    """
    分块处理法线贴图（适用于大尺寸图像）- 优化版本
    
    优化要点：
    - 使用numpy数组批量读取和写入，避免逐像素操作
    - 减少中间转换和内存拷贝
    - 优化坐标系转换逻辑
    
    参数:
        image: Blender图像对象
        width, height: 图像尺寸
    
    返回:
        是否成功处理
    """
    try:
        import time
        tile_size = NORMAL_MAP_TILE_SIZE
        
        # 计算需要处理的块数
        tiles_x = (width + tile_size - 1) // tile_size
        tiles_y = (height + tile_size - 1) // tile_size
        total_tiles = tiles_x * tiles_y
        
        log(f"图像将被分成 {tiles_x}x{tiles_y} = {total_tiles} 个块进行处理 (块大小: {tile_size})", "INFO")
        
        # 一次性读取所有像素到numpy数组（这比逐块读取更快）
        start_time = time.time()
        all_pixels = np.array(image.pixels[:], dtype=np.float32)
        pixel_array = all_pixels.reshape((height, width, 4))
        read_time = time.time() - start_time
        log(f"读取完整图像耗时: {read_time:.2f}秒", "DEBUG")
        
        # 逐块处理
        processed_tiles = 0
        process_start = time.time()
        
        for tile_y in range(tiles_y):
            for tile_x in range(tiles_x):
                # 计算当前块的边界
                start_x = tile_x * tile_size
                start_y = tile_y * tile_size
                end_x = min(start_x + tile_size, width)
                end_y = min(start_y + tile_size, height)
                
                # 计算在Blender坐标系中的位置（Y轴翻转）
                blender_start_y = height - end_y
                blender_end_y = height - start_y
                
                # 使用numpy切片直接提取块（无需逐像素读取）
                tile_rgba = pixel_array[blender_start_y:blender_end_y, start_x:end_x, :]
                
                # 转换为0-255范围的RGB数组
                rgb_tile = (tile_rgba[:, :, :3] * 255).astype(np.uint8)
                
                # 垂直翻转（因为我们从Blender坐标系读取）
                rgb_tile = np.flipud(rgb_tile)
                
                # 执行DirectX到OpenGL转换（原地修改）
                convert_normal_map_dx_to_opengl(rgb_tile, inplace=True)
                
                # 转换回0-1范围
                converted_tile = rgb_tile.astype(np.float32) / 255.0
                
                # 垂直翻转回Blender坐标系
                converted_tile = np.flipud(converted_tile)
                
                # 更新原数组的RGB通道（保持Alpha通道不变）
                pixel_array[blender_start_y:blender_end_y, start_x:end_x, :3] = converted_tile
                
                processed_tiles += 1
                if processed_tiles % 10 == 0 or processed_tiles == total_tiles:
                    elapsed = time.time() - process_start
                    avg_time_per_tile = elapsed / processed_tiles
                    eta = avg_time_per_tile * (total_tiles - processed_tiles)
                    log(f"处理进度: {processed_tiles}/{total_tiles} 块 ({processed_tiles*100//total_tiles}%), 预计剩余: {eta:.1f}秒", "INFO")
        
        process_time = time.time() - process_start
        log(f"处理所有块耗时: {process_time:.2f}秒", "DEBUG")
        
        # 批量写回图像（比逐像素写入快得多）
        write_start = time.time()
        image.pixels[:] = pixel_array.flatten()
        write_time = time.time() - write_start
        log(f"写回图像耗时: {write_time:.2f}秒", "DEBUG")
        
        # 更新图像
        image.update()
        
        total_time = time.time() - start_time
        log(f"✅ 法线贴图转换完成: {image.name} (DirectX → OpenGL, 分块处理, 总耗时: {total_time:.2f}秒)", "INFO")
        return True
        
    except Exception as e:
        log(f"分块处理法线贴图时出错: {e}", "ERROR")
        import traceback
        log(f"错误详情: {traceback.format_exc()}", "ERROR")
        return False

def convert_all_normal_maps_dx_to_opengl() -> int:
    """
    转换场景中所有材质的法线贴图从DirectX到OpenGL格式
    
    返回:
        转换的法线贴图数量
    """
    if not ENABLE_NORMAL_DX_TO_OPENGL:
        log("⏭️ 法线贴图DX转OpenGL已禁用", "DEBUG")
        return 0
    
    log("🔄 开始检查并转换法线贴图空间 (DirectX → OpenGL)", "INFO")
    converted_count = 0
    
    for mat in bpy.data.materials:
        if not mat.use_nodes:
            continue
        
        # 查找Principled BSDF节点
        bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if not bsdf:
            continue
        
        # 检查法线输入
        normal_input = bsdf.inputs.get("Normal")
        if not normal_input or not normal_input.is_linked:
            continue
        
        log(f"检查材质: {mat.name} 的法线输入", "DEBUG")
        
        # 向上递归查找图像纹理节点
        texture_node = find_image_texture_from_normal_input(normal_input)
        if not texture_node:
            log(f"材质 {mat.name} 的法线连接中没有找到图像纹理", "DEBUG")
            continue
        
        log(f"找到法线贴图: {texture_node.image.name}", "INFO")
        
        # 处理法线贴图（DirectX → OpenGL）
        if process_normal_texture_dx_to_opengl(texture_node):
            converted_count += 1
    
    if converted_count > 0:
        log(f"✅ 法线贴图转换完成，共处理了 {converted_count} 个法线贴图", "INFO")
    else:
        log("✅ 没有需要转换的法线贴图", "INFO")
    
    return converted_count

# =========================================================
# 加权法线修改器系统
# =========================================================
def add_and_apply_weighted_normal_modifier() -> int:
    """
    为场景中所有网格对象添加并应用加权法线修改器
    
    返回:
        处理的网格对象数量
    """
    if not ENABLE_ADD_AND_APPLY_WEIGHTED_NORMAL_MODIFIER:
        log("⏭️ 加权法线修改器已禁用", "DEBUG")
        return 0
    
    log("🔧 开始为网格对象添加并应用加权法线修改器...", "INFO")
    processed_count = 0
    
    # 获取所有网格对象
    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    
    if not mesh_objects:
        log("✅ 场景中没有网格对象，跳过加权法线修改器处理", "INFO")
        return 0
    
    # 保存当前选中状态
    previous_selection = list(bpy.context.selected_objects)
    previous_active = bpy.context.active_object
    
    try:
        for obj in mesh_objects:
            try:
                # 确保对象可见且可编辑
                if obj.hide_viewport or obj.hide_get():
                    log(f"跳过隐藏对象: {obj.name}", "DEBUG")
                    continue
                
                # 选中当前对象
                bpy.ops.object.select_all(action='DESELECT')
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj
                
                # 确保对象处于对象模式
                if bpy.context.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
                
                # 检查是否已有加权法线修改器
                existing_modifier = None
                for mod in obj.modifiers:
                    if mod.type == 'WEIGHTED_NORMAL':
                        existing_modifier = mod
                        break
                
                if existing_modifier:
                    # 如果已存在，先移除旧的
                    log(f"对象 {obj.name} 已存在加权法线修改器，先移除旧的", "DEBUG")
                    obj.modifiers.remove(existing_modifier)
                
                # 添加加权法线修改器
                modifier = obj.modifiers.new(name="WeightedNormal", type='WEIGHTED_NORMAL')
                
                # 设置修改器参数（使用默认值，可根据需要调整）
                # modifier.keep_sharp = True  # 保持锐边
                # modifier.weight = 50  # 权重（0-100）
                # modifier.threshold = 0.01  # 阈值
                
                log(f"为对象 {obj.name} 添加加权法线修改器", "DEBUG")
                
                # 应用修改器
                # 注意：应用修改器需要对象处于对象模式且被选中
                bpy.ops.object.modifier_apply(modifier=modifier.name)
                
                log(f"✅ 成功为对象 {obj.name} 应用加权法线修改器", "INFO")
                processed_count += 1
                
            except Exception as e:
                log(f"⚠️ 处理对象 {obj.name} 的加权法线修改器时出错: {e}", "WARNING")
                continue
        
        # 恢复之前的选中状态
        bpy.ops.object.select_all(action='DESELECT')
        for obj in previous_selection:
            obj.select_set(True)
        if previous_active:
            bpy.context.view_layer.objects.active = previous_active
        
        if processed_count > 0:
            log(f"✅ 加权法线修改器处理完成，共处理了 {processed_count} 个网格对象", "INFO")
        else:
            log("✅ 没有需要处理加权法线修改器的网格对象", "INFO")
        
    except Exception as e:
        log(f"❌ 加权法线修改器处理过程中出现错误: {e}", "ERROR")
        # 尝试恢复选中状态
        try:
            bpy.ops.object.select_all(action='DESELECT')
            for obj in previous_selection:
                obj.select_set(True)
            if previous_active:
                bpy.context.view_layer.objects.active = previous_active
        except:
            pass
    
    return processed_count

# =========================================================
# UV修复系统（从uvfixer.py集成）
# =========================================================
class DisjointSet:
    def __init__(self, n: int):
        self.p = list(range(n))
        self.r = [0] * n
    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x
    def union(self, a: int, b: int):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.r[ra] < self.r[rb]:
            self.p[ra] = rb
        elif self.r[ra] > self.r[rb]:
            self.p[rb] = ra
        else:
            self.p[rb] = ra
            self.r[ra] += 1

def uv_eq(a, b, eps=EPS):
    return abs(a.x - b.x) <= eps and abs(a.y - b.y) <= eps

def collect_uv_islands(bm: bmesh.types.BMesh, uv_layer) -> list:
    faces = [f for f in bm.faces if not f.hide]
    if not faces:
        return []
    idx = {f.index: i for i, f in enumerate(faces)}
    dsu = DisjointSet(len(faces))

    def edge_uv_pairs(face, edge):
        pairs = []
        for lp in face.loops:
            nxt = lp.link_loop_next
            if lp.vert in edge.verts and nxt.vert in edge.verts:
                pairs.append((lp, nxt))
        return pairs

    for f in faces:
        for e in f.edges:
            for of in e.link_faces:
                if of is f or of not in faces:
                    continue
                ok = False
                for a1, a2 in edge_uv_pairs(f, e):
                    for b1, b2 in edge_uv_pairs(of, e):
                        if uv_eq(a1[uv_layer].uv, b2[uv_layer].uv) and uv_eq(a2[uv_layer].uv, b1[uv_layer].uv):
                            ok = True
                            break
                    if ok:
                        break
                if ok:
                    dsu.union(idx[f.index], idx[of.index])

    groups = {}
    for f in faces:
        root = dsu.find(idx[f.index])
        groups.setdefault(root, []).append(f)
    return list(groups.values())

def bbox_island(island_faces, uv_layer):
    umin = vmin = 1e18
    umax = vmax = -1e18
    for f in island_faces:
        for lp in f.loops:
            uv = lp[uv_layer].uv
            if not (isfinite(uv.x) and isfinite(uv.y)):
                continue
            umin = min(umin, uv.x); vmin = min(vmin, uv.y)
            umax = max(umax, uv.x); vmax = max(vmax, uv.y)
    return umin, vmin, umax, vmax, max(0.0, umax - umin), max(0.0, vmax - vmin)

def translate_island(island_faces, uv_layer, du, dv):
    if abs(du) <= EPS and abs(dv) <= EPS:
        return
    for f in island_faces:
        for lp in f.loops:
            uv = lp[uv_layer].uv
            uv.x += du; uv.y += dv

def scale_translate_island_to_unit(island_faces, uv_layer, gutter=GUTTER):
    # 以岛 min 作为参考点，统一缩放以适应 [0,1]
    umin, vmin, umax, vmax, w, h = bbox_island(island_faces, uv_layer)
    if w <= EPS or h <= EPS:
        # 退化岛，仅平移
        translate_island(island_faces, uv_layer, -floor(umin), -floor(vmin))
        return
    # 先平移回第一象限瓦片
    du = -floor(umin); dv = -floor(vmin)
    translate_island(island_faces, uv_layer, du, dv)
    # 重新计算包围盒
    umin, vmin, umax, vmax, w, h = bbox_island(island_faces, uv_layer)
    s = min((1.0 - 2 * gutter) / w, (1.0 - 2 * gutter) / h)
    s = min(max(s, 1e-6), 1.0)
    for f in island_faces:
        for lp in f.loops:
            uv = lp[uv_layer].uv
            uv.x = (uv.x - umin) * s + gutter
            uv.y = (uv.y - vmin) * s + gutter

def clamp_island(island_faces, uv_layer):
    for f in island_faces:
        for lp in f.loops:
            uv = lp[uv_layer].uv
            uv.x = min(max(uv.x, -1e-7), 1.0 + 1e-7)
            uv.y = min(max(uv.y, -1e-7), 1.0 + 1e-7)

def quick_uv_check(bm: bmesh.types.BMesh, uv_layer) -> bool:
    """快速检查UV坐标是否需要修复，避免不必要的collect_uv_islands调用"""
    start_time = time.time()
    for f in bm.faces:
        if f.hide:
            continue
        for lp in f.loops:
            uv = lp[uv_layer].uv
            if not (isfinite(uv.x) and isfinite(uv.y)) or uv.x < -EPS or uv.y < -EPS or uv.x > 1 + EPS or uv.y > 1 + EPS:
                log(f"快速UV检查发现越界: uv=({uv.x}, {uv.y}), 耗时: {time.time() - start_time:.2f}秒", "DEBUG")
                return True
            if floor(uv.x) != 0 or floor(uv.y) != 0:
                log(f"快速UV检查发现非第一象限: uv=({uv.x}, {uv.y}), 耗时: {time.time() - start_time:.2f}秒", "DEBUG")
                return True
    log(f"快速UV检查通过，无需进一步检查，耗时: {time.time() - start_time:.2f}秒", "DEBUG")
    return False
def needs_uv_fix(islands, uv_layer) -> bool:
    """检查UV岛是否需要修复"""
    start_time = time.time()
    for faces in islands:
        umin, vmin, umax, vmax, w, h = bbox_island(faces, uv_layer)
        if w > 1.0 + EPS or h > 1.0 + EPS or umin < -EPS or vmin < -EPS or umax > 1.0 + EPS or vmax > 1.0 + EPS:
            log(f"UV岛需要修复: 越界 (umin={umin:.2f}, vmin={vmin:.2f}, umax={umax:.2f}, vmax={vmax:.2f}), 耗时: {time.time() - start_time:.2f}秒", "DEBUG")
            return True
        if floor(umin) != 0 or floor(vmin) != 0:
            log(f"UV岛需要修复: 非第一象限 (umin={umin:.2f}, vmin={vmin:.2f}), 耗时: {time.time() - start_time:.2f}秒", "DEBUG")
            return True
    log(f"UV岛检查通过，无需修复，耗时: {time.time() - start_time:.2f}秒", "DEBUG")
    return False

def process_mesh_uv(me: bpy.types.Mesh) -> tuple[int, int]:
    bm = bmesh.new()
    bm.from_mesh(me)
    uv_layer = bm.loops.layers.uv.active
    if uv_layer is None:
        bm.free(); return 0, 0

    islands = collect_uv_islands(bm, uv_layer)
    total_islands = len(islands)
    fixed_islands = 0

    for faces in islands:
        umin, vmin, umax, vmax, w, h = bbox_island(faces, uv_layer)
        # 需求1：UDIM平移（不重排）
        if w <= 1.0 + EPS and h <= 1.0 + EPS:
            du = -floor(umin); dv = -floor(vmin)
            if abs(du) > EPS or abs(dv) > EPS:
                translate_island(faces, uv_layer, du, dv)
                fixed_islands += 1
            # 若仍越界，执行需求3微调
            umin, vmin, umax, vmax, w, h = bbox_island(faces, uv_layer)
            if umin < -EPS or vmin < -EPS or umax > 1.0 + EPS or vmax > 1.0 + EPS:
                scale_translate_island_to_unit(faces, uv_layer, GUTTER)
                fixed_islands += 1
        else:
            # 需求2/3：散乱或过大，统一缩放+平移装入 [0,1]
            scale_translate_island_to_unit(faces, uv_layer, GUTTER)
            fixed_islands += 1

        clamp_island(faces, uv_layer)

    bm.to_mesh(me)
    me.update()
    bm.free()
    return total_islands, fixed_islands

def check_uv_needs_fix() -> bool:
    """仅检查所有网格的UV是否需要修复，不进行修复操作"""
    meshes = [o.data for o in bpy.context.scene.objects if o.type == 'MESH' and o.data]
    for me in meshes:
        bm = bmesh.new()
        bm.from_mesh(me)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            bm.free()
            continue
        # 先进行快速UV检查
        if quick_uv_check(bm, uv_layer):
            # 只有快速检查发现问题时才收集UV岛
            islands = collect_uv_islands(bm, uv_layer)
            if needs_uv_fix(islands, uv_layer):
                bm.free()
                log("🔍 检测到UV需要修复", "INFO")
                return True
        bm.free()
    log("✅ UV检查通过，无需修复", "INFO")
    return False

def check_and_fix_uv() -> bool:
    """检查所有网格的UV是否需要修复，如果需要则修复，返回是否进行了修复"""
    meshes = [o.data for o in bpy.context.scene.objects if o.type == 'MESH' and o.data]
    needs_fix = False
    total_fixed = 0
    for me in meshes:
        bm = bmesh.new()
        bm.from_mesh(me)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            bm.free()
            continue
        # 先进行快速UV检查
        if quick_uv_check(bm, uv_layer):
            # 只有快速检查发现问题时才收集UV岛
            islands = collect_uv_islands(bm, uv_layer)
            start_time = time.time()
            if needs_uv_fix(islands, uv_layer):
                needs_fix = True
                ti, fi = process_mesh_uv(me)  # 使用原有process_mesh_uv
                total_fixed += fi
                log(f"网格修复: 岛总数={ti}, 修复数={fi}, 耗时: {time.time() - start_time:.2f}秒", "DEBUG")
            else:
                log(f"UV岛检查通过，无需修复此网格，耗时: {time.time() - start_time:.2f}秒", "DEBUG")
        bm.free()
    if needs_fix:
        log(f"✅ UV修复完成: 修复了 {total_fixed} 个岛屿", "INFO")
    else:
        log("✅ UV检查通过，无需修复", "INFO")
    return needs_fix

# =========================================================
# 自动生成粗糙度和金属度贴图系统
# =========================================================
def create_image_from_array(name: str, data: np.ndarray) -> bpy.types.Image:
    """
    根据二维数组 data (h, w)，生成 Blender Image。
    data 值域 [0,1]，输出为 RGBA，RGB 三个通道都写入相同的灰度值。
    """
    h, w = data.shape
    img = bpy.data.images.new(name, width=w, height=h)

    # 创建 RGBA 缓冲区
    buf = np.zeros((h, w, 4), dtype=np.float32)
    buf[:, :, 0] = data  # R
    buf[:, :, 1] = data  # G
    buf[:, :, 2] = data  # B
    buf[:, :, 3] = 1.0   # Alpha 通道固定为 1

    # Blender 要求像素数据是 1D 展平数组
    img.pixels = buf.flatten()

    return img


def _find_texture_node_recursive(start_node: bpy.types.Node, visited: set) -> Optional[bpy.types.Node]:
    """递归辅助函数，向上遍历节点树以查找源TEX_IMAGE节点"""
    if not start_node or start_node in visited:
        return None
    
    visited.add(start_node)

    if start_node.type == 'TEX_IMAGE':
        return start_node

    # 遍历所有输入接口
    for input_socket in start_node.inputs:
        if input_socket.is_linked:
            # 从连接的接口获取来源节点
            from_node = input_socket.links[0].from_node
            result = _find_texture_node_recursive(from_node, visited)
            if result:
                return result # 一旦找到，立即返回结果
    
    return None

def find_base_color_texture_node(bsdf_node: bpy.types.Node) -> Optional[bpy.types.Node]:
    """
    从BSDF节点的基础色输入向上递归遍历，找到最终连接的图像纹理节点。
    """
    base_color_input = bsdf_node.inputs.get('Base Color')
    if not base_color_input or not base_color_input.is_linked:
        return None
    
    # 从基础色输入口直接连接的节点开始递归搜索
    start_node = base_color_input.links[0].from_node
    return _find_texture_node_recursive(start_node, set())

def generate_rm_from_tex_node(tex_node, material_name):
    """
    根据给定的图像纹理节点生成粗糙度和金属度贴图，参考generate_rm.py的实现
    """
    try:
        img = tex_node.image
        if not img:
            log(f"纹理节点没有图像", "WARNING")
            return None, None
            
        width, height = img.size
        if width == 0 or height == 0:
            log(f"图像尺寸为零，无法生成RM贴图", "WARNING")
            return None, None
            
        pixels = np.array(img.pixels[:])  # RGBA 浮点数组
        pixels = pixels.reshape((height, width, 4))

        # 生成粗糙度图（灰度 -> 反转，并限制最低值）
        gray = 0.2126 * pixels[:, :, 0] + 0.7152 * pixels[:, :, 1] + 0.0722 * pixels[:, :, 2]
        rough = 1.0 - gray  # 反转作为粗糙度
        rough = np.clip(rough, RM_ROUGHNESS_MIN, 1.0)  # 使用配置的下限值

        # 生成金属度图（低饱和度区域认为是金属）
        r, g, b = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
        maxc = np.maximum(np.maximum(r, g), b)
        minc = np.minimum(np.minimum(r, g), b)
        saturation = (maxc - minc) / (maxc + 1e-5)  # 避免除零

        # 阈值判定：低饱和度区域 -> 金属，高饱和度区域 -> 非金属
        metal = np.where(saturation < RM_SATURATION_THRESHOLD, RM_METALLIC_MAX, 0.0).astype(np.float32)

        roughness_name = f"{material_name}_Roughness_Generated"
        metallic_name = f"{material_name}_Metallic_Generated"
        
        rough_img = create_image_from_array(roughness_name, rough)
        metal_img = create_image_from_array(metallic_name, metal)

        return rough_img, metal_img
        
    except Exception as e:
        log(f"生成RM贴图失败: {e}", "ERROR")
        return None, None

def auto_generate_roughness_metallic() -> int:
    """
    为场景中所有材质进行检查，如果金属度和粗糙度都未连接，
    则自动生成并连接它们。
    """
    if not BLENDER_AVAILABLE: 
        log("⚠️ Blender环境不可用，跳过RM贴图生成", "WARNING")
        return 0
    
    log(f"🔍 开始检查材质RM贴图生成需求 (参数: 粗糙度下限={RM_ROUGHNESS_MIN}, 金属度上限={RM_METALLIC_MAX}, 饱和度阈值={RM_SATURATION_THRESHOLD})", "INFO")
    generated_count = 0
    
    for mat in bpy.data.materials:
        if not mat.use_nodes:
            continue
            
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        
        bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if not bsdf:
            continue
        
        # 核心条件：当金属度和粗糙度【都】未连接时
        is_metallic_linked = bsdf.inputs['Metallic'].is_linked
        is_roughness_linked = bsdf.inputs['Roughness'].is_linked
        
        if not is_metallic_linked and not is_roughness_linked:
            log(f"材质 '{mat.name}' 的金属度和粗糙度均未连接，尝试自动生成...", "INFO")
            
            base_color_node = find_base_color_texture_node(bsdf)
            
            if not base_color_node:
                log(f"材质 '{mat.name}' 未能递归找到基础色图像节点，跳过生成。", "WARNING")
                continue
            
            log(f"材质 '{mat.name}' 找到基础色源: 节点'{base_color_node.name}'，图像'{base_color_node.image.name}'", "INFO")
            
            rough_img, metal_img = generate_rm_from_tex_node(base_color_node, mat.name)
            
            if rough_img and metal_img:
                try:
                    # 创建并连接粗糙度节点
                    rough_node = nodes.new(type="ShaderNodeTexImage")
                    rough_node.image = rough_img
                    rough_node.location = (base_color_node.location.x + 300, base_color_node.location.y - 200)
                    links.new(rough_node.outputs['Color'], bsdf.inputs['Roughness'])
                    
                    # 创建并连接金属度节点
                    metal_node = nodes.new(type="ShaderNodeTexImage")
                    metal_node.image = metal_img
                    metal_node.location = (base_color_node.location.x + 300, base_color_node.location.y - 400)
                    links.new(metal_node.outputs['Color'], bsdf.inputs['Metallic'])
                    
                    log(f"材质 '{mat.name}' 成功生成并连接了RM贴图。", "INFO")
                    generated_count += 1
                except Exception as e:
                    log(f"材质 '{mat.name}' 连接生成的RM贴图时失败: {e}", "ERROR")

    if generated_count > 0:
        log(f"✅ RM自动生成完成，共处理了 {generated_count} 个材质。", "INFO")
    else:
        log("✅ 所有材质的金属度和粗糙度均已连接，无需自动生成。", "INFO")
        
    return generated_count

#清理错误贴图
def clear_all_image_textures_from_bsdf(mat, bsdf):
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    removed_nodes = set()

    for input_socket in bsdf.inputs:
        if not input_socket.is_linked:
            continue

        from_node = input_socket.links[0].from_node
        tex_node = _find_texture_node_recursive(from_node, set())

        if tex_node and tex_node.type == 'TEX_IMAGE':
            # 断开所有连接
            for link in list(tex_node.outputs[0].links):
                links.remove(link)

            removed_nodes.add(tex_node)

    # 真正删除节点
    for node in removed_nodes:
        nodes.remove(node)

    return len(removed_nodes)
    

# =========================================================
# Blender.exe导出系统（解决分离通道问题）
# =========================================================
def export_glb_via_blender_exe(output_path: Path) -> bool:
    """
    使用 blender.exe 命令行导出当前场景为GLB
    这种方式与GUI导出行为一致，可以解决脚本直接导出时的通道问题
    
    参数:
        output_path: 输出OBJ文件路径
    
    返回:
        是否成功导出
    """
    if not USE_BLENDER_EXE_EXPORT:
        log("未启用 blender.exe 导出模式", "DEBUG")
        return False
    
    if not os.path.exists(BLENDER_EXE_PATH):
        log(f"❌ Blender可执行文件不存在: {BLENDER_EXE_PATH}", "ERROR")
        log("请在配置区设置正确的 BLENDER_EXE_PATH", "ERROR")
        return False
    
    try:
        armature_display_changed, helper_hidden = normalize_scene_display()
        if armature_display_changed:
            log(f"导出前已将 {armature_display_changed} 个骨架改为正常显示", "DEBUG")
        if helper_hidden:
            log(f"导出前已隐藏 {helper_hidden} 个辅助显示对象", "INFO")

        # 创建临时目录（使用短路径名避免编码问题）
        temp_dir = Path(tempfile.gettempdir()) / "blender_export_temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # 在保存前，打包所有图像资源到 .blend 文件中，避免路径编码问题
        log("打包图像资源到 .blend 文件...", "DEBUG")
        try:
            for image in bpy.data.images:
                if image.filepath and not image.packed_file:
                    try:
                        image.pack()
                        log(f"已打包图像: {image.name}", "DEBUG")
                    except Exception as e:
                        log(f"打包图像失败 {image.name}: {e}", "WARNING")
        except Exception as e:
            log(f"打包图像资源时出错: {e}", "WARNING")
        
        # 清理未使用的数据块，释放内存
        try:
            bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
        except:
            pass  # 如果操作失败，继续执行
        
        # 保存当前场景为临时 .blend 文件
        temp_blend = temp_dir / f"temp_scene_{int(time.time())}.blend"
        log(f"保存临时场景文件: {temp_blend}", "DEBUG")
        
        # 使用绝对路径并确保路径存在
        temp_blend_str = str(temp_blend.resolve())
        try:
            bpy.ops.wm.save_as_mainfile(filepath=temp_blend_str, compress=False)
        except Exception as e:
            log(f"保存 .blend 文件失败: {e}", "ERROR")
            # 如果保存失败，尝试清理并重试一次
            try:
                gc.collect()
                bpy.ops.wm.save_as_mainfile(filepath=temp_blend_str, compress=False)
            except Exception as e2:
                log(f"重试保存 .blend 文件也失败: {e2}", "ERROR")
                return False
        
        # 创建导出脚本（使用绝对路径）
        export_script = temp_dir / f"export_script_{int(time.time())}.py"
        output_path_abs = str(output_path.resolve())
        script_content = f"""
import bpy
import sys
import os

try:
    print("开始导出GLB...")
    output_path = r"{output_path_abs}"
    
    # 确保输出目录存在
    output_dir = os.path.dirname(output_path)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    
    # 导出GLB：保留骨架/动画，但只导出可见对象，避免把隐藏的刚体/碰撞体辅助网格带进引擎。
    try:
        armatures = [
            obj for obj in bpy.data.objects
            if getattr(obj, 'type', None) == 'ARMATURE'
            and not getattr(obj, 'hide_viewport', False)
            and not obj.hide_get()
        ]
        total_bones = 0
        deform_bones = 0
        for arm_obj in armatures:
            bones = list(getattr(getattr(arm_obj, 'data', None), 'bones', []) or [])
            total_bones += len(bones)
            deform_bones += sum(1 for bone in bones if getattr(bone, 'use_deform', False))
        print(
            f"GLB导出骨骼统计: armature={{len(armatures)}} | "
            f"bones={{total_bones}} | deform={{deform_bones}} | "
            f"non_deform={{total_bones - deform_bones}} | actions={{len(bpy.data.actions)}}"
        )
    except Exception as e:
        print(f"GLB导出骨骼统计失败: {{e}}")

    def _filter_gltf_export_kwargs(kwargs):
        try:
            valid = {{p.identifier for p in bpy.ops.export_scene.gltf.get_rna_type().properties}}
            return {{key: value for key, value in kwargs.items() if key in valid}}
        except Exception:
            return kwargs

    export_kwargs = dict(
        filepath=output_path,
        export_format='GLB',
        use_selection=False,
        export_materials='EXPORT',
        export_apply=False,
        use_visible=True,
        export_skins=True,
        export_animations=True,
        export_def_bones=False,
        export_armature_object_remove=False,
        export_hierarchy_flatten_bones=False,
        export_hierarchy_flatten_objs=False,
        export_optimize_animation_keep_anim_armature=True,
    )
    export_kwargs = _filter_gltf_export_kwargs(export_kwargs)
    try:
        result = bpy.ops.export_scene.gltf(**export_kwargs)
    except TypeError:
        bpy.ops.object.select_all(action='DESELECT')
        selected = 0
        for obj in bpy.data.objects:
            try:
                if obj.hide_get() or getattr(obj, 'hide_viewport', False):
                    continue
            except Exception:
                if getattr(obj, 'hide_viewport', False):
                    continue
            try:
                obj.select_set(True)
                selected += 1
            except Exception:
                pass
        export_kwargs.pop('use_visible', None)
        export_kwargs['use_selection'] = True
        result = bpy.ops.export_scene.gltf(**export_kwargs)
    
    if result == {{'FINISHED'}}:
        # 验证文件是否生成
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            print(f"✅ GLB导出成功: {{output_path}}")
            sys.exit(0)
        else:
            print(f"❌ GLB文件未生成或为空: {{output_path}}")
            sys.exit(1)
    else:
        print(f"❌ GLB导出失败: {{result}}")
        sys.exit(1)
        
except Exception as e:
    print(f"❌ 导出过程出错: {{e}}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
"""
        
        with open(export_script, 'w', encoding='utf-8') as f:
            f.write(script_content)
        
        log(f"创建导出脚本: {export_script}", "DEBUG")
        
        # 构建命令行（使用绝对路径）
        cmd = [
            str(Path(BLENDER_EXE_PATH).resolve()),
            str(temp_blend.resolve()),
            "--background",
            "--python", str(export_script.resolve())
        ]
        
        log(f"执行命令: {' '.join(cmd)}", "DEBUG")
        log(f"🔄 使用 blender.exe 导出GLB到: {output_path}", "INFO")
        
        # 执行命令
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        )
        
        try:
            stdout, stderr = process.communicate(timeout=300)  # 5分钟超时
        except subprocess.TimeoutExpired:
            log("❌ blender.exe 导出超时（超过5分钟）", "ERROR")
            process.kill()
            process.wait()
            return False
        
        # 输出日志
        if stdout:
            for line in stdout.split('\n'):
                if line.strip():
                    log(f"  [Blender] {line}", "DEBUG")
        
        if stderr:
            for line in stderr.split('\n'):
                line_stripped = line.strip()
                if line_stripped:
                    # 检查是否是严重错误（内存错误、访问冲突等）
                    if any(keyword in line_stripped for keyword in ['MemoryError', 'EXCEPTION_ACCESS_VIOLATION', 'Error:', 'Fatal']):
                        log(f"  [Blender严重错误] {line_stripped}", "ERROR")
                    elif "Warning" not in line_stripped:
                        log(f"  [Blender错误] {line_stripped}", "DEBUG")
        
        # 检查返回码
        if process.returncode == 0:
            # 验证文件是否生成
            if output_path.exists() and output_path.stat().st_size > 0:
                log(f"✅ blender.exe 导出成功: {output_path.name} ({output_path.stat().st_size} bytes)", "INFO")
                success = True
            else:
                log(f"❌ 导出文件未生成或为空: {output_path}", "ERROR")
                success = False
        else:
            # 检查是否是内存或崩溃错误
            if stderr and any(keyword in stderr for keyword in ['MemoryError', 'EXCEPTION_ACCESS_VIOLATION', 'Fatal']):
                log(f"❌ blender.exe 导出失败（内存错误或崩溃），返回码: {process.returncode}", "ERROR")
                log("建议：尝试禁用 blender.exe 导出模式，使用脚本直接导出", "WARNING")
            else:
                log(f"❌ blender.exe 导出失败，返回码: {process.returncode}", "ERROR")
            success = False
        
        # 清理临时文件
        try:
            if temp_blend.exists():
                temp_blend.unlink()
            if export_script.exists():
                export_script.unlink()
            # 清理临时目录（如果为空）
            if temp_dir.exists() and not list(temp_dir.iterdir()):
                temp_dir.rmdir()
        except Exception as e:
            log(f"清理临时文件失败: {e}", "DEBUG")
        
        return success
        
    except subprocess.TimeoutExpired:
        log("❌ blender.exe 导出超时（超过5分钟）", "ERROR")
        process.kill()
        return False
    except Exception as e:
        log(f"❌ blender.exe 导出过程出错: {e}", "ERROR")
        import traceback
        log(f"详细错误: {traceback.format_exc()}", "DEBUG")
        return False

def export_glb_with_fallback(output_path: Path) -> bool:
    """
    导出GLB文件，优先使用 blender.exe 方式，失败时回退到脚本直接导出
    
    参数:
        output_path: 输出路径
    
    返回:
        是否成功导出
    """
    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 优先使用 blender.exe 导出
    if USE_BLENDER_EXE_EXPORT:
        log(f"尝试使用 blender.exe 导出: {output_path.name}", "DEBUG")
        if export_glb_via_blender_exe(output_path):
            return True
        else:
            log("⚠️ blender.exe 导出失败，回退到脚本直接导出", "WARNING")
    
    # 回退到脚本直接导出
    log(f"使用脚本直接导出: {output_path.name}", "DEBUG")
    normalize_scene_display()
    export_result = export_scene_gltf_visible(str(output_path))
    
    if export_result == {'FINISHED'}:
        log(f"✅ 脚本导出成功: {output_path.name}", "INFO")
        return True
    else:
        log(f"❌ 脚本导出失败: {output_path.name}", "ERROR")
        return False

# =========================================================
# RM关键词统计保存函数
# =========================================================
def save_rm_keyword_stats(output_path: Path):
    """
    保存RM混合贴图关键词统计结果到文件
    
    参数:
        output_path: 输出文件路径
    """
    if not RM_KEYWORD_STATS:
        log("📊 没有RM混合贴图关键词统计数据", "INFO")
        return
    
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("RM贴图命中关键词：\n")
            # 按关键词名称排序
            sorted_keywords = sorted(RM_KEYWORD_STATS.items(), key=lambda x: x[0])
            for keyword, count in sorted_keywords:
                f.write(f"{keyword.upper()}:  {count}\n")
        
        total_count = sum(RM_KEYWORD_STATS.values())
        log(f"✅ RM关键词统计已保存到: {output_path}", "INFO")
        log(f"📊 统计了 {len(RM_KEYWORD_STATS)} 个不同的关键词，总命中次数: {total_count}", "INFO")
    except Exception as e:
        log(f"❌ 保存RM关键词统计失败: {e}", "ERROR")

# =========================================================
# 核心处理函数
# =========================================================
def build_export_path(base_dst_root: Path, glb_name: str, has_rm_connected: bool, has_empty_textures: bool) -> Path:
    """
    根据分类条件构建导出路径
    
    参数:
        base_dst_root: 基础导出根目录
        glb_name: GLB文件名
        has_rm_connected: 是否连接了RM混合贴图
        has_empty_textures: 是否有材质JSON的textures为空
    
    返回:
        构建的导出路径
    """
    export_path = base_dst_root
    
    # 如果连接了RM混合贴图，添加到RM_Connected子目录
    if has_rm_connected:
        export_path = export_path / "RM_Connected"
    
    # 如果有材质JSON的textures为空，添加到No_Textures子目录
    if has_empty_textures:
        export_path = export_path / "No_Textures"
    
    return export_path / glb_name

def import_model_for_pipeline(model_path: Path):
    """按模型扩展名导入到 Blender，供完整流水线统一处理。"""
    ext = model_path.suffix.lower()

    if ext == '.obj':
        # Blender 4.x 使用 bpy.ops.wm.obj_import；旧版 Blender 回退到 import_scene.obj。
        try:
            return bpy.ops.wm.obj_import(filepath=str(model_path))
        except Exception:
            return bpy.ops.import_scene.obj(filepath=str(model_path))

    if ext == '.fbx':
        return bpy.ops.import_scene.fbx(filepath=str(model_path))

    if ext in ('.glb', '.gltf'):
        return bpy.ops.import_scene.gltf(filepath=str(model_path))

    raise Exception(f"不支持的模型格式: {ext or '(无扩展名)'}")

def material_has_image_textures(material) -> bool:
    """判断导入材质里是否已经带有图片贴图节点。"""
    try:
        if not material or not material.use_nodes or not material.node_tree:
            return False
        return any(
            node.type == 'TEX_IMAGE' and getattr(node, 'image', None) is not None
            for node in material.node_tree.nodes
        )
    except Exception:
        return False

def normalize_armature_display() -> int:
    """不删除骨架，只把 Envelope/球状显示改成正常骨架显示。"""
    changed = 0
    for obj in list(bpy.data.objects):
        if obj is None or getattr(obj, "type", None) != 'ARMATURE':
            continue
        try:
            arm = getattr(obj, "data", None)
            if arm is not None and getattr(arm, "display_type", None) != 'STICK':
                arm.display_type = 'STICK'
                changed += 1
            if arm is not None and hasattr(arm, "show_bone_custom_shapes") and arm.show_bone_custom_shapes:
                arm.show_bone_custom_shapes = False
                changed += 1
            obj.show_in_front = False
        except Exception as e:
            log(f"设置骨架显示失败 {getattr(obj, 'name', '<unnamed>')}: {e}", "WARNING")
    return changed

HELPER_COLLECTION_TOKENS = (
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

HELPER_OBJECT_NAME_TOKENS = (
    "rigidbody",
    "rigid_body",
    "rigid body",
    "collision",
    "collider",
    "剛体",
    "刚体",
    "ジョイント",
)

def is_helper_collection_name(name: str) -> bool:
    lowered = (name or "").lower()
    return any(token in lowered for token in HELPER_COLLECTION_TOKENS)

def is_helper_object_name(name: str) -> bool:
    lowered = (name or "").lower()
    return any(token in lowered for token in HELPER_OBJECT_NAME_TOKENS)

def is_helper_display_object(obj) -> bool:
    if obj is None:
        return False

    try:
        if any(is_helper_collection_name(coll.name) for coll in obj.users_collection):
            return True
    except Exception:
        pass

    try:
        name_blob = f"{getattr(obj, 'name', '')} {getattr(getattr(obj, 'data', None), 'name', '')}"
        if is_helper_object_name(name_blob):
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

def hide_helper_display_objects() -> int:
    """隐藏 MMD/GLTF 辅助显示对象，不删除骨架、蒙皮或动画数据。"""
    hidden = 0

    for coll in list(bpy.data.collections):
        if not is_helper_collection_name(coll.name):
            continue
        try:
            coll.hide_viewport = True
            coll.hide_render = True
        except Exception:
            pass

    for obj in list(bpy.data.objects):
        if not is_helper_display_object(obj):
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

def normalize_scene_display() -> Tuple[int, int]:
    changed_armatures = normalize_armature_display()
    hidden_helpers = hide_helper_display_objects()
    return changed_armatures, hidden_helpers

def log_armature_export_stats(prefix: str = "GLB导出") -> None:
    try:
        armatures = [
            obj for obj in list(bpy.data.objects)
            if obj is not None
            and getattr(obj, "type", None) == 'ARMATURE'
            and not getattr(obj, "hide_viewport", False)
            and not obj.hide_get()
        ]
        total_bones = 0
        deform_bones = 0
        for arm_obj in armatures:
            bones = list(getattr(getattr(arm_obj, "data", None), "bones", []) or [])
            total_bones += len(bones)
            deform_bones += sum(1 for bone in bones if getattr(bone, "use_deform", False))
        log(
            f"{prefix}骨骼统计: armature={len(armatures)} | "
            f"bones={total_bones} | deform={deform_bones} | "
            f"non_deform={total_bones - deform_bones} | actions={len(bpy.data.actions)}",
            "INFO",
        )
    except Exception as e:
        log(f"{prefix}骨骼统计失败: {e}", "WARNING")

def filter_supported_gltf_export_kwargs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """兼容不同 Blender 版本：只传当前 glTF 导出器认识的参数。"""
    try:
        valid = {prop.identifier for prop in bpy.ops.export_scene.gltf.get_rna_type().properties}
        return {key: value for key, value in kwargs.items() if key in valid}
    except Exception:
        return kwargs

def select_visible_export_objects() -> int:
    """选择当前仍可见的对象；用于旧版 GLTF 导出器没有 use_visible 参数时兜底。"""
    try:
        bpy.ops.object.select_all(action='DESELECT')
    except Exception:
        pass

    selected = 0
    first_obj = None
    for obj in list(bpy.data.objects):
        try:
            if obj.hide_get() or getattr(obj, "hide_viewport", False):
                continue
        except Exception:
            if getattr(obj, "hide_viewport", False):
                continue
        if is_helper_display_object(obj):
            continue
        try:
            obj.select_set(True)
            if first_obj is None:
                first_obj = obj
            selected += 1
        except Exception:
            pass

    if first_obj is not None:
        try:
            bpy.context.view_layer.objects.active = first_obj
        except Exception:
            pass
    return selected

def export_scene_gltf_visible(filepath: str):
    """导出可见对象，保留骨架/动画，跳过隐藏的辅助刚体/碰撞体。"""
    log_armature_export_stats()
    export_kwargs = {
        "filepath": filepath,
        "export_format": "GLB",
        "use_selection": False,
        "export_materials": "EXPORT",
        "export_apply": False,
        "use_visible": True,
        "export_skins": True,
        "export_animations": True,
        "export_def_bones": False,
        "export_armature_object_remove": False,
        "export_hierarchy_flatten_bones": False,
        "export_hierarchy_flatten_objs": False,
        "export_optimize_animation_keep_anim_armature": True,
    }
    export_kwargs = filter_supported_gltf_export_kwargs(export_kwargs)
    try:
        return bpy.ops.export_scene.gltf(**export_kwargs)
    except TypeError:
        select_visible_export_objects()
        export_kwargs.pop("use_visible", None)
        export_kwargs["use_selection"] = True
        export_kwargs = filter_supported_gltf_export_kwargs(export_kwargs)
        return bpy.ops.export_scene.gltf(**export_kwargs)

def process_single_glb(glb_path: Path, material_root: Path, texture_root: Path, dst_root: Path, glb_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    处理单个模型文件
    
    参数:
        glb_path: 模型文件路径（OBJ / FBX / GLB / GLTF）
        material_root: 材质根目录
        texture_root: 贴图根目录
        dst_root: 导出根目录
        glb_root: 模型根目录（用于单模型文件夹模式，确定模型所在的文件夹）
    """
    result = {'success': False, 'error': None, 'materials_processed': 0, 'textures_connected': 0, 'uv_needs_fix': False, 'uv_fixed': False, 'exported_paths': [], 'has_rm_connected': False, 'has_empty_textures': False, 'weighted_normal_applied': 0}
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        import_result = import_model_for_pipeline(glb_path)
        if import_result != {'FINISHED'}:
            raise Exception(f"模型导入失败: {glb_path.name} -> {import_result}")

        armature_display_changed, helper_hidden = normalize_scene_display()
        if armature_display_changed:
            log(f"已将 {armature_display_changed} 个骨架改为正常显示，不删除骨架数据", "INFO")
        if helper_hidden:
            log(f"已隐藏 {helper_hidden} 个辅助显示对象（刚体/碰撞体/GLTF非导出对象），不删除数据", "INFO")

        # 输入可以是 OBJ / FBX / GLB / GLTF，统一重新导出为 GLB，便于打包材质和贴图
        output_glb_name = glb_path.with_suffix(".glb").name

        materials = [m for m in bpy.data.materials if getattr(m, "users", 0) > 0]

        if not materials:
            log(f"模型文件 {glb_path.name} 没有材质，将根据文件名创建PBR材质并继续匹配贴图", "WARNING")
            created_mat = create_pbr_material_from_obj_name(glb_path)

            # 关键：重新获取材质列表，让后面的 for material in materials 能执行
            materials = [created_mat]

        # 单模型文件夹模式：确定当前模型对应的原始资源文件夹名称
        folder_name = None
        if ENABLE_SINGLE_GLB_FOLDER_BUILD and file_indexer and file_indexer.enable_folder_build:
            folder_name = file_indexer.get_folder_for_glb(glb_path, glb_root)
            if folder_name:
                log(f"📁 单模型文件夹模式: 模型文件 {glb_path.name} 匹配到文件夹 {folder_name}，在该文件夹内搜索JSON和贴图", "INFO")
            else:
                log(f"⚠️ 无法确定模型资源文件夹（模型: {glb_path.name}），回退到传统模式", "WARNING")

        total_textures_connected = 0
        has_rm_connected = False  # 全局标记：是否有任何材质连接了RM混合贴图
        has_empty_textures = False  # 全局标记：是否有任何材质JSON的textures为空，或基础色未连接且无有效贴图
        
        for material in materials:
            if not material.use_nodes:
                material.use_nodes = True

            texture_files = {}
            textures = {}
            used_fallback_without_json = False

            # 根据模式选择搜索范围（使用文件夹名称）。优先材质名，找不到时用模型名兜底。
            material_json_path = find_material_json(material.name, material_root, folder_name)
            if not material_json_path and material.name != glb_path.stem:
                material_json_path = find_material_json(glb_path.stem, material_root, folder_name)

            if material_json_path:
                with open(material_json_path, 'r', encoding='utf-8') as f:
                    textures = json.load(f).get('Textures', {})

                # 检查textures是否为空
                if not textures or len(textures) == 0:
                    log(f"材质 {material.name} 的JSON中textures为空，尝试使用贴图文件名直连模式", "INFO")
                else:
                    # 检查textures中是否有基础色贴图和其他有效贴图（排除黑名单）
                    has_basecolor_texture = False
                    valid_textures = {}
                    for key, path in textures.items():
                        # 使用classify_texture_type检查贴图类型，排除unknown类型（黑名单）
                        texture_type, _, _ = classify_texture_type(key, path)
                        if texture_type != 'unknown':
                            valid_textures[key] = path
                            if texture_type == 'basecolor':
                                has_basecolor_texture = True

                    # 如果textures中没有基础色贴图，且排除黑名单后没有其他有效贴图，则转入无JSON直连模式
                    if not has_basecolor_texture and len(valid_textures) == 0:
                        log(
                            f"材质 {material.name} 的JSON贴图全部被排除或无有效贴图，尝试使用贴图文件名直连模式",
                            "INFO"
                        )
                    else:
                        for key, path in textures.items():
                            # 根据模式选择搜索范围（使用文件夹名称）
                            tex_file = find_texture_file(path, texture_root, folder_name)
                            if tex_file:
                                texture_files[key] = tex_file
                            else:
                                log(f"未找到贴图文件: {path}", "DEBUG")

                        if not texture_files:
                            log(f"材质 {material.name} 的JSON存在，但未解析到任何贴图文件，尝试使用贴图文件名直连模式", "INFO")
            else:
                log(f"未找到材质JSON: {material.name}，尝试使用贴图文件名直连模式", "INFO")

            # JSON缺失/为空/无法找到有效贴图时，回退到无JSON模式
            if not texture_files:
                candidate_paths = collect_candidate_textures_for_material(
                    material.name,
                    texture_root,
                    folder_name,
                    glb_path
                )
                if not candidate_paths and material.name != glb_path.stem:
                    candidate_paths = collect_candidate_textures_for_material(
                        glb_path.stem,
                        texture_root,
                        folder_name,
                        glb_path
                    )
                if candidate_paths:
                    texture_files, textures = build_texture_files_from_filenames(candidate_paths)
                    used_fallback_without_json = True
                    log(
                        f"材质 {material.name} 无JSON直连模式收集到 {len(texture_files)} 个候选贴图",
                        "INFO"
                    )

            if not texture_files:
                if glb_path.suffix.lower() != '.obj' and material_has_image_textures(material):
                    result['materials_processed'] += 1
                    log(f"材质 {material.name} 未匹配到外部贴图，保留模型文件内已有贴图节点", "INFO")
                    continue

                has_empty_textures = True

                removed = 0
                if material.use_nodes:
                    bsdf = next(
                        (n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'),
                        None
                    )
                    if bsdf:
                        removed = clear_all_image_textures_from_bsdf(material, bsdf)

                log(
                    f"材质 {material.name} 最终仍无可用贴图，已清理 {removed} 个错误贴图节点，标记为 No_Textures",
                    "INFO"
                )
                continue

            connected, material_has_rm = connect_textures_to_material(
                material,
                texture_files,
                material.name,
                textures,
                glb_path.stem,
                glb_path.suffix.lower(),
            )
            if connected:
                total_textures_connected += len(texture_files)
                result['materials_processed'] += 1
                if material_has_rm:
                    has_rm_connected = True
            elif used_fallback_without_json:
                has_empty_textures = True
                log(f"材质 {material.name} 无JSON直连模式未能成功连接任何贴图", "WARNING")
        
        set_default_material_values()
        cleanup_color_attributes()
        
        # 保存分类标记到result
        result['has_rm_connected'] = has_rm_connected
        result['has_empty_textures'] = has_empty_textures
        
        # 记录分类信息
        if has_rm_connected:
            log(f"📌 GLB {glb_path.name} 已连接RM混合贴图", "INFO")
        if has_empty_textures:
            log(f"📌 GLB {glb_path.name} 有材质JSON的textures为空", "INFO")
        
        # RM贴图生成（可配置开关）
        if ENABLE_RM_GENERATION:
            generated_rm_count = auto_generate_roughness_metallic()
            result['rm_generated'] = generated_rm_count
            if generated_rm_count > 0:
                log(f"🎨 为 {generated_rm_count} 个材质生成了RM贴图", "INFO")
        else:
            result['rm_generated'] = 0
            log("⏭️ RM贴图生成已禁用", "DEBUG")

        # 法线贴图DX转OpenGL（可配置开关）
        if ENABLE_NORMAL_DX_TO_OPENGL:
            converted_normal_count = convert_all_normal_maps_dx_to_opengl()
            result['normal_converted'] = converted_normal_count
            if converted_normal_count > 0:
                log(f"🔄 转换了 {converted_normal_count} 个法线贴图 (DX→OpenGL)", "INFO")
        else:
            result['normal_converted'] = 0

        # 加权法线修改器处理（在UV修复之前，确保所有导出版本都应用）
        if ENABLE_ADD_AND_APPLY_WEIGHTED_NORMAL_MODIFIER:
            weighted_normal_count = add_and_apply_weighted_normal_modifier()
            result['weighted_normal_applied'] = weighted_normal_count
            if weighted_normal_count > 0:
                log(f"🔧 为 {weighted_normal_count} 个网格对象应用了加权法线修改器", "INFO")
        else:
            result['weighted_normal_applied'] = 0
            log("⏭️ 加权法线修改器已禁用", "DEBUG")
        
        # 新的UV处理逻辑
        result['uv_needs_fix'] = UV_FIXER_ENABLED and check_uv_needs_fix()
        
        # 根据配置和UV检查结果决定导出策略
        # 注意：分类路径（RM_Connected/No_Textures）在所有模式下都应用
        if UV_EXPORT_MODE == "auto":
            # 自动模式：如果不需要修复，导出到分类目录；如果需要修复，修复后导出到uvfixed分类目录
            if not result['uv_needs_fix']:
                # 无需修复，直接导出到分类目录
                dst_path = build_export_path(dst_root, output_glb_name, has_rm_connected, has_empty_textures)
                if export_glb_with_fallback(dst_path):
                    result['exported_paths'] = [str(dst_path)]
                    log(f"✅ 导出到分类目录: {dst_path.parent.relative_to(dst_root)}/{glb_path.name} (UV无需修复)")
                else:
                    raise Exception("GLB导出失败")
            else:
                # 需要修复，修复后导出到uvfixed分类目录
                result['uv_fixed'] = check_and_fix_uv()
                uvfixed_base = dst_root / UVFIXED_SUBDIR
                dst_path = build_export_path(uvfixed_base, output_glb_name, has_rm_connected, has_empty_textures)
                if export_glb_with_fallback(dst_path):
                    result['exported_paths'] = [str(dst_path)]
                    log(f"✅ 导出到UV修复分类目录: {dst_path.parent.relative_to(dst_root)}/{glb_path.name} (UV已修复)")
                else:
                    raise Exception("GLB导出失败")
                    
        elif UV_EXPORT_MODE == "dual":
            # 双版本模式：始终导出两个版本（如果需要修复的话）
            if not result['uv_needs_fix']:
                # 无需修复，只导出一个版本到分类目录
                dst_path = build_export_path(dst_root, output_glb_name, has_rm_connected, has_empty_textures)
                if export_glb_with_fallback(dst_path):
                    result['exported_paths'] = [str(dst_path)]
                    log(f"✅ 导出到分类目录: {dst_path.parent.relative_to(dst_root)}/{glb_path.name} (UV无需修复)")
                else:
                    raise Exception("GLB导出失败")
            else:
                # 需要修复，导出两个版本
                # 1. 先导出原版本到分类目录
                dst_path_original = build_export_path(dst_root, output_glb_name, has_rm_connected, has_empty_textures)
                if not export_glb_with_fallback(dst_path_original):
                    raise Exception("原版本GLB导出失败")
                
                # 2. 修复UV后导出到uvfixed分类目录
                result['uv_fixed'] = check_and_fix_uv()
                uvfixed_base = dst_root / UVFIXED_SUBDIR
                dst_path_fixed = build_export_path(uvfixed_base, output_glb_name, has_rm_connected, has_empty_textures)
                if not export_glb_with_fallback(dst_path_fixed):
                    raise Exception("修复版GLB导出失败")
                
                result['exported_paths'] = [str(dst_path_original), str(dst_path_fixed)]
                log(f"✅ 双版本导出: {glb_path.name} (原版 + UV修复版) 到分类目录")
                
        elif UV_EXPORT_MODE == "fixed_only":
            # 仅修复版模式：如果需要修复就修复后导出，否则直接导出
            if result['uv_needs_fix']:
                result['uv_fixed'] = check_and_fix_uv()
            
            dst_path = build_export_path(dst_root, output_glb_name, has_rm_connected, has_empty_textures)
            if export_glb_with_fallback(dst_path):
                result['exported_paths'] = [str(dst_path)]
                status_msg = "UV已修复" if result['uv_needs_fix'] else "UV无需修复"
                log(f"✅ 导出完成: {dst_path.parent.relative_to(dst_root)}/{glb_path.name} ({status_msg})")
            else:
                raise Exception("GLB导出失败")
        
        result['success'] = True
        result['textures_connected'] = total_textures_connected
        
    except Exception as e:
        result['error'] = str(e)
        log(f"❌ 处理失败 {glb_path.name}: {e}", "ERROR")
    finally:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        gc.collect()
    return result

# =========================================================
# 渲染部分（集成自45度包围盒渲染脚本）
# =========================================================

def load_render_progress():
    """加载渲染进度文件"""
    if os.path.exists(RENDER_PROGRESS_FILE):
        try:
            with open(RENDER_PROGRESS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_render_progress(progress_data):
    """保存渲染进度文件"""
    try:
        with open(RENDER_PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"保存渲染进度失败: {e}")

def find_all_glbs(root_dir):
    """
    递归遍历目录下所有GLB文件，返回绝对路径列表
    """
    all_glb_paths = []
    for dirpath, _, filenames in os.walk(root_dir):
        for fname in filenames:
            if fname.lower().endswith(".glb"):
                fullpath = os.path.join(dirpath, fname)
                all_glb_paths.append(fullpath)
    return all_glb_paths

def render_glb(glb_path, progress_data):
    """渲染单个GLB文件，输出到GLB同级目录 - 左侧45度角斜上角视图"""
    try:
        # 输出文件名与GLB保持相同名称，放在同级目录下
        glb_dir = os.path.dirname(glb_path)
        out_name = os.path.splitext(os.path.basename(glb_path))[0]
        out_png = os.path.join(glb_dir, out_name + ".png")

        bpy.ops.wm.read_factory_settings(use_empty=True)  # 清空场景
        bpy.ops.object.select_all(action="DESELECT")

        if str(glb_path).lower().endswith(".glb"):
            bpy.ops.import_scene.gltf(filepath=str(glb_path))
            normalize_scene_display()

        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        # 检查是否有Mesh对象
        mesh_objects = [obj for obj in bpy.context.selected_objects if obj.type == "MESH"]
        if not mesh_objects:
            warning_msg = f"跳过渲染：无Mesh对象 - {os.path.basename(glb_path)}"
            print(f"⚠️ {warning_msg}")
            progress_data[glb_path] = {
                "status": "skipped",
                "reason": "no_mesh_objects",
                "message": warning_msg,
                "timestamp": datetime.now().isoformat()
            }
            return True, warning_msg  # 返回True表示"成功处理"（虽然是跳过）

        print(f"  发现 {len(mesh_objects)} 个Mesh对象")

        # calc the bbox
        min_x = 999999
        min_y = 999999
        min_z = 999999
        max_x = -999999
        max_y = -999999
        max_z = -999999
        for obj in bpy.context.selected_objects:
            if obj.type == "MESH":
                bbox_world_space = [obj.matrix_world @ Vector(v) for v in obj.bound_box]
                obj_min_x = min(v.x for v in bbox_world_space)
                obj_min_y = min(v.y for v in bbox_world_space)
                obj_min_z = min(v.z for v in bbox_world_space)
                obj_max_x = max(v.x for v in bbox_world_space)
                obj_max_y = max(v.y for v in bbox_world_space)
                obj_max_z = max(v.z for v in bbox_world_space)
                min_x = min(min_x, obj_min_x)
                min_y = min(min_y, obj_min_y)
                min_z = min(min_z, obj_min_z)
                max_x = max(max_x, obj_max_x)
                max_y = max(max_y, obj_max_y)
                max_z = max(max_z, obj_max_z)

        bbox = [min_x, min_y, min_z, max_x, max_y, max_z]

        # scale to unit sphere
        bbox_center = [
            (bbox[0] + bbox[3]) / 2,
            (bbox[1] + bbox[4]) / 2,
            (bbox[2] + bbox[5]) / 2,
        ]
        # 计算包围盒尺寸向量
        bbox_size = [bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]]

        # ==== 安全缩放处理 ====
        # 使用欧几里得范数计算对角线长度；极小模型需特殊处理，避免除零
        max_corner_size = (bbox_size[0] ** 2 + bbox_size[1] ** 2 + bbox_size[2] ** 2) ** 0.5

        MIN_SIZE_THRESHOLD = 1e-6  # 小于该值视为零尺寸，跳过缩放
        if max_corner_size < MIN_SIZE_THRESHOLD:
            # 记录警告并使用单位比例，防止 ZeroDivisionError
            print(f"⚠️ 模型尺寸过小({max_corner_size:.3e})，跳过自动缩放: {glb_path}")
            scale = 1.0
        else:
            scale = 2.0 / max_corner_size

        bpy.context.scene.tool_settings.transform_pivot_point = "CURSOR"
        for obj in bpy.context.selected_objects:
            if obj.parent == None:
                obj.scale = [scale, scale, scale]
                obj.location = [
                    -bbox_center[0] * scale,
                    -bbox_center[1] * scale,
                    -bbox_center[2] * scale,
                ]
                obj.rotation_euler = [0, 0, 0]

        # set world
        bpy.ops.world.new()
        new_world = bpy.data.worlds[len(bpy.data.worlds) - 1]
        bpy.context.scene.world = new_world
        new_world.node_tree.nodes["Background"].inputs[0].default_value = [
            1,
            1,
            1,
            1,
        ]

        # render setting
        bpy.context.scene.render.resolution_x = 1024
        bpy.context.scene.render.resolution_y = 1024
        bpy.context.scene.render.resolution_percentage = 100
        bpy.context.scene.render.engine = "BLENDER_EEVEE_NEXT"
        bpy.data.scenes[0].render.film_transparent = True
        
        # 色彩管理设置
        bpy.context.scene.view_settings.view_transform = 'Raw'
        bpy.context.scene.view_settings.exposure = -1         # -0.5
        bpy.data.scenes["Scene"].view_settings.gamma = 1.5      # 1.5
        # 设置材质渲染为HASHED
        for obj in bpy.context.selected_objects:
            if obj.type == "MESH":
                for material_slot in obj.material_slots:
                    if material_slot.material:
                        material = material_slot.material

                        material.use_backface_culling = False

                        if hasattr(material, "blend_method"):
                            material.blend_method = "HASHED"

                        if hasattr(material, "shadow_method"):
                            material.shadow_method = "HASHED"

                        if hasattr(material, "use_screen_refraction"):
                            material.use_screen_refraction = True

                        print(f"  🔧 设置材质 {material.name} 为透明兼容渲染")

        # 添加摄像机：左侧45度角斜上角视图（假设模型正面朝向-Y方向）
        # 计算摄像机位置：从模型左前上方观察
        camera_distance = 3.0
        # 左侧45度角视图：X轴负方向（左侧），Y轴负方向（正面），Z轴正方向（上方）
        camera_x = -camera_distance * 0.707  # 负X轴（左侧）
        camera_y = -camera_distance * 0.707  # 朝向模型正面(-Y)
        camera_z = camera_distance * 0.5     # 斜上角视图
        
        bpy.ops.object.camera_add(location=(camera_x, camera_y, camera_z))
        cam = bpy.context.active_object
        bpy.context.scene.camera = cam
        
        # 让摄像机朝向场景中心
        # 设置旋转：俯视角度约30-45度，水平旋转-45度（左侧视角）
        import math
        cam.rotation_euler = (
            math.radians(60),     # X轴旋转：俯视角度
            0,                    # Y轴旋转
            math.radians(-45)     # Z轴旋转：-45度水平角度（左侧视角）
        )
        
        # 添加照明以更好地展示模型细节（适配左侧视角）
        # 添加主光源（从左前方照射）
        bpy.ops.object.light_add(type='SUN', location=(-2, -2, 4))
        sun_light = bpy.context.active_object
        sun_light.data.energy = 2
        sun_light.rotation_euler = (math.radians(30), 0, math.radians(-45))
        
        # 添加补光（从右侧补光）
        bpy.ops.object.light_add(type='AREA', location=(1, 1, 2))
        area_light = bpy.context.active_object
        area_light.data.energy = 1.5
        area_light.data.size = 2.0
        # 设置摄像机视图对准选中的物体
        for obj in bpy.data.objects:
            obj.select_set(obj.type == "MESH")
        bpy.ops.view3d.camera_to_view_selected()

        # 输出路径
        bpy.context.scene.render.filepath = out_png
        bpy.ops.render.render(write_still=True)

        # 验证输出文件
        if os.path.exists(out_png):
            file_size = os.path.getsize(out_png)
            progress_data[glb_path] = {
                "status": "completed",
                "output_path": out_png,
                "file_size": file_size,
                "timestamp": datetime.now().isoformat()
            }
            return True, f"渲染完成: {out_png}"
        else:
            raise Exception("渲染文件未生成")

    except Exception as e:
        error_msg = f"渲染失败: {str(e)}"
        progress_data[glb_path] = {
            "status": "failed",
            "error": error_msg,
            "timestamp": datetime.now().isoformat()
        }
        return False, error_msg

def render_main():
    """渲染主函数，对DST_ROOT下的GLB进行45度包围盒渲染"""
    print("=== 开始对导出GLB进行45度包围盒渲染 ===")

    # 检查bpy模块
    if not BLENDER_AVAILABLE:
        print("❌ 无法导入bpy模块，渲染功能不可用")
        return False

    # 检查目标目录
    if not os.path.exists(DST_ROOT):
        print(f"❌ 目标目录不存在: {DST_ROOT}")
        return False

    print(f"渲染目录: {DST_ROOT}")

    # 递归收集所有GLB文件
    print("\n递归扫描GLB文件...")
    all_glb_files = find_all_glbs(DST_ROOT)
    
    if not all_glb_files:
        print("❌ 未找到任何OBJ文件")
        return False

    print(f"总计找到 {len(all_glb_files)} 个GLB文件")

    # 加载进度
    progress_data = load_render_progress()

    # 统计信息
    total_files = len(all_glb_files)
    completed_files = sum(1 for path in all_glb_files if progress_data.get(path, {}).get("status") == "completed")
    failed_files = sum(1 for path in all_glb_files if progress_data.get(path, {}).get("status") == "failed")
    skipped_files = sum(1 for path in all_glb_files if progress_data.get(path, {}).get("status") == "skipped")
    remaining_files = total_files - completed_files - failed_files - skipped_files

    print(f"总文件数: {total_files}")
    print(f"已完成: {completed_files}")
    print(f"失败: {failed_files}")
    print(f"跳过: {skipped_files}")
    print(f"剩余: {remaining_files}")

    if remaining_files == 0:
        print("✅ 所有文件已渲染完成")
        return True

    # 开始处理
    start_time = time.time()
    processed_count = 0
    success_count = 0

    for i, glb_path in enumerate(all_glb_files, 1):
        # 跳过已处理的文件（完成、跳过）
        current_status = progress_data.get(glb_path, {}).get("status")
        if current_status in ["completed", "skipped"]:
            continue

        print(f"\n[{i}/{total_files}] 正在渲染: {os.path.basename(glb_path)}")
        print(f"  路径: {glb_path}")

        # 渲染文件
        success, message = render_glb(glb_path, progress_data)
        processed_count += 1
        if success:
            success_count += 1

        print(f"  {message}")

        # 保存进度
        if processed_count % 10 == 0:  # 每10个文件保存一次进度
            save_render_progress(progress_data)
            elapsed = time.time() - start_time
            avg_time = elapsed / processed_count
            remaining = remaining_files - processed_count
            eta = remaining * avg_time
            print(f"  进度: {processed_count}/{remaining_files}, 预计剩余时间: {eta/60:.1f}分钟")

    # 最终保存进度
    save_render_progress(progress_data)

    # 统计结果
    elapsed = time.time() - start_time
    print(f"\n=== 渲染完成 ===")
    print(f"处理文件数: {processed_count}")
    print(f"成功: {success_count}")
    print(f"失败: {processed_count - success_count}")
    if processed_count > 0:
        print(f"总耗时: {elapsed/60:.1f}分钟")
        print(f"平均耗时: {elapsed/processed_count:.1f}秒/文件")

    # 显示目录统计
    directory_stats = {}
    for glb_path in all_glb_files:
        dir_path = os.path.dirname(glb_path)
        if dir_path not in directory_stats:
            directory_stats[dir_path] = {"total": 0, "completed": 0, "failed": 0, "skipped": 0}
        directory_stats[dir_path]["total"] += 1
        status = progress_data.get(glb_path, {}).get("status", "pending")
        if status == "completed":
            directory_stats[dir_path]["completed"] += 1
        elif status == "failed":
            directory_stats[dir_path]["failed"] += 1
        elif status == "skipped":
            directory_stats[dir_path]["skipped"] += 1
    
    print("\n按目录统计:")
    for dir_path, stats in directory_stats.items():
        rel_path = os.path.relpath(dir_path, DST_ROOT)
        print(f"  {rel_path}: {stats['total']} 个文件, 完成: {stats['completed']}, 失败: {stats['failed']}, 跳过: {stats['skipped']}")

    # 显示失败的文件
    failed_list = [path for path, data in progress_data.items() if data.get("status") == "failed"]
    if failed_list:
        print(f"\n失败的文件 ({len(failed_list)}个):")
        for path in failed_list[:10]:  # 只显示前10个
            print(f"  {os.path.basename(path)}: {progress_data[path].get('error', 'Unknown error')}")
        if len(failed_list) > 10:
            print(f"  ... 还有 {len(failed_list) - 10} 个失败文件")

    # 显示跳过的文件
    skipped_list = [path for path, data in progress_data.items() if data.get("status") == "skipped"]
    if skipped_list:
        print(f"\n跳过的文件 ({len(skipped_list)}个):")
        skipped_reasons = {}
        for path in skipped_list:
            reason = progress_data[path].get('reason', 'unknown')
            if reason not in skipped_reasons:
                skipped_reasons[reason] = []
            skipped_reasons[reason].append(path)
        
        for reason, paths in skipped_reasons.items():
            reason_desc = {
                'no_mesh_objects': '无Mesh对象',
                'unknown': '未知原因'
            }.get(reason, reason)
            print(f"  {reason_desc}: {len(paths)}个文件")
            # 显示几个示例文件
            for path in paths[:3]:
                print(f"    - {os.path.basename(path)}")
            if len(paths) > 3:
                print(f"    ... 还有 {len(paths) - 3} 个文件")

    return True

# =========================================================
# 主程序
# =========================================================
def main():
    """主程序"""
    global file_indexer
    try:
        log("🚀 启动UE批处理_ProPlus_优化版")
        
        # 根据模式选择目录
        if ENABLE_SEPARATE_FOLDERS:
            log("📁 使用分离文件夹模式")
            log(f"  OBJ目录: {GLB_ROOT}")
            log(f"  材质/贴图目录: {MATERIAL_TEXTURE_ROOT}")
            
            if not GLB_ROOT.exists():
                raise Exception(f"OBJ目录不存在: {GLB_ROOT}")
            if not MATERIAL_TEXTURE_ROOT.exists():
                raise Exception(f"材质/贴图目录不存在: {MATERIAL_TEXTURE_ROOT}")
            
            glb_scan_root = GLB_ROOT                  # 用来找要处理的OBJ
            glb_match_root = GLB_ROOT                 # 用来根据OBJ文件名匹配材质/贴图文件夹
            material_root = MATERIAL_TEXTURE_ROOT
            texture_root = MATERIAL_TEXTURE_ROOT
            index_root = MATERIAL_TEXTURE_ROOT  # 索引构建在材质/贴图目录
        else:
            log("📁 使用传统模式（所有文件在同一目录树下）")
            log(f"  源目录: {SRC_ROOT}")
            
            if not SRC_ROOT.exists():
                raise Exception(f"源目录不存在: {SRC_ROOT}")
            
            glb_scan_root = SRC_ROOT
            glb_match_root = SRC_ROOT
            material_root = SRC_ROOT
            texture_root = SRC_ROOT
            index_root = SRC_ROOT
        
        DST_ROOT.mkdir(parents=True, exist_ok=True)
        log(f"  导出目录: {DST_ROOT}")

        # 显示单OBJ文件夹模式状态
        if ENABLE_SINGLE_GLB_FOLDER_BUILD:
            log("📁 单OBJ文件夹模式已启用：按文件夹组织索引，每个GLB在其独立文件夹内搜索JSON和贴图", "INFO")
        else:
            log("📁 传统模式：全局搜索JSON和贴图文件", "INFO")

        # Build or load file index
        log("🔍 构建或加载文件索引...")
        file_indexer = FileIndexer(index_root, TEXTURE_EXTENSIONS, enable_folder_build=ENABLE_SINGLE_GLB_FOLDER_BUILD)
        if not file_indexer.load_index(JSON_INDEX_CSV, TEXTURE_INDEX_CSV):
            file_indexer.build_index()
            file_indexer.save_index(JSON_INDEX_CSV, TEXTURE_INDEX_CSV)

        progress = load_progress()
        
        # 获取OBJ文件列表
        # 在分离模式下，OBJ文件在OBJ_ROOT下，需要从OBJ_ROOT获取
        # 在传统模式下，如果启用单OBJ文件夹模式，可以从索引获取（但为了统一，也从obj_root获取）
        glb_files = find_obj_files(glb_scan_root)
        log(f"📊 从 {glb_scan_root} 找到 {len(glb_files)} 个OBJ文件")
        
        if not glb_files:
            log("⚠️ 未找到任何OBJ文件", "WARNING")
            return
        
        progress['stats']['total_files'] = len(glb_files)
        
        processed_files = set(progress['processed'].keys())
        # 计算未处理的文件，使用相对路径作为key
        unprocessed_files = []
        for p in glb_files:
            try:
                file_key = str(p.relative_to(glb_scan_root))
                if file_key not in processed_files:
                    unprocessed_files.append(p)
            except ValueError:
                # 如果路径不在glb_root下，使用绝对路径作为key
                file_key = str(p)
                if file_key not in processed_files:
                    unprocessed_files.append(p)
        
        if not unprocessed_files:
            log("✅ 所有文件都已处理完成")
        else:
            log(f"🔄 开始处理 {len(unprocessed_files)} 个未处理文件")
            start_time = time.time()
            
            for i, glb_path in enumerate(unprocessed_files):
                # 计算file_key，用于进度保存
                try:
                    file_key = str(glb_path.relative_to(glb_scan_root))
                except ValueError:
                    # 如果路径不在glb_root下，使用绝对路径作为key
                    file_key = str(glb_path)
                
                log(f"\n[{i+1}/{len(unprocessed_files)}] 处理: {glb_path.name}", "INFO")
                log(f"\n[{i+1}/{len(unprocessed_files)}] 处理: {glb_path}", "INFO")
                try:
                    # 传递OBJ根目录参数，用于单OBJ文件夹模式
                    result = process_single_glb(glb_path, material_root, texture_root, DST_ROOT, glb_match_root)
                    progress['processed'][file_key] = result
                    if result['success']:
                        progress['stats']['success_count'] += 1
                        log(f"✅ 成功处理: {glb_path.name}, 材质数: {result['materials_processed']}, 贴图数: {result['textures_connected']}", "INFO")
                    else:
                        progress['stats']['failed_count'] += 1
                        log(f"❌ 处理失败: {glb_path.name} - {result.get('error', 'Unknown error')}", "ERROR")
                except Exception as e:
                    progress['processed'][file_key] = {'success': False, 'error': str(e)}
                    progress['stats']['failed_count'] += 1
                    log(f"❌ [{i+1}/{len(unprocessed_files)}] 处理异常: {glb_path.name} - {e}", "ERROR")
                
                progress['stats']['processed_files'] = len(progress['processed'])
                if (i + 1) % CHECKPOINT_INTERVAL == 0:
                    save_progress(progress)
                    log(f"📊 进度检查点: {i+1}/{len(unprocessed_files)}")
            
            save_progress(progress)
            
            duration = time.time() - start_time
            stats = progress['stats']
            log("=" * 60)
            log("🎉 处理完成！")
            log(f"⏱️ 总耗时: {duration:.2f} 秒 ({duration/60:.1f} 分钟)")
            log(f"📊 总文件: {stats['total_files']}, 成功: {stats['success_count']}, 失败: {stats['failed_count']}")
            if stats['total_files'] > 0:
                log(f"   • 成功率: {stats['success_count']/stats['total_files']*100:.1f}%")
            log(f"📁 输出目录: {DST_ROOT}")
            log("=" * 60)

        # 批处理完成后，保存RM关键词统计
        rm_stats_file = DST_ROOT / "1_RM_Texture_Info.txt"
        save_rm_keyword_stats(rm_stats_file)

        # 批处理完成后，进行渲染
        log("🔄 开始对导出GLB进行渲染...")
        render_success = render_main()
        if render_success:
            log("\n🎨 渲染任务完成！")
        else:
            log("\n❌ 渲染任务失败！")
        
    except Exception as e:
        log(f"主程序异常: {e}", "ERROR")
        raise

if __name__ == '__main__':
    main()
