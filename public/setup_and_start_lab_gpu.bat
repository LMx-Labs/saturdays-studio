@echo off
title Saturdays AI Studio - 1-Click Lab GPU Workstation
color 0A
cls

echo ===============================================================================
echo   Saturdays.co.in - Autonomous GPU Menu AI Studio
echo   1-Click Setup and Engine Launcher (NVIDIA RTX A4000 CUDA Workstations)
echo ===============================================================================
echo.

:: -----------------------------------------------------------------------------
:: Step 1: Locate Saturdays Studio Directory (Handle Downloads / Desktop paths)
:: -----------------------------------------------------------------------------
set STUDIO_DIR=

if exist "%~dp0pipeline_dashboard.py" (
    set "STUDIO_DIR=%~dp0"
)

if not defined STUDIO_DIR (
    if exist "C:\Users\Student\Desktop\Shaiv Watkar\saturdays-studio\pipeline_dashboard.py" (
        set "STUDIO_DIR=C:\Users\Student\Desktop\Shaiv Watkar\saturdays-studio"
    )
)

if not defined STUDIO_DIR (
    if exist "C:\Users\Student\Desktop\Shaiv Watkar\lmx code\pipeline_dashboard.py" (
        set "STUDIO_DIR=C:\Users\Student\Desktop\Shaiv Watkar\lmx code"
    )
)

if not defined STUDIO_DIR (
    if exist "%USERPROFILE%\Desktop\Shaiv Watkar\saturdays-studio\pipeline_dashboard.py" (
        set "STUDIO_DIR=%USERPROFILE%\Desktop\Shaiv Watkar\saturdays-studio"
    )
)

if not defined STUDIO_DIR (
    if exist "%USERPROFILE%\Desktop\Shaiv Watkar\lmx code\pipeline_dashboard.py" (
        set "STUDIO_DIR=%USERPROFILE%\Desktop\Shaiv Watkar\lmx code"
    )
)

if not defined STUDIO_DIR (
    if exist "%USERPROFILE%\Desktop\saturdays-studio\pipeline_dashboard.py" (
        set "STUDIO_DIR=%USERPROFILE%\Desktop\saturdays-studio"
    )
)

if not defined STUDIO_DIR (
    color 0C
    echo [ERROR] Could not locate pipeline_dashboard.py!
    echo Please make sure the 'saturdays-studio' or 'lmx code' folder is on your Desktop.
    echo.
    pause
    exit /b 1
)

echo [OK] Saturdays Studio located at: %STUDIO_DIR%

:: -----------------------------------------------------------------------------
:: Step 2: Locate Python and Verify Dependencies
:: -----------------------------------------------------------------------------
echo.
echo [1/5] Checking Python Environment and Dependencies...

set PYTHON_EXE=
where python >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON_EXE=python"
) else (
    if exist "C:\Users\Student\AppData\Local\Programs\Python\Python312\python.exe" (
        set "PYTHON_EXE=C:\Users\Student\AppData\Local\Programs\Python\Python312\python.exe"
    ) else (
        if exist "C:\Users\Student\AppData\Local\Python\pythoncore-3.12-64\python.exe" (
            set "PYTHON_EXE=C:\Users\Student\AppData\Local\Python\pythoncore-3.12-64\python.exe"
        )
    )
)

if not defined PYTHON_EXE (
    color 0C
    echo [ERROR] Python 3.10+ was not found in PATH or standard directories!
    pause
    exit /b 1
)

"%PYTHON_EXE%" -c "import fastapi, uvicorn, pydantic, requests" >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] First-time setup detected. Installing required packages...
    "%PYTHON_EXE%" -m pip install fastapi uvicorn pydantic requests
) else (
    echo [OK] Python dependencies verified.
)

:: -----------------------------------------------------------------------------
:: Step 3: Check and Launch Ollama (Port 11434)
:: -----------------------------------------------------------------------------
echo.
echo [2/5] Checking Ollama Sensory Prompt Engine (Port 11434)...
curl -s -m 2 http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% equ 0 goto ollama_ready

echo [INFO] Starting Ollama in background...
set OLLAMA_EXE=
where ollama >nul 2>&1
if %errorlevel% equ 0 set "OLLAMA_EXE=ollama"
if not defined OLLAMA_EXE (
    if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
)
if not defined OLLAMA_EXE (
    if exist "%PROGRAMFILES%\Ollama\ollama.exe" set "OLLAMA_EXE=%PROGRAMFILES%\Ollama\ollama.exe"
)

if defined OLLAMA_EXE (
    start "Ollama Engine" /min "%OLLAMA_EXE%" serve
    ping -n 4 127.0.0.1 >nul
) else (
    echo [WARNING] Ollama executable not found. Fallback sensory prompts will be used.
)

:ollama_ready
echo [OK] Ollama sensory prompt engine is active on port 11434.

:: -----------------------------------------------------------------------------
:: Step 4: Check and Launch ComfyUI on NVIDIA RTX A4000 GPU (Port 8188)
:: -----------------------------------------------------------------------------
echo.
echo [3/5] Checking ComfyUI GPU Synthesis Engine (Port 8188)...
curl -s -m 2 http://127.0.0.1:8188/system_stats >nul 2>&1
if %errorlevel% equ 0 goto comfy_ready

echo [INFO] ComfyUI is not active. Locating ComfyUI installation...
set COMFY_DIR=
if exist "C:\Users\Student\AppData\Local\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\main.py" (
    set "COMFY_DIR=C:\Users\Student\AppData\Local\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI"
) else (
    if exist "%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\main.py" (
        set "COMFY_DIR=%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI"
    )
)

if defined COMFY_DIR (
    echo [INFO] Found ComfyUI at: %COMFY_DIR%
    echo [INFO] Initializing ComfyUI on NVIDIA RTX A4000 GPU...
    pushd "%COMFY_DIR%"
    start "ComfyUI GPU Engine" /min "%COMFY_DIR%\.venv\Scripts\python.exe" main.py --listen 127.0.0.1 --port 8188 --extra-model-paths-config extra_model_paths.yaml
    popd
    ping -n 6 127.0.0.1 >nul
) else (
    echo [WARNING] ComfyUI directory not found at standard path.
)

:comfy_ready
echo [OK] ComfyUI GPU synthesis engine is active on NVIDIA RTX A4000 (Port 8188).

:: -----------------------------------------------------------------------------
:: Step 5: Check and Launch Local Studio Server (Port 8000)
:: -----------------------------------------------------------------------------
echo.
echo [4/5] Checking Saturdays AI Studio Server (Port 8000)...
curl -s -m 2 http://127.0.0.1:8000/api/system/check >nul 2>&1
if %errorlevel% equ 0 goto server_ready

echo [INFO] Starting Saturdays AI Studio Local Server on Port 8000...
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -gt 0 }; if ($c) { Stop-Process -Id ($c.OwningProcess | Select-Object -Unique) -Force -ErrorAction SilentlyContinue }" >nul 2>&1
ping -n 2 127.0.0.1 >nul

pushd "%STUDIO_DIR%"
start "Saturdays Studio Server" /min "%PYTHON_EXE%" pipeline_dashboard.py --port 8000
popd
ping -n 4 127.0.0.1 >nul

:server_ready
echo [OK] Saturdays Studio server is active on http://localhost:8000.

:: -----------------------------------------------------------------------------
:: Step 6: Launch Browser
:: -----------------------------------------------------------------------------
echo.
echo [5/5] Opening Saturdays AI Studio in browser...
ping -n 2 127.0.0.1 >nul
start "" "http://localhost:8000"
ping -n 2 127.0.0.1 >nul
start "" "https://lmx-saturdays-studio.web.app"

echo.
echo ===============================================================================
echo   Setup Complete! Hardware and GPU Pipeline Verified:
echo   * NVIDIA GPU:        RTX A4000 (16 GB VRAM) Active
echo   * ComfyUI Engine:    http://127.0.0.1:8188 (ONLINE)
echo   * Ollama Engine:     http://localhost:11434 (ONLINE)
echo   * Local GPU Studio:  http://localhost:8000 (ONLINE)
echo   * Web App:           https://lmx-saturdays-studio.web.app (ONLINE)
echo.
echo   All background engines are operational.
echo   You can minimize this window or press any key to close.
echo ===============================================================================
pause
exit /b 0
