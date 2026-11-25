"""
Standalone test for LMX AI Menu Pipeline
Runs without a database - tests only the AI service layer.
"""
import sys
import os

sys.path.insert(0, r"c:\Users\Student\Desktop\Shaiv Watkar\lmx code\saturdays-backend")
os.chdir(r"c:\Users\Student\Desktop\Shaiv Watkar\lmx code\saturdays-backend")

PASS = "✅ PASS"
FAIL = "❌ FAIL"
SKIP = "⚠️  SKIP"

print("=" * 60)
print("  LMX AI Menu Pipeline - Integration Test Suite")
print("=" * 60)

# TEST 1: Imports
print("\nTEST 1: Import AI modules...")
try:
    from app.ai.schemas import AIDishDescribeRequest, AIHardwareStatusResponse
    from app.ai.service import AIService
    print(f"  {PASS}: schemas and service imported OK")
except Exception as e:
    print(f"  {FAIL}: {e}")
    sys.exit(1)

# TEST 2: Hardware status
print("\nTEST 2: Hardware & GPU status...")
try:
    status = AIService.get_hardware_status()
    gpu_ok = status.gpu_available
    print(f"  GPU device  : {status.gpu_device}")
    print(f"  VRAM        : {status.vram_gb} GB")
    print(f"  Ollama      : {'ONLINE' if status.ollama_online else 'OFFLINE'}")
    print(f"  Python      : {status.python_version}")
    print(f"  {PASS}" if gpu_ok else f"  {SKIP}: GPU not visible from Python 3.15 (need 3.12 for torch)")
except Exception as e:
    print(f"  {FAIL}: {e}")

# TEST 3: Dish description via Ollama
print("\nTEST 3: LLM dish description (Ollama Qwen 32B)...")
try:
    req = AIDishDescribeRequest(
        dish_name="Panner Chilli Roll",
        category="Quick Bites",
        diet="Veg",
        style_hint="Indian street food, rustic wooden platter, vibrant warm colors"
    )
    result = AIService.describe_dish(req)
    desc = result.description
    if len(desc) > 20:
        print(f"  {PASS}: {desc[:120]}...")
    else:
        print(f"  {FAIL}: Got short/empty description: {desc}")
except Exception as e:
    print(f"  {FAIL}: {e}")

# TEST 4: CSV parser
print("\nTEST 4: CSV bulk import parser...")
try:
    csv_text = """Category, Dish Name, Price, Veg/NonVeg
Main Course, Paneer Butter Masala, 180, Veg
Beverages, Cold Coffee, 70, Veg
Quick Bites, Chicken Roll, 120, NonVeg"""
    items = AIService.parse_csv_to_items(csv_text)
    assert len(items) == 3, f"Expected 3 items, got {len(items)}"
    assert items[0].name == "Paneer Butter Masala"
    assert items[2].diet == "NonVeg"
    print(f"  {PASS}: Parsed {len(items)} items correctly")
    for it in items:
        print(f"    - [{it.category}] {it.name} ₹{it.price} ({it.diet})")
except Exception as e:
    print(f"  {FAIL}: {e}")

# TEST 5: Check manifest
print("\nTEST 5: Check existing KC FOODS manifest...")
try:
    import json
    manifest_path = r"c:\Users\Student\Desktop\Shaiv Watkar\lmx code\output\KC_FOODS\manifest.json"
    with open(manifest_path) as f:
        manifest = json.load(f)
    total = len(manifest)
    with_desc = sum(1 for v in manifest.values() if v.get("description"))
    with_img = sum(1 for v in manifest.values() if v.get("image_path") and v["image_path"])
    print(f"  {PASS}: {total} items in manifest")
    print(f"    Descriptions: {with_desc}/{total}")
    print(f"    Images:       {with_img}/{total} (need model weights for images)")
    sample = list(manifest.items())[0]
    print(f"    Sample [{sample[0]}]: {sample[1].get('description','')[:80]}...")
except Exception as e:
    print(f"  {FAIL}: {e}")

print("\n" + "=" * 60)
print("  Tests complete!")
print("=" * 60)
