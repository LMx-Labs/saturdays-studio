"""
=============================================================================
LMX Labs — Unified Autonomous Menu, LLM & GPU Image Generation Engine
=============================================================================
A self-contained, single-file workflow that:
  1. Auto-detects & bootstraps the Python GPU environment (Python 3.12 / CUDA RTX A4000).
  2. Parses raw menus via Gemini Free API (any text/scans/spreadsheets) or local Excel/CSV.
  3. Generates rich food photography descriptions using local Qwen 2.5 32B via Ollama.
  4. Generates high-fidelity food images on the local NVIDIA RTX A4000 (16 GB VRAM).
  5. Maps all menu items into categorized folders and maintains manifest.json.
  6. Connects directly to the Saturdays Admin Portal via REST API & database sync.

Usage:
  python lmx_workflow.py                       # Runs full menu generation & starts admin API
  python lmx_workflow.py --serve               # Starts only the Admin Portal AI API server
  python lmx_workflow.py --restaurant "KC FOODS"
  python lmx_workflow.py --dry-run             # Generates LLM descriptions without image rendering
  python lmx_workflow.py --raw-menu "path/to/raw_menu.txt" --gemini-key "YOUR_KEY"
=============================================================================
"""

import argparse
import base64
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# 0. SELF-BOOTSTRAP: Ensure PyTorch GPU Environment
# ─────────────────────────────────────────────────────────────────────────────

PY312_CANDIDATES = [
    r"C:\Users\Student\AppData\Local\Python\pythoncore-3.12-64\python.exe",
    r"C:\Users\Student\AppData\Local\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\.venv\Scripts\python.exe",
]


def ensure_gpu_python_environment():
    """
    If running under a Python interpreter without CUDA PyTorch,
    transparently re-executes using the installed GPU-enabled Python.
    """
    try:
        import torch
        if torch.cuda.is_available():
            return  # Already in an active CUDA environment!
    except Exception:
        pass

    # Find GPU-enabled Python interpreter
    for candidate in PY312_CANDIDATES:
        if Path(candidate).exists():
            print(f"[*] Switching to GPU Python runtime: {candidate}")
            cmd = [candidate, str(Path(__file__).resolve())] + sys.argv[1:]
            try:
                result = subprocess.run(cmd)
                sys.exit(result.returncode)
            except Exception as e:
                print(f"[!] Failed to re-spawn in {candidate}: {e}")

    print("[!] Warning: Running without confirmed CUDA PyTorch environment.")


# Run bootstrap check before importing heavy modules
if __name__ == "__main__" and os.environ.get("LMX_BOOTSTRAPPED") != "1":
    os.environ["LMX_BOOTSTRAPPED"] = "1"
    ensure_gpu_python_environment()


# ─────────────────────────────────────────────────────────────────────────────
# 1. AUTO-DEPENDENCY CHECK & SETUP
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_MODULES = {
    "openpyxl": "openpyxl",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "requests": "requests",
    "pydantic": "pydantic",
}


def install_missing_dependencies():
    """Installs any missing lightweight packages automatically."""
    for mod, pkg in REQUIRED_MODULES.items():
        try:
            __import__(mod)
        except ImportError:
            print(f"[*] Installing required dependency: {pkg}...")
            subprocess.run([sys.executable, "-m", "pip", "install", pkg, "--quiet"], check=False)


install_missing_dependencies()


# ─────────────────────────────────────────────────────────────────────────────
# 2. CONFIGURATION & HARDWARE SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT = BASE_DIR / "output"
COMFYUI_DIR = Path(r"C:\Users\Student\AppData\Local\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI")
COMFYUI_MODELS_DIR = Path(r"C:\Users\Student\AppData\Local\Comfy-Desktop\ComfyUI-Shared\models")
COMFYUI_URL = "http://127.0.0.1:8188"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:32b-vibecoder")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

DEFAULT_RESTAURANT = {
    "name": "KC FOODS",
    "menu_file": r"C:\Users\Student\Desktop\Shaiv Watkar\Restos\KC FOODS\KC_Foods_VIT_Bulk_Menu_Import.xlsx",
    "style": "Authentic Indian street food and casual dining, vibrant rich colors, warm cozy lighting, rustic wooden platter presentation",
}


def get_hardware_info() -> Dict[str, Any]:
    """Detects GPU device, VRAM, CUDA status, and Ollama connectivity."""
    info = {
        "gpu_available": False,
        "gpu_device": "None",
        "vram_gb": 0.0,
        "cuda_version": "None",
        "ollama_online": False,
        "gemini_api_configured": bool(GEMINI_API_KEY),
        "python_version": sys.version.split()[0],
        "comfyui_available": COMFYUI_DIR.exists(),
    }
    try:
        import torch
        if torch.cuda.is_available():
            info["gpu_available"] = True
            info["gpu_device"] = torch.cuda.get_device_name(0)
            info["vram_gb"] = round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 2)
            info["cuda_version"] = torch.version.cuda
    except Exception:
        pass

    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                info["ollama_online"] = True
    except Exception:
        pass

    return info


def safe_name(name: str) -> str:
    """Sanitize strings for folder and file names."""
    clean = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    return re.sub(r"\s+", "_", clean)[:64]


# ─────────────────────────────────────────────────────────────────────────────
# 3. GEMINI FREE API: UNSTRUCTURED MENU -> STRUCTURED JSON
# ─────────────────────────────────────────────────────────────────────────────

def parse_menu_with_gemini(raw_menu_content: str, api_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Uses Google Gemini Free API (Gemini 2.0 Flash / 1.5 Flash) to parse raw menu text,
    notes, messy tables, or OCR dumps into clean, normalized JSON.
    """
    key = api_key or GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise ValueError("GEMINI_API_KEY is required for Gemini menu extraction. Provide via $env:GEMINI_API_KEY or --gemini-key.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"

    prompt = f"""You are a restaurant menu data structuring assistant.
Analyze the following restaurant menu data and extract all items into a valid JSON array.
Every item must follow this exact structure:
[
  {{
    "category": "Category Name (e.g., Quick Bites, Main Course, Rolls, Beverages)",
    "name": "Dish Name",
    "price": 100.0,
    "diet": "Veg or NonVeg"
  }}
]

Important rules:
1. If price is missing or not clear, default to 0.0.
2. If diet is vegetarian, paneer, mushroom, veg roll, etc., mark as "Veg". If chicken, mutton, egg, fish, mark as "NonVeg".
3. Clean up dish names and remove duplicate items.
4. Output ONLY valid JSON matching the format above.

Menu Data:
{raw_menu_content[:100000]}
"""

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "response_mime_type": "application/json"
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=45) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        text_out = result["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text_out)
        if isinstance(parsed, dict) and "items" in parsed:
            parsed = parsed["items"]
        print(f"[✓] Gemini extracted {len(parsed)} structured menu items successfully!")
        return parsed


# ─────────────────────────────────────────────────────────────────────────────
# 4. LOCAL MENU PARSER & INGESTION (Excel / CSV / Text)
# ─────────────────────────────────────────────────────────────────────────────

def parse_menu(file_path_or_csv: str) -> List[Dict[str, Any]]:
    """
    Parses menu from Excel file (.xlsx), CSV file, or raw CSV text.
    Handles columns: Category, Dish Name, Price, Veg/NonVeg
    """
    p = Path(file_path_or_csv)
    if p.exists() and p.is_file():
        ext = p.suffix.lower()
        if ext in (".xlsx", ".xls"):
            import openpyxl
            wb = openpyxl.load_workbook(str(p), data_only=True)
            ws = wb.active
            headers = None
            raw_rows = []
            for row in ws.iter_rows(values_only=True):
                if not headers:
                    headers = [str(c).strip() if c is not None else "" for c in row]
                    continue
                if any(row):
                    raw_rows.append(dict(zip(headers, row)))
        elif ext == ".csv":
            with open(p, "r", encoding="utf-8-sig") as f:
                raw_rows = list(csv.DictReader(f))
        else:
            # Plain text file - try reading content
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if GEMINI_API_KEY:
                return parse_menu_with_gemini(content)
            raise ValueError(f"Unsupported menu format: {ext}. Provide .xlsx, .csv, or set GEMINI_API_KEY for unstructured text.")
    else:
        # Check if raw text with GEMINI_API_KEY
        if GEMINI_API_KEY and len(file_path_or_csv) > 20 and "\n" in file_path_or_csv and not file_path_or_csv.startswith("Category,"):
            return parse_menu_with_gemini(file_path_or_csv)

        # Treat as raw CSV string from Admin Portal
        lines = file_path_or_csv.strip().splitlines()
        reader = csv.reader(lines)
        items = []
        for r in reader:
            if not r or len(r) < 2:
                continue
            if "category" in r[0].lower() and "dish" in "".join(r).lower():
                continue
            cat = r[0].strip()
            name = r[1].strip()
            price = float(r[2].replace("₹", "").replace("$", "").strip()) if len(r) >= 3 else 0.0
            diet = r[3].strip() if len(r) >= 4 else "Veg"
            if name:
                items.append({"name": name, "category": cat, "price": price, "diet": diet})
        return items

    normalized = []
    for r in raw_rows:
        name = r.get("Dish Name") or r.get("Name") or r.get("dish_name") or r.get("item_name") or ""
        cat = r.get("Category") or r.get("category") or "General"
        price = r.get("Price") or r.get("price") or 0.0
        diet = r.get("Veg/NonVeg") or r.get("Dietary") or r.get("diet") or "Veg"

        name = str(name).strip()
        if not name:
            continue

        try:
            p_val = float(str(price).replace("₹", "").replace("$", "").strip())
        except Exception:
            p_val = 0.0

        normalized.append({
            "name": name,
            "category": str(cat).strip(),
            "price": p_val,
            "diet": str(diet).strip(),
        })

    return normalized


# ─────────────────────────────────────────────────────────────────────────────
# 5. LOCAL LLM PROMPT GENERATOR (Qwen 32B via Ollama on RTX A4000)
# ─────────────────────────────────────────────────────────────────────────────

def generate_dish_prompt(
    dish_name: str,
    category: str,
    diet: str,
    restaurant_style: str = "appetizing authentic culinary presentation, warm soft lighting",
) -> str:
    """Queries local Ollama to craft fine-dining food photography prompts."""
    system_prompt = (
        f"You are a master food photography art director and prompt engineer.\n"
        f"Generate a vivid, sensory, single-paragraph image generation prompt for this dish:\n"
        f"- Dish: {dish_name}\n"
        f"- Category: {category}\n"
        f"- Dietary: {diet}\n"
        f"- Style: {restaurant_style}\n\n"
        f"Instructions:\n"
        f"Describe plating, textures (crispness, steam, garnish, glaze), camera lens (50mm macro, f/1.8), "
        f"composition, and appetizing culinary details. Keep it under 100 words. "
        f"Output ONLY the prompt text with no intro or quotation marks."
    )

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": system_prompt}],
        "stream": False,
        "options": {"temperature": 0.7, "top_p": 0.9},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["message"]["content"].strip()
    except Exception as e:
        return (
            f"Vivid gourmet food photography of {dish_name}, freshly prepared {category}, {diet}, "
            f"beautifully garnished, appetizing presentation, soft ambient lighting, 8k resolution"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 6. GPU IMAGE GENERATION ENGINE (ComfyUI MiniMax/Krea2 & Direct GPU)
# ─────────────────────────────────────────────────────────────────────────────

def ensure_comfyui_server():
    """Starts ComfyUI in the background if available and not yet running."""
    try:
        req = urllib.request.Request(f"{COMFYUI_URL}/system_stats")
        with urllib.request.urlopen(req, timeout=2) as r:
            if r.status == 200:
                return True
    except Exception:
        pass

    if not COMFYUI_DIR.exists():
        return False

    venv_py = COMFYUI_DIR / ".venv" / "Scripts" / "python.exe"
    main_py = COMFYUI_DIR / "main.py"
    if not venv_py.exists() or not main_py.exists():
        return False

    print("[*] Launching local ComfyUI Server on port 8188...")
    extra_yaml = COMFYUI_DIR / "extra_model_paths.yaml"
    cmd = [
        str(venv_py), str(main_py),
        "--listen", "127.0.0.1",
        "--port", "8188",
        "--extra-model-paths-config", str(extra_yaml),
        "--highvram",
    ]
    creation_flags = 0x00000200 if sys.platform == "win32" else 0
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creation_flags)

    for _ in range(30):
        time.sleep(2)
        try:
            req = urllib.request.Request(f"{COMFYUI_URL}/system_stats")
            with urllib.request.urlopen(req, timeout=2) as r:
                if r.status == 200:
                    print("[✓] ComfyUI Engine is ready!")
                    return True
        except Exception:
            pass
    return False


def render_image_comfyui(prompt: str, output_path: Path) -> bool:
    """Executes prompt via ComfyUI MiniMax/Krea2 local API."""
    try:
        workflow = {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": int(time.time()) % 1000000,
                    "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal",
                    "denoise": 1.0, "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]
                }
            },
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "krea2_turbo_fp8_scaled.safetensors"}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt + ", professional food photography, award winning", "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry, low quality, bad food, distorted", "clip": ["4", 1]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "lmx", "images": ["8", 0]}}
        }
        req = urllib.request.Request(
            f"{COMFYUI_URL}/prompt",
            data=json.dumps({"prompt": workflow}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read().decode("utf-8"))
        prompt_id = res.get("prompt_id")
        if prompt_id:
            for _ in range(120):
                time.sleep(1)
                with urllib.request.urlopen(f"{COMFYUI_URL}/history/{prompt_id}", timeout=5) as hr:
                    hist = json.loads(hr.read().decode("utf-8"))
                if prompt_id in hist:
                    imgs = hist[prompt_id]["outputs"]["9"]["images"]
                    if imgs:
                        img_url = f"{COMFYUI_URL}/view?filename={imgs[0]['filename']}&type={imgs[0]['type']}"
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        urllib.request.urlretrieve(img_url, output_path)
                        return True
    except Exception as e:
        print(f"  [ComfyUI] {e}")
    return False


def render_image(prompt: str, output_path: Path) -> bool:
    """Dispatches image generation to available local GPU backends."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Try ComfyUI MiniMax / Krea2 if available
    if ensure_comfyui_server():
        ok = render_image_comfyui(prompt, output_path)
        if ok:
            return True

    # 2. Try MiniMax cloud API if key is present
    minimax_key = os.environ.get("MINIMAX_API_KEY", "")
    if minimax_key:
        try:
            payload = json.dumps({
                "model": "image-01",
                "prompt": prompt + ", professional food photo",
                "aspect_ratio": "1:1"
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.minimaxi.chat/v1/image_generation",
                data=payload,
                headers={"Authorization": f"Bearer {minimax_key}", "Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if "data" in data and len(data["data"]) > 0:
                url = data["data"][0]["url"]
                urllib.request.urlretrieve(url, output_path)
                return True
        except Exception:
            pass

    return False


# ─────────────────────────────────────────────────────────────────────────────
# 7. PIPELINE ORCHESTRATOR & MANIFEST MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class MenuWorkflowManager:
    """Manages parsing, parallel LLM prompt generation, GPU rendering, and manifest tracking."""

    def __init__(self, restaurant_name: str, style: str = ""):
        self.restaurant_name = restaurant_name
        self.style = style or "Modern fine dining presentation"
        self.output_dir = OUTPUT_ROOT / safe_name(restaurant_name)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_file = self.output_dir / "manifest.json"
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> Dict[str, Any]:
        if self.manifest_file.exists():
            try:
                with open(self.manifest_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_manifest(self):
        with open(self.manifest_file, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, indent=2, ensure_ascii=False)

    def process_menu(self, items: List[Dict[str, Any]], dry_run: bool = False, workers: int = 4) -> Dict[str, Any]:
        print(f"\n============================================================")
        print(f"  🍽  Restaurant: {self.restaurant_name} ({len(items)} items)")
        print(f"============================================================")

        # Step 1: Parallel LLM Description Generation via Ollama
        unprompted = [it for it in items if not self.manifest.get(it["name"], {}).get("description")]
        if unprompted:
            print(f"[*] Generating {len(unprompted)} dish descriptions via Ollama ({OLLAMA_MODEL})...")
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(generate_dish_prompt, it["name"], it["category"], it["diet"], self.style): it
                    for it in unprompted
                }
                for f in as_completed(futures):
                    it = futures[f]
                    desc = f.result()
                    self.manifest[it["name"]] = {
                        "category": it["category"],
                        "name": it["name"],
                        "price": it["price"],
                        "diet": it["diet"],
                        "description": desc,
                        "image_path": "",
                        "image_url": "",
                        "status": "described",
                    }
                    self.save_manifest()
                    print(f"  [✓] Described: {it['name']}")

        # Step 2: GPU Image Rendering
        for it in items:
            name = it["name"]
            cat = it["category"]
            record = self.manifest.get(name, {})
            desc = record.get("description", "")
            img_path = record.get("image_path", "")

            if img_path and Path(img_path).exists():
                continue

            target_path = self.output_dir / safe_name(cat) / f"{safe_name(name)}.png"

            if dry_run:
                self.manifest[name]["image_path"] = str(target_path)
                self.manifest[name]["status"] = "dry_run_ready"
            else:
                print(f"[*] Rendering image on GPU: {name}...")
                ok = render_image(desc, target_path)
                if ok:
                    self.manifest[name]["image_path"] = str(target_path)
                    self.manifest[name]["status"] = "completed"
                    print(f"  [✓] Image Saved: {target_path.name}")
                else:
                    self.manifest[name]["status"] = "image_pending"

            self.manifest[name]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
            self.save_manifest()

        print(f"\n[✓] Finished batch for {self.restaurant_name}! Manifest saved to:")
        print(f"    {self.manifest_file}\n")
        return self.manifest


# ─────────────────────────────────────────────────────────────────────────────
# 8. ADMIN PORTAL FASTAPI SERVER & SYNC
# ─────────────────────────────────────────────────────────────────────────────

def create_admin_api():
    """Builds the FastAPI application for Saturdays Admin Portal integration."""
    from fastapi import FastAPI, BackgroundTasks, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel

    app = FastAPI(
        title="LMX Menu AI Asset Generation API",
        version="2.0.0",
        description="Unified local GPU & LLM microservice connected to the Saturdays Admin Portal.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    class DescribeRequest(BaseModel):
        dish_name: str
        category: str
        diet: Optional[str] = "Veg"
        style_hint: Optional[str] = "Appetizing culinary presentation, warm soft lighting"

    class GenerateDishRequest(BaseModel):
        restaurant_name: str
        dish_name: str
        category: str
        diet: Optional[str] = "Veg"
        price: Optional[float] = 0.0
        style_hint: Optional[str] = ""

    class BulkImportRequest(BaseModel):
        restaurant_name: str
        style_hint: Optional[str] = ""
        csv_data: Optional[str] = None
        file_path: Optional[str] = None
        gemini_api_key: Optional[str] = None
        dry_run: Optional[bool] = False

    class GeminiExtractRequest(BaseModel):
        restaurant_name: str
        raw_menu_text: str
        gemini_api_key: Optional[str] = None

    @app.get("/health")
    def health():
        return get_hardware_info()

    @app.get("/api/v1/ai/hardware")
    def hardware_status():
        return get_hardware_info()

    @app.post("/api/v1/ai/menu/gemini-extract")
    def extract_with_gemini(req: GeminiExtractRequest):
        items = parse_menu_with_gemini(req.raw_menu_text, req.gemini_api_key)
        return {"restaurant": req.restaurant_name, "count": len(items), "items": items}

    @app.post("/api/v1/ai/dish/describe")
    def describe_single_dish(req: DescribeRequest):
        desc = generate_dish_prompt(req.dish_name, req.category, req.diet, req.style_hint)
        return {"dish_name": req.dish_name, "description": desc}

    @app.post("/api/v1/ai/dish/generate")
    def generate_single_dish(req: GenerateDishRequest):
        mgr = MenuWorkflowManager(req.restaurant_name, req.style_hint)
        desc = generate_dish_prompt(req.dish_name, req.category, req.diet, mgr.style)
        target = mgr.output_dir / safe_name(req.category) / f"{safe_name(req.dish_name)}.png"
        ok = render_image(desc, target)

        mgr.manifest[req.dish_name] = {
            "category": req.category,
            "name": req.dish_name,
            "price": req.price,
            "diet": req.diet,
            "description": desc,
            "image_path": str(target) if ok else "",
            "status": "completed" if ok else "image_pending",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        mgr.save_manifest()
        return {
            "dish_name": req.dish_name,
            "description": desc,
            "image_path": str(target) if ok else "",
            "success": ok,
        }

    @app.post("/api/v1/ai/menu/bulk-import")
    def bulk_import(req: BulkImportRequest, background_tasks: BackgroundTasks):
        items = []
        if req.file_path:
            items = parse_menu(req.file_path)
        elif req.csv_data:
            if req.gemini_api_key or (GEMINI_API_KEY and not req.csv_data.startswith("Category,")):
                items = parse_menu_with_gemini(req.csv_data, req.gemini_api_key)
            else:
                items = parse_menu(req.csv_data)
        else:
            raise HTTPException(status_code=400, detail="Provide either csv_data or file_path")

        mgr = MenuWorkflowManager(req.restaurant_name, req.style_hint)
        background_tasks.add_task(mgr.process_menu, items, req.dry_run)

        return {
            "status": "accepted",
            "restaurant": req.restaurant_name,
            "total_items": len(items),
            "manifest_file": str(mgr.manifest_file),
            "message": f"Processing {len(items)} menu items in background on local GPU.",
        }

    @app.get("/api/v1/ai/manifest/{restaurant_name}")
    def get_manifest(restaurant_name: str):
        mgr = MenuWorkflowManager(restaurant_name)
        return {
            "restaurant": restaurant_name,
            "count": len(mgr.manifest),
            "items": mgr.manifest,
        }

    return app


# ─────────────────────────────────────────────────────────────────────────────
# 9. CLI RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def main():
    global GEMINI_API_KEY
    parser = argparse.ArgumentParser(description="LMX Labs — Unified Autonomous Menu Workflow")
    parser.add_argument("--restaurant", default=DEFAULT_RESTAURANT["name"], help="Restaurant name")
    parser.add_argument("--file", help="Path to menu .xlsx, .csv, or raw text file")
    parser.add_argument("--raw-menu", help="Raw menu text to parse using Gemini")
    parser.add_argument("--gemini-key", help="Google Gemini API key for free menu extraction")
    parser.add_argument("--dry-run", action="store_true", help="Generate descriptions only, skip images")
    parser.add_argument("--serve", action="store_true", help="Start the Admin Portal REST server")
    parser.add_argument("--port", type=int, default=8000, help="Server port (default: 8000)")
    parser.add_argument("--workers", type=int, default=4, help="LLM concurrency workers")
    args = parser.parse_args()

    if args.gemini_key:
        GEMINI_API_KEY = args.gemini_key

    hw = get_hardware_info()
    print("=" * 60)
    print("  LMX Labs — Menu & AI Generation Engine")
    print("=" * 60)
    print(f"  GPU Device   : {hw['gpu_device']} ({hw['vram_gb']} GB VRAM)")
    print(f"  CUDA Version : {hw['cuda_version']}")
    print(f"  Ollama LLM   : {'ONLINE' if hw['ollama_online'] else 'OFFLINE'} ({OLLAMA_MODEL})")
    print(f"  Gemini API   : {'CONFIGURED' if hw['gemini_api_configured'] else 'NOT SET (optional for raw menu parsing)'}")
    print(f"  Python       : {hw['python_version']}")
    print("=" * 60)

    if args.serve:
        import uvicorn
        app = create_admin_api()
        print(f"\n🚀 Admin Portal AI API live on: http://127.0.0.1:{args.port}")
        print(f"📖 Interactive Swagger Docs:   http://127.0.0.1:{args.port}/docs\n")
        uvicorn.run(app, host="0.0.0.0", port=args.port)
        return

    # Ingestion handling:
    items = []
    if args.raw_menu:
        items = parse_menu_with_gemini(args.raw_menu, GEMINI_API_KEY)
    else:
        menu_file = args.file or DEFAULT_RESTAURANT["menu_file"]
        if not Path(menu_file).exists():
            print(f"[!] Menu file not found: {menu_file}")
            sys.exit(1)
        items = parse_menu(menu_file)

    mgr = MenuWorkflowManager(args.restaurant, DEFAULT_RESTAURANT["style"])
    mgr.process_menu(items, dry_run=args.dry_run, workers=args.workers)

    # After processing, start the server so the Admin Portal can query the result
    print("[*] Launching Admin Portal API server to expose generated assets...")
    import uvicorn
    app = create_admin_api()
    print(f"\n🚀 Admin Portal AI API live on: http://127.0.0.1:{args.port}")
    print(f"📖 Interactive Swagger Docs:   http://127.0.0.1:{args.port}/docs\n")
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
