"""
=============================================================================
 Saturdays.co.in — AI Menu & GPU Image Generation Studio (Frontend & Backend)
=============================================================================
Full Web Dashboard for:
  1. Image to Text (Visual Menu OCR via Gemini Vision 2.0 Flash)
  2. Text to Description (Local Qwen 2.5 32B via Ollama)
  3. Description to Image (Local NVIDIA RTX A4000 GPU via ComfyUI Krea2 Turbo)
  4. Live Saturdays Cloud Run Sync & Image Publishing

Run with:
  python pipeline_dashboard.py --port 8000
Then open:
  http://localhost:8000 in your browser.
=============================================================================
"""

import argparse
import base64
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Configuration Defaults
LIVE_API_BASE = os.environ.get(
    "SATURDAYS_API_BASE",
    "https://saturdays-api-373465250617.us-central1.run.app/api/v1"
)
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
COMFYUI_DISK_OUTPUT = Path(r"C:\Users\Student\AppData\Local\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\output")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")

# Firebase Storage & Auth Config for Saturdays
FIREBASE_CONFIG = {
    "apiKey": "AIzaSyCXos_Se1nxdMTFqyIUyoA6MLLzGH6O8lQ",
    "authDomain": "saturdays-lmx.firebaseapp.com",
    "projectId": "saturdays-lmx",
    "storageBucket": "saturdays-lmx.firebasestorage.app",
    "messagingSenderId": "819389358402",
    "appId": "1:819389358402:web:ae4050eef424a25feaa8db",
    "measurementId": "G-54WS2QLVEW",
}

WORKSPACE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = WORKSPACE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def safe_name(name: str) -> str:
    clean = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    return re.sub(r"\s+", "_", clean)[:64]


def make_mobile_safe_media_url(media_id: str, api_base: str = LIVE_API_BASE) -> str:
    """Formats the media URL with encoded first char (%30) so frontend regex A does not
    hijack it to non-existent Firebase GCS paths. Starlette/FastAPI unquotes %30 back to 0.
    """
    if not media_id:
        return ""
    encoded_first = f"%{ord(media_id[0]):02x}"
    return f"{api_base.rstrip('/')}/media/{encoded_first}{media_id[1:]}"



# -----------------------------------------------------------------------------
# Background Batch State Management
# -----------------------------------------------------------------------------

class BatchManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.is_running = False
        self.should_stop = False
        self.restaurant_name = ""
        self.total = 0
        self.completed = 0
        self.failed = 0
        self.current_dish = ""
        self.current_step = ""
        self.start_time = 0.0
        self.log_messages: List[str] = []

    def log(self, msg: str):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {msg}"
        with self.lock:
            self.log_messages.append(entry)
            if len(self.log_messages) > 100:
                self.log_messages.pop(0)

    def to_dict(self) -> Dict[str, Any]:
        with self.lock:
            elapsed = int(time.time() - self.start_time) if self.is_running else 0
            return {
                "is_running": self.is_running,
                "restaurant_name": self.restaurant_name,
                "total": self.total,
                "completed": self.completed,
                "failed": self.failed,
                "current_dish": self.current_dish,
                "current_step": self.current_step,
                "elapsed_seconds": elapsed,
                "recent_logs": self.log_messages[-15:],
            }


batch_mgr = BatchManager()

# Global Auth Token Storage
current_auth_state = {
    "token": os.environ.get("SATURDAYS_AUTH_TOKEN", ""),
    "email": "",
}


# -----------------------------------------------------------------------------
# Hardware & Engine Checks
# -----------------------------------------------------------------------------

def check_comfyui() -> bool:
    try:
        req = urllib.request.Request(f"{COMFYUI_URL}/system_stats")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def check_ollama() -> Tuple[bool, str]:
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name", "") for m in data.get("models", [])]
            for pref in ["qwen2.5-coder:7b", "gpt-oss:20b", "qwen2.5-coder:32b", DEFAULT_OLLAMA_MODEL]:
                if pref in models:
                    return True, pref
            return (True, models[0]) if models else (True, DEFAULT_OLLAMA_MODEL)
    except Exception:
        return False, ""


def check_cloud_run() -> bool:
    try:
        req = urllib.request.Request(f"{LIVE_API_BASE}/branches", headers={"User-Agent": "Studio/2.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception:
        return False


# -----------------------------------------------------------------------------
# AI Operations: Prompt & ComfyUI Synthesis
# -----------------------------------------------------------------------------

def generate_sensory_prompt(
    dish_name: str,
    category: str,
    restaurant_name: str,
    diet_is_veg: bool,
    active_model: str = DEFAULT_OLLAMA_MODEL
) -> str:
    diet_str = "Vegetarian" if diet_is_veg else "Non-Vegetarian"
    system_prompt = (
        f"You are a master food photography art director.\n"
        f"Generate a vivid, sensory food photography prompt for this item:\n"
        f"- Restaurant: {restaurant_name}\n"
        f"- Dish: {dish_name}\n"
        f"- Category: {category}\n"
        f"- Dietary: {diet_str}\n\n"
        f"Instructions:\n"
        f"Focus on artisanal plating, macro lens details (f/1.8, 50mm), textures (crust, glaze, steam, garnish), "
        f"lighting (warm soft morning/ambient light), and mouth-watering presentation. Keep it under 80 words. "
        f"Output ONLY the prompt text, without introductory remarks."
    )

    payload = json.dumps({
        "model": active_model,
        "messages": [{"role": "user", "content": system_prompt}],
        "stream": False,
        "keep_alive": 0,
        "options": {"temperature": 0.7, "top_p": 0.9},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            text = data["message"]["content"].strip()
            return re.sub(r'^["\']|["\']$', '', text).strip()
    except Exception:
        return (
            f"Artisanal culinary photography of freshly prepared {dish_name}, {category}, {diet_str}, "
            f"served at {restaurant_name}. Warm cinematic restaurant lighting, shallow depth of field, "
            f"gourmet garnish, mouth-watering textures, f/1.8 macro lens capture, 8k resolution."
        )


def queue_and_poll_comfyui(prompt_text: str, filename_prefix: str, resolution: int = 480, timeout_seconds: int = 180) -> Optional[bytes]:
    w = max(256, (int(resolution) // 16) * 16)
    h = max(256, (int(resolution) // 16) * 16)
    workflow = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt_text, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry, bad food, distorted, low quality, messy, unappetizing", "clip": ["2", 0]}},
        "6": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0], "seed": int(time.time() * 1000) % 2147483647, "steps": 6, "cfg": 1.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": filename_prefix, "images": ["8", 0]}}
    }

    payload = json.dumps({"prompt": workflow}).encode("utf-8")
    req = urllib.request.Request(f"{COMFYUI_URL}/prompt", data=payload, headers={"Content-Type": "application/json"}, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            prompt_id = data.get("prompt_id")
    except Exception as e:
        print(f"[ComfyUI Error] {e}")
        return None

    if not prompt_id:
        return None

    start = time.time()
    while time.time() - start < timeout_seconds:
        time.sleep(1)
        try:
            h_req = urllib.request.Request(f"{COMFYUI_URL}/history/{prompt_id}")
            with urllib.request.urlopen(h_req, timeout=5) as h_resp:
                h_data = json.loads(h_resp.read().decode("utf-8"))
                if prompt_id in h_data:
                    outputs = h_data[prompt_id].get("outputs", {})
                    for _, node_out in outputs.items():
                        imgs = node_out.get("images", [])
                        if imgs:
                            img_info = imgs[0]
                            fn = img_info["filename"]
                            sub = img_info.get("subfolder", "")
                            ft = img_info.get("type", "output")

                            disk_f = COMFYUI_DISK_OUTPUT / sub / fn
                            if disk_f.exists():
                                return disk_f.read_bytes()

                            v_url = f"{COMFYUI_URL}/view?filename={urllib.parse.quote(fn)}&subfolder={urllib.parse.quote(sub)}&type={ft}"
                            with urllib.request.urlopen(v_url, timeout=30) as v_resp:
                                return v_resp.read()
        except Exception:
            pass

    return None


# -----------------------------------------------------------------------------
# Vision: Image-to-Text via Gemini 2.0 Flash Free API
# -----------------------------------------------------------------------------

def extract_menu_from_image(image_bytes: bytes, mime_type: str, api_key: str) -> List[Dict[str, Any]]:
    """Extracts dishes, prices, categories from a menu photo using Gemini Vision."""
    b64_data = base64.b64encode(image_bytes).decode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"

    prompt = (
        "You are an expert restaurant menu transcription engine.\n"
        "Transcribe and parse every dish item from this menu image into a clean JSON array.\n"
        "Each object must have:\n"
        "- category: (string, e.g. Starters, Main Course, Cupcakes, Desserts, Beverages)\n"
        "- name: (string, the clear dish name)\n"
        "- price: (number, parsed numeric price in ₹ without currency symbols, default 0.0 if not listed)\n"
        "- diet: (string, exactly 'Veg' or 'NonVeg')\n\n"
        "Ensure all dishes are captured, typos are corrected, and duplicates removed.\n"
        "Output ONLY valid JSON."
    )

    payload = json.dumps({
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": mime_type, "data": b64_data}},
                {"text": prompt}
            ]
        }],
        "generationConfig": {
            "temperature": 0.2,
            "response_mime_type": "application/json"
        }
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "items" in parsed:
            parsed = parsed["items"]
        return parsed


# -----------------------------------------------------------------------------
# Saturdays Cloud Run Live Integration
# -----------------------------------------------------------------------------

def upload_image_to_saturdays(image_bytes: bytes, token: str) -> Optional[str]:
    headers = {"Content-Type": "image/png", "Authorization": f"Bearer {token}", "User-Agent": "Studio/2.0"}
    req = urllib.request.Request(f"{LIVE_API_BASE}/media", data=image_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return str(data.get("id"))
    except Exception as e:
        print(f"[Upload Error] {e}")
        return None


def patch_dish_on_saturdays(item_id: str, description: str, image_url: str, token: str) -> bool:
    payload = json.dumps({"description": description, "image_url": image_url}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}", "User-Agent": "Studio/2.0"}
    req = urllib.request.Request(f"{LIVE_API_BASE}/menu/items/{item_id}", data=payload, headers=headers, method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[Patch Error] {e}")
        return False


# -----------------------------------------------------------------------------
# FastAPI App & Endpoints
# -----------------------------------------------------------------------------

app = FastAPI(title="Saturdays AI Menu Studio", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Static Files Mount for Generated Images
app.mount("/static/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")


@app.get("/api/status")
def get_status():
    comfy_ok = check_comfyui()
    ollama_ok, ollama_model = check_ollama()
    cloud_ok = check_cloud_run()
    return {
        "gpu": "NVIDIA RTX A4000 (16 GB)",
        "comfyui": {"online": comfy_ok, "url": COMFYUI_URL},
        "ollama": {"online": ollama_ok, "model": ollama_model, "url": OLLAMA_URL},
        "cloud_run": {"online": cloud_ok, "url": LIVE_API_BASE},
        "auth": {"has_token": bool(current_auth_state["token"]), "email": current_auth_state["email"]},
    }


@app.post("/api/auth/token")
def set_auth_token(payload: Dict[str, str]):
    token = payload.get("token", "").strip()
    current_auth_state["token"] = token
    return {"success": True, "has_token": bool(token)}


@app.post("/api/auth/login")
def login_saturdays(payload: Dict[str, str]):
    email = payload.get("email", "").strip()
    password = payload.get("password", "").strip()
    login_data = json.dumps({"email": email, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        f"{LIVE_API_BASE}/auth/login",
        data=login_data,
        headers={"Content-Type": "application/json", "User-Agent": "Studio/2.0"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            token = data.get("access_token")
            if token:
                current_auth_state["token"] = token
                current_auth_state["email"] = email
                return {"success": True, "token": token, "email": email}
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=e.code, detail=err_msg)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/branches")
def list_branches():
    req = urllib.request.Request(f"{LIVE_API_BASE}/branches", headers={"User-Agent": "Studio/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            branches = json.loads(resp.read().decode("utf-8"))
            return branches
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch branches: {e}")


@app.get("/api/branches/{branch_id}/menu")
def get_branch_menu_endpoint(branch_id: str):
    b_req = urllib.request.Request(f"{LIVE_API_BASE}/branches", headers={"User-Agent": "Studio/2.0"})
    with urllib.request.urlopen(b_req, timeout=10) as b_resp:
        branches = json.loads(b_resp.read().decode("utf-8"))
    branch = next((b for b in branches if b["id"] == branch_id), None)
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    branch_name = branch["name"]
    rest_safe = safe_name(branch_name)
    manifest_file = OUTPUT_DIR / rest_safe / "manifest.json"
    manifest = {}
    if manifest_file.exists():
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            pass

    m_req = urllib.request.Request(f"{LIVE_API_BASE}/branches/{branch_id}/menu", headers={"User-Agent": "Studio/2.0"})
    with urllib.request.urlopen(m_req, timeout=10) as m_resp:
        menu = json.loads(m_resp.read().decode("utf-8"))

    categories = menu.get("categories", [])
    enriched_categories = []

    for cat in categories:
        cat_name = cat.get("name", "General")
        enriched_items = []
        for item in cat.get("items", []):
            d_name = item["name"]
            existing = manifest.get(d_name, {})

            local_img_path = OUTPUT_DIR / rest_safe / safe_name(cat_name) / f"{safe_name(d_name)}.png"
            has_local_img = local_img_path.exists()
            local_preview_url = f"/static/output/{rest_safe}/{safe_name(cat_name)}/{safe_name(d_name)}.png" if has_local_img else None

            enriched_items.append({
                "id": item["id"],
                "name": d_name,
                "category": cat_name,
                "price": item.get("base_price", 0.0),
                "is_veg": item.get("is_veg", False),
                "live_image_url": item.get("image_url"),
                "live_description": item.get("description"),
                "description": existing.get("description") or item.get("description") or "",
                "has_local_image": has_local_img,
                "local_image_url": local_preview_url,
                "firebase_image_url": existing.get("firebase_image_url"),
                "gcs_image_url": existing.get("gcs_image_url"),
                "uploaded": existing.get("uploaded", False),
                "status": "published_live" if existing.get("uploaded") else ("ready_to_upload" if has_local_img else "pending")
            })

        enriched_categories.append({
            "id": cat.get("id"),
            "name": cat_name,
            "items": enriched_items
        })

    return {
        "branch_id": branch_id,
        "branch_name": branch_name,
        "categories": enriched_categories,
        "total_dishes": sum(len(c["items"]) for c in enriched_categories),
    }


# Vision: Menu Image Upload -> Structured Dishes
@app.post("/api/vision/extract")
async def vision_extract(
    file: UploadFile = File(...),
    gemini_key: str = Form(...)
):
    if not gemini_key.strip():
        raise HTTPException(status_code=400, detail="Gemini API Key is required for Visual Menu extraction.")

    content = await file.read()
    mime = file.content_type or "image/jpeg"
    try:
        dishes = extract_menu_from_image(content, mime, gemini_key.strip())
        return {"count": len(dishes), "dishes": dishes}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Vision extraction failed: {e}")


# Generate Description for a single dish
@app.post("/api/dish/describe")
def describe_dish(payload: Dict[str, Any]):
    dish_name = payload.get("dish_name", "")
    category = payload.get("category", "")
    restaurant = payload.get("restaurant_name", "Gourmet Restaurant")
    is_veg = bool(payload.get("is_veg", True))

    _, active_model = check_ollama()
    desc = generate_sensory_prompt(dish_name, category, restaurant, is_veg, active_model=active_model or DEFAULT_OLLAMA_MODEL)
    return {"description": desc}


# Render Image on RTX A4000 GPU for a single dish
@app.post("/api/dish/render")
def render_dish(payload: Dict[str, Any]):
    restaurant = payload.get("restaurant_name", "Bonella Bakehouse")
    category = payload.get("category", "General")
    dish_name = payload.get("dish_name", "")
    description = payload.get("description", "")
    item_id = payload.get("item_id", "")
    price = payload.get("price", 0.0)
    is_veg = payload.get("is_veg", True)
    resolution = int(payload.get("resolution", 480))

    if not description:
        _, active_model = check_ollama()
        description = generate_sensory_prompt(dish_name, category, restaurant, is_veg, active_model=active_model or DEFAULT_OLLAMA_MODEL)

    rest_safe = safe_name(restaurant)
    cat_safe = safe_name(category)
    dish_safe = safe_name(dish_name)

    cat_dir = OUTPUT_DIR / rest_safe / cat_safe
    cat_dir.mkdir(parents=True, exist_ok=True)
    target_path = cat_dir / f"{dish_safe}.png"

    fn_prefix = f"{rest_safe}_{dish_safe}"
    img_bytes = queue_and_poll_comfyui(description, fn_prefix, resolution=resolution)
    if not img_bytes:
        raise HTTPException(status_code=500, detail="ComfyUI image synthesis timed out or failed.")

    target_path.write_bytes(img_bytes)

    # Update Manifest
    manifest_file = OUTPUT_DIR / rest_safe / "manifest.json"
    manifest = {}
    if manifest_file.exists():
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            pass

    manifest[dish_name] = {
        "id": item_id,
        "name": dish_name,
        "category": category,
        "price": price,
        "is_veg": is_veg,
        "description": description,
        "local_image_path": str(target_path),
        "uploaded": False,
        "status": "ready_to_upload",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    preview_url = f"/static/output/{rest_safe}/{cat_safe}/{dish_safe}.png"
    return {
        "success": True,
        "description": description,
        "local_image_url": preview_url,
        "bytes": len(img_bytes)
    }


# Upload & Patch a single dish to live Saturdays
@app.post("/api/dish/upload")
def upload_dish(payload: Dict[str, Any]):
    token = current_auth_state.get("token") or payload.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Saturdays Admin Token is required to publish live.")

    restaurant = payload.get("restaurant_name", "")
    category = payload.get("category", "")
    dish_name = payload.get("dish_name", "")
    item_id = payload.get("item_id", "")
    description = payload.get("description", "")

    rest_safe = safe_name(restaurant)
    cat_safe = safe_name(category)
    dish_safe = safe_name(dish_name)
    local_path = OUTPUT_DIR / rest_safe / cat_safe / f"{dish_safe}.png"

    if not local_path.exists():
        raise HTTPException(status_code=400, detail="Image has not been generated on GPU yet.")

    img_bytes = local_path.read_bytes()
    media_id = upload_image_to_saturdays(img_bytes, token)
    if not media_id:
        raise HTTPException(status_code=500, detail="Failed to upload image binary to /api/v1/media")

    live_media_url = make_mobile_safe_media_url(media_id)
    cdn_media_url = f"{LIVE_API_BASE}/cdn/{media_id}"
    patched = patch_dish_on_saturdays(item_id, description, live_media_url, token)
    if not patched:
        raise HTTPException(status_code=500, detail="Failed to patch menu item metadata on Cloud Run")

    # Update Manifest
    manifest_file = OUTPUT_DIR / rest_safe / "manifest.json"
    if manifest_file.exists():
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        if dish_name in manifest:
            manifest[dish_name]["uploaded"] = True
            manifest[dish_name]["media_id"] = media_id
            manifest[dish_name]["live_image_url"] = live_media_url
            manifest[dish_name]["safe_mobile_url"] = live_media_url
            manifest[dish_name]["cdn_image_url"] = cdn_media_url
            manifest[dish_name]["status"] = "published_live"
            with open(manifest_file, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)

    return {"success": True, "media_id": media_id, "live_image_url": live_media_url, "cdn_image_url": cdn_media_url}


# Upload all rendered dishes for a restaurant to live Saturdays
@app.post("/api/branch/upload-all")
def upload_all_for_branch(payload: Dict[str, Any]):
    token = current_auth_state.get("token") or payload.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Saturdays Admin Token is required to publish live.")

    restaurant = payload.get("restaurant_name", "")
    rest_safe = safe_name(restaurant)
    manifest_file = OUTPUT_DIR / rest_safe / "manifest.json"

    if not manifest_file.exists():
        raise HTTPException(status_code=404, detail=f"No generated manifest found for {restaurant}")

    with open(manifest_file, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    uploaded_count = 0
    errors = []

    for dish_name, data in manifest.items():
        if data.get("uploaded"):
            continue

        local_path = data.get("local_image_path")
        item_id = data.get("id")
        desc = data.get("description", "")

        if not local_path or not Path(local_path).exists():
            continue

        img_bytes = Path(local_path).read_bytes()
        media_id = upload_image_to_saturdays(img_bytes, token)
        if not media_id:
            errors.append(f"Failed to upload media for {dish_name}")
            continue

        live_media_url = make_mobile_safe_media_url(media_id)
        cdn_media_url = f"{LIVE_API_BASE}/cdn/{media_id}"
        patched = patch_dish_on_saturdays(item_id, desc, live_media_url, token)
        if not patched:
            errors.append(f"Failed to patch dish {dish_name}")
            continue

        data["uploaded"] = True
        data["media_id"] = media_id
        data["live_image_url"] = live_media_url
        data["safe_mobile_url"] = live_media_url
        data["cdn_image_url"] = cdn_media_url
        data["status"] = "published_live"
        uploaded_count += 1

    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    return {
        "success": True,
        "uploaded_count": uploaded_count,
        "errors": errors,
        "total_in_manifest": len(manifest),
    }


# Fix & Synchronize mobile-safe URLs for a branch (prevents frontend regex from redirecting to GCS)
@app.post("/api/branch/fix-mobile-urls")
def fix_mobile_urls_for_branch(payload: Dict[str, Any]):
    branch_id = payload.get("branch_id") or payload.get("branchId")
    branch_name = payload.get("branch_name") or payload.get("branchName")
    token = current_auth_state.get("token") or payload.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Saturdays Admin Token is required to update live items.")

    safe_folder = safe_name(branch_name or branch_id)
    manifest_file = OUTPUT_DIR / safe_folder / "manifest.json"
    manifest = {}
    if manifest_file.exists():
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            manifest = {}

    live_menu = get_branch_menu(branch_id)
    updated_items = []
    errors = []

    for cat in live_menu.get("categories", []):
        for item in cat.get("items", []):
            item_id = item.get("id")
            name = item.get("name")
            curr_url = item.get("image_url") or ""
            desc = item.get("description") or (manifest.get(name, {}).get("description") if manifest else None)

            media_id = None
            if name in manifest and manifest[name].get("media_id"):
                media_id = manifest[name]["media_id"]
            elif "/media/" in curr_url:
                parts = curr_url.split("/media/")[-1].split("?")[0].split("#")[0].replace("%30", "0")
                if len(parts) >= 32:
                    media_id = parts

            if media_id:
                safe_url = make_mobile_safe_media_url(media_id)
                cdn_url = f"{LIVE_API_BASE}/cdn/{media_id}"
                ok = patch_dish_on_saturdays(item_id, desc, safe_url, token)
                if ok:
                    updated_items.append({"name": name, "item_id": item_id, "safe_url": safe_url})
                    if name in manifest:
                        manifest[name]["live_image_url"] = safe_url
                        manifest[name]["safe_mobile_url"] = safe_url
                        manifest[name]["cdn_image_url"] = cdn_url
                        manifest[name]["uploaded"] = True
                        manifest[name]["status"] = "published_live"
                else:
                    errors.append(f"Failed to patch {name}")

    if manifest and manifest_file.parent.exists():
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    return {
        "success": True,
        "count": len(updated_items),
        "errors": errors,
        "items": updated_items,
    }


# Firebase Storage: Configuration & Bucket Mapping Endpoints
@app.get("/api/firebase/config")
def get_firebase_config():
    return FIREBASE_CONFIG


@app.post("/api/firebase/map-bucket")
def map_firebase_bucket(payload: Dict[str, Any]):
    restaurant = payload.get("restaurant_name", "")
    rest_safe = safe_name(restaurant)
    manifest_file = OUTPUT_DIR / rest_safe / "manifest.json"

    if not manifest_file.exists():
        raise HTTPException(status_code=404, detail=f"No manifest found for {restaurant}")

    with open(manifest_file, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    bucket = FIREBASE_CONFIG["storageBucket"]
    mapped = 0
    for name, item in manifest.items():
        local_p = item.get("local_image_path")
        if not local_p or not Path(local_p).exists():
            continue
        p = Path(local_p)
        remote_path = f"menu_images/{rest_safe}/{p.parent.name}/{p.name}"
        encoded_path = urllib.parse.quote(remote_path, safe="")
        fb_url = f"https://firebasestorage.googleapis.com/v0/b/{bucket}/o/{encoded_path}?alt=media"
        gcs_url = f"https://storage.googleapis.com/{bucket}/{remote_path}"

        item["firebase_bucket"] = bucket
        item["firebase_remote_path"] = remote_path
        item["firebase_image_url"] = fb_url
        item["gcs_image_url"] = gcs_url
        item["image_url"] = fb_url
        mapped += 1

    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    return {
        "success": True,
        "mapped_count": mapped,
        "bucket": bucket,
        "manifest": manifest
    }


# Batch Runner Worker Function
def batch_worker(restaurant_name: str, dishes: List[Dict[str, Any]], token: Optional[str] = None, resolution: int = 480):
    batch_mgr.is_running = True
    batch_mgr.should_stop = False
    batch_mgr.restaurant_name = restaurant_name
    batch_mgr.total = len(dishes)
    batch_mgr.completed = 0
    batch_mgr.failed = 0
    batch_mgr.start_time = time.time()
    batch_mgr.log(f"🚀 Started batch GPU image generation for {restaurant_name} ({len(dishes)} dishes at {resolution}px)")

    rest_safe = safe_name(restaurant_name)
    manifest_file = OUTPUT_DIR / rest_safe / "manifest.json"
    manifest = {}
    if manifest_file.exists():
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            pass

    _, active_model = check_ollama()

    for item in dishes:
        if batch_mgr.should_stop:
            batch_mgr.log("🛑 Batch generation stopped by user.")
            break

        d_name = item["name"]
        cat_name = item.get("category", "General")
        item_id = item.get("id", "")
        price = item.get("price", 0.0)
        is_veg = item.get("is_veg", False)

        batch_mgr.current_dish = d_name
        cat_safe = safe_name(cat_name)
        dish_safe = safe_name(d_name)
        target_path = OUTPUT_DIR / rest_safe / cat_safe / f"{dish_safe}.png"

        # Check existing description
        desc = manifest.get(d_name, {}).get("description")
        if not desc:
            batch_mgr.current_step = f"Generating prompt for {d_name}..."
            batch_mgr.log(f"Prompting {active_model or DEFAULT_OLLAMA_MODEL} for: {d_name}")
            desc = generate_sensory_prompt(d_name, cat_name, restaurant_name, is_veg, active_model=active_model or DEFAULT_OLLAMA_MODEL)

        # Check existing image
        img_bytes = None
        if target_path.exists():
            batch_mgr.log(f"Found existing render for {d_name}")
            img_bytes = target_path.read_bytes()
        else:
            batch_mgr.current_step = f"Rendering {d_name} ({resolution}px) on RTX A4000 GPU..."
            batch_mgr.log(f"Rendering {resolution}x{resolution} Krea2 Turbo on RTX A4000: {d_name}")
            fn_prefix = f"{rest_safe}_{dish_safe}"
            img_bytes = queue_and_poll_comfyui(desc, fn_prefix, resolution=resolution)
            if img_bytes:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(img_bytes)
                batch_mgr.log(f"✓ Render complete: {d_name} ({len(img_bytes):,} bytes)")
            else:
                batch_mgr.log(f"❌ Failed to render: {d_name}")
                batch_mgr.failed += 1
                continue

        # Optional Auto-Upload
        uploaded = False
        media_id = ""
        live_media_url = ""
        if token and img_bytes and item_id:
            batch_mgr.current_step = f"Uploading {d_name} to Saturdays..."
            media_id = upload_image_to_saturdays(img_bytes, token)
            if media_id:
                live_media_url = make_mobile_safe_media_url(media_id)
                cdn_media_url = f"{LIVE_API_BASE}/cdn/{media_id}"
                patched = patch_dish_on_saturdays(item_id, desc, live_media_url, token)
                if patched:
                    uploaded = True
                    batch_mgr.log(f"☁️ Published live to Saturdays (mobile-safe): {d_name}")

        manifest[d_name] = {
            "id": item_id,
            "name": d_name,
            "category": cat_name,
            "price": price,
            "is_veg": is_veg,
            "description": desc,
            "local_image_path": str(target_path),
            "uploaded": uploaded,
            "media_id": media_id,
            "live_image_url": live_media_url,
            "safe_mobile_url": live_media_url,
            "cdn_image_url": f"{LIVE_API_BASE}/cdn/{media_id}" if media_id else "",
            "status": "published_live" if uploaded else "ready_to_upload",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        batch_mgr.completed += 1

    batch_mgr.is_running = False
    batch_mgr.current_dish = ""
    batch_mgr.current_step = "Idle"
    batch_mgr.log(f"🏁 Batch complete! Processed {batch_mgr.completed}/{batch_mgr.total} dishes.")


@app.post("/api/batch/start")
def start_batch(payload: Dict[str, Any]):
    if batch_mgr.is_running:
        raise HTTPException(status_code=400, detail="A batch job is already running on the GPU.")

    restaurant_name = payload.get("restaurant_name", "")
    dishes = payload.get("dishes", [])
    token = current_auth_state.get("token") or payload.get("token")
    resolution = int(payload.get("resolution", 480))

    if not dishes:
        raise HTTPException(status_code=400, detail="No dishes provided to generate.")

    t = threading.Thread(target=batch_worker, args=(restaurant_name, dishes, token, resolution), daemon=True)
    t.start()
    return {"success": True, "message": f"Started batch generation for {len(dishes)} dishes at {resolution}px."}


@app.post("/api/batch/missing")
def start_batch_missing(payload: Dict[str, Any]):
    if batch_mgr.is_running:
        raise HTTPException(status_code=400, detail="A batch job is already running on the GPU.")

    branch_id = payload.get("branch_id")
    restaurant_name = payload.get("restaurant_name")
    token = current_auth_state.get("token") or payload.get("token")
    resolution = int(payload.get("resolution", 480))

    if not branch_id and not restaurant_name:
        raise HTTPException(status_code=400, detail="branch_id or restaurant_name is required.")

    if not branch_id:
        branches = get_live_branches()
        for b in branches:
            if restaurant_name.lower() in b.get("name", "").lower():
                branch_id = b["id"]
                restaurant_name = b["name"]
                break

    if not branch_id:
        raise HTTPException(status_code=404, detail=f"Branch not found for {restaurant_name}")

    menu = get_branch_menu(branch_id)
    b_name = menu.get("branch_name") or restaurant_name
    rest_safe = safe_name(b_name)

    missing_dishes = []
    for cat in menu.get("categories", []):
        cat_name = cat.get("name", "General")
        cat_safe = safe_name(cat_name)
        for item in cat.get("items", []):
            d_name = item.get("name", "")
            d_safe = safe_name(d_name)
            local_path = OUTPUT_DIR / rest_safe / cat_safe / f"{d_safe}.png"
            if not local_path.exists():
                missing_dishes.append({
                    "id": item.get("id"),
                    "name": d_name,
                    "category": cat_name,
                    "price": item.get("base_price", 0.0),
                    "is_veg": item.get("is_veg", False),
                    "description": item.get("description", "")
                })

    if not missing_dishes:
        return {"success": True, "count": 0, "message": f"All dishes for {b_name} already have generated images on disk! None missing."}

    t = threading.Thread(target=batch_worker, args=(b_name, missing_dishes, token, resolution), daemon=True)
    t.start()
    return {
        "success": True,
        "count": len(missing_dishes),
        "message": f"Started missing-only batch generation for {len(missing_dishes)} dishes at {resolution}px."
    }


@app.get("/api/batch/status")
def get_batch_status():
    return batch_mgr.to_dict()


@app.post("/api/batch/stop")
def stop_batch():
    batch_mgr.should_stop = True
    return {"success": True, "message": "Signal sent to stop batch generation."}


# -----------------------------------------------------------------------------
# Embedded Single-Page HTML Dashboard
# -----------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Saturdays AI Studio — Autonomous Food Photo Pipeline</title>
  <!-- Tailwind CSS & DaisyUI -->
  <link href="https://cdn.jsdelivr.net/npm/daisyui@4.10.1/dist/full.min.css" rel="stylesheet" type="text/css" />
  <script src="https://cdn.tailwindcss.com"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.13.8/dist/cdn.min.js"></script>
  <style>
    html, body {
      overflow-y: auto !important;
      overflow-x: hidden;
      height: auto !important;
      min-height: 100%;
      background-color: #0b0f17;
      color: #f1f5f9;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    .glass-card { background: rgba(22, 27, 34, 0.8); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.08); }
    .accent-gradient { background: linear-gradient(135deg, #f59e0b 0%, #ea580c 50%, #e11d48 100%); }
    .scrollbar-thin::-webkit-scrollbar { width: 6px; height: 6px; }
    .scrollbar-thin::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.15); border-radius: 4px; }
  </style>
</head>
<body x-data="studioApp()" x-init="init()" class="min-h-screen flex flex-col overflow-y-auto">

  <!-- Top Navigation Header -->
  <header class="border-b border-white/10 bg-black/40 backdrop-blur sticky top-0 z-50 px-6 py-3 flex items-center justify-between">
    <div class="flex items-center space-x-3">
      <div class="w-9 h-9 rounded-lg accent-gradient flex items-center justify-center font-black text-white shadow-lg shadow-orange-500/20">
        S
      </div>
      <div>
        <h1 class="text-lg font-bold text-white tracking-tight flex items-center gap-2">
          Saturdays AI Studio
          <span class="badge badge-sm badge-warning font-mono">RTX A4000</span>
        </h1>
        <p class="text-xs text-slate-400">Image to Text &bull; Sensory Art Direction &bull; GPU Synthesis &bull; Live Cloud Run</p>
      </div>
    </div>

    <!-- Live System Indicators -->
    <div class="flex items-center space-x-2 text-xs">
      <div class="flex items-center gap-1.5 px-3 py-1 rounded-full glass-card">
        <span class="w-2 h-2 rounded-full" :class="status.comfyui?.online ? 'bg-emerald-400 animate-pulse' : 'bg-rose-500'"></span>
        <span class="text-slate-300">GPU ComfyUI (8188)</span>
      </div>
      <div class="flex items-center gap-1.5 px-3 py-1 rounded-full glass-card">
        <span class="w-2 h-2 rounded-full" :class="status.ollama?.online ? 'bg-emerald-400' : 'bg-rose-500'"></span>
        <span class="text-slate-300">Ollama Qwen 32B</span>
      </div>
      <div class="flex items-center gap-1.5 px-3 py-1 rounded-full glass-card">
        <span class="w-2 h-2 rounded-full" :class="status.cloud_run?.online ? 'bg-emerald-400' : 'bg-amber-400'"></span>
        <span class="text-slate-300">Saturdays Cloud Run</span>
      </div>
      <div class="flex items-center gap-1.5 px-3 py-1 rounded-full glass-card">
        <span class="w-2 h-2 rounded-full bg-amber-400"></span>
        <span class="text-slate-300 font-mono text-[11px]">saturdays-lmx.firebasestorage.app</span>
      </div>

      <button @click="showAuthModal = true" class="btn btn-xs" :class="status.auth?.has_token ? 'btn-success btn-outline' : 'btn-warning'">
        <span x-text="status.auth?.has_token ? '🔑 Auth Active' : '🔑 Set Auth Token'"></span>
      </button>
    </div>
  </header>

  <!-- Main Container -->
  <main class="flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">

    <!-- Tab Selector -->
    <div class="flex border-b border-white/10 gap-2">
      <button @click="activeTab = 'live'" :class="activeTab === 'live' ? 'border-b-2 border-orange-500 text-orange-400 font-semibold' : 'text-slate-400 hover:text-white'" class="pb-3 px-4 text-sm flex items-center gap-2">
        <span>🏪</span> Live Saturdays Menus
      </button>
      <button @click="activeTab = 'vision'" :class="activeTab === 'vision' ? 'border-b-2 border-orange-500 text-orange-400 font-semibold' : 'text-slate-400 hover:text-white'" class="pb-3 px-4 text-sm flex items-center gap-2">
        <span>📸</span> Menu Image to Dishes (Vision Extraction)
      </button>
      <button @click="activeTab = 'batch'" :class="activeTab === 'batch' ? 'border-b-2 border-orange-500 text-orange-400 font-semibold' : 'text-slate-400 hover:text-white'" class="pb-3 px-4 text-sm flex items-center gap-2">
        <span>⚡</span> GPU Batch Generator
        <span x-show="batch.is_running" class="badge badge-xs badge-error animate-ping"></span>
      </button>
    </div>

    <!-- TAB 1: Live Saturdays Menus -->
    <div x-show="activeTab === 'live'" class="space-y-6">
      
      <!-- Branch Selector & Stats -->
      <div class="glass-card rounded-2xl p-6 flex flex-wrap items-center justify-between gap-4">
        <div class="flex items-center gap-4">
          <label class="text-sm font-semibold text-slate-300">Select Restaurant:</label>
          <select x-model="selectedBranchId" @change="loadMenu(selectedBranchId)" class="select select-bordered select-sm bg-slate-900 border-white/20 text-white min-w-[240px]">
            <template x-for="b in branches" :key="b.id">
              <option :value="b.id" x-text="b.name"></option>
            </template>
          </select>
          <button @click="loadMenu(selectedBranchId)" class="btn btn-square btn-ghost btn-xs text-slate-400">🔄</button>
        </div>

        <!-- Metrics -->
        <div class="flex items-center gap-3">
          <div class="text-center px-4 py-1.5 rounded-xl bg-white/5 border border-white/10">
            <div class="text-lg font-bold text-white" x-text="menuData.total_dishes || 0"></div>
            <div class="text-[10px] text-slate-400 uppercase tracking-wider">Total Items</div>
          </div>
          <div class="text-center px-4 py-1.5 rounded-xl bg-emerald-500/10 border border-emerald-500/20">
            <div class="text-lg font-bold text-emerald-400" x-text="countRendered()"></div>
            <div class="text-[10px] text-emerald-400 uppercase tracking-wider">Rendered</div>
          </div>
          <div class="text-center px-4 py-1.5 rounded-xl bg-blue-500/10 border border-blue-500/20">
            <div class="text-lg font-bold text-blue-400" x-text="countUploaded()"></div>
            <div class="text-[10px] text-blue-400 uppercase tracking-wider">Published</div>
          </div>
          <div class="text-center px-4 py-1.5 rounded-xl bg-amber-500/10 border border-amber-500/20 cursor-pointer hover:bg-amber-500/20 transition-all" @click="filterStatus = filterStatus === 'missing' ? 'all' : 'missing'" :class="filterStatus === 'missing' ? 'ring-2 ring-amber-400' : ''" title="Click to filter dishes by Missing">
            <div class="text-lg font-bold text-amber-400" x-text="countUnrendered()"></div>
            <div class="text-[10px] text-amber-400 uppercase tracking-wider">Missing</div>
          </div>

          <!-- Resolution Toggle (480px, 960px, 1600px) -->
          <div class="flex items-center gap-1 bg-white/5 border border-white/10 rounded-xl p-1 text-xs">
            <span class="text-slate-400 px-2 font-medium">Size:</span>
            <button @click="selectedResolution = 480" :class="selectedResolution === 480 ? 'bg-orange-500 text-white font-bold shadow-md' : 'text-slate-300 hover:text-white'" class="px-2.5 py-1 rounded-lg transition-colors" title="Fast Synthesis (4.5s on RTX A4000) - Recommended for Mobile">480px</button>
            <button @click="selectedResolution = 960" :class="selectedResolution === 960 ? 'bg-orange-500 text-white font-bold shadow-md' : 'text-slate-300 hover:text-white'" class="px-2.5 py-1 rounded-lg transition-colors" title="HD Synthesis (~12s)">960px</button>
            <button @click="selectedResolution = 1600" :class="selectedResolution === 1600 ? 'bg-orange-500 text-white font-bold shadow-md' : 'text-slate-300 hover:text-white'" class="px-2.5 py-1 rounded-lg transition-colors" title="Ultra HD Synthesis (~25s)">1600px</button>
          </div>

          <button @click="runAutomaticFlow()" :disabled="autoRunning || batch.is_running" class="btn btn-primary btn-sm gap-2 shadow-lg shadow-purple-500/20" title="1-Click Automatic: Generates all images on GPU, maps them to Firebase Storage, and publishes mobile-safe URLs live to Saturdays">
            <span>⚡</span>
            <span x-text="autoRunning ? 'Auto Running Pipeline...' : '⚡ 1-Click Auto Flow'"></span>
          </button>

          <button @click="startBatchForMissingDishes()" :disabled="batch.is_running || countUnrendered() === 0" class="btn btn-warning btn-sm gap-2 shadow-lg shadow-amber-500/20" title="Only generates dishes that do not have an image yet on the GPU">
            <span>⚡</span>
            <span x-text="batch.is_running ? 'Batch Running...' : (countUnrendered() > 0 ? `Render Missing (${countUnrendered()})` : 'All Rendered ✓')"></span>
          </button>

          <button @click="startBatchForCurrentBranch()" :disabled="batch.is_running" class="btn btn-outline btn-warning btn-sm gap-2">
            <span>🎨</span>
            <span x-text="batch.is_running ? 'Batch Running...' : 'Generate All on GPU'"></span>
          </button>

          <button @click="mapAllToFirebase()" :disabled="mappingFirebase || countRendered() === 0" class="btn btn-info btn-sm gap-2">
            <span>🔥</span>
            <span x-text="mappingFirebase ? 'Mapping...' : 'Map to Firebase Storage'"></span>
          </button>

          <button @click="publishAllForCurrentBranch()" :disabled="publishingAll || countRendered() === 0" class="btn btn-success btn-sm gap-2">
            <span>☁️</span>
            <span x-text="publishingAll ? 'Publishing Live...' : 'Publish All to Saturdays'"></span>
          </button>

          <button @click="fixMobileUrlsForCurrentBranch()" :disabled="fixingMobile" class="btn btn-sm btn-outline btn-info gap-2" title="Updates dish image URLs with mobile-safe encoding so images render properly on mobile phones without frontend regex hijacking">
            <span>📱</span>
            <span x-text="fixingMobile ? 'Fixing Mobile...' : '📱 Fix Mobile Display'"></span>
          </button>

        </div>
      </div>

      <!-- Dish Filter Tabs -->
      <div class="flex flex-wrap items-center justify-between bg-black/20 p-2.5 rounded-xl border border-white/5 gap-2">
        <div class="flex items-center gap-2">
          <span class="text-xs text-slate-400 font-medium px-2">Filter Menu:</span>
          <button @click="filterStatus = 'all'" :class="filterStatus === 'all' ? 'bg-white/20 text-white font-semibold' : 'text-slate-400 hover:text-white'" class="px-3 py-1 rounded-lg text-xs transition">
            All (<span x-text="menuData.total_dishes || 0"></span>)
          </button>
          <button @click="filterStatus = 'missing'" :class="filterStatus === 'missing' ? 'bg-amber-500 text-black font-bold shadow-md shadow-amber-500/30' : 'text-amber-400 hover:text-amber-300'" class="px-3 py-1 rounded-lg text-xs transition flex items-center gap-1.5">
            <span>⚠️</span> Missing Images (<span x-text="countUnrendered()"></span>)
          </button>
          <button @click="filterStatus = 'rendered'" :class="filterStatus === 'rendered' ? 'bg-emerald-500/20 text-emerald-400 font-semibold border border-emerald-500/40' : 'text-slate-400 hover:text-emerald-300'" class="px-3 py-1 rounded-lg text-xs transition">
            Rendered (<span x-text="countRendered()"></span>)
          </button>
          <button @click="filterStatus = 'published'" :class="filterStatus === 'published' ? 'bg-blue-500/20 text-blue-400 font-semibold border border-blue-500/40' : 'text-slate-400 hover:text-blue-300'" class="px-3 py-1 rounded-lg text-xs transition">
            Published (<span x-text="countUploaded()"></span>)
          </button>
        </div>
        <div x-show="filterStatus === 'missing' && countUnrendered() > 0" class="text-xs text-amber-400 font-medium flex items-center gap-2 pr-2">
          <span>Showing only unrendered dishes</span>
          <button @click="startBatchForMissingDishes()" :disabled="batch.is_running" class="btn btn-warning btn-xs gap-1">
            <span>⚡ Render Missing Now</span>
          </button>
        </div>
      </div>

      <!-- Categories & Dishes Grid -->
      <div class="space-y-8">
        <template x-for="cat in filteredCategories()" :key="cat.id">
          <div class="space-y-4">
            <div class="flex items-center justify-between border-b border-white/10 pb-2">
              <h2 class="text-base font-bold text-white flex items-center gap-2">
                <span class="w-2.5 h-2.5 rounded-full bg-orange-500"></span>
                <span x-text="cat.name"></span>
                <span class="text-xs font-normal text-slate-400" x-text="'(' + cat.items.length + ' items)'"></span>
              </h2>
            </div>

            <!-- Dishes Cards -->
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              <template x-for="dish in cat.items" :key="dish.id">
                <div class="glass-card rounded-xl p-4 flex flex-col justify-between space-y-3 transition hover:border-orange-500/40">
                  <div class="flex gap-4">
                    <!-- Thumbnail preview -->
                    <div class="w-24 h-24 rounded-lg bg-black/40 border border-white/10 flex-shrink-0 overflow-hidden relative group">
                      <img x-show="dish.local_image_url || dish.live_image_url" :src="dish.local_image_url || dish.live_image_url" class="w-full h-full object-cover">
                      <div x-show="!dish.local_image_url && !dish.live_image_url" class="w-full h-full flex flex-col items-center justify-center text-slate-600 text-xs text-center p-1">
                        <span>📷</span>
                        <span>No image</span>
                      </div>
                      <span x-show="dish.uploaded" class="absolute top-1 right-1 badge badge-success badge-xs">Live</span>
                    </div>

                    <!-- Details -->
                    <div class="flex-1 min-w-0">
                      <div class="flex items-start justify-between gap-1">
                        <h3 class="font-bold text-sm text-white truncate" x-text="dish.name"></h3>
                        <span class="badge badge-xs" :class="dish.is_veg ? 'badge-success' : 'badge-error'" x-text="dish.is_veg ? 'Veg' : 'NonVeg'"></span>
                      </div>
                      <div class="text-xs font-semibold text-orange-400 mt-0.5" x-text="'₹' + dish.price"></div>
                      <p class="text-[11px] text-slate-400 mt-1 line-clamp-2" :title="dish.description" x-text="dish.description || 'No sensory prompt generated yet.'"></p>
                    </div>
                  </div>

                  <!-- Actions -->
                  <div class="flex items-center justify-between pt-2 border-t border-white/5 text-xs">
                    <span class="text-[11px]" :class="dish.uploaded ? 'text-emerald-400' : (dish.has_local_image ? 'text-blue-400' : 'text-slate-500')" x-text="dish.uploaded ? '✓ Live on Saturdays' : (dish.has_local_image ? '● Ready to Upload' : '○ Not generated')"></span>

                    <div class="flex items-center gap-1.5">
                      <button @click="renderSingleDish(dish)" :disabled="dish.rendering" class="btn btn-xs btn-outline btn-warning">
                        <span x-text="dish.rendering ? 'Rendering...' : 'Render GPU'"></span>
                      </button>
                      <button x-show="dish.has_local_image && !dish.uploaded" @click="uploadSingleDish(dish)" :disabled="dish.uploading" class="btn btn-xs btn-success">
                        <span x-text="dish.uploading ? 'Uploading...' : 'Publish Live'"></span>
                      </button>
                    </div>
                  </div>
                </div>
              </template>
            </div>
          </div>
        </template>
      </div>

    </div>

    <!-- TAB 2: Menu Image to Dishes (Vision Extraction) -->
    <div x-show="activeTab === 'vision'" class="space-y-6">
      <div class="glass-card rounded-2xl p-6 space-y-5">
        <div>
          <h2 class="text-lg font-bold text-white flex items-center gap-2">
            <span>📸</span> Multimodal Visual Menu Extraction
          </h2>
          <p class="text-xs text-slate-400 mt-1">Upload a photo of a restaurant menu card, paper pamphlet, or printed sheet. Google Gemini 2.0 Flash Vision transcribes dishes, prices, and categories into clean data.</p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label class="block text-xs font-semibold text-slate-300 mb-1">Google Gemini API Key (Free tier):</label>
            <input type="password" x-model="geminiApiKey" placeholder="AIzaSy..." class="input input-bordered input-sm w-full bg-slate-900 border-white/20">
            <span class="text-[11px] text-slate-500">Get a free key from <a href="https://aistudio.google.com/app/apikey" target="_blank" class="text-orange-400 underline">Google AI Studio</a></span>
          </div>

          <div>
            <label class="block text-xs font-semibold text-slate-300 mb-1">Target Restaurant Name:</label>
            <input type="text" x-model="customRestaurantName" placeholder="e.g. Bonella Bakehouse" class="input input-bordered input-sm w-full bg-slate-900 border-white/20">
          </div>
        </div>

        <!-- File Upload Area -->
        <div class="border-2 border-dashed border-white/20 rounded-xl p-8 text-center hover:border-orange-500/50 transition cursor-pointer relative bg-black/20" @dragover.prevent @drop.prevent="handleFileDrop($event)">
          <input type="file" @change="handleFileSelect($event)" accept="image/*" class="absolute inset-0 opacity-0 cursor-pointer">
          <div class="space-y-2 pointer-events-none">
            <div class="text-3xl">📄</div>
            <div class="text-sm font-semibold text-white" x-text="selectedFile ? selectedFile.name : 'Drag & Drop Menu Image here, or click to browse'"></div>
            <p class="text-xs text-slate-500">Supports PNG, JPG, JPEG, WebP</p>
          </div>
        </div>

        <div class="flex justify-end">
          <button @click="extractFromImage()" :disabled="extracting || !selectedFile" class="btn btn-warning btn-sm gap-2">
            <span x-text="extracting ? '⏳ Transcribing with Gemini Vision...' : '✨ Extract Menu Items'"></span>
          </button>
        </div>
      </div>

      <!-- Extracted Dishes Table -->
      <div x-show="extractedDishes.length > 0" class="glass-card rounded-2xl p-6 space-y-4">
        <div class="flex items-center justify-between">
          <h3 class="font-bold text-white text-base" x-text="'Extracted Dishes (' + extractedDishes.length + ' found)'"></h3>
          <button @click="sendExtractedToBatch()" class="btn btn-warning btn-sm gap-2">
            <span>⚡</span>
            <span>Send All to RTX A4000 GPU Pipeline</span>
          </button>
        </div>

        <div class="overflow-x-auto max-h-[450px] scrollbar-thin">
          <table class="table table-xs w-full text-left">
            <thead>
              <tr class="border-b border-white/10 text-slate-400">
                <th>#</th>
                <th>Category</th>
                <th>Dish Name</th>
                <th>Price (₹)</th>
                <th>Diet</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              <template x-for="(d, i) in extractedDishes" :key="i">
                <tr class="border-b border-white/5 hover:bg-white/5">
                  <td class="text-slate-500" x-text="i + 1"></td>
                  <td><input type="text" x-model="d.category" class="input input-xs bg-transparent border border-white/10 rounded w-28"></td>
                  <td><input type="text" x-model="d.name" class="input input-xs bg-transparent border border-white/10 rounded w-48 font-semibold text-white"></td>
                  <td><input type="number" x-model="d.price" class="input input-xs bg-transparent border border-white/10 rounded w-20 text-orange-400"></td>
                  <td>
                    <select x-model="d.diet" class="select select-xs bg-transparent border border-white/10 rounded">
                      <option value="Veg">Veg</option>
                      <option value="NonVeg">NonVeg</option>
                    </select>
                  </td>
                  <td>
                    <button @click="extractedDishes.splice(i, 1)" class="btn btn-ghost btn-xs text-rose-400">✕</button>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- TAB 3: GPU Batch Generator -->
    <div x-show="activeTab === 'batch'" class="space-y-6">
      <div class="glass-card rounded-2xl p-6 space-y-4">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-lg font-bold text-white flex items-center gap-2">
              <span>⚡</span> Local GPU Synthesis Queue
            </h2>
            <p class="text-xs text-slate-400">Real-time monitoring of ComfyUI Krea2 Turbo rendering on NVIDIA RTX A4000 (16 GB VRAM).</p>
          </div>
          <button x-show="batch.is_running" @click="stopBatch()" class="btn btn-error btn-sm">Stop Pipeline</button>
        </div>

        <!-- Progress Bar -->
        <div class="space-y-2">
          <div class="flex justify-between text-xs text-slate-300">
            <span x-text="batch.is_running ? 'Processing: ' + batch.current_dish : 'Engine Idle'"></span>
            <span x-text="batch.completed + ' / ' + batch.total + ' completed'"></span>
          </div>
          <progress class="progress progress-warning w-full h-3" :value="batch.total ? (batch.completed / batch.total) * 100 : 0" max="100"></progress>
        </div>

        <!-- Live Terminal Log -->
        <div class="bg-black/60 rounded-xl p-4 font-mono text-xs text-slate-300 space-y-1 h-64 overflow-y-auto scrollbar-thin border border-white/10">
          <template x-for="log in batch.recent_logs" :key="log">
            <div x-text="log"></div>
          </template>
          <div x-show="batch.recent_logs.length === 0" class="text-slate-600 italic">No active logs. Start a batch from the Live Menus tab.</div>
        </div>
      </div>
    </div>

  </main>

  <!-- Auth Modal -->
  <div class="modal" :class="showAuthModal ? 'modal-open' : ''" x-show="showAuthModal">
    <div class="modal-box glass-card border border-white/20 bg-slate-900 text-white">
      <h3 class="font-bold text-lg">Saturdays Admin Authentication</h3>
      <p class="text-xs text-slate-400 py-2">Provide a Bearer JWT Token or log in with your Saturdays staff credentials to publish images live to production.</p>
      
      <div class="space-y-4 py-3">
        <div>
          <label class="block text-xs font-semibold mb-1">Bearer Access Token (JWT):</label>
          <textarea x-model="tempToken" placeholder="eyJhbGciOi..." class="textarea textarea-bordered w-full bg-black/40 text-xs font-mono"></textarea>
        </div>

        <div class="divider text-xs text-slate-500">OR LOGIN WITH CREDENTIALS</div>

        <div>
          <label class="block text-xs font-semibold mb-1">Staff Email:</label>
          <input type="email" x-model="loginEmail" placeholder="admin@saturdays.co.in" class="input input-bordered input-sm w-full bg-black/40">
        </div>
        <div>
          <label class="block text-xs font-semibold mb-1">Password:</label>
          <input type="password" x-model="loginPassword" placeholder="••••••••" class="input input-bordered input-sm w-full bg-black/40">
        </div>
      </div>

      <div class="modal-action flex justify-between">
        <button @click="performLogin()" :disabled="loggingIn" class="btn btn-sm btn-outline btn-warning">
          <span x-text="loggingIn ? 'Logging in...' : 'Login & Acquire Token'"></span>
        </button>
        <div class="space-x-2">
          <button @click="showAuthModal = false" class="btn btn-sm btn-ghost">Cancel</button>
          <button @click="saveToken()" class="btn btn-sm btn-success">Save Token</button>
        </div>
      </div>
    </div>
  </div>

  <script>
    function studioApp() {
      return {
        activeTab: 'live',
        status: {},
        branches: [],
        selectedBranchId: '',
        menuData: { categories: [] },
        geminiApiKey: localStorage.getItem('saturdays_gemini_key') || '',
        customRestaurantName: '',
        selectedFile: null,
        extracting: false,
        extractedDishes: [],
        batch: { is_running: false, recent_logs: [] },
        showAuthModal: false,
        tempToken: '',
        loginEmail: '',
        loginPassword: '',
        loggingIn: false,
        publishingAll: false,
        fixingMobile: false,
        selectedResolution: 480,
        autoRunning: false,
        filterStatus: 'all',

        async init() {
          await this.fetchStatus();
          await this.fetchBranches();
          this.pollBatch();
          setInterval(() => this.pollBatch(), 4000);
          setInterval(() => this.fetchStatus(), 10000);
        },

        async fetchStatus() {
          try {
            const res = await fetch('/api/status');
            this.status = await res.json();
          } catch (e) {}
        },

        async fetchBranches() {
          try {
            const res = await fetch('/api/branches');
            this.branches = await res.json();
            if (this.branches.length > 0 && !this.selectedBranchId) {
              this.selectedBranchId = this.branches[0].id;
              this.loadMenu(this.selectedBranchId);
            }
          } catch (e) {}
        },

        async loadMenu(branchId) {
          if (!branchId) return;
          try {
            const res = await fetch(`/api/branches/${branchId}/menu`);
            this.menuData = await res.json();
          } catch (e) {}
        },

        countRendered() {
          let c = 0;
          this.menuData.categories?.forEach(cat => cat.items.forEach(it => { if (it.has_local_image) c++; }));
          return c;
        },

        countUploaded() {
          let c = 0;
          this.menuData.categories?.forEach(cat => cat.items.forEach(it => { if (it.uploaded) c++; }));
          return c;
        },

        countUnrendered() {
          let c = 0;
          this.menuData.categories?.forEach(cat => cat.items.forEach(it => { if (!it.has_local_image) c++; }));
          return c;
        },

        matchesFilter(dish) {
          if (this.filterStatus === 'missing') return !dish.has_local_image;
          if (this.filterStatus === 'rendered') return dish.has_local_image;
          if (this.filterStatus === 'published') return dish.uploaded;
          return true;
        },

        filteredCategories() {
          if (!this.menuData.categories) return [];
          if (this.filterStatus === 'all') return this.menuData.categories;
          return this.menuData.categories.map(cat => ({
            ...cat,
            items: cat.items.filter(it => this.matchesFilter(it))
          })).filter(cat => cat.items.length > 0);
        },

        async renderSingleDish(dish) {
          dish.rendering = true;
          try {
            const res = await fetch('/api/dish/render', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                restaurant_name: this.menuData.branch_name,
                category: dish.category,
                dish_name: dish.name,
                description: dish.description,
                item_id: dish.id,
                price: dish.price,
                is_veg: dish.is_veg,
                resolution: this.selectedResolution
              })
            });
            const data = await res.json();
            if (data.success) {
              dish.local_image_url = data.local_image_url;
              dish.has_local_image = true;
              dish.description = data.description;
            } else {
              alert('Render failed: ' + (data.detail || 'Unknown error'));
            }
          } catch (e) {
            alert('Render failed: ' + e);
          } finally {
            dish.rendering = false;
          }
        },

        async uploadSingleDish(dish) {
          dish.uploading = true;
          try {
            const res = await fetch('/api/dish/upload', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                restaurant_name: this.menuData.branch_name,
                category: dish.category,
                dish_name: dish.name,
                item_id: dish.id,
                description: dish.description
              })
            });
            const data = await res.json();
            if (data.success) {
              dish.uploaded = true;
              dish.live_image_url = data.live_image_url;
            } else {
              alert('Upload failed: ' + (data.detail || 'Unknown error'));
            }
          } catch (e) {
            alert('Upload failed: ' + e);
          } finally {
            dish.uploading = false;
          }
        },

        async startBatchForMissingDishes() {
          if (this.batch.is_running) return alert('Batch generation is already running.');
          const missingDishes = [];
          this.menuData.categories?.forEach(cat => {
            cat.items.forEach(it => {
              if (!it.has_local_image) missingDishes.push(it);
            });
          });
          if (missingDishes.length === 0) {
            return alert('🎉 All dishes for this restaurant already have generated images on disk! None are missing.');
          }
          const res = await fetch('/api/batch/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              restaurant_name: this.menuData.branch_name,
              dishes: missingDishes,
              resolution: this.selectedResolution
            })
          });
          const data = await res.json();
          if (data.success) {
            this.activeTab = 'batch';
          } else {
            alert('Failed to start missing dishes batch: ' + (data.detail || 'Error'));
          }
        },

        async startBatchForCurrentBranch() {
          const allDishes = [];
          this.menuData.categories.forEach(cat => {
            cat.items.forEach(it => allDishes.push(it));
          });
          if (allDishes.length === 0) return alert('No dishes found.');

          const res = await fetch('/api/batch/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              restaurant_name: this.menuData.branch_name,
              dishes: allDishes,
              resolution: this.selectedResolution
            })
          });
          const data = await res.json();
          if (data.success) {
            this.activeTab = 'batch';
          } else {
            alert('Failed to start batch: ' + data.detail);
          }
        },

        async runAutomaticFlow() {
          if (this.batch.is_running) return alert('Batch generation is already running.');
          if (!this.selectedBranchId) return alert('Please select a restaurant first.');

          const allDishes = [];
          this.menuData.categories.forEach(cat => {
            cat.items.forEach(it => allDishes.push(it));
          });
          if (allDishes.length === 0) return alert('No dishes found in menu.');

          this.autoRunning = true;
          try {
            // Step 1: Start GPU Batch Generation
            const startRes = await fetch('/api/batch/start', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                restaurant_name: this.menuData.branch_name,
                dishes: allDishes,
                resolution: this.selectedResolution
              })
            });
            const startData = await startRes.json();
            if (!startData.success) {
              alert('Auto flow failed to start: ' + (startData.detail || 'Error'));
              return;
            }
            this.activeTab = 'batch';

            // Step 2: Poll until batch completes
            while (true) {
              await new Promise(r => setTimeout(r, 2000));
              const sRes = await fetch('/api/batch/status');
              const sData = await sRes.json();
              if (!sData.is_running) break;
            }

            // Step 3: Automatically map to Firebase Storage
            await fetch('/api/firebase/map-bucket', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                restaurant_name: this.menuData.branch_name
              })
            });

            // Step 4: Publish live / mobile-safe to Saturdays if authenticated
            if (this.status.auth?.has_token) {
              await fetch('/api/branch/upload-all', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  restaurant_name: this.menuData.branch_name,
                  branch_id: this.selectedBranchId
                })
              });
            }

            await this.loadMenu(this.selectedBranchId);
            this.activeTab = 'live';
            alert(`🎉 1-Click Auto Flow Complete!\nAll ${allDishes.length} dishes synthesized at ${this.selectedResolution}px, mapped to Firebase, and published live!`);
          } catch (e) {
            alert('Auto Flow encountered an error: ' + e);
          } finally {
            this.autoRunning = false;
          }
        },

        mappingFirebase: false,
        async mapAllToFirebase() {
          this.mappingFirebase = true;
          try {
            const res = await fetch('/api/firebase/map-bucket', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                restaurant_name: this.menuData.branch_name
              })
            });
            const data = await res.json();
            if (data.success) {
              alert(`Successfully mapped ${data.mapped_count} dishes to Firebase Storage bucket:\n${data.bucket}`);
              await this.loadMenu(this.selectedBranchId);
            } else {
              alert('Mapping failed: ' + (data.detail || 'Unknown error'));
            }
          } catch (e) {
            alert('Mapping failed: ' + e);
          } finally {
            this.mappingFirebase = false;
          }
        },

        async publishAllForCurrentBranch() {
          if (!this.status.auth?.has_token) {
            this.showAuthModal = true;
            return;
          }
          if (!confirm(`Publish all rendered images for ${this.menuData.branch_name} to live Saturdays?`)) return;

          this.publishingAll = true;
          try {
            const res = await fetch('/api/branch/upload-all', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                restaurant_name: this.menuData.branch_name,
                branch_id: this.selectedBranchId
              })
            });
            const data = await res.json();
            if (data.success) {
              alert(`Successfully published ${data.uploaded_count} dishes live to Saturdays!`);
              await this.loadMenu(this.selectedBranchId);
            } else {
              alert('Upload failed: ' + (data.detail || 'Unknown error'));
            }
          } catch (e) {
            alert('Upload failed: ' + e);
          } finally {
            this.publishingAll = false;
          }
        },

        async fixMobileUrlsForCurrentBranch() {
          if (!this.status.auth?.has_token) {
            this.showAuthModal = true;
            return;
          }
          if (!this.selectedBranchId) {
            alert('Please select a restaurant first.');
            return;
          }
          this.fixingMobile = true;
          try {
            const res = await fetch('/api/branch/fix-mobile-urls', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                branch_id: this.selectedBranchId,
                branch_name: this.menuData.branch_name
              })
            });
            const data = await res.json();
            if (res.ok && data.success) {
              alert(`🎉 Success! Updated ${data.count} dishes with mobile-safe image URLs.\nImages are now live and will display cleanly on mobile phones!`);
              await this.loadMenu(this.selectedBranchId);
            } else {
              alert('Failed to fix mobile URLs: ' + (data.detail || (data.errors && data.errors.join(', ')) || 'Unknown error'));
            }
          } catch (e) {
            alert('Error fixing mobile URLs: ' + e);
          } finally {
            this.fixingMobile = false;
          }
        },


        async pollBatch() {
          try {
            const res = await fetch('/api/batch/status');
            this.batch = await res.json();
          } catch (e) {}
        },

        async stopBatch() {
          await fetch('/api/batch/stop', { method: 'POST' });
        },

        handleFileSelect(e) {
          this.selectedFile = e.target.files[0];
        },

        handleFileDrop(e) {
          this.selectedFile = e.dataTransfer.files[0];
        },

        async extractFromImage() {
          if (!this.selectedFile) return;
          if (!this.geminiApiKey) return alert('Please enter your Gemini API Key.');
          localStorage.setItem('saturdays_gemini_key', this.geminiApiKey);

          this.extracting = true;
          const formData = new FormData();
          formData.append('file', this.selectedFile);
          formData.append('gemini_key', this.geminiApiKey);

          try {
            const res = await fetch('/api/vision/extract', {
              method: 'POST',
              body: formData
            });
            const data = await res.json();
            if (data.dishes) {
              this.extractedDishes = data.dishes;
            } else {
              alert('Extraction failed: ' + (data.detail || 'No dishes found'));
            }
          } catch (e) {
            alert('Extraction failed: ' + e);
          } finally {
            this.extracting = false;
          }
        },

        async sendExtractedToBatch() {
          if (this.extractedDishes.length === 0) return;
          const restName = this.customRestaurantName || 'Extracted Restaurant';
          const res = await fetch('/api/batch/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              restaurant_name: restName,
              dishes: this.extractedDishes
            })
          });
          const data = await res.json();
          if (data.success) {
            this.activeTab = 'batch';
          } else {
            alert('Failed: ' + data.detail);
          }
        },

        async saveToken() {
          await fetch('/api/auth/token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token: this.tempToken })
          });
          this.showAuthModal = false;
          this.fetchStatus();
        },

        async performLogin() {
          this.loggingIn = true;
          try {
            const res = await fetch('/api/auth/login', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ email: this.loginEmail, password: this.loginPassword })
            });
            const data = await res.json();
            if (data.success) {
              this.showAuthModal = false;
              this.fetchStatus();
              alert('Authenticated as ' + data.email);
            } else {
              alert('Login failed: ' + (data.detail || 'Invalid credentials'));
            }
          } catch (e) {
            alert('Login failed: ' + e);
          } finally {
            this.loggingIn = false;
          }
        }
      }
    }
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return DASHBOARD_HTML


# -----------------------------------------------------------------------------
# Main Entry Point
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Saturdays AI Menu Studio Dashboard")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the studio dashboard (default: 8000)")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface (default: 0.0.0.0)")
    args = parser.parse_args()

    print("\n" + "=" * 65)
    print("  🎨  Saturdays.co.in — Autonomous GPU Menu AI Studio")
    print("=" * 65)
    print(f"  ⚡ Live Dashboard:  http://localhost:{args.port}")
    print(f"  🧠 Ollama Engine:   {OLLAMA_URL} ({DEFAULT_OLLAMA_MODEL})")
    print(f"  🎨 ComfyUI Engine:  {COMFYUI_URL} (NVIDIA RTX A4000 GPU)")
    print(f"  ☁️  Production API: {LIVE_API_BASE}")
    print("=" * 65 + "\n")

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
