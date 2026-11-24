# setup_lab_pc.ps1 — Automated Environment & Dependency Verification for Saturdays Studio Lab PCs
Write-Host "=====================================================================" -ForegroundColor Yellow
Write-Host "  Saturdays AI Studio — Lab PC Hardware & Environment Setup" -ForegroundColor White
Write-Host "=====================================================================" -ForegroundColor Yellow

# 1. Check Python
Write-Host "`n[1/5] Checking Python..." -ForegroundColor Cyan
try {
    $pyVer = python --version 2>&1
    Write-Host "  ✓ Found: $pyVer" -ForegroundColor Green
} catch {
    Write-Host "  ❌ Python is not installed or not in PATH." -ForegroundColor Red
}

# 2. Check NVIDIA GPU & CUDA
Write-Host "`n[2/5] Checking NVIDIA GPU (nvidia-smi)..." -ForegroundColor Cyan
try {
    $gpu = nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>&1
    Write-Host "  ✓ GPU Detected: $gpu" -ForegroundColor Green
} catch {
    Write-Host "  ❌ nvidia-smi failed. Ensure NVIDIA GPU drivers are installed." -ForegroundColor Red
}

# 3. Check Python Dependencies
Write-Host "`n[3/5] Installing Python Dependencies..." -ForegroundColor Cyan
pip install -r requirements.txt --quiet
Write-Host "  ✓ Dependencies installed." -ForegroundColor Green

# 4. Check Ollama & Download Model
Write-Host "`n[4/5] Checking Ollama & qwen2.5-coder:7b..." -ForegroundColor Cyan
try {
    $models = ollama list 2>&1
    if ($models -match "qwen2.5-coder:7b") {
        Write-Host "  ✓ qwen2.5-coder:7b is already installed." -ForegroundColor Green
    } else {
        Write-Host "  Pulling qwen2.5-coder:7b..." -ForegroundColor Yellow
        ollama pull qwen2.5-coder:7b
        Write-Host "  ✓ qwen2.5-coder:7b downloaded." -ForegroundColor Green
    }
} catch {
    Write-Host "  ⚠️ Ollama not detected in PATH. Install from https://ollama.com" -ForegroundColor Yellow
}

# 5. Check ComfyUI Models
Write-Host "`n[5/5] Checking ComfyUI Model Files..." -ForegroundColor Cyan
$comfyBase = "$env:LOCALAPPDATA\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI"
if (Test-Path $comfyBase) {
    Write-Host "  ✓ ComfyUI install found at $comfyBase" -ForegroundColor Green
    $unet = Test-Path "$comfyBase\models\unet\krea2_turbo_fp8_scaled.safetensors"
    $clip = Test-Path "$comfyBase\models\clip\qwen3vl_4b_fp8_scaled.safetensors"
    $vae = Test-Path "$comfyBase\models\vae\qwen_image_vae.safetensors"
    Write-Host "    - UNet (krea2_turbo_fp8_scaled.safetensors): $(if ($unet) {'✓ Present'} else {'❌ Missing'})"
    Write-Host "    - CLIP (qwen3vl_4b_fp8_scaled.safetensors): $(if ($clip) {'✓ Present'} else {'❌ Missing'})"
    Write-Host "    - VAE (qwen_image_vae.safetensors): $(if ($vae) {'✓ Present'} else {'❌ Missing'})"
} else {
    Write-Host "  ℹ️ Copy ComfyUI directory from the primary GPU machine if not already installed." -ForegroundColor Gray
}

Write-Host "`n=====================================================================" -ForegroundColor Yellow
Write-Host "  Setup verification complete! Run .\start_studio.bat to launch." -ForegroundColor Green
Write-Host "=====================================================================" -ForegroundColor Yellow
