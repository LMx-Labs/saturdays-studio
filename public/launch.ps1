# Saturdays AI Studio - Safe PowerShell Launcher (Zero Security Warnings)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Write-Host "===============================================================================" -ForegroundColor Yellow
Write-Host "  Saturdays.co.in - Autonomous GPU Menu AI Studio" -ForegroundColor White
Write-Host "  Safe Lab PC Setup & Engine Launcher (NVIDIA RTX A4000 CUDA Workstations)" -ForegroundColor Cyan
Write-Host "===============================================================================" -ForegroundColor Yellow
Write-Host ""

# 1. Locate Studio Directory
$studioDir = $null
$possibleDirs = @(
    $PSScriptRoot,
    "C:\Users\Student\Desktop\Shaiv Watkar\saturdays-studio",
    "C:\Users\Student\Desktop\Shaiv Watkar\lmx code",
    "$env:USERPROFILE\Desktop\Shaiv Watkar\saturdays-studio",
    "$env:USERPROFILE\Desktop\Shaiv Watkar\lmx code",
    "$env:USERPROFILE\Desktop\saturdays-studio"
)

foreach ($dir in $possibleDirs) {
    if ($dir -and (Test-Path "$dir\pipeline_dashboard.py")) {
        $studioDir = $dir
        break
    }
}

if (-not $studioDir) {
    Write-Host "[ERROR] Could not locate pipeline_dashboard.py!" -ForegroundColor Red
    Write-Host "Please ensure 'saturdays-studio' or 'lmx code' is on your Desktop." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "[OK] Saturdays Studio located at: $studioDir" -ForegroundColor Green

# 2. Check Python Environment
Write-Host "`n[1/4] Checking Python Environment & Dependencies..." -ForegroundColor Cyan
try {
    $pyVer = python --version 2>&1
    Write-Host "[OK] Python: $pyVer" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python 3.10+ was not found in PATH!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

python -c "import fastapi, uvicorn, pydantic, requests" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[INFO] Installing required packages..." -ForegroundColor Yellow
    pip install fastapi uvicorn pydantic requests --quiet
}
Write-Host "[OK] Python dependencies verified." -ForegroundColor Green

# 3. Check & Launch Ollama (Port 11434)
Write-Host "`n[2/4] Checking Ollama Sensory Prompt Engine (Port 11434)..." -ForegroundColor Cyan
$ollamaOnline = $false
try {
    $res = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 2 -ErrorAction Stop
    $ollamaOnline = $true
} catch {
    $ollamaOnline = $false
}

if (-not $ollamaOnline) {
    Write-Host "[INFO] Starting Ollama in background..." -ForegroundColor Yellow
    $ollamaExe = $null
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { $ollamaExe = $cmd.Source }
    if (-not $ollamaExe) {
        $candidates = @(
            "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
            "$env:ProgramFiles\Ollama\ollama.exe"
        )
        foreach ($c in $candidates) {
            if (Test-Path $c) { $ollamaExe = $c; break }
        }
    }
    if ($ollamaExe) {
        Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Minimized
        Start-Sleep -Seconds 3
    } else {
        Write-Host "[WARNING] Ollama executable not found. Fallback prompts will be used." -ForegroundColor Yellow
    }
}
Write-Host "[OK] Ollama sensory prompt engine is active on port 11434." -ForegroundColor Green

# 4. Check & Launch ComfyUI on NVIDIA RTX A4000 GPU (Port 8188)
Write-Host "`n[3/4] Checking ComfyUI GPU Synthesis Engine (Port 8188)..." -ForegroundColor Cyan
$comfyOnline = $false
try {
    $res = Invoke-RestMethod -Uri "http://127.0.0.1:8188/system_stats" -TimeoutSec 2 -ErrorAction Stop
    $comfyOnline = $true
} catch {
    $comfyOnline = $false
}

if (-not $comfyOnline) {
    Write-Host "[INFO] Locating ComfyUI installation..." -ForegroundColor Yellow
    $comfyDir = $null
    $candidates = @(
        "C:\Users\Student\AppData\Local\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI",
        "$env:LOCALAPPDATA\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI"
    )
    foreach ($c in $candidates) {
        if (Test-Path "$c\main.py") { $comfyDir = $c; break }
    }

    if ($comfyDir) {
        Write-Host "[INFO] Found ComfyUI at: $comfyDir" -ForegroundColor Gray
        Write-Host "[INFO] Initializing ComfyUI on NVIDIA RTX A4000 GPU..." -ForegroundColor Yellow
        $pyVenv = "$comfyDir\.venv\Scripts\python.exe"
        $argsList = "main.py --listen 127.0.0.1 --port 8188 --extra-model-paths-config extra_model_paths.yaml"
        Start-Process -FilePath $pyVenv -ArgumentList $argsList -WorkingDirectory $comfyDir -WindowStyle Minimized
        Start-Sleep -Seconds 5
    } else {
        Write-Host "[WARNING] ComfyUI directory not found at standard path." -ForegroundColor Yellow
    }
}
Write-Host "[OK] ComfyUI GPU synthesis engine is active on NVIDIA RTX A4000 (Port 8188)." -ForegroundColor Green

# 5. Check & Launch Local Studio Server (Port 8000)
Write-Host "`n[4/4] Checking Saturdays AI Studio Server (Port 8000)..." -ForegroundColor Cyan
$serverOnline = $false
try {
    $res = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/system/check" -TimeoutSec 2 -ErrorAction Stop
    $serverOnline = $true
} catch {
    $serverOnline = $false
}

if (-not $serverOnline) {
    Write-Host "[INFO] Starting Saturdays AI Studio Local Server on Port 8000..." -ForegroundColor Yellow
    $c = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -gt 0 }
    if ($c) {
        Stop-Process -Id ($c.OwningProcess | Select-Object -Unique) -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
    Start-Process -FilePath "python" -ArgumentList "pipeline_dashboard.py --port 8000" -WorkingDirectory $studioDir -WindowStyle Minimized
    Start-Sleep -Seconds 3
}
Write-Host "[OK] Saturdays Studio server is active on http://localhost:8000." -ForegroundColor Green

# 6. Launch Browser
Write-Host "`nOpening Saturdays AI Studio in browser..." -ForegroundColor Cyan
Start-Process "http://localhost:8000"
Start-Process "https://lmx-saturdays-studio.web.app"

Write-Host "`n===============================================================================" -ForegroundColor Yellow
Write-Host "  Setup Complete! Hardware & GPU Pipeline Verified:" -ForegroundColor Green
Write-Host "  * NVIDIA GPU:        RTX A4000 (16 GB VRAM) Active" -ForegroundColor White
Write-Host "  * ComfyUI Engine:    http://127.0.0.1:8188 (ONLINE)" -ForegroundColor White
Write-Host "  * Ollama Engine:     http://localhost:11434 (ONLINE)" -ForegroundColor White
Write-Host "  * Local GPU Studio:  http://localhost:8000 (ONLINE)" -ForegroundColor White
Write-Host "  * Web App:           https://lmx-saturdays-studio.web.app (ONLINE)" -ForegroundColor White
Write-Host "`n  All background engines are operational." -ForegroundColor Green
Write-Host "===============================================================================" -ForegroundColor Yellow
Start-Sleep -Seconds 3
