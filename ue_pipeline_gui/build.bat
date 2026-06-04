@echo off
setlocal

REM One-click packaging: builds dist\UEPipelineGUI\UEPipelineGUI.exe
REM Requires Python 3.10+ and "pip install -r requirements.txt pyinstaller"

set HERE=%~dp0
cd /d "%HERE%"

where pyinstaller >nul 2>nul
if errorlevel 1 (
    echo [Build] pyinstaller not found, installing...
    pip install pyinstaller
    if errorlevel 1 (
        echo [Build] Failed to install pyinstaller. Run: pip install pyinstaller
        exit /b 1
    )
)

echo [Build] Installing dependencies
pip install -r requirements.txt
if errorlevel 1 (
    echo [Build] Dependency install failed
    exit /b 1
)

echo [Build] Running pyinstaller
pyinstaller ^
    --noconfirm ^
    --windowed ^
    --onedir ^
    --name "UEPipelineGUI" ^
    --add-data "blender_scripts;blender_scripts" ^
    --add-data "ue_scripts;ue_scripts" ^
    --add-data "unity_scripts;unity_scripts" ^
    --add-data "core/keyword_classifier.py;ue_scripts" ^
    --add-data "config.example.json;." ^
    --hidden-import "uuid" ^
    --hidden-import "socket" ^
    --hidden-import "logging" ^
    --hidden-import "threading" ^
    --hidden-import "pygltflib" ^
    --hidden-import "PySide6.QtCore" ^
    --hidden-import "PySide6.QtGui" ^
    --hidden-import "PySide6.QtWidgets" ^
    main.py

if errorlevel 1 (
    echo [Build] Packaging failed
    exit /b 1
)

echo.
echo [Build] Done. Executable is at:
echo   %HERE%dist\UEPipelineGUI\UEPipelineGUI.exe
echo.
echo Copy the whole dist\UEPipelineGUI folder to artists (no Python needed).

endlocal
