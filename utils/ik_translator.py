"""
IK Series Translator — Convert decoded IKSpec (any brand) into SAS IK equivalent.

Two main functions:
  translate_motor(spec)     → IKSpec with brand='sas' (motor)
  translate_gearhead(spec,  → IKSpec with brand='sas' (gearhead)
      motor_power_w)           motor power is needed to determine KB vs K mount

Then compose_sas_ik_code() assembles the full code string.
"""

from __future__ import annotations
from typing import Optional
from .ik_decoder import (
    IKSpec, MM_TO_SAS_SIZE, SAS_VOLTAGE, STANDARD_RATIOS,
    decode_sas_motor, decode_sas_gear,
)


# ============================================================
# Voltage translation tables (to SAS codes)
# ============================================================
# Oriental → SAS
ORIENTAL_TO_SAS_VOLTAGE = {
    "AW": "A",    # 100V 4P → closest: 110V 4P = A
    "BW": "B",    # 100V 2P → closest: 110V 2P = B
    "CW": "C",    # 200-230V 4P = SAS C
    "DW": "D",    # 200-230V 2P = SAS D
    "SW": "S",    # 3-ph 200V 4P = SAS S
    "TW": "T",    # 3-ph 200V 2P = SAS T
    "U":  "S3",   # 3-ph 400V 4P = SAS S3
}

# SPG → SAS
SPG_TO_SAS_VOLTAGE = {
    "A": "A",     # 1-ph 110V 4P
    "B": "C",     # 1-ph 220V 4P (SPG B at 60Hz, SAS C at 50Hz — closest)
    "C": "A",     # 1-ph 100V → no direct; closest 110V
    "D": "C",     # 1-ph 200V → closest 220V
    "E": "E",     # 1-ph 115V 4P
    "X": "C",     # 1-ph 220-240V 4P
    "U": "S",     # 3-ph 200V 4P = S
    "T": "S",     # 3-ph 220V 4P = S (same voltage band)
    "S": "S3",    # 3-ph 380-440V = S3
}

# Panasonic → SAS
# Panasonic voltage depends on phase (1-ph) and poles (2/4)
def panasonic_to_sas_voltage(pan_volt_code: str, poles: int) -> str:
    """Map Panasonic voltage code + poles to SAS voltage code."""
    if pan_volt_code == "L":   # 100V → closest 110V
        return "A" if poles == 4 else "B"
    if pan_volt_code == "Y":   # 200V
        return "C" if poles == 4 else "D"
    if pan_volt_code == "D":   # 110/115V
        return "E" if poles == 4 else "B"
    if pan_volt_code == "G":   # 220/230V
        return "C" if poles == 4 else "D"
    return "C"  # default


# ============================================================
# Pinion type translation
# ============================================================
# Oriental → SAS
ORIENTAL_PINION_TO_SAS = {
    "GN": "GN",
    "GE": "GU",   # Oriental GE (high-eff splined) ≈ SAS GU (high-eff splined)
    "A":  "A",    # round shaft
}


def spg_shaft_to_sas_pinion(spg_shaft: str, motor_power_w: int) -> str:
    """
    SPG shaft type → SAS pinion type, using motor power as determinant.
    
    Rules from user:
      - SPG 'G' (Gear type with pinion):
          ≤ 40W → GN
          60W   → GN (default; GU valid too)
          > 60W → GU
      - SPG 'S' (Straight)  → SAS 'A'
      - SPG 'D' (D-cut)     → SAS 'A'  (no direct equivalent)
      - SPG 'K' (Keyway)    → SAS 'A1'
    """
    if spg_shaft == "G":
        if motor_power_w <= 40:
            return "GN"
        elif motor_power_w == 60:
            return "GN"   # default; can override if customer wants GU
        else:
            return "GU"
    elif spg_shaft == "S":
        return "A"
    elif spg_shaft == "D":
        return "A"
    elif spg_shaft == "K":
        return "A1"
    return "GN"  # fallback


def panasonic_to_sas_pinion(pan_shaft: str, motor_power_w: int) -> str:
    """Panasonic shaft → SAS pinion (same logic as SPG, since both use 'G' indiscriminately)."""
    if pan_shaft == "G":
        if motor_power_w <= 40:
            return "GN"
        elif motor_power_w == 60:
            return "GN"
        else:
            return "GU"
    elif pan_shaft == "S":
        return "A"
    return "GN"


# ============================================================
# Mount translation (Gear head)
# ============================================================
def sas_gear_mount(pinion: str, motor_power_w: Optional[int]) -> str:
    """
    SAS gear head mount rule:
      - GU pinion + 60W~120W → KB (square case)
      - All other combinations → K
    """
    if pinion == "GU" and motor_power_w is not None and 60 <= motor_power_w <= 120:
        return "KB"
    return "K"


# ============================================================
# Option translation
# ============================================================
def translate_options(brand: str, options: list) -> list:
    """
    Normalize option codes across brands to SAS option letters.
    
    SAS options: T (terminal), F (fan), FF (forced fan), P (thermal), M (brake)
    """
    result = []
    for opt in options:
        opt_up = opt.upper() if isinstance(opt, str) else str(opt)

        if brand in ("sas", "zd", "suntech"):
            # already SAS
            result.append(opt_up)
            continue

        if brand == "oriental":
            if opt_up == "F":
                result.append("F")
            elif opt_up == "T":
                result.append("T")
            continue

        if brand == "spg":
            # SPG special types → SAS
            if opt_up == "E":
                result.append("M")    # E = Electromagnetic brake → SAS M
            elif opt_up in ("T", "T1", "T2"):
                result.append("T")    # terminal box variants → T
            # B (semi-brake), S12/S24 (speed controller), V12, ES — skipped
            continue

        if brand == "panasonic":
            if opt_up == "SEALED_CONNECTOR":
                pass  # SAS has no sealed connector option → skip
            continue

    # De-dup while preserving order
    seen = set()
    out = []
    for x in result:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ============================================================
# Ratio snapping
# ============================================================
def snap_ratio(ratio: float) -> tuple[float, bool]:
    """
    Snap ratio to closest value in STANDARD_RATIOS.
    Returns (snapped_ratio, was_modified).
    """
    if ratio is None:
        return None, False
    closest = min(STANDARD_RATIOS, key=lambda r: abs(r - ratio))
    modified = abs(closest - ratio) > 0.01
    return float(closest), modified


# ============================================================
# Main translation functions
# ============================================================
def translate_motor(spec: IKSpec) -> tuple[IKSpec, list[str]]:
    """
    Translate a motor IKSpec from any brand → SAS IK equivalent.
    Returns (sas_spec, warnings).
    """
    warnings = list(spec.warnings) if spec.warnings else []

    if spec.brand in ("sas", "zd", "suntech"):
        # Drop-in: just change brand label
        out = IKSpec(
            brand="sas", kind="motor",
            frame_mm=spec.frame_mm,
            motor_type=spec.motor_type,
            power_w=spec.power_w,
            pinion_type=spec.pinion_type,
            voltage_code=spec.voltage_code,
            voltage_spec=spec.voltage_spec,
            option=list(spec.option),
            raw_code=spec.raw_code,
            warnings=warnings,
            extras=dict(spec.extras),
        )
        return out, warnings

    if spec.brand == "oriental":
        sas_pinion = ORIENTAL_PINION_TO_SAS.get(spec.pinion_type, "GN")
        sas_voltage = ORIENTAL_TO_SAS_VOLTAGE.get(spec.voltage_code, "C")
        options = translate_options("oriental", spec.option)
        return IKSpec(
            brand="sas", kind="motor",
            frame_mm=spec.frame_mm,
            motor_type=spec.motor_type,
            power_w=spec.power_w,
            pinion_type=sas_pinion,
            voltage_code=sas_voltage,
            voltage_spec=SAS_VOLTAGE.get(sas_voltage, {}),
            option=options,
            raw_code=spec.raw_code,
            warnings=warnings,
            extras={"translated_from": "oriental", **spec.extras},
        ), warnings

    if spec.brand == "spg":
        sas_pinion = spg_shaft_to_sas_pinion(spec.pinion_type, spec.power_w)
        sas_voltage = SPG_TO_SAS_VOLTAGE.get(spec.voltage_code, "C")
        options = translate_options("spg", spec.option)
        if spec.power_w == 60 and spec.pinion_type == "G":
            warnings.append(
                "60W + SPG shaft 'G' is ambiguous — defaulting to GN. "
                "Confirm with customer if GU (high-eff) is required."
            )
        return IKSpec(
            brand="sas", kind="motor",
            frame_mm=spec.frame_mm,
            motor_type=spec.motor_type,
            power_w=spec.power_w,
            pinion_type=sas_pinion,
            voltage_code=sas_voltage,
            voltage_spec=SAS_VOLTAGE.get(sas_voltage, {}),
            option=options,
            raw_code=spec.raw_code,
            warnings=warnings,
            extras={"translated_from": "spg", **spec.extras},
        ), warnings

    if spec.brand == "panasonic":
        sas_pinion = panasonic_to_sas_pinion(spec.pinion_type, spec.power_w)
        poles = spec.voltage_spec.get("poles") or 4
        sas_voltage = panasonic_to_sas_voltage(spec.voltage_code, poles)
        options = translate_options("panasonic", spec.option)
        if spec.power_w == 60 and spec.pinion_type == "G":
            warnings.append(
                "60W + Panasonic shaft 'G' is ambiguous — defaulting to GN. "
                "Confirm with customer if GU (high-eff) is required."
            )
        # Map motor type
        mtype = spec.motor_type
        if mtype == "M_3ph":
            mtype = "I"  # SAS represents 3-ph induction still as 'I' (distinguished by voltage S/S3/T/T3)
            if sas_voltage not in ("S", "S3", "T", "T3"):
                warnings.append("Panasonic motor is 3-phase but voltage didn't map to 3-phase SAS code")
        return IKSpec(
            brand="sas", kind="motor",
            frame_mm=spec.frame_mm,
            motor_type=mtype,
            power_w=spec.power_w,
            pinion_type=sas_pinion,
            voltage_code=sas_voltage,
            voltage_spec=SAS_VOLTAGE.get(sas_voltage, {}),
            option=options,
            raw_code=spec.raw_code,
            warnings=warnings,
            extras={"translated_from": "panasonic", **spec.extras},
        ), warnings

    warnings.append(f"Unknown brand: {spec.brand}")
    return spec, warnings


def translate_gearhead(
    spec: IKSpec, motor_power_w: Optional[int], motor_pinion: Optional[str] = None
) -> tuple[IKSpec, list[str]]:
    """
    Translate a gearhead IKSpec → SAS IK equivalent.
    Needs motor context to determine SAS mount (K vs KB) and pinion compatibility.
    
    Args:
        spec: the decoded gearhead spec
        motor_power_w: power of paired motor (for KB rule)
        motor_pinion: SAS pinion type of paired motor (GN/GU/A/A1) — overrides gear pinion detection
    """
    warnings = list(spec.warnings) if spec.warnings else []

    # Reject hinge variant (Panasonic F)
    if spec.mount == "F":
        warnings.append("Hinge-attached gear head has no SAS equivalent")
        return spec, warnings

    # Determine SAS pinion for gear head
    sas_pinion = motor_pinion  # prefer paired motor's pinion
    if sas_pinion is None:
        if spec.brand in ("sas", "zd", "suntech"):
            sas_pinion = spec.pinion_type
        elif spec.brand == "oriental":
            sas_pinion = ORIENTAL_PINION_TO_SAS.get(spec.pinion_type, "GN")
        elif spec.brand == "spg":
            # SPG gear head shaft is S/D/K — can't determine GN/GU directly
            # Use motor power hint
            if motor_power_w and motor_power_w > 60:
                sas_pinion = "GU"
            else:
                sas_pinion = "GN"
        elif spec.brand == "panasonic":
            # Panasonic gear head doesn't encode GN/GU
            if motor_power_w and motor_power_w > 60:
                sas_pinion = "GU"
            else:
                sas_pinion = "GN"

    # Snap ratio
    snapped_ratio, ratio_modified = snap_ratio(spec.ratio)
    if ratio_modified:
        warnings.append(
            f"Ratio {spec.ratio} snapped to nearest SAS standard: {snapped_ratio}"
        )

    # Determine SAS mount
    # If we know motor power → apply KB rule
    # If not (gear scanned alone) → preserve mount from original if it's a SAS-compatible mount;
    # otherwise default to K
    if motor_power_w is not None:
        sas_mount = sas_gear_mount(sas_pinion, motor_power_w)
    else:
        # No motor context — use gear's existing mount if valid
        if spec.brand in ("sas", "zd", "suntech") and spec.mount in ("K", "KB"):
            sas_mount = spec.mount
        elif sas_pinion == "GU":
            # Without motor power, but GU pinion → most GU gears use KB
            sas_mount = "KB"
        else:
            sas_mount = "K"

    return IKSpec(
        brand="sas", kind="gearhead",
        frame_mm=spec.frame_mm,
        pinion_type=sas_pinion,
        ratio=snapped_ratio,
        has_middle_10x=spec.has_middle_10x,
        mount=sas_mount,
        raw_code=spec.raw_code,
        warnings=warnings,
        extras={"translated_from": spec.brand, **spec.extras},
    ), warnings


# ============================================================
# Composer — build SAS code strings
# ============================================================
def _sas_size_digit(frame_mm: int) -> str:
    return MM_TO_SAS_SIZE.get(frame_mm, "?")


def compose_sas_motor_code(spec: IKSpec) -> Optional[str]:
    """Build SAS motor code like '5IK60GU-CF'."""
    if spec.brand != "sas" or spec.kind != "motor":
        return None
    if not all([spec.frame_mm, spec.motor_type, spec.power_w, spec.pinion_type, spec.voltage_code]):
        return None
    size = _sas_size_digit(spec.frame_mm)
    if size == "?":
        return None
    power_str = str(int(spec.power_w))
    speed_r = "R" if spec.extras.get("speed_adjustable") else ""
    option_str = "".join(spec.option) if spec.option else ""
    return f"{size}{spec.motor_type}K{power_str}{speed_r}{spec.pinion_type}-{spec.voltage_code}{option_str}"


def compose_sas_gear_code(spec: IKSpec) -> Optional[str]:
    """Build SAS gear head code like '5GU50KB' or '5GU10X100K'."""
    if spec.brand != "sas" or spec.kind != "gearhead":
        return None
    if not all([spec.frame_mm, spec.pinion_type, spec.ratio is not None, spec.mount]):
        return None
    size = _sas_size_digit(spec.frame_mm)
    if size == "?":
        return None
    if spec.pinion_type not in ("GN", "GU"):
        return None
    ratio_str = (
        f"{int(spec.ratio)}" if spec.ratio == int(spec.ratio) else f"{spec.ratio}"
    )
    middle_str = "10X" if spec.has_middle_10x else ""
    return f"{size}{spec.pinion_type}{middle_str}{ratio_str}{spec.mount}"


def compose_sas_full_code(
    motor_spec: Optional[IKSpec], gear_spec: Optional[IKSpec]
) -> Optional[str]:
    """Build full SAS code like '5IK60GU-CF-5GU50KB'. Requires both parts."""
    if motor_spec is None or gear_spec is None:
        return None
    mc = compose_sas_motor_code(motor_spec)
    gc = compose_sas_gear_code(gear_spec)
    if mc is None or gc is None:
        return None
    return f"{mc}-{gc}"
