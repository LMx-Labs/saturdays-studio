@echo off
title Saturdays AI Studio — 1-Click Lab GPU Setup & Launcher
color 0A
cls
echo ===============================================================================
echo   Saturdays.co.in — Autonomous GPU Menu AI Studio
echo   1-Click Setup ^& Engine Launcher (NVIDIA RTX A4000 CUDA Workstations)
echo ===============================================================================
echo.

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

:: -----------------------------------------------------------------------------
:: Step 1: Check Python Environment & Required Packages
:: -----------------------------------------------------------------------------
echo [1/5] Checking Python Environment & Dependencies...
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python was not found in PATH!
    echo Please install Python 3.10+ or add it to Windows PATH.
    pause
    exit /b 1
)

python -c "import fastapi, uvicorn, pydantic" 2>nul
if %errorlevel% neq 0 (
    echo [INFO] First-time setup detected. Installing required packages...
    pip install fastapi uvicorn pydantic requests
) else (
    echo [OK] Python dependencies verified.
)

:: -----------------------------------------------------------------------------
:: Step 2: Check & Launch Ollama (Port 11434)
:: -----------------------------------------------------------------------------
echo.
echo [2/5] Checking Ollama Sensory Prompt Engine (Port 11434)...
curl -s -m 2 http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Ollama is not active. Starting Ollama in background...
    where ollama >nul 2>&1
    if %errorlevel% equ 0 (
        start /min "Ollama Engine" ollama serve
    ) else if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
        start /min "Ollama Engine" "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve
    ) else if exist "%PROGRAMFILES%\Ollama\ollama.exe" (
        start /min "Ollama Engine" "%PROGRAMFILES%\Ollama\ollama.exe" serve
    ) else (
        echo [WARNING] Ollama executable not found in PATH.
        echo Procedural sensory prompts will be used as fallback.
    )
    timeout /t 2 >nul
) else (
    echo [OK] Ollama is already active on port 11434.
)

:: -----------------------------------------------------------------------------
:: Step 3: Check & Launch ComfyUI on NVIDIA RTX A4000 (Port 8188)
:: -----------------------------------------------------------------------------
echo.
echo [3/5] Checking ComfyUI GPU Synthesis Engine (Port 8188)...
curl -s -m 2 http://127.0.0.1:8188/system_stats >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] ComfyUI is not active. Locating ComfyUI installation...
    set "COMFY_FOUND=0"
    
    :: Path A: Standard Desktop install on Lab PCs
    set "CPATH_A=C:\Users\Student\AppData\Local\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI"
    if exist "%CPATH_A%\main.py" (
        set "COMFY_FOUND=1"
        echo [INFO] Found ComfyUI at %CPATH_A%
        start /min "ComfyUI GPU Engine" "%CPATH_A%\.venv\Scripts\python.exe" "%CPATH_A%\main.py" --listen 127.0.0.1 --port 8188 --extra-model-paths-config "%CPATH_A%\extra_model_paths.yaml" --dont-print-server
    )
    
    :: Path B: LocalAppData path fallback
    if "%COMFY_FOUND%"=="0" (
        set "CPATH_B=%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI"
        if exist "%CPATH_B%\main.py" (
            set "COMFY_FOUND=1"
            echo [INFO] Found ComfyUI at %CPATH_B%
            start /min "ComfyUI GPU Engine" "%CPATH_B%\.venv\Scripts\python.exe" "%CPATH_B%\main.py" --listen 127.0.0.1 --port 8188 --extra-model-paths-config "%CPATH_B%\extra_model_paths.yaml" --dont-print-server
        )
    )
    
    if "%COMFY_FOUND%"=="0" (
        echo [WARNING] ComfyUI directory not found at standard path.
        echo If ComfyUI is installed in a custom location, please start it manually.
    ) else (
        echo [INFO] Initializing ComfyUI on NVIDIA RTX A4000 GPU...
        timeout /t 4 >nul
    )
) else (
    echo [OK] ComfyUI GPU engine is already active on port 8188.
)

:: -----------------------------------------------------------------------------
:: Step 4: Clean Old Process & Start Saturdays Studio Daemon (Port 8000)
:: -----------------------------------------------------------------------------
echo.
echo [4/5] Starting Saturdays AI Studio Local Server (Port 8000)...
powershell -Command "$c = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -gt 0 }; if ($c) { Stop-Process -Id ($c.OwningProcess | Select-Object -Unique) -Force -ErrorAction SilentlyContinue }" >nul 2>&1
timeout /t 1 >nul

start /min "Saturdays Studio Server" python pipeline_dashboard.py --port 8000
timeout /t 2 >nul
echo [OK] Saturdays Studio server running on http://localhost:8000.

:: -----------------------------------------------------------------------------
:: Step 5: Launch Browser to Studio
:: -----------------------------------------------------------------------------
echo.
echo [5/5] Opening Saturdays AI Studio in browser...
timeout /t 1 >nul
start "" "https://lmx-saturdays-studio.web.app"

echo.
echo ===============================================================================
echo   Setup Complete!
echo   * Studio Dashboard:  https://lmx-saturdays-studio.web.app
echo   * Standalone WebApp: https://lmx-saturdays-studio.web.app
echo   * Local GPU Engine:  http://localhost:8000
echo.
echo   All services are running in background. You can safely minimize this window.
echo ===============================================================================
timeout /t 5 >nul
exit /b 0
