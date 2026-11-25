"""
LMX Labs — Unified Restaurant Menu & Image Generation Engine
=============================================================
All-in-one pipeline combining:
  1. Menu Ingestion (Excel / CSV / JSON / Saturdays Bulk Import format)
  2. Local LLM Description Generation (Qwen 2.5 32B via Ollama - multithreaded)
  3. RTX A4000 GPU Image Generation (SDXL / SDXL-Turbo / ComfyUI / MiniMax)
  4. Asset Mapping & Manifest Generation (dish -> category -> image path -> URL)
  5. Built-in FastAPI Server / REST Endpoints for Saturdays Backend integration

Hardware Target: NVIDIA RTX A4000 (16 GB VRAM) | CUDA 13.2 | Ollama Qwen 32B
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
# CONFIGURATION & DEFAULTS
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT = BASE_DIR / "output"
PY312_EXE = r"C:\Users\Student\AppData\Local\Python\pythoncore-3.12-64\python.exe"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:32b-vibecoder")

# Image generation backends: "gpu_server" | "diffusers" | "comfyui" | "minimax_api" | "a1111"
DEFAULT_IMAGE_BACKEND = "gpu_server"
GPU_SERVER_URL = "http://127.0.0.1:8765"
COMFYUI_URL = "http://127.0.0.1:8188"
MINIMAX_URL = "http://127.0.0.1:7860"

DEFAULT_RESTAURANTS = [
    {
        "name": "KC FOODS",
        "menu_file": r"C:\Users\Student\Desktop\Shaiv Watkar\Restos\KC FOODS\KC_Foods_VIT_Bulk_Menu_Import.xlsx",
        "style": "Authentic Indian street food and casual dining, vibrant rich colors, warm cozy lighting, rustic wooden presentation",
    }
]

# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def safe_filename(name: str) -> str:
    """Sanitize strings for folder and file names."""
    clean = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    return re.sub(r"\s+", "_", clean)[:64]


def detect_system_resources() -> Dict[str, Any]:
    """Inspects GPU, VRAM, and Ollama status."""
    gpu_info = {"available": False, "device": "None", "vram_gb": 0.0}
    try:
        import torch
        if torch.cuda.is_available():
            gpu_info = {
                "available": True,
                "device": torch.cuda.get_device_name(0),
                "vram_gb": round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 2),
            }
    except Exception:
        pass

    ollama_ok = False
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                ollama_ok = True
    except Exception:
        pass

    return {
        "gpu": gpu_info,
        "ollama_online": ollama_ok,
        "python_version": sys.version.split()[0],
        "py312_torch_available": Path(PY312_EXE).exists(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. MENU PARSER & INGESTION
# ─────────────────────────────────────────────────────────────────────────────

def parse_menu_file(file_path: str) -> List[Dict[str, Any]]:
    """
    Parses Excel (.xlsx/.xls) or CSV files into normalized menu items.
    Accepts: Category, Dish Name, Price, Veg/NonVeg
    """
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"Menu file not found: {file_path}")

    ext = p.suffix.lower()
    raw_rows = []

    if ext in (".xlsx", ".xls"):
        import openpyxl
        wb = openpyxl.load_workbook(file_path, data_only=True)
        ws = wb.active
        headers = None
        for row in ws.iter_rows(values_only=True):
            if not headers:
                headers = [str(c).strip() if c is not None else "" for c in row]
                continue
            if any(row):
                raw_rows.append(dict(zip(headers, row)))
    elif ext == ".csv":
        with open(file_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            raw_rows = [dict(r) for r in reader]
    else:
        raise ValueError(f"Unsupported file format: {ext}. Provide .xlsx, .xls, or .csv")

    normalized = []
    for r in raw_rows:
        name = r.get("Dish Name") or r.get("Name") or r.get("dish_name") or r.get("item_name") or ""
        cat = r.get("Category") or r.get("category") or "General"
        price = r.get("Price") or r.get("price") or 0
        diet = r.get("Veg/NonVeg") or r.get("Dietary") or r.get("diet") or "Veg"

        name = str(name).strip()
        if not name:
            continue

        try:
            price_val = float(str(price).replace("₹", "").replace("$", "").strip())
        except Exception:
            price_val = 0.0

        normalized.append({
            "name": name,
            "category": str(cat).strip(),
            "price": price_val,
            "diet": str(diet).strip(),
        })

    return normalized


def parse_bulk_csv_text(csv_text: str) -> List[Dict[str, Any]]:
    """
    Parses Saturdays Bulk Import format:
    Category, Dish Name, Price, Veg/NonVeg
    """
    lines = csv_text.strip().splitlines()
    items = []
    reader = csv.reader(lines)
    for row in reader:
        if not row or len(row) < 2:
            continue
        first = row[0].strip().lower()
        if "category" in first and "dish" in "".join(row).lower():
            continue  # Header row
        category = row[0].strip()
        name = row[1].strip()
        price = 0.0
        diet = "Veg"
        if len(row) >= 3:
            try:
                price = float(row[2].replace("₹", "").strip())
            except Exception:
                price = 0.0
        if len(row) >= 4:
            diet = row[3].strip()

        if name:
            items.append({
                "category": category,
                "name": name,
                "price": price,
                "diet": diet,
            })
    return items


# ─────────────────────────────────────────────────────────────────────────────
# 2. LOCAL LLM DESCRIPTION ENGINE (Ollama Qwen 32B)
# ─────────────────────────────────────────────────────────────────────────────

def generate_dish_description(
    dish_name: str,
    category: str,
    diet: str,
    restaurant_style: str = "appetizing authentic culinary presentation, warm soft lighting",
    model: str = OLLAMA_MODEL,
) -> str:
    """
    Queries local Qwen 32B via Ollama to generate high-fidelity food prompts.
    """
    prompt = (
        f"You are a master food photography art director and prompt engineer.\n"
        f"Generate a vivid, sensory, single-paragraph image generation prompt for this dish:\n"
        f"- Dish: {dish_name}\n"
        f"- Category: {category}\n"
        f"- Dietary: {diet}\n"
        f"- Style: {restaurant_style}\n\n"
        f"Instructions:\n"
        f"Describe plating, textures (steam, sizzle, garnish, glaze), camera lens (50mm macro, f/1.8), "
        f"composition, and appetizing culinary details. Keep it under 100 words. "
        f"Output ONLY the prompt text with no intro or quotation marks."
    )

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
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
        # Fallback if Ollama is unreachable
        return (
            f"Vivid gourmet food photography of {dish_name}, fresh {category}, {diet}, "
            f"beautifully garnished, mouth-watering presentation, soft ambient lighting, high resolution"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. GPU IMAGE GENERATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def ensure_gpu_server_running(model: str = "sdxl") -> bool:
    """Verifies that the GPU helper server is running on port 8765, or starts it."""
    try:
        req = urllib.request.Request(f"{GPU_SERVER_URL}/health")
        with urllib.request.urlopen(req, timeout=2) as r:
            if r.status == 200:
                return True
    except Exception:
        pass

    gen_script = BASE_DIR / "menu_image_pipeline" / "generate_image.py"
    if not gen_script.exists():
        return False

    print("  [GPU] Launching PyTorch GPU Image Server (Python 3.12)...")
    cmd = [PY312_EXE, str(gen_script), "serve", "--model", model]
    flags = 0x00000200 if sys.platform == "win32" else 0
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)

    for _ in range(45):
        time.sleep(2)
        try:
            req = urllib.request.Request(f"{GPU_SERVER_URL}/health")
            with urllib.request.urlopen(req, timeout=2) as r:
                if r.status == 200:
                    print("  [GPU] Worker is ready and loaded in VRAM.")
                    return True
        except Exception:
            pass
    return False


def generate_image_asset(
    prompt: str,
    output_path: Path,
    backend: str = DEFAULT_IMAGE_BACKEND,
) -> bool:
    """
    Renders image using the selected backend.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if backend == "gpu_server":
        if ensure_gpu_server_running():
            try:
                payload = json.dumps({
                    "prompt": prompt,
                    "out_path": str(output_path),
                    "steps": 25,
                    "guidance": 7.0,
                }).encode("utf-8")
                req = urllib.request.Request(
                    f"{GPU_SERVER_URL}/generate",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=180) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    return res.get("ok", False)
            except Exception as e:
                print(f"  [GPU Server Warning] {e}")

    elif backend == "comfyui":
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
                "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
                "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
                "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt + ", 8k food photography", "clip": ["4", 1]}},
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
                            urllib.request.urlretrieve(img_url, output_path)
                            return True
        except Exception as e:
            print(f"  [ComfyUI Error] {e}")

    elif backend == "minimax_api":
        api_key = os.environ.get("MINIMAX_API_KEY", "")
        if api_key:
            try:
                payload = json.dumps({
                    "model": "image-01",
                    "prompt": prompt + ", professional food photo",
                    "aspect_ratio": "1:1"
                }).encode("utf-8")
                req = urllib.request.Request(
                    "https://api.minimaxi.chat/v1/image_generation",
                    data=payload,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if "data" in data and len(data["data"]) > 0:
                    url = data["data"][0]["url"]
                    urllib.request.urlretrieve(url, output_path)
                    return True
            except Exception as e:
                print(f"  [MiniMax Error] {e}")

    return False


# ─────────────────────────────────────────────────────────────────────────────
# 4. MANIFEST & BATCH PROCESSOR
# ─────────────────────────────────────────────────────────────────────────────

class MenuPipelineManager:
    """Manages the full lifecycle from raw dishes to final manifest and images."""

    def __init__(self, restaurant_name: str, style_hint: str = "", output_dir: Optional[Path] = None):
        self.restaurant_name = restaurant_name
        self.style_hint = style_hint or "Authentic food photography, high quality"
        self.output_dir = output_dir or (OUTPUT_ROOT / safe_filename(restaurant_name))
        self.manifest_file = self.output_dir / "manifest.json"
        self.output_dir.mkdir(parents=True, exist_ok=True)
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

    def process_items(
        self,
        items: List[Dict[str, Any]],
        backend: str = DEFAULT_IMAGE_BACKEND,
        dry_run: bool = False,
        concurrency: int = 4,
    ) -> Dict[str, Any]:
        """
        Executes description generation and image synthesis across all items.
        Utilizes multithreading to speed up LLM calls.
        """
        print(f"\n🚀 Processing {len(items)} items for '{self.restaurant_name}'...")
        
        # Phase 1: Ensure descriptions exist (parallelized for speed)
        items_to_describe = [
            it for it in items
            if it["name"] not in self.manifest or not self.manifest[it["name"]].get("description")
        ]

        if items_to_describe:
            print(f"  ⚡ Generating {len(items_to_describe)} descriptions via Ollama ({OLLAMA_MODEL})...")
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {
                    executor.submit(
                        generate_dish_description,
                        it["name"],
                        it["category"],
                        it["diet"],
                        self.style_hint
                    ): it for it in items_to_describe
                }
                for f in as_completed(futures):
                    it = futures[f]
                    desc = f.result()
                    item_name = it["name"]
                    if item_name not in self.manifest:
                        self.manifest[item_name] = {
                            "category": it["category"],
                            "name": it["name"],
                            "price": it["price"],
                            "diet": it["diet"],
                            "description": desc,
                            "image_path": "",
                            "image_url": "",
                            "status": "described",
                        }
                    else:
                        self.manifest[item_name]["description"] = desc
                    self.save_manifest()

        # Phase 2: Generate Images
        for it in items:
            name = it["name"]
            cat = it["category"]
            record = self.manifest.get(name, {})
            desc = record.get("description", "")
            img_path = record.get("image_path", "")

            # If image already generated and exists on disk, skip
            if img_path and Path(img_path).exists():
                print(f"  ✓ [Cached] {name}")
                continue

            target_path = self.output_dir / safe_filename(cat) / f"{safe_filename(name)}.png"

            if dry_run:
                print(f"  [Dry-Run] {name} -> prompt ready.")
                self.manifest[name]["image_path"] = str(target_path)
                self.manifest[name]["status"] = "dry_run_ready"
            else:
                print(f"  🎨 Generating image for '{name}'...")
                ok = generate_image_asset(desc, target_path, backend=backend)
                if ok:
                    self.manifest[name]["image_path"] = str(target_path)
                    self.manifest[name]["status"] = "completed"
                    print(f"    ✓ Saved -> {target_path.name}")
                else:
                    self.manifest[name]["status"] = "image_pending"
                    print(f"    ⚠ Image pending (Model / backend standby)")

            self.manifest[name]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
            self.save_manifest()

        print(f"\n✅ Completed batch for {self.restaurant_name}. Manifest at {self.manifest_file}")
        return self.manifest


# ─────────────────────────────────────────────────────────────────────────────
# 5. FASTAPI MICROSERVICE / EMBEDDED ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────

def create_fastapi_app():
    """Builds the standalone FastAPI app for serving the pipeline via REST API."""
    try:
        from fastapi import FastAPI, HTTPException, BackgroundTasks
        from fastapi.middleware.cors import CORSMiddleware
        from pydantic import BaseModel
    except ImportError:
        print("FastAPI not installed in current environment. Run: pip install fastapi uvicorn")
        sys.exit(1)

    app = FastAPI(
        title="LMX Menu Image & Asset Pipeline API",
        version="1.0.0",
        description="High-performance AI menu asset generation microservice for Saturdays Platform",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    class BulkImportRequest(BaseModel):
        restaurant_name: str
        style_hint: Optional[str] = "Modern culinary plating, warm soft lighting"
        csv_data: Optional[str] = None
        file_path: Optional[str] = None
        backend: Optional[str] = DEFAULT_IMAGE_BACKEND
        dry_run: Optional[bool] = False

    class SingleDishRequest(BaseModel):
        restaurant_name: str
        dish_name: str
        category: str
        diet: Optional[str] = "Veg"
        price: Optional[float] = 0.0
        style_hint: Optional[str] = ""
        backend: Optional[str] = DEFAULT_IMAGE_BACKEND

    @app.get("/health")
    def health():
        return detect_system_resources()

    @app.get("/manifest/{restaurant_name}")
    def get_manifest(restaurant_name: str):
        mgr = MenuPipelineManager(restaurant_name)
        return {"restaurant": restaurant_name, "count": len(mgr.manifest), "items": mgr.manifest}

    @app.post("/menu/generate-batch")
    def generate_batch(req: BulkImportRequest, background_tasks: BackgroundTasks):
        items = []
        if req.file_path:
            items = parse_menu_file(req.file_path)
        elif req.csv_data:
            items = parse_bulk_csv_text(req.csv_data)
        else:
            raise HTTPException(status_code=400, detail="Provide either csv_data or file_path")

        mgr = MenuPipelineManager(req.restaurant_name, req.style_hint)
        # Run asynchronously in background tasks
        background_tasks.add_task(mgr.process_items, items, req.backend, req.dry_run)

        return {
            "status": "queued",
            "restaurant": req.restaurant_name,
            "total_items": len(items),
            "manifest_file": str(mgr.manifest_file),
        }

    @app.post("/dish/generate-single")
    def generate_single(req: SingleDishRequest):
        mgr = MenuPipelineManager(req.restaurant_name, req.style_hint)
        desc = generate_dish_description(req.dish_name, req.category, req.diet, mgr.style_hint)
        target_path = mgr.output_dir / safe_filename(req.category) / f"{safe_filename(req.dish_name)}.png"
        
        ok = generate_image_asset(desc, target_path, backend=req.backend)
        mgr.manifest[req.dish_name] = {
            "category": req.category,
            "name": req.dish_name,
            "price": req.price,
            "diet": req.diet,
            "description": desc,
            "image_path": str(target_path) if ok else "",
            "status": "completed" if ok else "image_pending",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        mgr.save_manifest()

        return {
            "dish_name": req.dish_name,
            "description": desc,
            "image_saved": ok,
            "image_path": str(target_path) if ok else "",
        }

    return app


# ─────────────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LMX Labs — Unified Menu Pipeline")
    parser.add_argument("--restaurant", default="KC FOODS", help="Target restaurant name")
    parser.add_argument("--file", help="Path to menu .xlsx / .csv file")
    parser.add_argument("--backend", default=DEFAULT_IMAGE_BACKEND, choices=["gpu_server", "comfyui", "minimax_api", "a1111"])
    parser.add_argument("--dry-run", action="store_true", help="Generate LLM descriptions without image synthesis")
    parser.add_argument("--serve", action="store_true", help="Start standalone FastAPI REST microservice")
    parser.add_argument("--port", type=int, default=8000, help="API server port")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent workers for Ollama calls")

    args = parser.parse_args()

    if args.serve:
        import uvicorn
        app = create_fastapi_app()
        print(f"🔥 Starting LMX Menu Pipeline Server on http://0.0.0.0:{args.port}...")
        uvicorn.run(app, host="0.0.0.0", port=args.port)
        return

    # Find restaurant config
    resto_conf = next((r for r in DEFAULT_RESTAURANTS if r["name"].lower() == args.restaurant.lower()), None)
    menu_file = args.file or (resto_conf["menu_file"] if resto_conf else None)
    style = resto_conf["style"] if resto_conf else "Fine dining culinary plating"

    if not menu_file or not Path(menu_file).exists():
        print(f"❌ Error: Menu file not found for restaurant '{args.restaurant}'.")
        print("Please provide --file path/to/menu.xlsx")
        sys.exit(1)

    items = parse_menu_file(menu_file)
    mgr = MenuPipelineManager(args.restaurant, style)
    mgr.process_items(items, backend=args.backend, dry_run=args.dry_run, concurrency=args.workers)


if __name__ == "__main__":
    main()
