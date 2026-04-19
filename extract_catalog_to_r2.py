"""
extract_catalog_to_r2.py  (v2 — robust)
========================================
ดึง catalog PDF จาก R2 → extract ด้วย Claude Vision → อัปโหลด JSON กลับ R2

v2 Improvements over v1:
  ✅ MAX_TOKENS: 4096 → 8192 (รองรับหน้า selection ยาว)
  ✅ ถ้า parse fail → เก็บ full raw response (ไม่ตัด 300 chars)
  ✅ Stricter prompts: แยก gear_size กับ power_kw ชัดเจน
  ✅ Auto-continuation เมื่อ response ถูกตัด (stop_reason=max_tokens)
  ✅ Prompt ห้ามใช้ "1.5kW" / "45kW" เป็น gear_size
  ✅ Gear model prefix (R, RF, K, S...) ต้องอยู่ใน full_model

Usage:
  python extract_catalog_to_r2.py --brand sas --catalog r_series.pdf
  python extract_catalog_to_r2.py --all --only-new
  python extract_catalog_to_r2.py --brand sas --catalog r_series.pdf --pages 30-35

Required env:
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_KEY, R2_BUCKET_NAME
  ANTHROPIC_API_KEY
"""

from __future__ import annotations

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import argparse
import base64
import json
import os
import sys
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
    print("Install: pip install boto3 pdf2image pillow tqdm anthropic python-dotenv")
    sys.exit(1)


# ============================================================
# Config
# ============================================================

MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 8192            # v2: doubled from 4096
DPI = 150
JPEG_QUALITY = 85
RETRY_COUNT = 3
CONTINUATION_RETRY = 2

CATALOGS_PREFIX  = "catalogs/"
DATABASES_PREFIX = "databases/"


# ============================================================
# Prompts — v2 (stricter, clearer about gear_size vs power_kw)
# ============================================================

CLASSIFY_PROMPT = """You are looking at ONE page from a gear motor catalog.
Classify it into EXACTLY ONE category. Return ONLY JSON:

{
  "category": "<one of: cover | naming_convention | design_variants | structure_diagram | ratio_torque_table | selection_table_by_kw | dimension_drawing | lubricant_table | motor_data | accessories | other>",
  "gear_size_or_family": "<e.g. R37, S67, K77 — NOT a kW value!>",
  "has_data_tables": true|false,
  "brief_note": "<one sentence description>"
}

DISTINGUISHING ratio_torque_table vs selection_table_by_kw:

- **ratio_torque_table**: organized BY GEAR SIZE. Has ONE header at the top like "S37 90Nm" or "S67 520Nm".
  Columns: i (ratio), n_a (output speed), M_amax (max torque), F_Ra, AD.
  No motor model shown per row.

- **selection_table_by_kw**: organized BY MOTOR POWER. Has headers like "0.75kW", "1.5kW", "5.5kW"
  BETWEEN tables. Columns: output_speed, output_torque, ratio, radial_load, service_factor, Model.
  Each row has a "Model" column showing e.g. "R 37 D90L4" or "RF 67 D132S4".
  Multiple kW sections may appear on one page.

CRITICAL:
- Never use a kW value (like "1.5kW", "5.5kW") as gear_size_or_family. That's power, not gear size!
- Gear sizes look like "R37", "R107", "S67", "K87", "KAF77" — always letter(s) + 2-3 digits.
- If page has kW headers, category is "selection_table_by_kw".
- If page shows gear-size chart (ratio-torque only), category is "ratio_torque_table".

Return ONLY the JSON."""


EXTRACT_NAMING_PROMPT = """Extract the naming convention decoder from this page.
Return EXACT JSON:
{
  "example_code": "<the example code shown>",
  "positions": [
    {"position_number": 1, "field_name": "...", "codes": {"X": "meaning"}, "notes": "..."}
  ]
}
Include ALL positions (usually 15-17). Return ONLY JSON."""


EXTRACT_RATIO_TORQUE_PROMPT = """This page shows ratio/torque tables for specific GEAR SIZES at a
fixed input speed (usually n_e = 1400 rpm for 50Hz).

Each table header shows the gear size code (e.g. "S67 520Nm", "R107 3770Nm").
Extract ALL rows from ALL tables on this page.

Return EXACT JSON:
{
  "input_speed_rpm": <number>,
  "tables": [
    {
      "gear_size": "<e.g. R107, S67 — MUST start with letter(s) then digits, never a kW value>",
      "rated_max_torque_nm": <number from header>,
      "rows": [
        {"ratio": <n>, "output_speed_rpm": <n>, "max_torque_nm": <n>,
         "radial_load_n": <n>, "design_variant": "AD1|AD2|null"}
      ]
    }
  ]
}

CRITICAL RULES:
- gear_size MUST be a gear code like "R107", "S67", "K87" — NEVER a kW value like "1.5kW" or "45kW"!
- If you see kW headers on this page, this is NOT a ratio_torque_table; use selection_table_by_kw instead.
- Extract EVERY row. Do not skip or summarize.

Return ONLY JSON."""


EXTRACT_SELECTION_PROMPT = """This page shows SELECTION TABLES organized by motor POWER (kW).

You'll see multiple sections, each with a kW header like "1.5kW", "5.5kW", etc.
Within each kW section, each row lists a gearbox configuration with a "Model" column.

The Model column typically looks like:
  R    37 D90L4           <- gear type variant + gear size + motor frame
  RF   67 D132S4
  RX   87 D112M4

Where:
- "R", "RF", "RX", "RM" = gear type variant (may span multiple rows -- inherit if blank)
- "37", "67", "87" = gear size number
- "D90L4", "D132S4" = motor frame code

Return EXACT JSON:
{
  "tables": [
    {
      "power_kw": <number e.g. 1.5>,
      "rows": [
        {
          "output_speed_rpm": <n>,
          "output_torque_nm": <n>,
          "ratio": <n>,
          "radial_load_n": <n>,
          "service_factor": <n>,
          "variants": ["R", "RF"],
          "gear_size": "37",
          "motor_frame": "D90L4",
          "full_model": "R37 D90L4"
        }
      ]
    }
  ]
}

CRITICAL RULES FOR MODEL CODE:
- "full_model" MUST include gear type prefix + gear size + motor frame (e.g. "R37 D90L4", "RF167 D280M4").
- Never omit the letter prefix! "37 D90L4" alone is WRONG; it must be "R37 D90L4".
- If the Model column shows variants stacked (R / RF on different lines but same numbers/frame), combine variants into array.
- If a row's Model field is blank (inherits from row above), fill it in using the most recent Model seen.
- Extract EVERY row from EVERY kW section. Do not skip rows or summarize.

CRITICAL RULES FOR READING NUMBERS (READ EACH DIGIT INDIVIDUALLY):
These tables have LOTS of similar-looking numbers. OCR mistakes are catastrophic
because they produce model codes that don't exist in the catalog. Follow these rules:

1. RATIO COLUMN ("i"):
   - Ratio values in SEW catalogs end with a decimal: 10.11, 13.12, 20.21, 29.49, 35.26, 47.27, 52.68, 65.60, 78.57, 92.70, 115.63, 128.97, 188.73, 247.57, 501.8, 1283
   - Read each digit LEFT TO RIGHT. Do not swap digits.
   - Common mistakes to AVOID:
     * 92.70 read as 92.27 (swapping 70 <-> 27)
     * 10.11 read as 10.01 or 10.17
     * 128.97 read as 128.07 or 128.27
   - If the last digit is 0, it's usually followed by a trailing style (e.g. "92.70" not "92.7" in printed tables).
   - DOUBLE CHECK: if you're unsure whether "92.70" or "92.27", look at the RATIO column
     above and below this row. SEW tables are monotonically DECREASING or INCREASING.
     For example in 7.5kW section: the order is typically ..., 78.57, 92.70, (next)
     — if reading "92.27" would break the monotonic order, you misread.

2. SERVICE FACTOR COLUMN ("fB" or "SF"):
   - Values are always 0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20, ..., 3.80, 4.00
   - Steps are 0.05 or 0.10
   - If you read "1.12" or "2.35", re-check -- catalogs do not use those values.

3. TORQUE COLUMN ("Ma" or "Nm"):
   - Usually 3-5 digit integers (101, 450, 1130, 4950, 12300).
   - If you see a decimal there, you probably confused the Torque column with another.

4. OUTPUT SPEED COLUMN ("na" or "rpm"):
   - Small integers: 2, 5, 11, 16, 25, 141, 245, 500, etc.

5. VERIFICATION STEP (DO THIS BEFORE FINALIZING):
   After reading all rows in a kW section, verify the RATIO column is monotonic
   (either strictly decreasing top-to-bottom, or strictly increasing).
   If you find a row where the ratio BREAKS the monotonic order, RE-READ that row.
   Example: if you got ratios [92.27, 78.57, 72.88], then 92.27 is WRONG because
   92.27 < 92.70 would still be decreasing. Re-read the top value to get 92.70.

Return ONLY JSON. No markdown. No explanation."""


EXTRACT_DIMENSION_PROMPT = """Extract dimension drawings and tables from this page.

Return EXACT JSON:
{
  "drawing_applies_to": ["SA67", "SA77"],
  "mounting_type": "foot|flange_B5|flange_B14|hollow_shaft|hollow_shaft_shrink_disc",
  "dimension_table": [
    {
      "model": "SA67",
      "dimensions": {
        "a": <mm>, "b": <mm>, "c": <mm>,
        "e": <mm>, "f": <mm>, "g": <mm>, "h": <mm>,
        "k": <mm>, "m": "<e.g. M12>", "p": <mm>,
        "d1_hollow_shaft": "45H7",
        "s1_key": "M16x40",
        "H_overall_height": <mm>,
        "L1": <mm>, "L2": <mm>
      }
    }
  ]
}

Capture ALL dimension labels and values. Return ONLY JSON."""


GENERIC_PROMPT = """Extract all structured data from this catalog page as JSON.
Focus on tables, model codes, specifications. Return ONLY JSON."""


CONTINUATION_PROMPT = """Your previous JSON response was cut off due to length limit.
Please return the REMAINING rows as a JSON array only (no wrapper object).

Example format:
[
  {"output_speed_rpm": 24, "ratio": 58.65, ...},
  {"output_speed_rpm": 27, ...}
]

Continue from where you stopped. Do not repeat rows you already returned.
Return ONLY the JSON array. No markdown, no explanation."""


# ============================================================
# R2 helpers
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
# Extractor with continuation
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

    def _api_call(self, image_b64: str, prompt: str,
                  prior_turns: Optional[list] = None) -> tuple[str, str]:
        """Returns (response_text, stop_reason)."""
        messages = [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                {"type": "text", "text": prompt},
            ],
        }]
        if prior_turns:
            messages.extend(prior_turns)

        msg = self.client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=messages,
        )
        text = "".join(b.text for b in msg.content if hasattr(b, "text"))
        return text, (msg.stop_reason or "end_turn")

    def call(self, image: Image.Image, prompt: str,
             allow_continuation: bool = True) -> dict:
        """Main entry — returns parsed JSON dict or {_parse_error, _raw}."""
        b64 = self._b64(image)
        last_err = None

        for attempt in range(RETRY_COUNT):
            try:
                text, stop = self._api_call(b64, prompt)
                cleaned = text.strip().replace("```json", "").replace("```", "").strip()

                # Try parse as-is
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    pass

                # Truncated? Try continuation.
                if stop == "max_tokens" and allow_continuation:
                    combined = self._continue(b64, prompt, text)
                    if combined:
                        try:
                            return json.loads(combined)
                        except json.JSONDecodeError:
                            return {
                                "_parse_error": True,
                                "_raw": combined,
                                "_stop_reason": "max_tokens_after_continuation",
                            }

                # Fallback: try outermost { ... }
                start = cleaned.find("{")
                end = cleaned.rfind("}")
                if start >= 0 and end > start:
                    try:
                        return json.loads(cleaned[start:end+1])
                    except json.JSONDecodeError:
                        pass

                # Give up — save FULL raw (not truncated)
                return {
                    "_parse_error": True,
                    "_raw": text,   # ← full, not [:300]
                    "_stop_reason": stop,
                }

            except anthropic.APIError as e:
                last_err = e
                if attempt < RETRY_COUNT - 1:
                    wait = 5 * (2 ** attempt)
                    print(f"    ⚠ API error: {e}. Retry in {wait}s...")
                    time.sleep(wait)
                else:
                    raise

        raise RuntimeError(f"Failed: {last_err}")

    def _continue(self, b64: str, original_prompt: str, prior_text: str) -> Optional[str]:
        """Ask Claude to continue the truncated JSON."""
        # Anchor at last complete closing brace
        last_close = prior_text.rfind("}")
        if last_close < 0:
            return None
        truncated = prior_text[:last_close + 1]

        for _ in range(CONTINUATION_RETRY):
            try:
                text, stop = self._api_call(
                    b64, original_prompt,
                    prior_turns=[
                        {"role": "assistant", "content": prior_text},
                        {"role": "user", "content": CONTINUATION_PROMPT},
                    ],
                )
                cleaned = text.strip().replace("```json", "").replace("```", "").strip()

                # Extract JSON array
                arr_start = cleaned.find("[")
                arr_end = cleaned.rfind("]")
                if arr_start < 0 or arr_end <= arr_start:
                    continue
                try:
                    new_rows = json.loads(cleaned[arr_start:arr_end+1])
                except json.JSONDecodeError:
                    continue
                if not isinstance(new_rows, list):
                    continue

                # Stitch: append new_rows to truncated JSON then close brackets
                return self._stitch(truncated, new_rows)
            except Exception:
                continue
        return None

    def _stitch(self, truncated_json: str, new_rows: list) -> Optional[str]:
        """Append new rows to truncated JSON and close remaining brackets."""
        # Count unclosed brackets
        depth_brace = 0
        depth_bracket = 0
        in_string = False
        escape = False
        for ch in truncated_json:
            if escape: escape = False; continue
            if ch == "\\": escape = True; continue
            if ch == '"': in_string = not in_string; continue
            if in_string: continue
            if ch == "{": depth_brace += 1
            elif ch == "}": depth_brace -= 1
            elif ch == "[": depth_bracket += 1
            elif ch == "]": depth_bracket -= 1

        suffix = truncated_json.rstrip()
        need_comma = suffix.endswith("}")

        parts = [suffix]
        if need_comma:
            parts.append(",\n")
        parts.append(",\n".join(json.dumps(r, ensure_ascii=False) for r in new_rows))
        parts.append("]" * depth_bracket)
        parts.append("}" * depth_brace)

        return "".join(parts)


# ============================================================
# Pipeline
# ============================================================

def extract_catalog(pdf_bytes: bytes, extractor: Extractor,
                    start_page: int = 1, end_page: Optional[int] = None) -> dict:
    print(f"  Rasterizing PDF at {DPI} DPI...")
    images = convert_from_bytes(pdf_bytes, dpi=DPI, first_page=start_page, last_page=end_page)
    print(f"  → {len(images)} pages")

    db = {
        "total_pages": len(images),
        "page_range": [start_page, start_page + len(images) - 1],
        "extraction_model": MODEL,
        "extraction_max_tokens": MAX_TOKENS,
        "extractor_version": "v2",
        "pages": [],
        "naming_convention": None,
        "ratio_torque_tables": [],
        "selection_tables": [],
        "dimension_tables": [],
    }

    # Stage 1: Classify
    print("  Stage 1: classifying pages...")
    classes = []
    for idx, img in enumerate(tqdm(images, desc="  classify", unit="pg")):
        try:
            c = extractor.call(img, CLASSIFY_PROMPT, allow_continuation=False)
        except Exception as e:
            c = {"category": "error", "_error": str(e)}
        c["_page"] = idx + start_page
        classes.append(c)
        time.sleep(0.3)
    db["pages"] = classes

    # Stage 2: Extract relevant pages
    prompt_map = {
        "naming_convention": EXTRACT_NAMING_PROMPT,
        "ratio_torque_table": EXTRACT_RATIO_TORQUE_PROMPT,
        "selection_table_by_kw": EXTRACT_SELECTION_PROMPT,
        "dimension_drawing": EXTRACT_DIMENSION_PROMPT,
    }
    relevant = [c for c in classes if c.get("category") in prompt_map]
    print(f"  Stage 2: extracting {len(relevant)} relevant pages...")

    success = 0
    errors = 0

    # --- Post-processor: verify ratio monotonicity and retry if broken ---
    def _check_monotonic(rows: list) -> tuple[bool, Optional[int]]:
        """
        Check if rows' ratios are monotonic (decreasing OR increasing).
        Returns (is_monotonic, first_break_index).
        """
        ratios = [r.get("ratio") for r in rows if r.get("ratio") is not None]
        if len(ratios) < 3:
            return True, None
        # determine direction from first two values
        decreasing = ratios[0] > ratios[1]
        for i in range(1, len(ratios)):
            prev, cur = ratios[i-1], ratios[i]
            if decreasing:
                if cur >= prev:  # expected smaller, got equal or larger
                    return False, i
            else:
                if cur <= prev:
                    return False, i
        return True, None

    for c in tqdm(relevant, desc="  extract", unit="pg"):
        page_num = c["_page"]
        img = images[page_num - start_page]
        try:
            d = extractor.call(img, prompt_map[c["category"]])
            d["_page"] = page_num
            cat = c["category"]
            
            # v3: For selection tables, verify ratio monotonicity
            if cat == "selection_table_by_kw" and d.get("tables") and not d.get("_parse_error"):
                needs_retry = False
                for tbl in d["tables"]:
                    mono, break_idx = _check_monotonic(tbl.get("rows", []))
                    if not mono:
                        needs_retry = True
                        print(f"  page {page_num} kW={tbl.get('power_kw')}: ratio monotonicity broken at row {break_idx}, retrying...")
                        break
                
                if needs_retry:
                    retry_prompt = prompt_map[cat] + """

RETRY: Your previous response had a ratio value that broke the monotonic order.
Re-read every row VERY carefully, digit by digit. Pay extra attention to:
- Numbers with "70" vs "27" (easy to swap)  
- Numbers ending in "0" (read as trailing zero, not omitted)
- Decimal point placement

Return the corrected JSON."""
                    time.sleep(1.0)
                    d2 = extractor.call(img, retry_prompt)
                    d2["_page"] = page_num
                    d2["_retried_monotonic"] = True
                    # Check if retry fixed it
                    retry_ok = True
                    if d2.get("tables"):
                        for tbl in d2["tables"]:
                            mono, _ = _check_monotonic(tbl.get("rows", []))
                            if not mono:
                                retry_ok = False
                                break
                    if retry_ok:
                        d = d2
                    else:
                        d["_monotonic_warning"] = "Ratios not monotonic after retry"
            
            if d.get("_parse_error"):
                errors += 1
            else:
                success += 1

            if cat == "naming_convention" and db["naming_convention"] is None:
                db["naming_convention"] = d
            elif cat == "ratio_torque_table":
                db["ratio_torque_tables"].append(d)
            elif cat == "selection_table_by_kw":
                db["selection_tables"].append(d)
            elif cat == "dimension_drawing":
                db["dimension_tables"].append(d)
        except Exception as e:
            print(f"  page {page_num}: {e}")
            errors += 1
        time.sleep(0.3)

    print(f"\n  Stage 2 summary: {success} success, {errors} parse errors")

    # Stage 3: Build model index
    print("  Stage 3: building model index...")
    index = {}
    for st in db["selection_tables"]:
        for t in st.get("tables", []):
            for r in t.get("rows", []):
                m = r.get("full_model")
                if not m:
                    continue
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
            if not m:
                continue
            e = index.setdefault(m, {"model": m, "applications": [], "dimensions": {}})
            e["dimensions"][dt.get("mounting_type", "unknown")] = r.get("dimensions", {})
    db["model_index"] = index

    return db


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
    parts = catalog_key[len(CATALOGS_PREFIX):].split("/", 1)
    if len(parts) != 2:
        return f"{DATABASES_PREFIX}unknown__{parts[0]}.json"
    brand, filename = parts
    stem = filename.rsplit(".", 1)[0].replace("/", "_")
    return f"{DATABASES_PREFIX}{brand}__{stem}.json"


def process_one(r2, bucket, catalog_key, extractor, pages=None, dry_run=False, output_file=None):
    db_key = catalog_to_db_key(catalog_key)
    print(f"\n{catalog_key} -> {db_key}")

    print("  Downloading from R2...")
    resp = r2.get_object(Bucket=bucket, Key=catalog_key)
    pdf_bytes = resp["Body"].read()
    print(f"  {len(pdf_bytes)/1024/1024:.1f} MB")

    start_page, end_page = 1, None
    if pages:
        a, b = pages.split("-")
        start_page = int(a)
        end_page = int(b)

    db = extract_catalog(pdf_bytes, extractor, start_page, end_page)
    db["source_key"] = catalog_key

    payload = json.dumps(db, ensure_ascii=False, indent=2).encode("utf-8")

    if dry_run or output_file:
        out = output_file or f"/tmp/{db_key.replace('/', '__')}.dryrun.json"
        with open(out, "wb") as f:
            f.write(payload)
        print(f"  [DRY-RUN] Saved locally: {out} ({len(payload)/1024:.1f} KB)")
        print(f"  [DRY-RUN] NOT uploaded to R2 (use without --dry-run to upload)")
    else:
        r2.put_object(Bucket=bucket, Key=db_key, Body=payload, ContentType="application/json")
        print(f"  Uploaded {db_key} ({len(payload)/1024:.1f} KB)")

    n_sel = sum(len(t.get("rows", [])) for st in db["selection_tables"] for t in st.get("tables", []))
    n_rt  = sum(len(t.get("rows", [])) for rt in db["ratio_torque_tables"] for t in rt.get("tables", []))
    n_dim = sum(len(dt.get("dimension_table", [])) for dt in db["dimension_tables"])
    print(f"     naming: {'Y' if db['naming_convention'] else 'N'} | "
          f"selection rows: {n_sel} | ratio rows: {n_rt} | dim rows: {n_dim} | "
          f"models: {len(db.get('model_index', {}))}")


def main():
    p = argparse.ArgumentParser(description="Extract gear motor catalogs from R2 (v2)")
    p.add_argument("--brand")
    p.add_argument("--catalog")
    p.add_argument("--all", action="store_true")
    p.add_argument("--only-new", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--pages", help="e.g. 30-35 for testing")
    p.add_argument("--dry-run", action="store_true",
                   help="Extract but DON'T upload to R2 — save to /tmp instead")
    p.add_argument("--output-file",
                   help="Write JSON to this local path instead of R2")
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

    print("=" * 60)
    print(f"SAS Catalog Extractor v2")
    print(f"  Model:      {MODEL}")
    print(f"  Max tokens: {MAX_TOKENS}")
    print(f"  Bucket:     {bucket}")
    print("=" * 60)

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
                process_one(r2, bucket, c, extractor, args.pages,
                            dry_run=args.dry_run, output_file=args.output_file)
            except Exception as e:
                print(f"  {c}: {e}")
    else:
        key = f"{CATALOGS_PREFIX}{args.brand}/{args.catalog}"
        process_one(r2, bucket, key, extractor, args.pages,
                    dry_run=args.dry_run, output_file=args.output_file)


if __name__ == "__main__":
    main()
