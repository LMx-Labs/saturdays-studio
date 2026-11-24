"""
=============================================================================
 Saturdays.co.in — Autonomous GPU Menu AI & Image Generation Pipeline
=============================================================================
End-to-end automation for restaurant menu enrichment on Saturdays.co.in:
  1. Fetches live restaurant menus directly from Saturdays production API.
  2. Uses local Qwen 2.5 32B via Ollama for vivid food photography prompts.
  3. Uses local NVIDIA RTX A4000 GPU (ComfyUI Krea2 Turbo) for 1024x1024 food photos.
  4. Uploads generated images directly to Saturdays media storage (POST /api/v1/media).
  5. Updates menu items live with sensory descriptions & image URLs (PATCH /api/v1/menu/items/{id}).
  6. Tracks progress in local manifests so runs can be resumed anytime.

Usage Examples:
  # 1. List all 8 live restaurants on Saturdays:
  python live_restaurant_pipeline.py --list

  # 2. Generate a 3-dish sample for Bonella Bakehouse locally:
  python live_restaurant_pipeline.py --restaurant "Bonella Bakehouse" --count 3

  # 3. Generate and immediately upload/patch live using an auth token:
  python live_restaurant_pipeline.py --restaurant "Bonella Bakehouse" --count 3 --token "<JWT_TOKEN>"

  # 4. Generate and immediately upload/patch using admin login credentials:
  python live_restaurant_pipeline.py --restaurant "APNA DHABA" --count 5 --email admin@saturdays.co.in --password "secret"

  # 5. Push previously generated images/descriptions to live without re-generating:
  python live_restaurant_pipeline.py --restaurant "Bonella Bakehouse" --upload-only --token "<JWT_TOKEN>"

  # 6. Run full menu generation for a restaurant:
  python live_restaurant_pipeline.py --restaurant "KC FOODS" --all --token "<JWT_TOKEN>"
=============================================================================
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LIVE_API_BASE = os.environ.get(
    "SATURDAYS_API_BASE",
    "https://saturdays-api-373465250617.us-central1.run.app/api/v1"
)
COMFYUI_URL = "http://127.0.0.1:8188"
COMFYUI_DISK_OUTPUT = Path(r"C:\Users\Student\AppData\Local\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\output")
OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:7b"

WORKSPACE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = WORKSPACE_DIR / "output"


def safe_name(name: str) -> str:
    clean = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    return re.sub(r"\s+", "_", clean)[:64]


def make_mobile_safe_media_url(media_id: str, api_base: str = LIVE_API_BASE) -> str:
    """Formats media URL with encoded first char (%30) so frontend regex A doesn't redirect to non-existent GCS files."""
    if not media_id:
        return ""
    encoded_first = f"%{ord(media_id[0]):02x}"
    return f"{api_base.rstrip('/')}/media/{encoded_first}{media_id[1:]}"



# -----------------------------------------------------------------------------
# API Client Helpers
# -----------------------------------------------------------------------------

def api_request(
    endpoint: str,
    method: str = "GET",
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    api_base: str = LIVE_API_BASE,
    timeout: int = 30
) -> Tuple[int, bytes]:
    url = f"{api_base.rstrip('/')}/{endpoint.lstrip('/')}"
    req_headers = {"User-Agent": "Saturdays-GPU-Pipeline/2.0"}
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        err_body = e.read()
        return e.code, err_body
    except Exception as e:
        print(f"  [API Error] {method} {url}: {e}")
        return 0, str(e).encode("utf-8")


def login_for_token(email: str, password: str, api_base: str = LIVE_API_BASE) -> Optional[str]:
    """Authenticates user against Saturdays API to retrieve JWT access token."""
    print(f"  🔐 Authenticating as {email}...")
    payload = json.dumps({"email": email, "password": password}).encode("utf-8")
    status, body = api_request(
        "auth/login",
        method="POST",
        data=payload,
        headers={"Content-Type": "application/json"},
        api_base=api_base,
    )
    if status == 200:
        data = json.loads(body.decode("utf-8"))
        token = data.get("access_token")
        if token:
            print("  ✓ Authenticated successfully! JWT token acquired.")
            return token
    print(f"  ❌ Authentication failed (HTTP {status}): {body.decode('utf-8', errors='ignore')}")
    return None


def get_live_branches(api_base: str = LIVE_API_BASE) -> List[Dict[str, Any]]:
    """Fetches all live branches from Saturdays production API."""
    status, body = api_request("branches", method="GET", api_base=api_base)
    if status == 200:
        return json.loads(body.decode("utf-8"))
    raise RuntimeError(f"Failed to fetch branches (HTTP {status}): {body.decode('utf-8', errors='ignore')}")


def get_branch_menu(branch_id: str, api_base: str = LIVE_API_BASE) -> Dict[str, Any]:
    """Fetches the live menu hierarchy for a given branch ID."""
    status, body = api_request(f"branches/{branch_id}/menu", method="GET", api_base=api_base)
    if status == 200:
        return json.loads(body.decode("utf-8"))
    raise RuntimeError(f"Failed to fetch menu for {branch_id} (HTTP {status}): {body.decode('utf-8', errors='ignore')}")


def upload_media_image(image_bytes: bytes, token: str, api_base: str = LIVE_API_BASE) -> Optional[str]:
    """Uploads raw image bytes to /api/v1/media and returns the media ID."""
    headers = {
        "Content-Type": "image/png",
        "Authorization": f"Bearer {token}",
    }
    status, body = api_request("media", method="POST", data=image_bytes, headers=headers, api_base=api_base)
    if status in (200, 201):
        data = json.loads(body.decode("utf-8"))
        media_id = str(data.get("id"))
        return media_id
    print(f"    ❌ Media upload failed (HTTP {status}): {body.decode('utf-8', errors='ignore')}")
    return None


def patch_dish_menu_item(
    item_id: str,
    description: Optional[str],
    image_url: Optional[str],
    token: str,
    api_base: str = LIVE_API_BASE
) -> bool:
    """Updates description and image_url of a menu item on Saturdays live."""
    update_payload: Dict[str, Any] = {}
    if description is not None:
        update_payload["description"] = description
    if image_url is not None:
        update_payload["image_url"] = image_url

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    status, body = api_request(
        f"menu/items/{item_id}",
        method="PATCH",
        data=json.dumps(update_payload).encode("utf-8"),
        headers=headers,
        api_base=api_base,
    )
    if status == 200:
        return True
    print(f"    ❌ Dish update failed (HTTP {status}): {body.decode('utf-8', errors='ignore')}")
    return False


# -----------------------------------------------------------------------------
# AI Prompt & GPU Image Generation
# -----------------------------------------------------------------------------

def check_ollama_status(ollama_url: str = OLLAMA_URL) -> Tuple[bool, str]:
    """Checks if Ollama is running and which model is available."""
    try:
        req = urllib.request.Request(f"{ollama_url}/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name", "") for m in data.get("models", [])]
            for pref in [DEFAULT_OLLAMA_MODEL, "qwen2.5-coder:32b", "qwen2.5-coder:7b", "gpt-oss:20b"]:
                if pref in models:
                    return True, pref
            if models:
                return True, models[0]
            return True, DEFAULT_OLLAMA_MODEL
    except Exception:
        return False, ""


def generate_dish_prompt(
    dish_name: str,
    category: str,
    restaurant_name: str,
    diet_is_veg: bool,
    ollama_url: str = OLLAMA_URL,
    ollama_model: str = DEFAULT_OLLAMA_MODEL
) -> str:
    """Uses local Qwen 32B via Ollama to generate a sensory food photography prompt."""
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
        "model": ollama_model,
        "messages": [{"role": "user", "content": system_prompt}],
        "stream": False,
        "keep_alive": 0,
        "options": {"temperature": 0.7, "top_p": 0.9},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{ollama_url}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            text = data["message"]["content"].strip()
            # Clean any markdown quotation marks
            text = re.sub(r'^["\']|["\']$', '', text).strip()
            return text
    except Exception as e:
        # Fallback to high-definition culinary photographic template
        return (
            f"Artisanal culinary photography of freshly prepared {dish_name}, {category}, {diet_str}, "
            f"served at {restaurant_name}. Warm cinematic restaurant lighting, shallow depth of field, "
            f"gourmet garnish, mouth-watering textures, f/1.8 macro lens capture, 8k resolution."
        )


def check_comfyui_status(comfy_url: str = COMFYUI_URL) -> bool:
    """Verifies that ComfyUI server is reachable."""
    try:
        req = urllib.request.Request(f"{comfy_url}/system_stats")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def queue_comfyui_image(
    prompt_text: str,
    filename_prefix: str,
    comfy_url: str = COMFYUI_URL,
    width: int = 480,
    height: int = 480
) -> Optional[str]:
    """Queues generation to ComfyUI Krea2/MiniMax engine on RTX A4000 GPU."""
    w = max(256, (int(width) // 16) * 16)
    h = max(256, (int(height) // 16) * 16)
    workflow = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": "krea2_turbo_fp8_scaled.safetensors",
                "weight_dtype": "default"
            }
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "qwen3vl_4b_fp8_scaled.safetensors",
                "type": "krea2"
            }
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {
                "vae_name": "qwen_image_vae.safetensors"
            }
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": prompt_text,
                "clip": ["2", 0]
            }
        },
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "blurry, bad food, distorted, low quality, oversaturated, messy, unappetizing",
                "clip": ["2", 0]
            }
        },
        "6": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": w,
                "height": h,
                "batch_size": 1
            }
        },
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["6", 0],
                "seed": int(time.time() * 1000) % 2147483647,
                "steps": 6,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0
            }
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["7", 0],
                "vae": ["3", 0]
            }
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": filename_prefix,
                "images": ["8", 0]
            }
        }
    }

    payload = json.dumps({"prompt": workflow}).encode("utf-8")
    req = urllib.request.Request(
        f"{comfy_url}/prompt",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("prompt_id")
    except Exception as e:
        print(f"    [ComfyUI Queue Error] {e}")
        return None


def poll_comfyui_output(
    prompt_id: str,
    comfy_url: str = COMFYUI_URL,
    timeout_seconds: int = 400
) -> Tuple[Optional[str], Optional[bytes]]:
    """Polls ComfyUI history until image generation is complete, then retrieves the raw image bytes."""
    start = time.time()
    last_print = 0

    while time.time() - start < timeout_seconds:
        elapsed = int(time.time() - start)
        if elapsed - last_print >= 15:
            print(f"      ... GPU rendering in progress ({elapsed}s elapsed) ...")
            last_print = elapsed

        time.sleep(3)
        try:
            req = urllib.request.Request(f"{comfy_url}/history/{prompt_id}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if prompt_id in data:
                    outputs = data[prompt_id].get("outputs", {})
                    for node_id, node_out in outputs.items():
                        imgs = node_out.get("images", [])
                        if imgs:
                            img_info = imgs[0]
                            filename = img_info["filename"]
                            subfolder = img_info.get("subfolder", "")
                            folder_type = img_info.get("type", "output")

                            # 1. Try reading directly from local disk
                            disk_path = COMFYUI_DISK_OUTPUT / subfolder / filename
                            if disk_path.exists():
                                return filename, disk_path.read_bytes()

                            # 2. Fetch via ComfyUI HTTP /view endpoint
                            view_url = (
                                f"{comfy_url}/view?filename={urllib.parse.quote(filename)}"
                                f"&subfolder={urllib.parse.quote(subfolder)}&type={folder_type}"
                            )
                            view_req = urllib.request.Request(view_url)
                            with urllib.request.urlopen(view_req, timeout=30) as view_resp:
                                return filename, view_resp.read()
        except Exception:
            pass

    return None, None


# -----------------------------------------------------------------------------
# Main Restaurant Processing Logic
# -----------------------------------------------------------------------------

def process_restaurant(
    restaurant_query: str,
    count: int = 3,
    process_all: bool = False,
    token: Optional[str] = None,
    upload_only: bool = False,
    missing_only: bool = False,
    target_dish: Optional[str] = None,
    target_category: Optional[str] = None,
    api_base: str = LIVE_API_BASE,
    comfy_url: str = COMFYUI_URL,
    ollama_url: str = OLLAMA_URL,
    skip_images: bool = False,
    resolution: int = 480,
) -> Dict[str, Any]:
    """Fetches menu items, generates AI descriptions and images, and updates Saturdays live platform."""

    print("\n" + "=" * 70)
    print("  🚀  Saturdays.co.in — Local GPU Menu Pipeline")
    print("=" * 70)

    # 1. Preflight checks
    branches = get_live_branches(api_base=api_base)
    target = next((b for b in branches if restaurant_query.lower() in b["name"].lower()), None)
    if not target:
        print(f"\n❌ Restaurant '{restaurant_query}' not found.")
        print(f"   Available restaurants ({len(branches)}):")
        for b in branches:
            print(f"     - {b['name']} (ID: {b['id']})")
        return {}

    branch_id = target["id"]
    branch_name = target["name"]
    print(f"  🏢 Target Restaurant: {branch_name}")
    print(f"     Branch ID:        {branch_id}")
    print(f"     Address:          {target.get('address_text', 'N/A')}")
    print(f"     Live API:         {api_base}")

    # Ollama health
    ollama_ok, active_model = check_ollama_status(ollama_url)
    if ollama_ok:
        print(f"  🧠 Ollama LLM:       ONLINE (Model: {active_model})")
    else:
        print(f"  ⚠️  Ollama LLM:       OFFLINE (Will use procedural sensory prompts)")

    # ComfyUI health
    comfy_ok = check_comfyui_status(comfy_url)
    if comfy_ok:
        print(f"  🎨 ComfyUI GPU:      ONLINE (127.0.0.1:8188 - NVIDIA RTX A4000 Ready)")
    else:
        if not upload_only and not skip_images:
            print(f"  ❌ ComfyUI GPU:      OFFLINE on {comfy_url}")
            print(f"     Please start ComfyUI or run with --upload-only")
            return {}

    # Auth status
    if token:
        print(f"  🔑 Auth Token:       CONFIGURED (Live uploads ENABLED)")
    else:
        print(f"  ⚠️  Auth Token:       NOT PROVIDED (Images saved locally; run with --token to upload)")

    # 2. Fetch live menu catalog
    menu_data = get_branch_menu(branch_id, api_base=api_base)
    categories = menu_data.get("categories", [])
    total_dishes = sum(len(c.get("items", [])) for c in categories)
    print(f"\n  📋 Live Menu Loaded: {len(categories)} categories, {total_dishes} total dishes")

    # 3. Local storage & manifest
    rest_safe = safe_name(branch_name)
    restaurant_dir = OUTPUT_DIR / rest_safe
    restaurant_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = restaurant_dir / "manifest.json"

    manifest: Dict[str, Any] = {}
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            manifest = {}

    # 4. Filter dishes
    dishes_to_process = []
    for cat in categories:
        cat_name = cat.get("name", "General")
        if target_category and target_category.lower() not in cat_name.lower():
            continue
        for item in cat.get("items", []):
            if target_dish and target_dish.lower() not in item.get("name", "").lower():
                continue
            dishes_to_process.append((cat_name, item))

    if missing_only:
        filtered = []
        for cat_name, item in dishes_to_process:
            c_safe = safe_name(cat_name)
            d_safe = safe_name(item["name"])
            target_file = restaurant_dir / c_safe / f"{d_safe}.png"
            if not target_file.exists():
                filtered.append((cat_name, item))
        dishes_to_process = filtered
        print(f"  🔍 Filtered for missing images only: {len(dishes_to_process)} unrendered dishes found.")

    if not process_all and not target_dish:
        dishes_to_process = dishes_to_process[:count]

    print(f"  🎯 Processing Queue: {len(dishes_to_process)} dishes")
    print("-" * 70)

    # 5. Process each dish
    for idx, (cat_name, item) in enumerate(dishes_to_process, start=1):
        dish_id = item["id"]
        dish_name = item["name"]
        price = item.get("base_price", 0.0)
        is_veg = item.get("is_veg", False)
        live_img = item.get("image_url")
        live_desc = item.get("description")

        cat_safe = safe_name(cat_name)
        dish_safe = safe_name(dish_name)
        cat_dir = restaurant_dir / cat_safe
        cat_dir.mkdir(parents=True, exist_ok=True)
        local_image_path = cat_dir / f"{dish_safe}.png"

        print(f"\n[{idx}/{len(dishes_to_process)}] 🍲 {dish_name} (₹{price}) — [{cat_name}]")
        print(f"    Item ID:   {dish_id}")

        existing_entry = manifest.get(dish_name, {})
        desc = existing_entry.get("description") or live_desc
        img_bytes = None

        # Check if already generated locally
        if local_image_path.exists():
            img_bytes = local_image_path.read_bytes()
            print(f"    📂 Found local image on disk: {local_image_path.name} ({len(img_bytes):,} bytes)")

        # In upload_only mode, we just push the existing local file
        if upload_only:
            if not img_bytes:
                print(f"    ⚠️  No local image found for '{dish_name}'. Skipping upload.")
                continue
        else:
            # Step A: Description Generation
            if not desc:
                print(f"    ⏳ Generating description via local Qwen 32B...")
                desc = generate_dish_prompt(
                    dish_name=dish_name,
                    category=cat_name,
                    restaurant_name=branch_name,
                    diet_is_veg=is_veg,
                    ollama_url=ollama_url,
                    ollama_model=active_model or DEFAULT_OLLAMA_MODEL,
                )
                print(f"    📝 Description:\n       \"{desc[:120]}...\"")
            else:
                print(f"    📝 Description (cached): \"{desc[:90]}...\"")

            # Step B: GPU Image Synthesis
            if not img_bytes and not skip_images:
                fn_prefix = f"{rest_safe}_{dish_safe}"
                print(f"    🎨 Submitting to RTX A4000 GPU (ComfyUI Krea2 Turbo, {resolution}x{resolution})...")
                prompt_id = queue_comfyui_image(desc, fn_prefix, comfy_url=comfy_url, width=resolution, height=resolution)

                if prompt_id:
                    print(f"    ⏳ Sampling {resolution}x{resolution} on GPU (Task: {prompt_id[:8]})...")
                    img_fn, img_bytes = poll_comfyui_output(prompt_id, comfy_url=comfy_url)
                    if img_bytes:
                        local_image_path.write_bytes(img_bytes)
                        print(f"    ✓ Render complete! Saved to {local_image_path} ({len(img_bytes):,} bytes)")
                    else:
                        print(f"    ❌ Generation timed out or failed.")
                else:
                    print(f"    ❌ Failed to queue image on ComfyUI.")

        # Step C: Upload to Saturdays Live Platform
        uploaded_media_id = existing_entry.get("media_id")
        live_media_url = existing_entry.get("live_image_url")
        upload_success = existing_entry.get("uploaded", False)

        if token and img_bytes and not upload_success:
            print(f"    ☁️  Uploading image binary to Saturdays (POST /api/v1/media)...")
            uploaded_media_id = upload_media_image(img_bytes, token, api_base=api_base)
            if uploaded_media_id:
                live_media_url = make_mobile_safe_media_url(uploaded_media_id, api_base=api_base)
                cdn_media_url = f"{api_base}/cdn/{uploaded_media_id}"
                print(f"    ✓ Media uploaded! ID: {uploaded_media_id}")
                print(f"      Live URL (Mobile Safe): {live_media_url}")
                print(f"      CDN Alias URL:          {cdn_media_url}")

                print(f"    🔄 Patching dish metadata (PATCH /api/v1/menu/items/{dish_id})...")
                patched = patch_dish_menu_item(
                    item_id=dish_id,
                    description=desc,
                    image_url=live_media_url,
                    token=token,
                    api_base=api_base,
                )
                if patched:
                    upload_success = True
                    print(f"    ✅ Successfully published to Saturdays live menu!")

        # Step D: Update Manifest
        manifest[dish_name] = {
            "id": dish_id,
            "category": cat_name,
            "name": dish_name,
            "price": price,
            "is_veg": is_veg,
            "description": desc,
            "local_image_path": str(local_image_path) if local_image_path.exists() else "",
            "media_id": uploaded_media_id or "",
            "live_image_url": live_media_url or live_img or "",
            "safe_mobile_url": live_media_url or "",
            "cdn_image_url": f"{api_base}/cdn/{uploaded_media_id}" if uploaded_media_id else "",
            "uploaded": upload_success,
            "status": "published_live" if upload_success else ("ready_for_upload" if local_image_path.exists() else "pending"),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 70)
    print(f"  🏁 Run Complete for {branch_name}!")
    print(f"     Manifest saved: {manifest_path}")
    print("=" * 70)
    return manifest


# -----------------------------------------------------------------------------
# CLI Entrypoint
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Saturdays.co.in Autonomous GPU Menu AI & Image Generation Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python live_restaurant_pipeline.py --list
  python live_restaurant_pipeline.py --restaurant "Bonella Bakehouse" --count 1
  python live_restaurant_pipeline.py --restaurant "APNA DHABA" --count 3 --token "JWT_HERE"
  python live_restaurant_pipeline.py --restaurant "Bonella Bakehouse" --upload-only --token "JWT_HERE"
        """
    )
    parser.add_argument("--list", action="store_true", help="List all live restaurants on Saturdays")
    parser.add_argument("--restaurant", default="Bonella Bakehouse", help="Restaurant name filter (default: Bonella Bakehouse)")
    parser.add_argument("--category", default=None, help="Optional category filter (e.g. Cupcakes, Starters)")
    parser.add_argument("--dish", default=None, help="Target a specific dish name")
    parser.add_argument("--count", type=int, default=3, help="Number of dishes to process (default: 3)")
    parser.add_argument("--all", action="store_true", help="Process ALL dishes for the selected restaurant")
    parser.add_argument("--token", default=os.environ.get("SATURDAYS_AUTH_TOKEN") or os.environ.get("SATURDAYS_TOKEN"), help="JWT Bearer token for Saturdays Admin API")
    parser.add_argument("--email", default=None, help="Saturdays admin email for auto-login")
    parser.add_argument("--password", default=None, help="Saturdays admin password for auto-login")
    parser.add_argument("--upload-only", action="store_true", help="Upload already generated images/descriptions to Saturdays without re-rendering")
    parser.add_argument("--missing-only", action="store_true", help="Render only dishes that do not have an image on disk")
    parser.add_argument("--skip-images", action="store_true", help="Generate only descriptions, skipping GPU image synthesis")
    parser.add_argument("--resolution", type=int, default=480, choices=[480, 960, 1600], help="Image resolution width/height in px (default: 480)")
    parser.add_argument("--api-base", default=LIVE_API_BASE, help=f"Saturdays API base URL (default: {LIVE_API_BASE})")
    parser.add_argument("--comfy-url", default=COMFYUI_URL, help=f"ComfyUI URL (default: {COMFYUI_URL})")
    parser.add_argument("--ollama-url", default=OLLAMA_URL, help=f"Ollama URL (default: {OLLAMA_URL})")

    args = parser.parse_args()

    if args.list:
        print("\n" + "=" * 60)
        print("  🍽  Live Restaurants on Saturdays.co.in")
        print("=" * 60)
        try:
            branches = get_live_branches(args.api_base)
            for idx, b in enumerate(branches, start=1):
                status_str = "🟢 Active" if b.get("is_active") else "🔴 Inactive"
                print(f"  {idx}. {b['name']:<22} | {status_str} | ID: {b['id']}")
                print(f"     Address: {b.get('address_text', 'N/A')}")
            print("=" * 60)
        except Exception as e:
            print(f"❌ Failed to fetch restaurants: {e}")
        return

    # Handle auto-login if email/password provided
    token = args.token
    if not token and args.email and args.password:
        token = login_for_token(args.email, args.password, api_base=args.api_base)

    process_restaurant(
        restaurant_query=args.restaurant,
        count=args.count,
        process_all=args.all,
        token=token,
        upload_only=args.upload_only,
        missing_only=args.missing_only,
        target_dish=args.dish,
        target_category=args.category,
        api_base=args.api_base,
        comfy_url=args.comfy_url,
        ollama_url=args.ollama_url,
        skip_images=args.skip_images,
        resolution=args.resolution,
    )


if __name__ == "__main__":
    main()
