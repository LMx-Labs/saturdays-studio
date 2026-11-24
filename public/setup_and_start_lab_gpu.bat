@echo off
title Saturdays AI Studio — 1-Click Lab GPU Workstation
cls
echo =====================================================================
echo   Saturdays.co.in — Autonomous GPU Menu AI Studio
echo   Local NVIDIA CUDA Workstation (RTX A4000 - 16 GB VRAM)
echo =====================================================================
echo.

:: 1. Check & start Ollama if not running
echo [1/4] Checking Ollama (Port 11434)...
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Starting Ollama in background...
    start /min "" ollama serve
    timeout /t 2 >nul
) else (
    echo [OK] Ollama sensory prompt engine is active.
)

:: 2. Check & start ComfyUI on RTX A4000 GPU if not running
echo [2/4] Checking ComfyUI (Port 8188)...
curl -s http://127.0.0.1:8188/system_stats >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Launching ComfyUI engine on NVIDIA RTX A4000...
    set "COMFY_PY=C:\Users\Student\AppData\Local\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\.venv\Scripts\python.exe"
    set "COMFY_MAIN=C:\Users\Student\AppData\Local\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\main.py"
    set "COMFY_YAML=C:\Users\Student\AppData\Local\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\extra_model_paths.yaml"
    if exist "%COMFY_PY%" (
        start /min "ComfyUI GPU Engine" "%COMFY_PY%" "%COMFY_MAIN%" --listen 127.0.0.1 --port 8188 --extra-model-paths-config "%COMFY_YAML%" --dont-print-server
        timeout /t 3 >nul
    ) else (
        echo [WARNING] ComfyUI path not found at standard location. If on another Lab PC, launch ComfyUI Desktop.
    )
) else (
    echo [OK] ComfyUI GPU synthesis engine is active.
)

:: 3. Launch local Studio Server on Port 8000
echo [3/4] Starting Local Studio API Server (Port 8000)...
set "STUDIO_DIR=%~dp0"
cd /d "%STUDIO_DIR%"
start /min "Saturdays Studio Server" python pipeline_dashboard.py --port 8000

:: 4. Launch browser directly to https://saturdays.co.in/studio
echo [4/4] Opening Saturdays AI Studio in browser...
timeout /t 2 >nul
start "" "https://saturdays.co.in/studio"

echo.
echo =====================================================================
echo   Studio is running! Connected to local NVIDIA RTX A4000 GPU.
echo   You can close this window or keep it open.
echo =====================================================================
