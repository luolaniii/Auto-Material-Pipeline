// BatchImporter.cs - deployed to <UnityProject>/Assets/Editor/ by UE Pipeline GUI.
// Unity side only imports model files. Material/texture matching is handled in Blender,
// so Unity does not search texture folders or rebuild materials.

using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

[InitializeOnLoad]
public static class BatchImporter
{
    const string PendingConfigRel = "ProjectSettings/UEPipelineGUI_import.json";
    const float StUnityMetallic = 0.0f;
    const float StUnitySmoothness = 0.18f;
    static bool _autoRunStarted = false;
    static double _nextPendingPoll = 0.0;

    [Serializable]
    class ImportConfig
    {
        public List<string> files = new List<string>();
        public string dest_subdir = "Imports";
        public string material_mode = "embedded";
        public float obj_scale = 1f;
        public bool glb_supported = false;
        public string log_file = "";
    }

    static string _logFile = "";

    static BatchImporter()
    {
        EditorApplication.delayCall += AutoRunPendingConfig;
        EditorApplication.update += PollPendingConfig;
    }

    static void AppendLogFile(string line)
    {
        if (string.IsNullOrEmpty(_logFile)) return;
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(_logFile));
            File.AppendAllText(_logFile, line + Environment.NewLine);
        }
        catch { }
    }

    static void Log(string m)
    {
        string line = "[Unity-Pipeline] " + m;
        Debug.Log(line);
        AppendLogFile(line);
    }

    static void Warn(string m)
    {
        string line = "[Unity-Pipeline][WARN] " + m;
        Debug.LogWarning(line);
        AppendLogFile(line);
    }

    static string ProjectRoot()
    {
        return Directory.GetParent(Application.dataPath).FullName;
    }

    static string PendingConfigPath()
    {
        return Path.Combine(ProjectRoot(), PendingConfigRel);
    }

    static void AutoRunPendingConfig()
    {
        if (_autoRunStarted) return;
        string cfgPath = PendingConfigPath();
        if (!File.Exists(cfgPath)) return;
        _autoRunStarted = true;
        RunWithConfig(cfgPath, false, true);
    }

    static void PollPendingConfig()
    {
        if (_autoRunStarted) return;
        if (EditorApplication.timeSinceStartup < _nextPendingPoll) return;
        _nextPendingPoll = EditorApplication.timeSinceStartup + 1.0;
        AutoRunPendingConfig();
    }

    public static void Run()
    {
        string cfgPath = Environment.GetEnvironmentVariable("UE_PIPELINE_UNITY_CONFIG");
        if (string.IsNullOrEmpty(cfgPath) || !File.Exists(cfgPath))
        {
            Warn("Missing config: env UE_PIPELINE_UNITY_CONFIG");
            EditorApplication.Exit(1);
            return;
        }
        RunWithConfig(cfgPath, true, false);
    }

    static void RunWithConfig(string cfgPath, bool quitWhenDone, bool deleteConfigWhenDone)
    {
        int exitCode = 0;
        try
        {
            ImportConfig cfg = JsonUtility.FromJson<ImportConfig>(File.ReadAllText(cfgPath));
            _logFile = cfg.log_file ?? "";
            if (!string.IsNullOrEmpty(_logFile))
            {
                try { File.WriteAllText(_logFile, "=== Unity Pipeline import " + DateTime.Now + Environment.NewLine); } catch { }
            }
            if (cfg.files == null) cfg.files = new List<string>();
            if (cfg.material_mode != "none" && cfg.material_mode != "embedded")
                cfg.material_mode = "embedded";

            string subdir = string.IsNullOrEmpty(cfg.dest_subdir) ? "Imports" : cfg.dest_subdir;
            string meshDirAbs = Path.Combine(Application.dataPath, subdir, "Meshes");
            Directory.CreateDirectory(meshDirAbs);
            string meshDirRel = "Assets/" + subdir + "/Meshes";

            Log(string.Format("Start import: {0} files, mode={1}, objScale={2}",
                cfg.files.Count, cfg.material_mode, cfg.obj_scale));

            List<string> copied = new List<string>();
            for (int i = 0; i < cfg.files.Count; i++)
            {
                string f = cfg.files[i];
                string ext = Path.GetExtension(f).ToLowerInvariant();
                Log(string.Format("[{0}/{1}] Copy start: {2}", i + 1, cfg.files.Count, Path.GetFileName(f)));
                if ((ext == ".glb" || ext == ".gltf") && !cfg.glb_supported)
                {
                    Warn("Skip GLB/GLTF because gltfast is not installed: " + Path.GetFileName(f));
                    continue;
                }
                if (!File.Exists(f))
                {
                    Warn("Source file missing: " + f);
                    continue;
                }

                string name = Path.GetFileName(f);
                string dstAbs = Path.Combine(meshDirAbs, name);
                try
                {
                    File.Copy(f, dstAbs, true);
                    copied.Add(meshDirRel + "/" + name);
                    Log(string.Format("[{0}/{1}] Copy done: {2}", i + 1, cfg.files.Count, name));
                }
                catch (Exception e)
                {
                    Warn("Copy failed " + name + ": " + e.Message);
                }
            }

            Log("AssetDatabase.Refresh begin");
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            Log("AssetDatabase.Refresh done");
            Log("Copied " + copied.Count + " model files");

            for (int i = 0; i < copied.Count; i++)
            {
                string rel = copied[i];
                Log(string.Format("[{0}/{1}] Configure importer: {2}", i + 1, copied.Count, rel));
                ConfigureImporter(rel, cfg);
                FixImportedStMaterials(rel, cfg);
            }

            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            Log("Done");
        }
        catch (Exception e)
        {
            Debug.LogError("[Unity-Pipeline] Fatal: " + e);
            exitCode = 1;
        }
        finally
        {
            if (deleteConfigWhenDone)
            {
                try { File.Delete(cfgPath); } catch { }
            }
            if (!quitWhenDone) _autoRunStarted = false;
        }

        if (quitWhenDone) EditorApplication.Exit(exitCode);
    }

    static void ConfigureImporter(string rel, ImportConfig cfg)
    {
        ModelImporter mi = AssetImporter.GetAtPath(rel) as ModelImporter;
        if (mi == null)
        {
            Log("Not a ModelImporter, material postprocess may still run: " + rel);
            return;
        }

        string ext = Path.GetExtension(rel).ToLowerInvariant();
        try
        {
            if (ext == ".obj" && cfg.obj_scale > 0f)
                mi.globalScale = cfg.obj_scale;
            if (ext == ".obj")
            {
                mi.importAnimation = false;
                mi.animationType = ModelImporterAnimationType.None;
            }
            else
            {
                mi.importAnimation = true;
                mi.animationType = ModelImporterAnimationType.Generic;
                mi.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
            }
        }
        catch (Exception e)
        {
            Warn("Basic importer settings failed " + rel + ": " + e.Message);
        }

        try
        {
            if (cfg.material_mode == "embedded")
            {
                mi.materialImportMode = ModelImporterMaterialImportMode.ImportStandard;
                mi.materialLocation = ModelImporterMaterialLocation.External;
            }
            else
            {
                mi.materialImportMode = ModelImporterMaterialImportMode.None;
            }
        }
        catch (Exception e)
        {
            Warn("Material importer settings failed: " + e.Message);
        }

        try
        {
            mi.SaveAndReimport();
        }
        catch (Exception e)
        {
            Warn("Reimport failed " + rel + ": " + e.Message);
        }
    }

    static bool IsStAsset(string rel)
    {
        string name = Path.GetFileNameWithoutExtension(rel);
        return name.StartsWith("ST", StringComparison.OrdinalIgnoreCase);
    }

    static string SafeAssetName(string value)
    {
        foreach (char c in Path.GetInvalidFileNameChars())
            value = value.Replace(c, '_');
        return value.Replace('/', '_').Replace('\\', '_').Replace(':', '_');
    }

    static bool SetFloatIfExists(Material mat, string prop, float value)
    {
        if (!mat.HasProperty(prop)) return false;
        mat.SetFloat(prop, value);
        return true;
    }

    static void ApplyStUnityMaterialDefaults(Material mat)
    {
        SetFloatIfExists(mat, "_Metallic", StUnityMetallic);
        SetFloatIfExists(mat, "_Glossiness", StUnitySmoothness);
        SetFloatIfExists(mat, "_Smoothness", StUnitySmoothness);
        SetFloatIfExists(mat, "_Roughness", 1.0f - StUnitySmoothness);
        SetFloatIfExists(mat, "metallicFactor", StUnityMetallic);
        SetFloatIfExists(mat, "roughnessFactor", 1.0f - StUnitySmoothness);

        if (mat.HasProperty("_WorkflowMode"))
            mat.SetFloat("_WorkflowMode", 1.0f);
        if (mat.HasProperty("_GlossMapScale"))
            mat.SetFloat("_GlossMapScale", StUnitySmoothness);
        if (mat.HasProperty("_SmoothnessTextureChannel"))
            mat.SetFloat("_SmoothnessTextureChannel", 0.0f);

        mat.EnableKeyword("_NORMALMAP");
        mat.DisableKeyword("_METALLICGLOSSMAP");
        EditorUtility.SetDirty(mat);
    }

    static void FixImportedStMaterials(string rel, ImportConfig cfg)
    {
        if (!IsStAsset(rel)) return;

        string ext = Path.GetExtension(rel).ToLowerInvariant();
        if (ext != ".glb" && ext != ".gltf" && ext != ".obj")
            return;

        UnityEngine.Object[] subAssets = AssetDatabase.LoadAllAssetsAtPath(rel);
        List<Material> sourceMaterials = new List<Material>();
        foreach (UnityEngine.Object obj in subAssets)
        {
            Material mat = obj as Material;
            if (mat != null)
                sourceMaterials.Add(mat);
        }

        if (sourceMaterials.Count == 0)
        {
            Log("ST material fix skipped, no material subassets: " + rel);
            return;
        }

        string materialDir = Path.GetDirectoryName(rel).Replace('\\', '/') + "/Materials";
        if (!AssetDatabase.IsValidFolder(materialDir))
            AssetDatabase.CreateFolder(Path.GetDirectoryName(rel).Replace('\\', '/'), "Materials");

        AssetImporter importer = AssetImporter.GetAtPath(rel);
        bool remapped = false;
        foreach (Material src in sourceMaterials)
        {
            string matName = SafeAssetName(Path.GetFileNameWithoutExtension(rel) + "_" + src.name + "_UnityFixed");
            string matPath = AssetDatabase.GenerateUniqueAssetPath(materialDir + "/" + matName + ".mat");
            string existingPath = materialDir + "/" + matName + ".mat";
            Material dst = AssetDatabase.LoadAssetAtPath<Material>(existingPath);
            if (dst == null)
            {
                dst = new Material(src);
                AssetDatabase.CreateAsset(dst, matPath);
            }
            else
            {
                dst.CopyPropertiesFromMaterial(src);
            }

            ApplyStUnityMaterialDefaults(dst);

            if (importer != null)
            {
                try
                {
                    var id = new AssetImporter.SourceAssetIdentifier(typeof(Material), src.name);
                    importer.AddRemap(id, dst);
                    remapped = true;
                    Log(string.Format("ST material remap: {0} -> {1} (Metallic={2}, Smoothness={3})",
                        src.name, AssetDatabase.GetAssetPath(dst), StUnityMetallic, StUnitySmoothness));
                }
                catch (Exception e)
                {
                    Warn("ST material remap failed " + src.name + ": " + e.Message);
                }
            }
        }

        AssetDatabase.SaveAssets();
        if (remapped && importer != null)
        {
            try
            {
                importer.SaveAndReimport();
                Log("ST material remap reimport done: " + rel);
            }
            catch (Exception e)
            {
                Warn("ST material remap reimport failed " + rel + ": " + e.Message);
            }
        }
    }
}
