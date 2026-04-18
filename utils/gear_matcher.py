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
  "full_model_code": "full model code shown",
  "gear_type_hint": "R|S|K|F|W (single letter — first char of gear type)",
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
  "_confidence": { "<field_name>": "high|medium|low" }
}

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
    """Extract spec from nameplate image."""
    raw = _call_claude_vision(image_bytes, mime_type, NAMEPLATE_PROMPT)

    if raw.get("_error") or raw.get("_is_nameplate") is False:
        spec = NameplateSpec(raw=raw)
        return spec

    # Coerce number fields
    def _num(v):
        if v is None or v == "":
            return None
        try:
            return float(v) if "." in str(v) or isinstance(v, float) else int(v)
        except (ValueError, TypeError):
            try:
                return float(str(v).replace(",", ""))
            except (ValueError, TypeError):
                return None

    spec = NameplateSpec(
        brand=raw.get("brand"),
        full_model_code=raw.get("full_model_code"),
        gear_type_hint=(raw.get("gear_type_hint") or "").upper()[:1] or None,
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
    return spec


# ============================================================
# Matching engine
# ============================================================

def match_spec_to_database(spec: NameplateSpec, database: dict, top_n: int = 5) -> list[MatchResult]:
    """
    Rank models in `database` against the input `spec`.
    Returns top_n MatchResult objects sorted by score desc.
    """
    if not spec.ratio or not database:
        return []

    target_kw = spec.power_kw
    target_ratio = spec.ratio
    target_torque = spec.torque_nm
    target_type = (spec.gear_type_hint or "").upper()[:1]

    candidates: list[MatchResult] = []
    for st in database.get("selection_tables", []):
        for table in st.get("tables", []):
            kw = table.get("power_kw")
            # Power filter (skip if too far)
            if target_kw and kw:
                diff = abs(kw - target_kw)
                if diff > 0.3 and (target_kw > 0 and diff / target_kw > 0.25):
                    continue

            for row in table.get("rows", []):
                r = row.get("ratio")
                t = row.get("output_torque_nm")
                model = row.get("full_model") or row.get("model_code")
                if not r or not model:
                    continue

                # Gear type filter
                if target_type and not model.upper().startswith(target_type):
                    continue

                ratio_diff_pct = abs(r - target_ratio) / target_ratio * 100
                if ratio_diff_pct > 15:
                    continue

                ratio_score = max(0.0, 100 - ratio_diff_pct * 3)

                warnings: list[str] = []
                torque_score = 100.0
                if target_torque and t:
                    if t < target_torque:
                        torque_score = 40.0
                        warnings.append(
                            f"Gearbox rating {t} Nm < required {target_torque} Nm")
                    elif t > target_torque * 3:
                        torque_score = 80.0
                        warnings.append("Gearbox significantly oversized")

                power_score = 100.0
                if target_kw and kw:
                    power_score = max(0.0, 100 - abs(kw - target_kw) / max(target_kw, 0.01) * 200)

                total = ratio_score * 0.5 + torque_score * 0.3 + power_score * 0.2

                candidates.append(MatchResult(
                    sas_model=model,
                    power_kw=kw,
                    ratio=r,
                    output_speed_rpm=row.get("output_speed_rpm"),
                    output_torque_nm=t,
                    variants=row.get("variants", []),
                    service_factor=row.get("service_factor"),
                    ratio_match_pct=round(100 - ratio_diff_pct, 1),
                    total_score=round(total, 1),
                    warnings=warnings,
                ))

    # Deduplicate by (model, ratio) keeping highest score
    seen: dict[tuple, MatchResult] = {}
    for c in sorted(candidates, key=lambda x: -x.total_score):
        key = (c.sas_model, c.ratio)
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
        "from_brand": from_brand,
        "to_brand": to_brand,
        "timing": {"read_ms": int(t_read * 1000), "match_ms": int(t_match * 1000)},
        "archived_for_review": archived,
        "top_score": top_score,
    })
