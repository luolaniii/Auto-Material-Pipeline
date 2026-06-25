# Auto Material Pipeline

Auto Material Pipeline 是一个面向技术美术的批量模型与材质处理工具。项目包含一个 PySide6 桌面 GUI，以及配套的 Blender、Unreal Engine、Unity 自动化脚本，用于检查模型、生成预览、运行 OBJ 到 GLB 的材质流水线，并把处理后的模型批量导入引擎。

## 主要功能

- 批量添加 `.obj`、`.fbx`、`.glb`、`.gltf` 模型文件或目录。
- 在 GUI 中查看模型格式、顶点数、面数、UV 套数和单位判断结果。
- 用 Blender 打开选中模型，方便美术快速检查。
- 用 Blender headless 运行完整流水线：查找贴图、匹配材质、连接 BSDF、导出 GLB。
- 生成白模缩略图，用于快速浏览模型列表。
- 批量导入 Unreal Engine，支持仅模型或内嵌材质模式。
- 批量导入 Unity 项目，并自动部署 Unity 端导入脚本。
- 支持通过配置自定义贴图关键字，用于更灵活地匹配 BaseColor、Metallic、Roughness、Normal 等通道。

## 目录结构

```text
.
├─ ue_obj_to_glb_pipeline.py          # Blender 侧 OBJ/材质/贴图到 GLB 的核心流水线
└─ ue_pipeline_gui/
   ├─ main.py                         # GUI 入口
   ├─ requirements.txt                # Python 依赖
   ├─ config.example.json             # 配置示例
   ├─ build.bat                       # PyInstaller 打包脚本
   ├─ UEPipelineGUI.spec              # PyInstaller 配置
   ├─ core/                           # 模型解析、配置、桥接和缩略图逻辑
   ├─ gui/                            # PySide6 界面组件
   ├─ blender_scripts/                # Blender 辅助脚本
   ├─ ue_scripts/                     # UE Python 导入脚本
   └─ unity_scripts/                  # Unity C# 导入脚本
```

## 环境要求

- Windows
- Python 3.10 或更新版本
- Blender
- Unreal Engine 5.x
- Unity 2021.3 或更新版本

Python 依赖：

```bash
pip install -r ue_pipeline_gui/requirements.txt
```

当前 GUI 依赖 `PySide6`。核心 Blender 流水线需要在 Blender Python 环境中运行，并依赖脚本里使用的 Blender API。

## 运行 GUI

在仓库根目录执行：

```bash
python ue_pipeline_gui/main.py
```

第一次运行后，程序会根据 `ue_pipeline_gui/config.example.json` 生成本机 `ue_pipeline_gui/config.json`。`config.json` 包含本机 Blender、UE、Unity 路径和资源目录，不应提交到仓库。

建议在 GUI 的“设置”中填写：

- `blender_exe`：`blender.exe` 路径。
- `ue_editor_exe`：`UnrealEditor-Cmd.exe` 或同目录 UE 编辑器路径。
- `ue_project`：目标 `.uproject` 文件。
- `pipeline_script`：`ue_obj_to_glb_pipeline.py` 路径。
- `material_search_root`：材质 JSON 或资源根目录。
- `texture_search_root`：贴图根目录。
- `unity_exe`：`Unity.exe` 路径。
- `unity_project`：目标 Unity 项目目录。

## 常用流程

1. 打开 GUI。
2. 拖入模型文件，或点击“添加文件 / 添加目录”。
3. 检查模型的 UV、单位、顶点数、面数。
4. 可选：点击“生成白模预览”生成缩略图。
5. 可选：点击“在 Blender 中打开”进行人工检查。
6. 点击“Blender 完整流水线”生成带材质的 GLB。
7. 根据目标引擎点击“导入 UE”或“导入 Unity”。

## Unreal Engine 导入说明

UE 导入逻辑位于：

- `ue_pipeline_gui/core/ue_bridge.py`
- `ue_pipeline_gui/ue_scripts/ue_batch_import.py`

工具会把待导入模型、目标内容目录、单位缩放和材质模式写入临时 JSON，然后调用 UE Python 脚本执行导入。若 UE 编辑器已经运行并启用 Python Remote Execution，工具会优先尝试远程执行；否则会启动 UE 编辑器执行脚本。

## Unity 导入说明

Unity 导入逻辑位于：

- `ue_pipeline_gui/core/unity_bridge.py`
- `ue_pipeline_gui/unity_scripts/BatchImporter.cs`

工具会把 `BatchImporter.cs` 部署到目标 Unity 项目的 `Assets/Editor/`，再通过 Unity batchmode 或项目内自动执行脚本导入模型。GLB/GLTF 导入依赖 `com.unity.cloud.gltfast`，工具会尝试写入目标项目的 `Packages/manifest.json`。

## 打包

如需打包 GUI，可在 `ue_pipeline_gui` 目录下运行：

```bat
build.bat
```

打包输出位于 `ue_pipeline_gui/dist/`，中间文件位于 `ue_pipeline_gui/build/`。这些目录是生成物，不提交到源码仓库。

## 仓库内容说明

本仓库保留源码、脚本、示例配置和使用说明。以下内容不应提交：

- `__pycache__` 和 `.pyc`
- `ue_pipeline_gui/build/`
- `ue_pipeline_gui/dist/`
- 本机 `ue_pipeline_gui/config.json`
- 运行日志、缩略图缓存和流水线输出目录

如果需要分发可执行文件，建议单独打包发布，不要把 PyInstaller 输出目录直接提交到源码分支。
