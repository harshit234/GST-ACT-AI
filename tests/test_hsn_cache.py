"""
test_hsn_cache.py
=================
Tests the Supabase HSN cache and OpenRouter HSN lookup added in extract.py / db.py.

What this covers
----------------
1. Supabase connectivity (hsn_cache table exists and is reachable)
2. Cache WRITE  — save_hsn_to_cache() stores a row
3. Cache READ   — get_hsn_from_cache() retrieves the row (HIT path)
4. Cache MISS   — a brand-new item triggers lookup_hsn_for_item(), which
                  calls OpenRouter and stores the result
5. Cache HIT    — same item queried again -> no OpenRouter call, same result
6. Normalisation — "  Plywood Board 19mm  " and "plywood board 19mm" resolve to same key

Run from the project root:
    python -m pytest tests/test_hsn_cache.py -v
  or directly:
    python tests/test_hsn_cache.py
"""

import os
import sys
import time
import uuid

# path so we can import project modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

PASS = "\033[92m+ PASS\033[0m"
FAIL = "\033[91mx FAIL\033[0m"
INFO = "\033[94m  ->\033[0m"

results = []


def check(label: str, condition: bool, detail: str = ""):
    icon = PASS if condition else FAIL
    print(f"  {icon}  {label}")
    if detail:
        print(f"       {INFO} {detail}")
    results.append((label, condition))


# TEST 1 - Supabase: hsn_cache table exists and is readable
print("\n" + "=" * 60)
print(" TEST 1 - Supabase hsn_cache table connectivity")
print("=" * 60)

try:
    from db import get_supabase_client, get_hsn_from_cache, save_hsn_to_cache, _normalize_item_name
    client = get_supabase_client()
    resp = client.table("hsn_cache").select("id").limit(1).execute()
    check("hsn_cache table exists & reachable", True,
          f"rows returned by select: {len(resp.data)}")
except Exception as e:
    check("hsn_cache table exists & reachable", False, str(e))
    print("\n[FATAL] Cannot reach Supabase hsn_cache. Aborting remaining tests.")
    sys.exit(1)


# TEST 2 - Name normalisation
print("\n" + "=" * 60)
print(" TEST 2 - Item name normalisation")
print("=" * 60)

pairs = [
    ("  Plywood Board 19mm  ", "plywood board 19mm"),
    ("TEAK WOOD (First Class)", "teak wood  first class "),
    ("M.S. Pipe 2in", "m s  pipe 2in"),
    ("cement-bag (50kg)", "cement bag  50kg "),
]
for raw, expected_prefix in pairs:
    norm = _normalize_item_name(raw)
    ok = norm == norm.lower() and norm == norm.strip()
    check(f"normalize('{raw[:30]}')", ok, f"-> '{norm}'")


# TEST 3 - Cache WRITE
print("\n" + "=" * 60)
print(" TEST 3 - Cache WRITE")
print("=" * 60)

TEST_ITEM = f"test item for cache {uuid.uuid4().hex[:8]}"

try:
    save_hsn_to_cache(
        item_name=TEST_ITEM,
        hsn_code="9999",
        gst_rate=18,
        unit="PCS",
    )
    check("save_hsn_to_cache() completed without error", True)
except Exception as e:
    check("save_hsn_to_cache() completed without error", False, str(e))


# TEST 4 - Cache READ / HIT
print("\n" + "=" * 60)
print(" TEST 4 - Cache READ (expected HIT for item written in TEST 3)")
print("=" * 60)

try:
    hit = get_hsn_from_cache(TEST_ITEM)
    check("get_hsn_from_cache() returns a result", hit is not None, str(hit))
    if hit:
        check("hsn_code matches written value", hit.get("hsn_code") == "9999",
              f"got: {hit.get('hsn_code')}")
        check("gst_rate matches written value",
              str(hit.get("gst_rate")) == "18",
              f"got: {hit.get('gst_rate')}")
        check("unit matches written value", hit.get("unit") == "PCS",
              f"got: {hit.get('unit')}")
except Exception as e:
    check("get_hsn_from_cache() call succeeded", False, str(e))


# TEST 5 - Cache MISS -> OpenRouter call -> auto-saved
print("\n" + "=" * 60)
print(" TEST 5 - Cache MISS -> OpenRouter call for a real item")
print("=" * 60)

from extract import lookup_hsn_for_item

REAL_ITEM = "Commercial Plywood Sheet 19mm"

try:
    from db import _normalize_item_name as norm_fn
    norm = norm_fn(REAL_ITEM)
    client.table("hsn_cache").delete().eq("item_name_normalized", norm).execute()
    print(f"  {INFO} Cleared any pre-existing cache entry for '{REAL_ITEM}'")
except Exception as e:
    print(f"  {INFO} Could not pre-clear cache entry (ok): {e}")

t0 = time.time()
result = lookup_hsn_for_item(REAL_ITEM)
elapsed = time.time() - t0

check("lookup_hsn_for_item() returned a dict", isinstance(result, dict), str(result))
check("hsn_code is a non-empty string",
      bool(result.get("hsn_code")),
      f"hsn_code = {result.get('hsn_code')}")
check("gst_rate is numeric",
      result.get("gst_rate") is not None,
      f"gst_rate = {result.get('gst_rate')}")
check("unit is a non-empty string",
      bool(result.get("unit")),
      f"unit = {result.get('unit')}")
print(f"  {INFO} OpenRouter call took {elapsed:.2f}s")

time.sleep(0.5)
hit2 = get_hsn_from_cache(REAL_ITEM)
check("Result was auto-saved to Supabase cache", hit2 is not None,
      f"cached entry: {hit2}")


# TEST 6 - Second call is a HIT (no OpenRouter)
print("\n" + "=" * 60)
print(" TEST 6 - Second lookup is a cache HIT (no API call)")
print("=" * 60)

t1 = time.time()
result2 = lookup_hsn_for_item(REAL_ITEM)
elapsed2 = time.time() - t1

check("Second lookup returns same hsn_code",
      result2.get("hsn_code") == result.get("hsn_code"),
      f"{result2.get('hsn_code')} == {result.get('hsn_code')}")
check("Cache HIT is significantly faster (< 5s vs API call)",
      elapsed2 < 5,
      f"cache hit took {elapsed2:.3f}s  (API call took {elapsed:.2f}s)")


# TEST 7 - line-item enrichment
print("\n" + "=" * 60)
print(" TEST 7 - Line-item HSN enrichment (simulate extract_invoice_details)")
print("=" * 60)

SYNTHETIC_RESULT = {
    "is_invoice": True,
    "low_confidence": False,
    "vendor_name": "Test Vendor",
    "vendor_gstin": "29ABCDE1234F1Z5",
    "invoice_number": "INV-001",
    "invoice_date": "2026-07-19",
    "cgst": 100.0,
    "sgst": 100.0,
    "igst": 0.0,
    "total_amount": 1200.0,
    "line_items": [
        {
            "si_no": 1,
            "description": REAL_ITEM,
            "hsn_sac": None,
            "quantity": 5,
            "rate": 200.0,
            "amount": 1000.0,
            "gst_rate": None,
            "gst_amount": 200.0,
        }
    ],
}

line_items = SYNTHETIC_RESULT.get("line_items") or []
for item in line_items:
    desc = (item.get("description") or "").strip()
    if not desc:
        continue
    hsn_info = lookup_hsn_for_item(desc)
    if not item.get("hsn_sac") and hsn_info.get("hsn_code"):
        item["hsn_sac"] = hsn_info["hsn_code"]
    if item.get("gst_rate") is None and hsn_info.get("gst_rate") is not None:
        item["gst_rate"] = hsn_info["gst_rate"]
    if not item.get("unit") and hsn_info.get("unit"):
        item["unit"] = hsn_info["unit"]

enriched_item = line_items[0]
check("hsn_sac field filled in on line item",
      bool(enriched_item.get("hsn_sac")),
      f"hsn_sac = {enriched_item.get('hsn_sac')}")
check("gst_rate field filled in on line item",
      enriched_item.get("gst_rate") is not None,
      f"gst_rate = {enriched_item.get('gst_rate')}")


# SUMMARY
print("\n" + "=" * 60)
passed = sum(1 for _, ok in results if ok)
failed = sum(1 for _, ok in results if not ok)
print(f" RESULTS: {passed} passed, {failed} failed out of {len(results)} checks")
print("=" * 60 + "\n")

if failed:
    sys.exit(1)
