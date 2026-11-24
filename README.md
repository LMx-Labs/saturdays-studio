# Saturdays AI Studio (Local CUDA Workstation Repository)

> **Dedicated, standalone repository for autonomous food photography, multimodal menu extraction, and GPU image synthesis for Saturdays.co.in.**  
> **Strictly isolated to local Lab PCs with NVIDIA CUDA GPUs. Zero cloud dependencies.**

---

## 🚀 Architecture Overview

This repository operates strictly on local lab hardware to provide **instant ~4.5-second image synthesis** without cloud GPU hosting costs:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Local Lab Workstation                           │
│                                                                        │
│   ┌─────────────────────┐       ┌──────────────────────────────────┐   │
│   │   ComfyUI Engine    │       │        Ollama LLM Engine         │   │
│   │   Port: 8188        │       │        Port: 11434               │   │
│   │   Krea2 Turbo FP8   │       │        qwen2.5-coder:7b          │   │
│   │   NVIDIA RTX A4000  │       │        (keep_alive: 0)           │   │
│   └──────────▲──────────┘       └─────────────────▲────────────────┘   │
│              │                                    │                    │
│   ┌──────────┴────────────────────────────────────┴────────────────┐   │
│   │             Saturdays AI Studio Server & UI                    │   │
│   │             pipeline_dashboard.py (Port: 8000)                 │   │
│   │             • Multimodal Gemini Vision Menu Extraction         │   │
│   │             • Sensory Art Direction Engine                     │   │
│   │             • 480px / 960px / 1600px Resolution Toggles        │   │
│   │             • Firebase Storage Auto-Mapping                    │   │
│   │             • Mobile-Safe Saturdays Production Publishing      │   │
│   │             • 1-Click Auto Pipeline Flow                       │   │
│   └────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## ⚙️ Quick Start (1-Click Launch)

### Prerequisites on the Lab PC:
1. **GPU:** NVIDIA RTX A4000 (16 GB VRAM) or equivalent CUDA GPU.
2. **Python:** 3.10+ installed and available in PATH.
3. **Ollama:** Running with `qwen2.5-coder:7b` on port `11434`.
4. **ComfyUI:** Running on port `8188` (with `krea2_turbo_fp8_scaled.safetensors`).

### Launch the Studio:
Double-click **`start_studio.bat`** or run:

```powershell
python pipeline_dashboard.py --port 8000
```

Open your browser to: **`http://localhost:8000`**

---

## 🖥️ Setting Up the 4 to 5 Lab PCs

To replicate this environment across your 4 to 5 Lab PCs:

### Step 1: Clone / Copy this Repository
Copy this `saturdays-studio` folder to the target PC.

### Step 2: Run Automated Environment Check
In PowerShell:
```powershell
.\setup_lab_pc.ps1
```
This automatically verifies GPU status, Python dependencies, and pulls the `qwen2.5-coder:7b` Ollama model.

### Step 3: Copy ComfyUI Directory
Copy the local ComfyUI folder from the primary PC:
`C:\Users\Student\AppData\Local\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI`  
Ensure the model files are present in:
* `models/unet/krea2_turbo_fp8_scaled.safetensors`
* `models/clip/qwen3vl_4b_fp8_scaled.safetensors`
* `models/vae/qwen_image_vae.safetensors`

### Step 4: Run ComfyUI with Dynamic VRAM
> **IMPORTANT:** Do NOT use `--highvram`! Dynamic VRAM staging allows the 12.2GB model to fit into 16GB VRAM cleanly without slow PCIe paging.
```powershell
python main.py --listen 127.0.0.1 --port 8188
```

---

## 🌐 Local Network Sharing (Optional)

If only 1 or 2 of the PCs have high-end RTX GPUs, the GPU PC can host the engine for the rest of the lab:

1. On the GPU machine, launch with `--host 0.0.0.0`:
   ```powershell
   python pipeline_dashboard.py --host 0.0.0.0 --port 8000
   ```
2. Any other PC in the lab can open:
   `http://<GPU_PC_LOCAL_IP>:8000`
   and generate images using the shared RTX A4000 GPU over local LAN.

---

## 📦 Key Capabilities

| Feature | Details |
| :--- | :--- |
| **Speed** | **4.53s** per dish at 480x480 (down from 8–10 minutes → 120x speedup). |
| **Multi-Resolution** | Quick toggles for **480px** (Fast 4.5s), **960px** (HD ~12s), and **1600px** (Ultra ~25s). |
| **Multimodal Extraction** | Upload a photo of any restaurant menu paper/board; extracts all dishes, prices, and categories via Gemini Vision API. |
| **Sensory Art Direction** | Local `qwen2.5-coder:7b` writes vivid, sensory macro food photography prompts in ~1s and releases VRAM immediately (`keep_alive: 0`). |
| **Mobile-Safe URLs** | Automatically formats media URLs with `%30` encoding so mobile frontends display images properly without regex redirection bugs. |
| **Firebase Cloud Storage** | Maps all generated assets into `saturdays-lmx.firebasestorage.app`. |
| **1-Click Auto Flow** | Single button runs generation, Firebase mapping, and Saturdays production publishing end-to-end. |

---

## 📄 License & Attribution
Part of the **Saturdays.co.in** culinary AI ecosystem — LMx Labs. Strictly isolated to local lab workstations.
