"""
extract_catalog_to_r2.py
========================
ดึง catalog PDF จาก R2 → extract ด้วย Claude Vision → อัปโหลด JSON กลับ R2

ใช้รันเป็น command-line script เวลาต้องการเพิ่มแบรนด์หรืออัปเดต catalog

Usage:
  # Extract 1 catalog เฉพาะ
  python extract_catalog_to_r2.py --brand sas --catalog s_series.pdf

  # Extract ทุก catalog ที่ยังไม่มี JSON ใน databases/
  python extract_catalog_to_r2.py --all --only-new

  # Re-extract ทุกอย่างใหม่ (force)
  python extract_catalog_to_r2.py --all --force

  # Test (ดึงแค่บาง page)
  python extract_catalog_to_r2.py --brand sas --catalog s_series.pdf --pages 5-10

Required env vars:
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_KEY, R2_BUCKET_NAME, R2_PUBLIC_URL
  ANTHROPIC_API_KEY
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path
from typing import Optional

try:
    import boto3
    from botocore.client import Config
    from pdf2image import convert_from_bytes
    from PIL import Image
    from tqdm import tqdm
    import anthropic
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install: pip install boto3 pdf2image pillow tqdm anthropic")
    sys.exit(1)


# ============================================================
# Config
# ============================================================

MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 4096
DPI = 150
JPEG_QUALITY = 85
RETRY_COUNT = 3

CATALOGS_PREFIX  = "catalogs/"
DATABASES_PREFIX = "databases/"


# Same prompts as before (shortened here — full prompts in the v1 script)
CLASSIFY_PROMPT = """Classify this gear motor catalog page. Return ONLY JSON:
{
  "category": "cover|naming_convention|design_variants|structure_diagram|ratio_torque_table|selection_table_by_kw|dimension_drawing|lubricant_table|motor_data|accessories|other",
  "gear_size_or_family": "e.g. S37, S67, or null",
  "has_data_tables": true|false
}
Return ONLY the JSON."""


EXTRACT_NAMING_PROMPT = """Extract the naming convention decoder from this page.
Return JSON: {
  "example_code": "...",
  "positions": [
    {"position_number": 1, "field_name": "...", "codes": {"X": "meaning"}, "notes": "..."}
  ]
}
Return ONLY JSON."""


EXTRACT_RATIO_TORQUE_PROMPT = """Extract ALL ratio/torque table rows.
Return: {
  "input_speed_rpm": <number>,
  "tables": [{
    "gear_size": "S67",
    "rated_max_torque_nm": <number>,
    "rows": [{"ratio": <n>, "output_speed_rpm": <n>, "max_torque_nm": <n>,
              "radial_load_n": <n>, "design_variant": "AD1|null"}]
  }]
}
Extract EVERY row. Return ONLY JSON."""


EXTRACT_SELECTION_PROMPT = """Extract selection tables by kW.
Return: {
  "tables": [{
    "power_kw": <n>,
    "rows": [{"output_speed_rpm": <n>, "output_torque_nm": <n>, "ratio": <n>,
              "radial_load_n": <n>, "service_factor": <n>,
              "variants": ["S","SF","SA","SAF"], "model_code": "67 D90L4",
              "full_model": "S67 D90L4"}]
  }]
}
Extract EVERY row from EVERY kW section. Return ONLY JSON."""


EXTRACT_DIMENSION_PROMPT = """Extract dimension tables and drawings.
Return: {
  "drawing_applies_to": ["SA67", "SA77"],
  "mounting_type": "foot|flange_B5|flange_B14|hollow_shaft|hollow_shaft_shrink_disc",
  "dimension_table": [{
    "model": "SA67",
    "dimensions": {"a":88,"b":71.5,"h":140,"d1_hollow_shaft":"45H7", "s1_key":"M16x40", ...}
  }]
}
Return ONLY JSON."""


GENERIC_PROMPT = "Extract all structured data as JSON. Return ONLY JSON."


# ============================================================
# R2 setup
# ============================================================

def make_r2_client():
    acct = os.environ.get("R2_ACCOUNT_ID")
    key  = os.environ.get("R2_ACCESS_KEY_ID")
    sec  = os.environ.get("R2_SECRET_KEY")
    if not all([acct, key, sec]):
        print("❌ Missing R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_KEY")
        sys.exit(1)
    return boto3.client(
        "s3",
        endpoint_url=f"https://{acct}.r2.cloudflarestorage.com",
        aws_access_key_id=key,
        aws_secret_access_key=sec,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


# ============================================================
# Extraction
# ============================================================

class Extractor:
    def __init__(self, anthropic_key: str):
        self.client = anthropic.Anthropic(api_key=anthropic_key)

    def _b64(self, image: Image.Image) -> str:
        if image.width > 2000:
            ratio = 2000 / image.width
            image = image.resize((2000, int(image.height * ratio)), Image.LANCZOS)
        if image.mode != "RGB":
            image = image.convert("RGB")
        buf = BytesIO()
        image.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return base64.standard_b64encode(buf.getvalue()).decode("ascii")

    def call(self, image: Image.Image, prompt: str) -> dict:
        b64 = self._b64(image)
        last = None
        for attempt in range(RETRY_COUNT):
            try:
                msg = self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {
                                "type": "base64", "media_type": "image/jpeg", "data": b64}},
                            {"type": "text", "text": prompt},
                        ],
                    }],
                )
                text = "".join(b.text for b in msg.content if hasattr(b, "text")).strip()
                text = text.replace("```json", "").replace("```", "").strip()
                return json.loads(text)
            except json.JSONDecodeError:
                # salvage
                start, end = text.find("{"), text.rfind("}")
                if start >= 0 and end > start:
                    try: return json.loads(text[start:end+1])
                    except: pass
                return {"_parse_error": True, "_raw": text[:300]}
            except Exception as e:
                last = e
                time.sleep(5 * (2 ** attempt))
        raise RuntimeError(f"API failed: {last}")


def extract_catalog(pdf_bytes: bytes, extractor: Extractor,
                    start_page: int = 1, end_page: Optional[int] = None) -> dict:
    print(f"  Rasterizing PDF at {DPI} DPI...")
    images = convert_from_bytes(pdf_bytes, dpi=DPI, first_page=start_page, last_page=end_page)
    print(f"  → {len(images)} pages")

    db = {
        "total_pages": len(images),
        "page_range": [start_page, start_page + len(images) - 1],
        "extraction_model": MODEL,
        "pages": [],
        "naming_convention": None,
        "ratio_torque_tables": [],
        "selection_tables": [],
        "dimension_tables": [],
    }

    # Classify
    print("  Stage 1: classifying pages...")
    classes = []
    for idx, img in enumerate(tqdm(images, desc="  classify", unit="pg")):
        try: c = extractor.call(img, CLASSIFY_PROMPT)
        except Exception as e: c = {"category": "error", "_error": str(e)}
        c["_page"] = idx + start_page
        classes.append(c)
        time.sleep(0.3)
    db["pages"] = classes

    # Extract relevant
    prompt_map = {
        "naming_convention": EXTRACT_NAMING_PROMPT,
        "ratio_torque_table": EXTRACT_RATIO_TORQUE_PROMPT,
        "selection_table_by_kw": EXTRACT_SELECTION_PROMPT,
        "dimension_drawing": EXTRACT_DIMENSION_PROMPT,
    }
    relevant = [c for c in classes if c.get("category") in prompt_map]
    print(f"  Stage 2: extracting {len(relevant)} relevant pages...")
    for c in tqdm(relevant, desc="  extract", unit="pg"):
        img = images[c["_page"] - start_page]
        try:
            d = extractor.call(img, prompt_map[c["category"]])
            d["_page"] = c["_page"]
            cat = c["category"]
            if cat == "naming_convention" and db["naming_convention"] is None:
                db["naming_convention"] = d
            elif cat == "ratio_torque_table":
                db["ratio_torque_tables"].append(d)
            elif cat == "selection_table_by_kw":
                db["selection_tables"].append(d)
            elif cat == "dimension_drawing":
                db["dimension_tables"].append(d)
        except Exception as e:
            print(f"  ⚠ page {c['_page']}: {e}")
        time.sleep(0.3)

    # Build index
    print("  Stage 3: building model index...")
    index = {}
    for st in db["selection_tables"]:
        for t in st.get("tables", []):
            for r in t.get("rows", []):
                m = r.get("full_model")
                if not m: continue
                e = index.setdefault(m, {"model": m, "applications": [], "dimensions": {}})
                e["applications"].append({
                    "power_kw": t.get("power_kw"),
                    "ratio": r.get("ratio"),
                    "output_speed_rpm": r.get("output_speed_rpm"),
                    "output_torque_nm": r.get("output_torque_nm"),
                    "variants": r.get("variants", []),
                })
    for dt in db["dimension_tables"]:
        for r in dt.get("dimension_table", []):
            m = r.get("model")
            if not m: continue
            e = index.setdefault(m, {"model": m, "applications": [], "dimensions": {}})
            e["dimensions"][dt.get("mounting_type", "unknown")] = r.get("dimensions", {})
    db["model_index"] = index

    return db


# ============================================================
# R2 helpers
# ============================================================

def list_catalogs(r2, bucket):
    out = []
    continuation = None
    while True:
        kw = {"Bucket": bucket, "Prefix": CATALOGS_PREFIX, "MaxKeys": 1000}
        if continuation: kw["ContinuationToken"] = continuation
        resp = r2.list_objects_v2(**kw)
        for o in resp.get("Contents", []):
            if o["Key"].lower().endswith(".pdf"):
                out.append(o["Key"])
        if resp.get("IsTruncated"): continuation = resp.get("NextContinuationToken")
        else: break
    return out


def list_databases(r2, bucket):
    out = set()
    continuation = None
    while True:
        kw = {"Bucket": bucket, "Prefix": DATABASES_PREFIX, "MaxKeys": 1000}
        if continuation: kw["ContinuationToken"] = continuation
        resp = r2.list_objects_v2(**kw)
        for o in resp.get("Contents", []):
            out.add(o["Key"])
        if resp.get("IsTruncated"): continuation = resp.get("NextContinuationToken")
        else: break
    return out


def catalog_to_db_key(catalog_key: str) -> str:
    # catalogs/sas/s_series.pdf → databases/sas__s_series.json
    parts = catalog_key[len(CATALOGS_PREFIX):].split("/", 1)
    if len(parts) != 2:
        return f"{DATABASES_PREFIX}unknown__{parts[0]}.json"
    brand, filename = parts
    stem = filename.rsplit(".", 1)[0].replace("/", "_")
    return f"{DATABASES_PREFIX}{brand}__{stem}.json"


# ============================================================
# Main
# ============================================================

def process_one(r2, bucket, catalog_key: str, extractor: Extractor,
                pages: Optional[str] = None):
    db_key = catalog_to_db_key(catalog_key)
    print(f"\n📥 {catalog_key} → {db_key}")

    # Download PDF
    print("  Downloading from R2...")
    resp = r2.get_object(Bucket=bucket, Key=catalog_key)
    pdf_bytes = resp["Body"].read()
    print(f"  → {len(pdf_bytes)/1024/1024:.1f} MB")

    # Page range
    start_page, end_page = 1, None
    if pages:
        a, b = pages.split("-")
        start_page = int(a)
        end_page = int(b)

    # Extract
    db = extract_catalog(pdf_bytes, extractor, start_page, end_page)
    db["source_key"] = catalog_key

    # Upload JSON to R2
    payload = json.dumps(db, ensure_ascii=False, indent=2).encode("utf-8")
    r2.put_object(Bucket=bucket, Key=db_key, Body=payload, ContentType="application/json")
    print(f"  ✅ Uploaded {db_key} ({len(payload)/1024:.1f} KB)")

    # Summary
    n_sel = sum(len(t.get("rows", [])) for st in db["selection_tables"] for t in st.get("tables", []))
    n_rt  = sum(len(t.get("rows", [])) for rt in db["ratio_torque_tables"] for t in rt.get("tables", []))
    n_dim = sum(len(dt.get("dimension_table", [])) for dt in db["dimension_tables"])
    print(f"     naming: {'✓' if db['naming_convention'] else '✗'} · "
          f"selection rows: {n_sel} · ratio rows: {n_rt} · dim rows: {n_dim} · "
          f"models: {len(db.get('model_index', {}))}")


def main():
    p = argparse.ArgumentParser(description="Extract gear motor catalogs from R2 into R2")
    p.add_argument("--brand")
    p.add_argument("--catalog", help="Catalog filename under catalogs/<brand>/")
    p.add_argument("--all", action="store_true")
    p.add_argument("--only-new", action="store_true", help="Skip catalogs already in databases/")
    p.add_argument("--force", action="store_true")
    p.add_argument("--pages", help="e.g. 1-10 (testing only)")
    args = p.parse_args()

    if not (args.all or (args.brand and args.catalog)):
        p.error("Either --all or (--brand AND --catalog) required")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        print("❌ ANTHROPIC_API_KEY not set")
        sys.exit(1)

    bucket = os.environ.get("R2_BUCKET_NAME", "sas-qc-gearmotor")
    r2 = make_r2_client()
    extractor = Extractor(anthropic_key)

    if args.all:
        all_catalogs = list_catalogs(r2, bucket)
        if args.only_new:
            existing = list_databases(r2, bucket)
            todo = [c for c in all_catalogs if catalog_to_db_key(c) not in existing]
        else:
            todo = all_catalogs
        print(f"Found {len(all_catalogs)} catalog(s), {len(todo)} to process")
        for c in todo:
            try:
                process_one(r2, bucket, c, extractor, args.pages)
            except Exception as e:
                print(f"  ❌ {c}: {e}")
    else:
        key = f"{CATALOGS_PREFIX}{args.brand}/{args.catalog}"
        process_one(r2, bucket, key, extractor, args.pages)


if __name__ == "__main__":
    main()
