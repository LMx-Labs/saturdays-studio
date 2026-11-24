# Saturdays AI Studio - PowerShell Launcher
Write-Host "=====================================================================" -ForegroundColor Yellow
Write-Host "  Saturdays.co.in — Autonomous GPU Menu AI Studio" -ForegroundColor White
Write-Host "  Local NVIDIA CUDA Workstation (RTX A4000 - 16 GB VRAM)" -ForegroundColor Cyan
Write-Host "=====================================================================" -ForegroundColor Yellow

# 1. ComfyUI check
try {
    $c = Invoke-RestMethod -Uri "http://127.0.0.1:8188/system_stats" -TimeoutSec 2 -ErrorAction Stop
    Write-Host "[OK] ComfyUI (Port 8188) is online and ready." -ForegroundColor Green
} catch {
    Write-Host "[WARNING] ComfyUI is not reachable on http://127.0.0.1:8188." -ForegroundColor Red
    Write-Host "          Start ComfyUI without --highvram before rendering images." -ForegroundColor Gray
}

# 2. Ollama check
try {
    $o = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 2 -ErrorAction Stop
    Write-Host "[OK] Ollama (Port 11434) is online and ready." -ForegroundColor Green
} catch {
    Write-Host "[WARNING] Ollama is not reachable on http://localhost:11434." -ForegroundColor Red
    Write-Host "          Run: ollama run qwen2.5-coder:7b" -ForegroundColor Gray
}

# 3. Launch Dashboard
Write-Host "`nLaunching Studio Dashboard on http://localhost:8000..." -ForegroundColor Cyan
python pipeline_dashboard.py --port 8000
