"""
utils/sew_decoder.py
====================
Decodes SEW-EURODRIVE full model codes (motor type designation) into
human-readable features list.

Based on: SEW DR. motor series catalog, Section 3.2 (Variants and options)

Example input:  "R107 DV132S4/BMG/HR/EV1A/TF"
Example output:
{
  "gear_unit": "R107",
  "motor_base": "DV132S4",
  "motor_efficiency": "DV (legacy series)",
  "frame_size": "132",
  "frame_length": "S",
  "poles": 4,
  "suffixes": [
    {"code": "BMG", "label": "Spring-loaded brake", "category": "brake"},
    {"code": "HR",  "label": "Manual brake release, automatic disengaging", "category": "brake_release"},
    {"code": "EV1A","label": "Encoder sin/cos (V-type)", "category": "encoder"},
    {"code": "TF",  "label": "PTC thermistor (temperature sensor)", "category": "thermal"}
  ],
  "summary": {
    "has_brake": true,
    "has_encoder": true,
    "has_thermal_protection": true,
    "is_ex_proof": false,
    "has_forced_cooling": false
  }
}
"""

from __future__ import annotations

import re
from typing import Optional

# ============================================================
# SEW suffix code registry (organized by section in catalog)
# ============================================================

# Sorted so that LONGER codes are matched first (e.g. "EV7S" before "V")
# This is critical: if we match "V" before "EV7S", we'd mis-decode.
_SEW_SUFFIX_CODES = {
    # ---------- 3.2.3 Mechanical attachments ----------
    "BMGZ": ("Spring-loaded brake with Z-attachment",      "brake"),
    "BMG":  ("Spring-loaded brake",                         "brake"),
    "BM":   ("Spring-loaded brake (older series)",          "brake"),
    "BE":   ("Brake (specification follows)",               "brake"),
    "HR":   ("Manual brake release, automatic disengaging", "brake_release"),
    "HF":   ("Manual brake release, lockable",              "brake_release"),
    "DUB":  ("Brake monitoring unit",                        "monitoring"),

    # ---------- 3.2.2 Output options (mounting) ----------
    "FI":   ("IEC foot-mounted motor",                       "mounting"),
    "FF":   ("IEC flange-mounted with bore holes",           "mounting"),
    "FT":   ("IEC flange-mounted with threads",              "mounting"),
    "FE":   ("IEC flange-mounted with bore + IEC feet",      "mounting"),
    "FY":   ("IEC flange-mounted with threads + IEC feet",   "mounting"),
    "FL":   ("General flange-mounted (non-IEC)",             "mounting"),
    "FK":   ("General flange-mounted (non-IEC) with feet",   "mounting"),
    "FG":   ("SEW attached gearmotor as stand-alone",        "mounting"),
    "FM":   ("SEW attached gearmotor with IEC feet",         "mounting"),
    "FC":   ("C-face flange (inch dimensions)",              "mounting"),

    # ---------- 3.2.3 Other mechanical ----------
    "MSW":  ("MOVI-SWITCH (motor + switch integrated)",      "mechatronics"),
    "MM03": ("MOVIMOT integrated inverter 0.3 kW",           "mechatronics"),
    "MM04": ("MOVIMOT integrated inverter 0.4 kW",           "mechatronics"),
    "MM05": ("MOVIMOT integrated inverter 0.5 kW",           "mechatronics"),
    "MM07": ("MOVIMOT integrated inverter 0.75 kW",          "mechatronics"),
    "MM11": ("MOVIMOT integrated inverter 1.1 kW",           "mechatronics"),
    "MM15": ("MOVIMOT integrated inverter 1.5 kW",           "mechatronics"),
    "MM22": ("MOVIMOT integrated inverter 2.2 kW",           "mechatronics"),
    "MM30": ("MOVIMOT integrated inverter 3.0 kW",           "mechatronics"),
    "MM40": ("MOVIMOT integrated inverter 4.0 kW",           "mechatronics"),
    "MM":   ("MOVIMOT integrated inverter",                  "mechatronics"),
    "MI":   ("MOVIMOT identification module",                "mechatronics"),
    "MO":   ("MOVIMOT options",                              "mechatronics"),
    "RS":   ("Backstop (anti-reverse)",                      "mechanical"),

    # ---------- 3.2.4 Temperature sensors ----------
    "TF":   ("PTC thermistor (temperature sensor)",          "thermal"),
    "TH":   ("Thermostat bimetallic switch",                 "thermal"),
    "KY":   ("KTY84-130 sensor",                             "thermal"),
    "PT":   ("PT100 sensor(s)",                              "thermal"),

    # ---------- 3.2.5 Encoders (longer codes first) ----------
    # Sin/cos encoders
    "ES7S": ("Encoder sin/cos, shaft-centered",              "encoder"),
    "EG7S": ("Encoder sin/cos, gear-side",                   "encoder"),
    "EH7S": ("Encoder sin/cos, hollow-shaft",                "encoder"),
    "EV7S": ("Encoder sin/cos, V-type",                      "encoder"),
    # TTL/RS-422
    "ES7R": ("Encoder TTL/RS-422, shaft-centered",           "encoder"),
    "EG7R": ("Encoder TTL/RS-422, gear-side",                "encoder"),
    "EH7R": ("Encoder TTL/RS-422, hollow-shaft",             "encoder"),
    # HTL
    "EI7C": ("Encoder HTL interface",                        "encoder"),
    "EI76": ("Encoder HTL, 6-period",                        "encoder"),
    "EI72": ("Encoder HTL, 2-period",                        "encoder"),
    "EI71": ("Encoder HTL, 1-period",                        "encoder"),
    # Absolute multi-turn
    "AS7W": ("Absolute encoder RS-485, multi-turn",          "encoder"),
    "AG7W": ("Absolute encoder RS-485, gear-side",           "encoder"),
    "AS7Y": ("Absolute encoder SSI, multi-turn",             "encoder"),
    "AG7Y": ("Absolute encoder SSI, gear-side",              "encoder"),
    "AH7Y": ("Absolute encoder SSI, hollow-shaft",           "encoder"),
    # Adapters
    "ES7A": ("Adapter for SEW encoders (shaft)",             "encoder"),
    "EG7A": ("Adapter for SEW encoders (gear)",              "encoder"),
    "XV.A": ("Adapter for non-SEW encoders",                 "encoder"),
    "XV":   ("Non-SEW speed sensor",                         "encoder"),

    # Also: older legacy encoders that may appear on older nameplates
    "EV1A": ("Encoder (legacy V-type)",                      "encoder"),
    "EV1":  ("Encoder (legacy V-type, older)",               "encoder"),

    # ---------- 3.2.6 Connection options ----------
    "IS":   ("Integrated plug connector",                    "connection"),
    "ASE":  ("HAN 10ES plug, single latch",                  "connection"),
    "ASB":  ("HAN 10ES plug, double latch",                  "connection"),
    "ACE":  ("HAN 10E crimp plug, single latch",             "connection"),
    "ACB":  ("HAN 10E crimp plug, double latch",             "connection"),
    "AME":  ("HAN Modular 10B, single latch",                "connection"),
    "ABE":  ("HAN Modular 10B, single latch (variant)",      "connection"),
    "ADE":  ("HAN Modular 10B, single latch (variant)",      "connection"),
    "AKE":  ("HAN Modular 10B, single latch (variant)",      "connection"),
    "AMB":  ("HAN Modular 10B, double latch",                "connection"),
    "ABB":  ("HAN Modular 10B, double latch (variant)",      "connection"),
    "ADB":  ("HAN Modular 10B, double latch (variant)",      "connection"),
    "AKB":  ("HAN Modular 10B, double latch (variant)",      "connection"),
    "KCC":  ("6/10-pole terminal strip (cage clamp)",        "connection"),
    "KC1":  ("C1 profile connection (monorail system)",      "connection"),
    "IV":   ("Industrial plug (custom spec)",                "connection"),

    # ---------- 3.2.7 Ventilation (careful: "V" alone is broad) ----------
    "VH":   ("Radial fan on guard",                          "cooling"),
    "VE":   ("Forced cooling (Ex-proof Cat 3)",              "cooling"),
    "AL":   ("Metal fan",                                    "cooling"),
    "OL":   ("Non-ventilated (closed B-side)",               "cooling"),
    "LF":   ("Air filter",                                   "cooling"),
    "LN":   ("Low-noise fan guard",                          "cooling"),
    # Single-letter cooling codes — matched only when they stand alone
    # (These are handled specially in _decode_suffix to avoid false hits)
    "_V":   ("Forced cooling fan",                           "cooling"),
    "_Z":   ("Additional inertia (flywheel fan)",            "cooling"),
    "_U":   ("Non-ventilated (no fan)",                      "cooling"),
    "_C":   ("Canopy for fan guard",                         "cooling"),

    # ---------- 3.2.8 Storage ----------
    "NS":   ("Relubrication device",                         "bearing"),
    "ERF":  ("Reinforced bearing A-side",                    "bearing"),
    "NIB":  ("Insulated bearing B-side",                     "bearing"),

    # ---------- 3.2.10 Explosion-proof ----------
    "2GD":  ("Ex-proof Cat 2 gas+dust (ATEX)",               "atex"),
    "2G":   ("Ex-proof Cat 2 gas (ATEX)",                    "atex"),
    "3GD":  ("Ex-proof Cat 3 gas+dust (ATEX)",               "atex"),
    "3D":   ("Ex-proof Cat 3 dust (ATEX)",                   "atex"),

    # ---------- 3.2.11 Other ----------
    "DH":   ("Condensation drain hole",                      "other"),
    "RI2":  ("Reinforced winding + partial discharge res.",  "other"),
    "RI":   ("Reinforced winding insulation",                "other"),
    "2W":   ("Second shaft end (dual shaft motor)",          "other"),
}

# Build a sorted list for longest-first matching
_SORTED_SUFFIXES = sorted(
    [(k, v) for k, v in _SEW_SUFFIX_CODES.items() if not k.startswith("_")],
    key=lambda x: (-len(x[0]), x[0])
)

# Single-letter cooling codes (matched as whole token only)
_SINGLE_LETTER_SUFFIXES = {
    "V": _SEW_SUFFIX_CODES["_V"],
    "Z": _SEW_SUFFIX_CODES["_Z"],
    "U": _SEW_SUFFIX_CODES["_U"],
    "C": _SEW_SUFFIX_CODES["_C"],
}


# ============================================================
# Motor series prefix (DRS, DRE, DRP, DRN, DV, DT, etc.)
# ============================================================

_MOTOR_SERIES_PREFIX = [
    # Longer first
    ("DRJ",  "Line-start permanent magnet motor (LSPM)",    "IE2–IE4"),
    ("DRU",  "Super premium efficiency motor",              "IE4"),
    ("DRP",  "Premium efficiency motor",                    "IE3"),
    ("DRN",  "Modular series motor (typically IE3)",        "IE3"),
    ("DRE",  "High efficiency motor",                       "IE2"),
    ("DRS",  "Standard efficiency motor",                   "IE1"),
    ("DRL",  "Asynchronous servomotor (4-pole)",            ""),
    ("DRK",  "Single-phase motor with capacitor",           ""),
    ("DRM",  "12-pole torque motor",                        ""),
    ("EDR",  "Explosion-proof motor",                       ""),
    ("DR",   "DR-series motor (generic)",                   ""),
    # Legacy
    ("DV",   "Legacy DV-series motor",                      ""),
    ("DT",   "Legacy DT-series motor",                      ""),
    ("SDT",  "Legacy SDT-series motor (smooth start)",      ""),
    ("SDV",  "Legacy SDV-series motor (smooth start)",      ""),
]


# ============================================================
# Gear unit prefix (R, F, K, S, W, etc.)
# ============================================================

_GEAR_UNIT_TYPES = {
    "R":   "Helical gear unit (in-line)",
    "RF":  "Helical gear unit, flange-mounted",
    "RM":  "Helical gear unit, agitator design",
    "RX":  "Helical gear unit, single-stage",
    "F":   "Parallel-shaft helical gear unit",
    "FA":  "Parallel-shaft gear unit, hollow shaft",
    "FH":  "Parallel-shaft gear unit, hollow shaft + shrink disc",
    "FAF": "Parallel-shaft gear unit, hollow shaft + flange",
    "K":   "Helical-bevel gear unit (right-angle)",
    "KA":  "Helical-bevel gear unit, hollow shaft",
    "KH":  "Helical-bevel gear unit, hollow shaft + shrink disc",
    "KAF": "Helical-bevel gear unit, hollow shaft + flange",
    "S":   "Helical-worm gear unit (right-angle)",
    "SA":  "Helical-worm gear unit, hollow shaft",
    "SH":  "Helical-worm gear unit, hollow shaft + shrink disc",
    "SF":  "Helical-worm gear unit, flange",
    "SAF": "Helical-worm gear unit, hollow shaft + flange",
    "W":   "SPIROPLAN right-angle gear unit",
    "WA":  "SPIROPLAN, hollow shaft",
    "WF":  "SPIROPLAN, flange",
}


# ============================================================
# Main decoder
# ============================================================

def decode_sew_model(full_code: str) -> dict:
    """
    Decode a SEW model code into structured components.

    Input examples:
      "R107 DV132S4/BMG/HR/EV1A"
      "R37 DRE80M4/BMG/HF/TF"
      "KAF87 DRN112M4/BE5/HR"

    Returns a dict with:
      - gear_unit:    dict (code, label, size)
      - motor:        dict (code, series, efficiency, frame, length, poles)
      - suffixes:     list of decoded option codes
      - unknown:      list of codes that couldn't be decoded
      - summary:      flags summarizing motor features
      - original:     the raw input
    """
    if not full_code:
        return {"original": "", "error": "empty input"}

    # Normalize: strip spaces, keep original case for regex
    raw = full_code.strip()
    normalized = raw.upper()

    # 1) Extract gear unit + motor (before first "/")
    first_slash = normalized.find("/")
    head = normalized[:first_slash] if first_slash >= 0 else normalized
    tail = normalized[first_slash+1:] if first_slash >= 0 else ""

    # head looks like: "R107 DV132S4" or "R107DV132S4"
    # Split gear from motor by finding the motor series prefix
    gear_info = _parse_gear_unit(head)
    motor_info = _parse_motor(head, gear_info.get("consumed_length", 0))

    # 2) Parse suffixes from the tail
    suffix_tokens = [t.strip() for t in tail.split("/") if t.strip()]
    decoded_suffixes = []
    unknown_codes = []
    for token in suffix_tokens:
        result = _decode_suffix(token)
        if result:
            decoded_suffixes.append(result)
        else:
            unknown_codes.append(token)

    # 3) Build summary flags
    categories = {s["category"] for s in decoded_suffixes}
    summary = {
        "has_brake":              "brake" in categories,
        "has_brake_release":      "brake_release" in categories,
        "has_encoder":            "encoder" in categories,
        "has_thermal_protection": "thermal" in categories,
        "has_forced_cooling":     any(s["code"] in ("V", "VH", "VE") for s in decoded_suffixes),
        "is_ex_proof":            "atex" in categories,
        "has_mechatronics":       "mechatronics" in categories,
        "has_plug_connector":     "connection" in categories,
        "has_special_bearing":    "bearing" in categories,
        "has_backstop":           any(s["code"] == "RS" for s in decoded_suffixes),
        "has_dual_shaft":         any(s["code"] == "2W" for s in decoded_suffixes),
    }

    # Determine highest-priority brake info (if any)
    brake_code = None
    for s in decoded_suffixes:
        if s["category"] == "brake":
            brake_code = s["code"]
            break

    return {
        "original":    raw,
        "gear_unit":   gear_info,
        "motor":       motor_info,
        "suffixes":    decoded_suffixes,
        "unknown":     unknown_codes,
        "summary":     summary,
        "brake_code":  brake_code,
    }


def _parse_gear_unit(head: str) -> dict:
    """Extract gear type + size from beginning of head string."""
    if not head:
        return {"code": None, "size": None, "label": None, "consumed_length": 0}

    # Try matching gear type (longest first, up to 3 chars)
    upper = head.upper()
    gear_prefixes = sorted(_GEAR_UNIT_TYPES.keys(), key=len, reverse=True)
    for gt in gear_prefixes:
        if upper.startswith(gt):
            rest = head[len(gt):]
            # Extract size digits following the gear letter(s)
            m = re.match(r"^(\d{1,4})", rest)
            size = m.group(1) if m else None
            full_gear = f"{gt}{size}" if size else gt
            consumed = len(gt) + (len(size) if size else 0)
            return {
                "code": full_gear,
                "type": gt,
                "size": size,
                "label": _GEAR_UNIT_TYPES.get(gt, ""),
                "consumed_length": consumed,
            }

    # No known gear prefix
    return {"code": None, "size": None, "label": None, "consumed_length": 0}


def _parse_motor(head: str, start: int) -> dict:
    """
    Extract motor series, frame size, length, poles from head starting at `start`.
    Example: from "R107 DV132S4" starting after "R107" → "DV132S4"
    """
    motor_str = head[start:].lstrip(" -")
    if not motor_str:
        return {"code": None}

    # Find motor series prefix (longest match)
    upper = motor_str.upper()
    series = None
    series_label = None
    efficiency = None
    after_series = motor_str

    for prefix, label, eff in _MOTOR_SERIES_PREFIX:
        if upper.startswith(prefix):
            series = prefix
            series_label = label
            efficiency = eff
            after_series = motor_str[len(prefix):]
            break

    # Frame size (digits)
    frame_match = re.match(r"^(\d{2,3})", after_series)
    frame_size = frame_match.group(1) if frame_match else None
    rest = after_series[len(frame_size):] if frame_size else after_series

    # Length letter (K, S, M, L, MC, LC, LA, LB, etc.)
    length_match = re.match(r"^([KSMLA-Z]{1,2})", rest)
    length = length_match.group(1) if length_match else None
    if length:
        rest = rest[len(length):]
    # Strip: length should not be "KEY" — only allow known length codes
    if length and length not in ("K", "S", "M", "L", "MC", "LC", "LA", "LB", "LE", "MA", "MB", "SA", "SB"):
        # Not a real length code; treat as poles or unknown
        rest = (length or "") + rest
        length = None

    # Poles — trailing digit(s). Can be "4", "2/4", "8/2", "8/4"
    poles_match = re.match(r"^(\d+(?:/\d+)?)", rest)
    poles_str = poles_match.group(1) if poles_match else None
    poles = None
    if poles_str:
        # Primary poles = first number
        try:
            poles = int(poles_str.split("/")[0])
        except (ValueError, IndexError):
            pass

    return {
        "code":       motor_str.rstrip(),
        "series":     series,
        "series_label": series_label,
        "efficiency": efficiency,
        "frame_size": frame_size,
        "length":     length,
        "poles":      poles,
        "poles_raw":  poles_str,
    }


def _decode_suffix(token: str) -> Optional[dict]:
    """
    Decode one suffix token. Token may contain trailing numbers (e.g. "BE5", "MM11").
    Strategy:
      1. Try exact match
      2. Try longest prefix match from registry
      3. Try single-letter cooling codes
    """
    token = token.strip().upper()
    if not token:
        return None

    # Exact match
    if token in _SEW_SUFFIX_CODES:
        label, category = _SEW_SUFFIX_CODES[token]
        return {"code": token, "label": label, "category": category}

    # Prefix match (longest first) — e.g. "BE5" matches "BE"
    for key, (label, category) in _SORTED_SUFFIXES:
        if token.startswith(key):
            # Ensure the remainder is digits or empty (to avoid matching "BETA" with "BE")
            remainder = token[len(key):]
            if remainder == "" or remainder.isdigit():
                full_label = f"{label}" + (f" (size/variant {remainder})" if remainder else "")
                return {"code": token, "label": full_label, "category": category}

    # Single-letter cooling codes
    if len(token) == 1 and token in _SINGLE_LETTER_SUFFIXES:
        label, category = _SINGLE_LETTER_SUFFIXES[token]
        return {"code": token, "label": label, "category": category}

    return None


# ============================================================
# Utility: human-friendly summary for UI display
# ============================================================

def format_features_for_display(decoded: dict) -> list[dict]:
    """
    Format decoded result as a list of feature cards for UI display.

    Returns list like:
    [
      {"icon": "🛑", "title": "Brake",  "detail": "BMG — Spring-loaded brake"},
      {"icon": "🔧", "title": "Brake release", "detail": "HR — Manual release, auto-disengaging"},
      {"icon": "📡", "title": "Encoder","detail": "EV1A — Legacy V-type sin/cos"},
      ...
    ]
    """
    category_icons = {
        "brake":          "🛑",
        "brake_release":  "🔧",
        "monitoring":     "📊",
        "encoder":        "📡",
        "thermal":        "🌡️",
        "cooling":        "💨",
        "connection":     "🔌",
        "mounting":       "📐",
        "mechatronics":   "⚡",
        "atex":           "⚠️",
        "bearing":        "⚙️",
        "mechanical":     "🔩",
        "other":          "📝",
    }
    category_labels = {
        "brake":          "Brake",
        "brake_release":  "Brake release",
        "monitoring":     "Monitoring",
        "encoder":        "Encoder",
        "thermal":        "Thermal protection",
        "cooling":        "Cooling / ventilation",
        "connection":     "Connection",
        "mounting":       "Mounting",
        "mechatronics":   "Mechatronics",
        "atex":           "Explosion-proof",
        "bearing":        "Bearing",
        "mechanical":     "Mechanical",
        "other":          "Other",
    }

    # Group suffixes by category
    features = []
    for s in decoded.get("suffixes", []):
        features.append({
            "icon":     category_icons.get(s["category"], "•"),
            "title":    category_labels.get(s["category"], s["category"]),
            "code":     s["code"],
            "detail":   s["label"],
            "category": s["category"],
        })

    return features
