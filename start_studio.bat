@echo off
title Saturdays AI Studio — Local CUDA Workstation
cls
echo =====================================================================
echo   Saturdays.co.in — Autonomous GPU Menu AI Studio
echo   Local NVIDIA CUDA Workstation (RTX A4000 - 16 GB VRAM)
echo =====================================================================
echo.
echo Checking dependencies...
python -c "import fastapi, uvicorn, pydantic" 2>nul
if %errorlevel% neq 0 (
    echo [INFO] Installing required packages from requirements.txt...
    pip install -r requirements.txt
)

echo.
echo [1/3] Verifying ComfyUI (Port 8188)...
curl -s http://127.0.0.1:8188/system_stats >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] ComfyUI does not appear to be running on http://127.0.0.1:8188!
    echo Please make sure ComfyUI is started with Krea2 Turbo FP8 model.
) else (
    echo [OK] ComfyUI GPU synthesis engine is reachable.
)

echo [2/3] Verifying Ollama (Port 11434)...
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] Ollama does not appear to be running on http://localhost:11434!
    echo Please make sure Ollama is running with qwen2.5-coder:7b.
) else (
    echo [OK] Ollama sensory prompt engine is reachable.
)

echo.
echo [3/3] Launching Saturdays AI Studio Dashboard on http://localhost:8000...
echo.
python pipeline_dashboard.py --port 8000
pause
