"""
utils/gear_matcher.py
=====================
Gear Motor Matcher core module for SAS.

Integrates with existing Flask app (uses same R2 client from app.py).
Auto-discovers brands by scanning R2 bucket prefix `catalogs/`.

R2 bucket layout expected:
    catalogs/
        sas/
            s_series.pdf
            r_series.pdf
            k_series.pdf
            ...
        sew/
            r_series.pdf
            s_series.pdf
            ...
        nord/
            ...
        siemens/
            ...

    databases/                  # <-- extracted JSON databases (auto-generated)
        sas__s_series.json
        sew__r_series.json
        ...

    nameplate_reviews/          # <-- low-confidence matches saved for review
        2026-04-18/
            <uuid>.jpg
            <uuid>.json

Usage inside Flask app (add to app.py):

    from utils.gear_matcher import init_matcher, matcher_bp

    init_matcher(r2_client=r2, bucket=R2_BUCKET_NAME, public_url=R2_PUBLIC_URL)
    app.register_blueprint(matcher_bp, url_prefix='/matcher')
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from io import BytesIO
from typing import Optional

from flask import (
    Blueprint, jsonify, render_template, request, abort, current_app
)
from werkzeug.utils import secure_filename


# ============================================================
# Configuration
# ============================================================

CATALOGS_PREFIX   = "catalogs/"
DATABASES_PREFIX  = "databases/"
REVIEWS_PREFIX    = "nameplate_reviews/"

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAX_IMAGE_SIZE    = 10 * 1024 * 1024  # 10 MB

# Anthropic Claude API
CLAUDE_MODEL      = "claude-sonnet-4-5"
CLAUDE_MAX_TOKENS = 2048

# Rate limiting per IP (separate from main app's login rate limit)
MATCHER_RATE_MAX    = 30   # scans per IP per hour
MATCHER_RATE_WINDOW = 3600 # seconds

# Match score threshold for archiving to R2
LOW_CONFIDENCE_THRESHOLD = 80.0


# ============================================================
# Module-level state (initialized via init_matcher)
# ============================================================

_state: dict = {
    "r2": None,
    "bucket": None,
    "public_url": None,
    "anthropic_key": None,
    "databases_cache": {},          # key -> (mtime, database_dict)
    "databases_cache_lock": threading.Lock(),
    "brand_list_cache": None,
    "brand_list_mtime": 0,
    "brand_list_lock": threading.Lock(),
}

_ip_scan_log: dict[str, list[float]] = {}
_ip_lock = threading.Lock()


# ============================================================
# Data classes
# ============================================================

@dataclass
class NameplateSpec:
    """Extracted spec from a competitor or SAS nameplate photo."""
    brand: Optional[str] = None
    full_model_code: Optional[str] = None
    gear_type_hint: Optional[str] = None
    gear_size_code: Optional[str] = None     # e.g. "R37", "R107"
    power_kw: Optional[float] = None
    power_hp: Optional[float] = None
    input_rpm: Optional[float] = None
    output_rpm: Optional[float] = None
    ratio: Optional[float] = None
    torque_nm: Optional[float] = None
    voltage: Optional[str] = None
    frequency_hz: Optional[int] = None
    frame_size: Optional[str] = None
    poles: Optional[int] = None
    mounting_position: Optional[str] = None
    ip_rating: Optional[str] = None
    efficiency_class: Optional[str] = None
    service_factor: Optional[float] = None
    serial_number: Optional[str] = None
    ratio_consistent: Optional[bool] = None  # set by sanity check
    ratio_computed: Optional[float] = None   # from input_rpm / output_rpm
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "raw"}


@dataclass
class MatchResult:
    sas_model: str
    power_kw: Optional[float]
    ratio: Optional[float]
    output_speed_rpm: Optional[float]
    output_torque_nm: Optional[float]
    variants: list[str]
    service_factor: Optional[float]
    ratio_match_pct: float
    total_score: float
    warnings: list[str] = field(default_factory=list)
    # v4: dimensions for side-by-side comparison
    dimensions: Optional[dict] = None       # SAS dims (d, h, b, ...) from hardcoded ref table
    exact_size_match: bool = False           # True if gear size matches competitor exactly (e.g. both R37)
    dimension_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__


# ============================================================
# Init
# ============================================================

def init_matcher(r2_client, bucket: str, public_url: str, anthropic_key: Optional[str] = None):
    """Call this once from app.py after R2 is configured."""
    _state["r2"] = r2_client
    _state["bucket"] = bucket
    _state["public_url"] = public_url.rstrip("/") if public_url else ""
    _state["anthropic_key"] = anthropic_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not _state["anthropic_key"]:
        print("[matcher] ⚠ ANTHROPIC_API_KEY not set — nameplate reading will fail")
    print(f"[matcher] ✓ initialized with bucket={bucket}")


# ============================================================
# R2 helpers (reuse the client from app.py)
# ============================================================

def _r2():
    if _state["r2"] is None:
        raise RuntimeError("Matcher not initialized — call init_matcher() in app.py")
    return _state["r2"]


def _list_objects(prefix: str) -> list[dict]:
    """List all objects under a prefix (handles pagination)."""
    r2 = _r2()
    bucket = _state["bucket"]
    out = []
    continuation = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if continuation:
            kwargs["ContinuationToken"] = continuation
        resp = r2.list_objects_v2(**kwargs)
        for o in resp.get("Contents", []):
            out.append({
                "key": o["Key"],
                "size": o["Size"],
                "last_modified": o["LastModified"],
            })
        if resp.get("IsTruncated"):
            continuation = resp.get("NextContinuationToken")
        else:
            break
    return out


def _download_bytes(key: str) -> bytes:
    resp = _r2().get_object(Bucket=_state["bucket"], Key=key)
    return resp["Body"].read()


def _upload_bytes(key: str, data: bytes, content_type: str):
    _r2().put_object(
        Bucket=_state["bucket"],
        Key=key,
        Body=data,
        ContentType=content_type,
    )


# ============================================================
# Brand discovery (auto from R2)
# ============================================================

def get_brand_list(force_refresh: bool = False) -> list[dict]:
    """
    Auto-discover brands from R2 bucket structure.
    Cached for 5 minutes to avoid hammering R2.
    
    Returns: [{"id": "sew", "name": "SEW-EURODRIVE", "catalogs": [...]}, ...]
    """
    now = time.time()
    with _state["brand_list_lock"]:
        if (not force_refresh
                and _state["brand_list_cache"] is not None
                and now - _state["brand_list_mtime"] < 300):
            return _state["brand_list_cache"]

        objects = _list_objects(CATALOGS_PREFIX)
        brands: dict[str, dict] = {}

        for o in objects:
            # key looks like: catalogs/sew/r_series.pdf
            parts = o["key"].split("/")
            if len(parts) < 3:
                continue
            brand_id = parts[1].lower().strip()
            filename = "/".join(parts[2:])
            if not brand_id or brand_id.startswith("."):
                continue

            brand = brands.setdefault(brand_id, {
                "id": brand_id,
                "name": _brand_display_name(brand_id),
                "catalogs": [],
            })
            if filename.lower().endswith(".pdf"):
                brand["catalogs"].append({
                    "filename": filename,
                    "size_mb": round(o["size"] / (1024 * 1024), 2),
                    "last_modified": str(o["last_modified"]),
                })

        # Always include SAS first if present
        result = sorted(
            brands.values(),
            key=lambda b: (0 if b["id"] == "sas" else 1, b["name"])
        )
        _state["brand_list_cache"] = result
        _state["brand_list_mtime"] = now
        return result


def _brand_display_name(brand_id: str) -> str:
    """Map brand_id to display name."""
    known = {
        "sas": "SAS (Synergy Asia Solution)",
        "sew": "SEW-EURODRIVE",
        "nord": "NORD Drivesystems",
        "siemens": "Siemens",
        "bonfiglioli": "Bonfiglioli",
        "lenze": "Lenze",
        "bauer": "Bauer Gear Motor",
        "sumitomo": "Sumitomo",
        "motovario": "Motovario",
        "abb": "ABB",
    }
    return known.get(brand_id, brand_id.upper())


# ============================================================
# Database loading (extracted catalog JSON)
# ============================================================

def get_database(brand_id: str) -> Optional[dict]:
    """
    Load merged database for a brand from R2.
    Combines all JSON files under databases/<brand_id>__*.json
    Cached by R2 LastModified.
    """
    brand_id = brand_id.lower().strip()
    prefix = f"{DATABASES_PREFIX}{brand_id}__"

    cache_key = f"brand_db:{brand_id}"
    with _state["databases_cache_lock"]:
        # Check cache — compute combined mtime from all matching objects
        try:
            objects = _list_objects(prefix)
        except Exception as e:
            print(f"[matcher] failed to list databases for {brand_id}: {e}", flush=True)
            return None

        if not objects:
            return None

        combined_mtime = str(max(o["last_modified"] for o in objects))
        cached = _state["databases_cache"].get(cache_key)
        if cached and cached[0] == combined_mtime:
            return cached[1]

        # Download and merge
        merged = {
            "brand_id": brand_id,
            "brand_name": _brand_display_name(brand_id),
            "source_catalogs": [],
            "naming_conventions": [],
            "ratio_torque_tables": [],
            "selection_tables": [],
            "dimension_tables": [],
            "model_index": {},
        }
        for o in objects:
            try:
                raw = _download_bytes(o["key"])
                db = json.loads(raw)
            except Exception as e:
                print(f"[matcher] failed to load {o['key']}: {e}", flush=True)
                continue
            merged["source_catalogs"].append(o["key"])
            if db.get("naming_convention"):
                merged["naming_conventions"].append(db["naming_convention"])
            merged["ratio_torque_tables"].extend(db.get("ratio_torque_tables", []))
            merged["selection_tables"].extend(db.get("selection_tables", []))
            merged["dimension_tables"].extend(db.get("dimension_tables", []))
            for model, info in (db.get("model_index") or {}).items():
                existing = merged["model_index"].setdefault(model, {
                    "model": model, "applications": [], "dimensions": {}
                })
                existing["applications"].extend(info.get("applications", []))
                existing["dimensions"].update(info.get("dimensions", {}))

        _state["databases_cache"][cache_key] = (combined_mtime, merged)
        return merged


# ============================================================
# Claude Vision — nameplate reader
# ============================================================

NAMEPLATE_PROMPT = """You are reading a gear motor nameplate photo.

Extract the specifications into JSON with this EXACT shape (use null for fields
you cannot read clearly):

{
  "brand": "e.g. SEW-EURODRIVE",
  "full_model_code": "full model code shown, e.g. R37 DRE90L4 or R107 DV132S4/BMG/HR/EV1A",
  "gear_type_hint": "R|S|K|F|W (single letter — first char of gear type from full_model_code)",
  "gear_size_code": "full gear size with prefix, e.g. R37, R107, S67, K87 (MUST be letter(s)+2-3 digits)",
  "power_kw": number,
  "power_hp": number,
  "input_rpm": number,
  "output_rpm": number,
  "ratio": number,
  "torque_nm": number,
  "voltage": "e.g. 460V 3ph",
  "frequency_hz": 50 or 60,
  "frame_size": "e.g. 90L",
  "poles": number,
  "mounting_position": "e.g. M1, M1A, B3",
  "ip_rating": "e.g. IP66",
  "efficiency_class": "e.g. IE3",
  "service_factor": number,
  "serial_number": "if visible",
  "_confidence": { "<field_name>": "high|medium|low" },
  "_ratio_consistent": true|false
}

CRITICAL READING RULES — SEW-EURODRIVE nameplates especially:

1. NUMBER FORMAT (European decimal notation):
   - SEW uses COMMA as decimal separator. "10,11" means 10.11 (ten point eleven), NOT 1011 or two numbers!
   - Always convert commas in numbers to periods: "10,11" → 10.11
   - Exception: "1430/141" with slash = TWO separate numbers

2. RATIO FIELD (labeled "i" or "i=" on SEW plates):
   - Look for the lowercase "i" label, usually on its own line
   - "i 10,11" or "i=10,11" means ratio = 10.11
   - Do NOT confuse ratio with torque, RPM, or efficiency values

3. RPM FIELDS ("r/min" or "min^-1"):
   - "r/min 1430/141" means input_rpm=1430, output_rpm=141
   - Different lines may show 50Hz and 60Hz values — report 50Hz primary
   - NEVER put torque values in RPM fields

4. TORQUE FIELD (labeled "Nm"):
   - "Nm 101/83" means 101 Nm @ 50Hz, 83 Nm @ 60Hz — use 101 as primary
   - NEVER put this in output_rpm field

5. MODEL CODE READING:
   - "R37 DRE90L4" → full_model_code="R37 DRE90L4", gear_size_code="R37"
   - "R107 DV132S4/BMG/HR" → gear_size_code="R107" (NOT R17 or R7)
   - Capture everything after gear code (motor type + suffixes) in full_model_code

6. CONSISTENCY CHECK (VERY IMPORTANT):
   After reading, verify: input_rpm / output_rpm ≈ ratio
   - Example: 1430/141 = 10.14 ≈ 10.11 ✅ (set _ratio_consistent=true)
   - If they don't match within 5%, you likely misread something → re-read carefully
   - If still inconsistent, set _ratio_consistent=false and lower confidence

CRITICAL RULES:
- Only read what is CLEARLY VISIBLE. Do not guess or infer.
- If the image is blurry, not a nameplate, or unreadable, return:
  {"_error": "explain why", "_is_nameplate": false}
- Return ONLY the JSON. No markdown fences, no explanation.
"""


def _call_claude_vision(image_bytes: bytes, mime_type: str, prompt: str) -> dict:
    """
    Call Claude API directly via HTTP (no anthropic SDK dependency to keep
    the Flask app lean).
    """
    import urllib.request
    import urllib.error

    api_key = _state["anthropic_key"]
    if not api_key:
        return {"_error": "ANTHROPIC_API_KEY not configured", "_is_nameplate": False}

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": CLAUDE_MAX_TOKENS,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": mime_type, "data": b64
                }},
                {"type": "text", "text": prompt},
            ],
        }],
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:500]
        return {"_error": f"API HTTP {e.code}: {err_body}", "_is_nameplate": False}
    except Exception as e:
        return {"_error": f"API call failed: {e}", "_is_nameplate": False}

    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text += block.get("text", "")
    text = text.strip().replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to salvage JSON from within the response
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end+1])
            except json.JSONDecodeError:
                pass
        return {"_error": "Could not parse JSON from Claude response",
                "_raw": text[:500], "_is_nameplate": False}


def read_nameplate(image_bytes: bytes, mime_type: str) -> NameplateSpec:
    """Extract spec from nameplate image + ratio sanity check."""
    raw = _call_claude_vision(image_bytes, mime_type, NAMEPLATE_PROMPT)

    if raw.get("_error") or raw.get("_is_nameplate") is False:
        spec = NameplateSpec(raw=raw)
        return spec

    # Coerce number fields — handle European "10,11" notation
    def _num(v):
        if v is None or v == "":
            return None
        if isinstance(v, (int, float)):
            return v
        s = str(v).strip()
        if not s:
            return None
        # Handle European decimal separator: "10,11" → "10.11"
        # But only if there's exactly ONE comma and it's between digits (decimal mark)
        # AND the string doesn't contain a period already (which would indicate thousand separator mix)
        if "," in s and "." not in s:
            # Check if it looks like decimal: digits,digits
            if re.fullmatch(r"-?\d+,\d+", s):
                s = s.replace(",", ".")
        try:
            return float(s) if "." in s else int(s)
        except (ValueError, TypeError):
            try:
                return float(s)
            except (ValueError, TypeError):
                return None

    # Extract gear size from full_model_code (or explicit field from AI)
    gear_size = raw.get("gear_size_code") or extract_gear_size(raw.get("full_model_code"))

    spec = NameplateSpec(
        brand=raw.get("brand"),
        full_model_code=raw.get("full_model_code"),
        gear_type_hint=(raw.get("gear_type_hint") or "").upper()[:1] or None,
        gear_size_code=gear_size,
        power_kw=_num(raw.get("power_kw")),
        power_hp=_num(raw.get("power_hp")),
        input_rpm=_num(raw.get("input_rpm")),
        output_rpm=_num(raw.get("output_rpm")),
        ratio=_num(raw.get("ratio")),
        torque_nm=_num(raw.get("torque_nm")),
        voltage=raw.get("voltage"),
        frequency_hz=_num(raw.get("frequency_hz")),
        frame_size=raw.get("frame_size"),
        poles=_num(raw.get("poles")),
        mounting_position=raw.get("mounting_position"),
        ip_rating=raw.get("ip_rating"),
        efficiency_class=raw.get("efficiency_class"),
        service_factor=_num(raw.get("service_factor")),
        serial_number=raw.get("serial_number"),
        raw=raw,
    )
    # ========== v4: Ratio sanity check ==========
    # Cross-verify: input_rpm / output_rpm should equal ratio (within 5%)
    if spec.input_rpm and spec.output_rpm and spec.output_rpm > 0:
        computed = spec.input_rpm / spec.output_rpm
        spec.ratio_computed = round(computed, 2)
        if spec.ratio:
            diff_pct = abs(spec.ratio - computed) / max(spec.ratio, 0.01) * 100
            spec.ratio_consistent = diff_pct <= 5.0
            # If they disagree significantly, PREFER computed ratio
            # (AI often mis-reads the "i" label; rpm readings are usually clearer)
            if not spec.ratio_consistent:
                # Flag in raw for UI display
                spec.raw["_ratio_mismatch"] = {
                    "extracted": spec.ratio,
                    "computed": spec.ratio_computed,
                    "diff_pct": round(diff_pct, 1),
                    "action": "using_computed",
                }
                # Over-write the extracted ratio with the computed one
                spec.ratio = spec.ratio_computed
        else:
            # No ratio extracted — use computed
            spec.ratio = spec.ratio_computed
            spec.ratio_consistent = True
    elif spec.ratio and not spec.ratio_computed:
        # Can't verify without both rpms
        spec.ratio_consistent = None

    return spec


# ============================================================
# Matching engine
# ============================================================

# ============================================================
# SAS Model Composer — สร้าง SAS model code จาก spec เนมเพลท
# ============================================================
# รูปแบบ: {gear_code}-{motor_code}-{kw}-{poles}P-{ratio}-{IM}-{terminal_box}-{cable_outlet}
# ตัวอย่าง: R107-YEJ-5.5-4P-115.63-M6-270-X

# Mounting position อ่านจากเนมเพลท SEW และ map เป็น SAS M1-M6
# SEW ใช้ B3/B5/V1/V3/M1-M6, SAS ใช้ M1-M6 อย่างเดียว
_SEW_MOUNTING_MAP = {
    "B3": "M1", "B5": "M1", "B6": "M2", "B7": "M3", "B8": "M4",
    "V1": "M5", "V3": "M6",
    "M1": "M1", "M2": "M2", "M3": "M3", "M4": "M4", "M5": "M5", "M6": "M6",
    "M1A": "M1", "M1B": "M1",
}

VALID_TERMINAL_BOX_ANGLES = ["0", "90", "180", "270"]
VALID_CABLE_OUTLETS = ["X", "1", "2", "3"]


def detect_motor_code(spec) -> str:
    """
    Decide motor code based on nameplate.

    - Has brake (detected via SEW decoder, or BMG/Brake V visible) → YEJ
    - No brake → YE3 (SAS sells IE3+ motors only)
    """
    # Try SEW decoder first (most accurate)
    if spec.full_model_code:
        try:
            from utils.sew_decoder import decode_sew_model
            decoded = decode_sew_model(spec.full_model_code)
            if decoded.get("summary", {}).get("has_brake"):
                return "YEJ"
        except Exception:
            pass

    # Fallback: check full model code for BMG/BE (SEW brake designations)
    model_str = (spec.full_model_code or "").upper()
    tokens = re.split(r"[/\s-]", model_str)
    for t in tokens:
        if t.startswith("BMG") or t.startswith("BM") or t.startswith("BE"):
            return "YEJ"

    # Fallback: check raw text for "Brake V" keyword
    raw_str = json.dumps(spec.raw or {}, ensure_ascii=False).upper()
    if "BRAKE V" in raw_str or '"BRAKE"' in raw_str or "BRAKE:" in raw_str:
        return "YEJ"

    # Default: IE3+ motor without brake
    return "YE3"


def map_mounting_to_sas(im_value: Optional[str]) -> str:
    """Map SEW/generic mounting position to SAS M1-M6. Default M1."""
    if not im_value:
        return "M1"
    # Normalize: strip spaces, uppercase
    v = str(im_value).strip().upper().replace(" ", "")
    # Try direct map
    if v in _SEW_MOUNTING_MAP:
        return _SEW_MOUNTING_MAP[v]
    # Try substring (e.g. "IM M6" → "M6")
    for key, val in _SEW_MOUNTING_MAP.items():
        if key in v:
            return val
    return "M1"


def _format_number(n) -> str:
    """Format number: remove trailing .0, keep decimals as shown."""
    if n is None:
        return "?"
    if isinstance(n, float):
        if n == int(n):
            return str(int(n))
        return f"{n:g}"
    return str(n)


def compose_sas_model(spec, gear_size: str,
                      terminal_box: str = "270",
                      cable_outlet: str = "X") -> dict:
    """
    Compose SAS model code from nameplate spec + matched gear_size.

    Returns dict with:
      - full_code:  "R107-YEJ-5.5-4P-115.63-M6-270-X"
      - parts:      dict of each component (for dropdown display)
      - options:    valid values for dropdowns (terminal_box, cable_outlet)
    """
    motor_code = detect_motor_code(spec)
    kw_str = _format_number(spec.power_kw) if spec.power_kw else "?"
    poles_str = f"{spec.poles}P" if spec.poles else "4P"  # default 4P
    ratio_str = _format_number(spec.ratio) if spec.ratio else "?"
    mounting = map_mounting_to_sas(spec.mounting_position)

    tb = str(terminal_box) if str(terminal_box) in VALID_TERMINAL_BOX_ANGLES else "270"
    co = str(cable_outlet) if str(cable_outlet) in VALID_CABLE_OUTLETS else "X"

    full = f"{gear_size}-{motor_code}-{kw_str}-{poles_str}-{ratio_str}-{mounting}-{tb}-{co}"

    return {
        "full_code": full,
        "parts": {
            "gear": gear_size,
            "motor": motor_code,
            "power_kw": kw_str,
            "poles": poles_str,
            "ratio": ratio_str,
            "mounting": mounting,
            "terminal_box": tb,
            "cable_outlet": co,
        },
        "options": {
            "motor_codes": ["YE3", "YEJ", "YB", "YVP", "YD"],
            "mounting": ["M1", "M2", "M3", "M4", "M5", "M6"],
            "terminal_box": VALID_TERMINAL_BOX_ANGLES,
            "cable_outlet": VALID_CABLE_OUTLETS,
        },
        "help": {
            "motor": "YE3=standard IE3, YEJ=มีเบรค, YB=flameproof, YVP=variable frequency, YD=multi-speed",
            "terminal_box": "มุมกล่องไฟ (องศา)",
            "cable_outlet": "ทิศทางสายไฟออก (X=default)",
        },
    }


# ============================================================
# R-Series Dimension Reference Table
# ============================================================
# Hardcoded from SAS Catalog page 130 / SEW DRE-GM R-series dimensions
# All dimensions in mm. These are CRITICAL for installation compatibility —
# ถ้า customer มี R37 แล้วเราเสนอ R47 shaft ไม่ตรง h/b ก็ไม่ตรง = ใส่ไม่ได้

R_SERIES_DIMENSIONS = {
    "R17":  {"d": "20k6", "d_mm": 20, "h": 75,  "b_foot": 110, "e": 131, "a": None, "f_foot": 110, "shaft_length": 40,  "key": "M6"},
    "R17F": {"d": "20k6", "d_mm": 20, "h": 75,  "b_foot": 110, "e": 135, "a": None, "f_foot": 110, "shaft_length": 40,  "key": "M6"},
    "R27":  {"d": "25k6", "d_mm": 25, "h": 90,  "b_foot": 130, "e": 152, "a": None, "f_foot": 110, "shaft_length": 50,  "key": "M10"},
    "R27F": {"d": "25k6", "d_mm": 25, "h": 90,  "b_foot": 110, "e": 145, "a": None, "f_foot": 110, "shaft_length": 50,  "key": "M10"},
    "R37":  {"d": "25k6", "d_mm": 25, "h": 90,  "b_foot": 130, "e": 160, "a": None, "f_foot": 145, "shaft_length": 50,  "key": "M10"},
    "R37F": {"d": "25k6", "d_mm": 25, "h": 90,  "b_foot": 110, "e": 145, "a": None, "f_foot": 145, "shaft_length": 50,  "key": "M10"},
    "R47":  {"d": "30k6", "d_mm": 30, "h": 115, "b_foot": 165, "e": 195, "a": None, "f_foot": 170, "shaft_length": 60,  "key": "M10"},
    "R47F": {"d": "30k6", "d_mm": 30, "h": 115, "b_foot": 135, "e": 170, "a": None, "f_foot": 170, "shaft_length": 60,  "key": "M10"},
    "R57":  {"d": "35k6", "d_mm": 35, "h": 115, "b_foot": 165, "e": 200, "a": None, "f_foot": 190, "shaft_length": 70,  "key": "M12"},
    "R57F": {"d": "35k6", "d_mm": 35, "h": 115, "b_foot": 135, "e": 190, "a": None, "f_foot": 190, "shaft_length": 70,  "key": "M12"},
    "R67":  {"d": "35k6", "d_mm": 35, "h": 130, "b_foot": 195, "e": 235, "a": None, "f_foot": 210, "shaft_length": 70,  "key": "M12"},
    "R67F": {"d": "35k6", "d_mm": 35, "h": 130, "b_foot": 150, "e": 210, "a": None, "f_foot": 210, "shaft_length": 70,  "key": "M12"},
    "R77":  {"d": "40k6", "d_mm": 40, "h": 140, "b_foot": 205, "e": 245, "a": None, "f_foot": 230, "shaft_length": 80,  "key": "M16"},
    "R77F": {"d": "40k6", "d_mm": 40, "h": 140, "b_foot": 170, "e": 230, "a": None, "f_foot": 230, "shaft_length": 80,  "key": "M16"},
    "R87":  {"d": "50k6", "d_mm": 50, "h": 180, "b_foot": 260, "e": 310, "a": None, "f_foot": 290, "shaft_length": 100, "key": "M16"},
    "R87F": {"d": "50k6", "d_mm": 50, "h": 180, "b_foot": 215, "e": 290, "a": None, "f_foot": 290, "shaft_length": 100, "key": "M16"},
    "R97":  {"d": "60m6", "d_mm": 60, "h": 225, "b_foot": 320, "e": 375, "a": None, "f_foot": 360, "shaft_length": 120, "key": "M20"},
    "R107": {"d": "70m6", "d_mm": 70, "h": 250, "b_foot": 360, "e": 420, "a": None, "f_foot": 400, "shaft_length": 140, "key": "M20"},
    "R137": {"d": "90m6", "d_mm": 90, "h": 315, "b_foot": 450, "e": 525, "a": None, "f_foot": 500, "shaft_length": 170, "key": "M24"},
    "R147": {"d":"110m6", "d_mm":110, "h": 355, "b_foot": 510, "e": 590, "a": None, "f_foot": 560, "shaft_length": 210, "key": "M30"},
    "R167": {"d":"120m6", "d_mm":120, "h": 400, "b_foot": 580, "e": 675, "a": None, "f_foot": 635, "shaft_length": 210, "key": "M36"},
}


_GEAR_SIZE_RE = re.compile(r"^([RSKFW][A-Z]*)(\d{2,3})", re.IGNORECASE)


def extract_gear_size(code: Optional[str]) -> Optional[str]:
    """
    ดึง gear size (เช่น 'R37', 'R107') จาก full model code.
    
    Examples:
        'R37 DRE90L4' → 'R37'
        'R107 DV132S4/BMG/HR' → 'R107'
        'RF47 D90L4' → 'RF47'  (flange variant)
        'R37' → 'R37'
    """
    if not code:
        return None
    # Try first token of the string
    first_token = str(code).strip().split()[0] if str(code).strip() else ""
    m = _GEAR_SIZE_RE.match(first_token)
    if m:
        prefix = m.group(1).upper()
        size = m.group(2)
        return f"{prefix}{size}"
    return None


def get_dimensions(gear_size: Optional[str]) -> Optional[dict]:
    """Look up dimension info for a gear size. Returns None if not in reference."""
    if not gear_size:
        return None
    # Normalize: R37F → R37F, R37 → R37
    gs = gear_size.upper()
    return R_SERIES_DIMENSIONS.get(gs)


def compare_dimensions(competitor_size: Optional[str], sas_size: Optional[str]) -> dict:
    """
    Compare dimensions between competitor gear size and SAS candidate.
    
    Returns:
        {
          "competitor": {...} or None,
          "sas": {...} or None,
          "differences": [{"field": "d", "competitor": 25, "sas": 30, "critical": True}],
          "installation_compatible": bool,
          "notes": ["shaft diameter mismatch", "mounting holes differ"],
        }
    """
    comp = get_dimensions(competitor_size)
    sas = get_dimensions(sas_size)
    
    result = {
        "competitor": comp,
        "sas": sas,
        "differences": [],
        "installation_compatible": True,
        "notes": [],
    }
    
    if not comp or not sas:
        return result
    
    # Check critical dimensions (these MUST match for drop-in replacement)
    critical_fields = [
        ("d_mm", "Shaft diameter", True),
        ("h", "Center height", True),
        ("f_foot", "Foot-hole pitch (length)", True),
        ("b_foot", "Foot-hole width", False),
        ("shaft_length", "Shaft length", False),
        ("key", "Keyway size", False),
    ]
    
    for field_key, label, is_critical in critical_fields:
        cv = comp.get(field_key)
        sv = sas.get(field_key)
        if cv is None or sv is None:
            continue
        if cv != sv:
            result["differences"].append({
                "field": field_key,
                "label": label,
                "competitor": cv,
                "sas": sv,
                "critical": is_critical,
            })
            if is_critical:
                result["installation_compatible"] = False
                result["notes"].append(f"{label} ต่างกัน: SEW {cv} vs SAS {sv}")
    
    return result


def _score_candidate(r: float, t: Optional[float], kw: Optional[float],
                     target_ratio: float, target_torque: Optional[float],
                     target_kw: Optional[float]) -> tuple[float, float, list[str]]:
    """Compute (total_score, ratio_match_pct, warnings) for a single candidate."""
    ratio_diff_pct = abs(r - target_ratio) / target_ratio * 100
    ratio_score = max(0.0, 100 - ratio_diff_pct * 3)

    warnings: list[str] = []
    torque_score = 100.0
    if target_torque and t:
        if t < target_torque:
            torque_score = 40.0
            warnings.append(f"Gearbox rating {t} Nm < required {target_torque} Nm")
        elif t > target_torque * 3:
            torque_score = 80.0
            warnings.append("Gearbox significantly oversized")

    power_score = 100.0
    if target_kw and kw:
        power_score = max(0.0, 100 - abs(kw - target_kw) / max(target_kw, 0.01) * 200)

    total = ratio_score * 0.5 + torque_score * 0.3 + power_score * 0.2
    return total, round(100 - ratio_diff_pct, 1), warnings


def match_spec_to_database(spec: NameplateSpec, database: dict, top_n: int = 5) -> list[MatchResult]:
    """
    Rank SAS models against the competitor nameplate spec.

    CORE PRINCIPLE (v5 — fixed):
    ============================================================
    SAS makes gear boxes with dimensions IDENTICAL to SEW (and most
    European brands) for R-series R17-R167, S-series S37-S97, K-series K37-K97.
    Therefore:
      - SEW R57  →  SAS R57  (direct size mapping)
      - SEW R37  →  SAS R37
      - SEW R107 →  SAS R107

    The ratio/torque from the database is used ONLY to:
      - Confirm the exact model exists (e.g. R57 D100M4 at ratio 5.82)
      - Pick the motor frame (D90L4 vs D100M4 vs D112M4)

    Without a database, we can still compose a valid SAS model from the
    competitor gear size + dimensions reference table.
    ============================================================
    """
    if not database and not spec.gear_size_code and not spec.full_model_code:
        return []

    target_kw = spec.power_kw
    target_ratio = spec.ratio
    target_torque = spec.torque_nm
    target_type = (spec.gear_type_hint or "").upper()[:1]

    # Extract competitor gear size (e.g. "R57" from "R57 DRE100M4/AND8")
    competitor_gear_size = (
        spec.gear_size_code
        or extract_gear_size(spec.full_model_code)
    )

    # Normalize to base (R57, K57, S57 — drop F/A/M variants for matching)
    competitor_gear_base = None
    if competitor_gear_size:
        m = _GEAR_SIZE_RE.match(competitor_gear_size)
        if m:
            base_letter = m.group(1).upper()[0]
            competitor_gear_base = f"{base_letter}{m.group(2)}"

    candidates: list[MatchResult] = []

    def _accept_model_size(model_str: str) -> bool:
        """Strict: candidate's gear size must match competitor's."""
        if not competitor_gear_base:
            return True
        cand_size = extract_gear_size(model_str)
        if not cand_size:
            return False   # ไม่รู้ขนาด = ไม่รับ
        m = _GEAR_SIZE_RE.match(cand_size)
        if not m:
            return False
        cand_base = f"{m.group(1).upper()[0]}{m.group(2)}"
        return cand_base == competitor_gear_base

    # ========== Source 1: selection_tables (model + kW + ratio complete) ==========
    if database and target_ratio:
        for st in database.get("selection_tables", []):
            for table in st.get("tables", []):
                kw = table.get("power_kw")
                if target_kw and kw:
                    diff = abs(kw - target_kw)
                    if diff > 0.5 and (target_kw > 0 and diff / target_kw > 0.3):
                        continue

                for row in table.get("rows", []):
                    r = row.get("ratio")
                    t = row.get("output_torque_nm")
                    model = row.get("full_model") or row.get("model_code")
                    if not r or not model:
                        continue
                    if not _accept_model_size(model):
                        continue

                    ratio_diff_pct = abs(r - target_ratio) / target_ratio * 100
                    if ratio_diff_pct > 25:
                        continue

                    total, ratio_pct, warnings = _score_candidate(
                        r, t, kw, target_ratio, target_torque, target_kw)

                    cand_size = extract_gear_size(model)
                    dims = get_dimensions(cand_size) if cand_size else None

                    candidates.append(MatchResult(
                        sas_model=model,
                        power_kw=kw,
                        ratio=r,
                        output_speed_rpm=row.get("output_speed_rpm"),
                        output_torque_nm=t,
                        variants=row.get("variants", []),
                        service_factor=row.get("service_factor"),
                        ratio_match_pct=ratio_pct,
                        total_score=round(total, 1),
                        warnings=warnings,
                        dimensions=dims,
                        exact_size_match=True,   # already filtered
                    ))

    # ========== Source 2: ratio_torque_tables ==========
    if not candidates and database and target_ratio:
        for rt in database.get("ratio_torque_tables", []):
            for table in rt.get("tables", []):
                gear_size = table.get("gear_size") or ""
                if not gear_size:
                    continue
                if not _accept_model_size(gear_size):
                    continue

                for row in table.get("rows", []):
                    r = row.get("ratio")
                    t = row.get("max_torque_nm")
                    out_rpm = row.get("output_speed_rpm")
                    if not r:
                        continue

                    ratio_diff_pct = abs(r - target_ratio) / target_ratio * 100
                    if ratio_diff_pct > 25:
                        continue

                    if target_torque and t and t < target_torque * 0.9:
                        continue

                    total, ratio_pct, warnings = _score_candidate(
                        r, t, None, target_ratio, target_torque, None)

                    candidates.append(MatchResult(
                        sas_model=gear_size,
                        power_kw=None,
                        ratio=r,
                        output_speed_rpm=out_rpm,
                        output_torque_nm=t,
                        variants=[],
                        service_factor=None,
                        ratio_match_pct=ratio_pct,
                        total_score=round(total, 1),
                        warnings=warnings,
                        dimensions=get_dimensions(gear_size),
                        exact_size_match=True,
                    ))

    # ========== Source 3: DIRECT MAPPING (no database hit — v5 core fix) ==========
    # If we know the competitor gear size (e.g. R57) and SAS makes R57 with
    # identical dimensions, we can synthesize a match without needing the
    # exact row in the database.
    if not candidates and competitor_gear_base:
        dims = get_dimensions(competitor_gear_base)
        if dims:
            # Compose synthesized match
            synth = MatchResult(
                sas_model=competitor_gear_base,
                power_kw=target_kw,
                ratio=target_ratio,
                output_speed_rpm=spec.output_rpm,
                output_torque_nm=target_torque,
                variants=["R", "RF"],
                service_factor=spec.service_factor,
                ratio_match_pct=100.0,
                total_score=95.0,   # high confidence: exact size match
                warnings=["🎯 SAS " + competitor_gear_base + " uses IDENTICAL dimensions as SEW " + competitor_gear_base + " — drop-in replacement"],
                dimensions=dims,
                exact_size_match=True,
            )
            candidates.append(synth)

    # Deduplicate by (model, ratio)
    seen: dict[tuple, MatchResult] = {}
    for c in sorted(candidates, key=lambda x: (-int(x.exact_size_match), -x.total_score)):
        key = (c.sas_model, round(c.ratio or 0, 2))
        if key not in seen:
            seen[key] = c
    return list(seen.values())[:top_n]


def match_spec_to_database_loose(spec: NameplateSpec, database: dict, top_n: int = 5) -> list[MatchResult]:
    """
    Legacy loose match (no gear size enforcement). Used by tests only.
    In v5, the main function never falls back to loose matching — it synthesizes
    a result from the dimension reference table instead.
    """
    if not spec.ratio or not database:
        return []

    target_ratio = spec.ratio
    target_torque = spec.torque_nm
    target_type = (spec.gear_type_hint or "").upper()[:1]

    candidates: list[MatchResult] = []

    for rt in database.get("ratio_torque_tables", []):
        for table in rt.get("tables", []):
            gear_size = table.get("gear_size") or ""
            if not gear_size:
                continue
            if target_type and not gear_size.upper().startswith(target_type):
                continue

            for row in table.get("rows", []):
                r = row.get("ratio")
                t = row.get("max_torque_nm")
                if not r:
                    continue
                ratio_diff_pct = abs(r - target_ratio) / target_ratio * 100
                if ratio_diff_pct > 20:
                    continue
                if target_torque and t and t < target_torque * 0.9:
                    continue

                total, ratio_pct, warnings = _score_candidate(
                    r, t, None, target_ratio, target_torque, None)
                total *= 0.85

                candidates.append(MatchResult(
                    sas_model=gear_size,
                    power_kw=None, ratio=r,
                    output_speed_rpm=row.get("output_speed_rpm"),
                    output_torque_nm=t, variants=[], service_factor=None,
                    ratio_match_pct=ratio_pct,
                    total_score=round(total, 1),
                    warnings=warnings,
                    dimensions=get_dimensions(gear_size),
                ))

    seen = {}
    for c in sorted(candidates, key=lambda x: -x.total_score):
        key = (c.sas_model, round(c.ratio or 0, 2))
        if key not in seen:
            seen[key] = c
    return list(seen.values())[:top_n]


# ============================================================
# Low-confidence archival
# ============================================================

def archive_low_confidence(image_bytes: bytes, mime: str, spec: NameplateSpec,
                           matches: list[MatchResult], from_brand: str, to_brand: str):
    """Save image + result JSON to R2 for human review later."""
    try:
        day = datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d")
        uid = uuid.uuid4().hex[:12]
        ext = ".jpg" if "jpeg" in mime else f".{mime.split('/')[-1]}"
        img_key = f"{REVIEWS_PREFIX}{day}/{uid}{ext}"
        json_key = f"{REVIEWS_PREFIX}{day}/{uid}.json"

        _upload_bytes(img_key, image_bytes, mime)
        payload = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "from_brand": from_brand,
            "to_brand": to_brand,
            "spec": spec.to_dict(),
            "spec_raw": spec.raw,
            "top_matches": [m.to_dict() for m in matches],
            "top_score": matches[0].total_score if matches else None,
        }
        _upload_bytes(json_key, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                      "application/json")
    except Exception as e:
        print(f"[matcher] archive_low_confidence failed: {e}", flush=True)


# ============================================================
# Rate limiting
# ============================================================

def _check_rate_limit(ip: str) -> bool:
    now = time.time()
    with _ip_lock:
        hits = [t for t in _ip_scan_log.get(ip, []) if t > now - MATCHER_RATE_WINDOW]
        if len(hits) >= MATCHER_RATE_MAX:
            _ip_scan_log[ip] = hits
            return False
        hits.append(now)
        _ip_scan_log[ip] = hits
        return True


def _client_ip() -> str:
    return (request.headers.get("X-Forwarded-For", "") or "").split(",")[0].strip() \
           or request.remote_addr or "unknown"


# ============================================================
# Blueprint — Flask routes
# ============================================================

matcher_bp = Blueprint("matcher", __name__, template_folder="../templates/matcher")


@matcher_bp.route("/")
def home():
    """Landing page with START button."""
    return render_template("matcher/start.html")


@matcher_bp.route("/app")
def scanner():
    """Main scanner screen."""
    return render_template("matcher/scanner.html")


@matcher_bp.route("/api/brands")
def api_brands():
    """Return list of brands discovered from R2."""
    try:
        brands = get_brand_list()
        return jsonify({"ok": True, "brands": brands})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@matcher_bp.route("/api/brands/refresh", methods=["POST"])
def api_brands_refresh():
    """Force refresh brand list from R2."""
    try:
        brands = get_brand_list(force_refresh=True)
        return jsonify({"ok": True, "brands": brands})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@matcher_bp.route("/api/compose", methods=["POST"])
def api_compose():
    """
    Recompose SAS model when Sale changes dropdown options (terminal box / cable outlet / IM / motor code).
    No image upload — just takes current spec + new choices.
    
    JSON body:
      {
        "gear": "R107",
        "motor": "YEJ",           (optional)
        "power_kw": 5.5,
        "poles": 4,
        "ratio": 115.63,
        "mounting": "M6",
        "terminal_box": "270",    (optional, default 270)
        "cable_outlet": "X"       (optional, default X)
      }
    """
    data = request.get_json(silent=True) or {}

    gear = (data.get("gear") or "").strip()
    if not gear or not re.fullmatch(r"[A-Za-z0-9]{1,10}", gear):
        return jsonify({"ok": False, "error": "Invalid gear code"}), 400

    motor = data.get("motor")
    if motor and not re.fullmatch(r"[A-Z]{1,4}[0-9]?", str(motor)):
        return jsonify({"ok": False, "error": "Invalid motor code"}), 400

    # Build minimal spec-like object
    class _S:
        pass
    s = _S()
    s.full_model_code = None
    s.raw = {}
    s.power_kw = data.get("power_kw")
    s.poles = data.get("poles")
    s.ratio = data.get("ratio")
    s.mounting_position = data.get("mounting")

    tb = str(data.get("terminal_box") or "270")
    co = str(data.get("cable_outlet") or "X")

    composed = compose_sas_model(s, gear, terminal_box=tb, cable_outlet=co)

    # If motor override provided, replace it
    if motor:
        composed["parts"]["motor"] = motor
        parts = composed["parts"]
        composed["full_code"] = (
            f"{parts['gear']}-{parts['motor']}-{parts['power_kw']}-{parts['poles']}-"
            f"{parts['ratio']}-{parts['mounting']}-{parts['terminal_box']}-{parts['cable_outlet']}"
        )

    return jsonify({"ok": True, "composed": composed})


@matcher_bp.route("/api/scan", methods=["POST"])
def api_scan():
    """
    Main endpoint: upload nameplate → get matches.
    
    Form data:
        image:       file upload (JPEG/PNG/WEBP)
        from_brand:  brand id of nameplate (e.g. "sew")
        to_brand:    brand id to match against (e.g. "sas")
    """
    ip = _client_ip()
    if not _check_rate_limit(ip):
        return jsonify({"ok": False, "error": "Rate limit exceeded. Try again later."}), 429

    # Validate file
    if "image" not in request.files:
        return jsonify({"ok": False, "error": "No image uploaded"}), 400
    f = request.files["image"]
    if not f.filename:
        return jsonify({"ok": False, "error": "Empty filename"}), 400

    filename = secure_filename(f.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXT:
        return jsonify({"ok": False, "error": f"Unsupported extension: {ext}"}), 400

    # Read and size-check
    image_bytes = f.read()
    if len(image_bytes) > MAX_IMAGE_SIZE:
        return jsonify({"ok": False, "error": "Image too large (max 10MB)"}), 400
    if len(image_bytes) < 1000:
        return jsonify({"ok": False, "error": "Image too small (corrupted?)"}), 400

    # Detect MIME
    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
        ".heic": "image/heic",
    }
    mime = mime_map.get(ext, "image/jpeg")

    # Get brand selections
    from_brand = (request.form.get("from_brand") or "").lower().strip()
    to_brand = (request.form.get("to_brand") or "sas").lower().strip()
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", from_brand or "x"):
        return jsonify({"ok": False, "error": "Invalid from_brand"}), 400
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", to_brand):
        return jsonify({"ok": False, "error": "Invalid to_brand"}), 400

    # Step 1: Read nameplate with Claude
    t0 = time.time()
    spec = read_nameplate(image_bytes, mime)
    t_read = time.time() - t0

    if spec.raw.get("_error") or spec.raw.get("_is_nameplate") is False:
        return jsonify({
            "ok": False,
            "error": "Could not read nameplate",
            "detail": spec.raw.get("_error"),
        }), 422

    # Step 2: Load target database
    db = get_database(to_brand)
    if not db:
        return jsonify({
            "ok": False,
            "error": f"No database available for brand '{to_brand}'. Upload catalog to R2 first.",
            "spec": spec.to_dict(),
        }), 404

    # Step 3: Match
    t1 = time.time()
    matches = match_spec_to_database(spec, db, top_n=5)
    t_match = time.time() - t1

    top_score = matches[0].total_score if matches else 0.0

    # Step 3.5: Compose SAS model code from top match (ถ้ามี และ target เป็น SAS)
    composed = None
    if to_brand == "sas" and matches:
        top_gear = matches[0].sas_model
        # ดึงเฉพาะส่วน gear code (เช่น "R107" จาก "R107 D132S4")
        gear_code = top_gear.split()[0] if top_gear else ""
        if gear_code:
            composed = compose_sas_model(spec, gear_code)

    # Step 3.6: Decode competitor model features (SEW suffix codes etc.)
    # Sale ต้องรู้ features ที่ลูกค้าใช้งานเพื่อเสนอ SAS ให้ครบ
    decoded_competitor = None
    if spec.full_model_code and from_brand == "sew":
        try:
            from utils.sew_decoder import decode_sew_model, format_features_for_display
            decoded = decode_sew_model(spec.full_model_code)
            decoded_competitor = {
                "brand": "sew",
                "decoded": decoded,
                "features_display": format_features_for_display(decoded),
            }
        except Exception as e:
            print(f"[matcher] SEW decoder failed: {e}", flush=True)

    # Step 3.7: Dimension comparison (competitor gear size vs top SAS match)
    # For R-series, critical dimensions must match or customer can't install SAS as drop-in
    dimension_compare = None
    competitor_gear = spec.gear_size_code or extract_gear_size(spec.full_model_code)
    if competitor_gear and matches:
        top_sas_gear = extract_gear_size(matches[0].sas_model)
        if top_sas_gear:
            dimension_compare = compare_dimensions(competitor_gear, top_sas_gear)
            dimension_compare["competitor_size"] = competitor_gear
            dimension_compare["sas_size"] = top_sas_gear

    # Step 4: Archive if low confidence
    archived = False
    if top_score < LOW_CONFIDENCE_THRESHOLD:
        threading.Thread(
            target=archive_low_confidence,
            args=(image_bytes, mime, spec, matches, from_brand, to_brand),
            daemon=True,
        ).start()
        archived = True

    return jsonify({
        "ok": True,
        "spec": spec.to_dict(),
        "spec_raw": spec.raw,
        "matches": [m.to_dict() for m in matches],
        "composed_sas_model": composed,
        "decoded_competitor": decoded_competitor,
        "dimension_compare": dimension_compare,
        "from_brand": from_brand,
        "to_brand": to_brand,
        "timing": {"read_ms": int(t_read * 1000), "match_ms": int(t_match * 1000)},
        "archived_for_review": archived,
        "top_score": top_score,
    })
