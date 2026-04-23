"""
IK Series Decoder — Parse motor and gear head codes for 5 brands.

Brands:
  - SAS (baseline)         : 5IK60GU-CF / 5GU50KB
  - ZD (drop-in to SAS)    : same as SAS
  - Suntech (drop-in)      : same as SAS
  - Oriental Motor         : 5IK60GE-UT4F / 5GE50S
  - SPG                    : S9I60GBH / S9KC50BH
  - Panasonic              : M91Z60G4L / MX9G50B

Each decoder returns a normalized spec dict with these keys:
  brand           : str  ('sas' | 'oriental' | 'spg' | 'panasonic' | 'zd' | 'suntech')
  kind            : str  ('motor' | 'gearhead')
  frame_mm        : int  (42, 60, 70, 80, 90, 104)
  motor_type      : str  ('I' | 'R' | 'T' | 'M_3ph')   (motor only)
  power_w         : int  (motor only)
  pinion_type     : str  ('GN' | 'GU' | 'GE' | 'A' | 'A1' | 'K' | 'S' | 'D' | 'G')
  voltage_code    : str  (raw brand-specific, e.g. 'C', 'CW', 'B', 'L')
  voltage_spec    : dict {phase, voltage, frequency, poles}
  option          : list (raw brand-specific options, e.g. ['F','T'] or ['brake'])
  ratio           : float (gearhead only)
  has_middle_10x  : bool  (gearhead only)
  mount           : str   (gearhead only, raw brand-specific: 'K' | 'KB' | 'S' | 'RH' | 'RA' | 'B' | 'M' | 'BA' | 'MA')
  raw_code        : str
  warnings        : list[str]
  extras          : dict  (anything else brand-specific)
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field, asdict
from typing import Optional


# ============================================================
# Frame size mapping (physical mm → each brand's code)
# ============================================================
# Physical mm → SAS size, SPG size, Panasonic size
MM_TO_SAS_SIZE = {42: "0", 60: "2", 70: "3", 80: "4", 90: "5", 104: "6"}
SAS_SIZE_TO_MM = {v: k for k, v in MM_TO_SAS_SIZE.items()}

MM_TO_SPG_SIZE = {60: "6", 70: "7", 80: "8", 90: "9"}
SPG_SIZE_TO_MM = {v: k for k, v in MM_TO_SPG_SIZE.items()}

MM_TO_PAN_SIZE = {42: "4", 60: "6", 70: "7", 80: "8", 90: "9"}
PAN_SIZE_TO_MM = {v: k for k, v in MM_TO_PAN_SIZE.items()}

# Oriental uses SAS-style size: 0/2/3/4/5
MM_TO_ORIENTAL_SIZE = {42: "0", 60: "2", 70: "3", 80: "4", 90: "5"}
ORIENTAL_SIZE_TO_MM = {v: k for k, v in MM_TO_ORIENTAL_SIZE.items()}


# ============================================================
# Voltage mapping tables
# ============================================================
SAS_VOLTAGE = {
    "A":  {"phase": 1, "voltage": "110V",      "freq": "50/60Hz", "poles": 4},
    "B":  {"phase": 1, "voltage": "110V",      "freq": "50Hz",    "poles": 2},
    "C":  {"phase": 1, "voltage": "220V",      "freq": "50Hz",    "poles": 4},
    "D":  {"phase": 1, "voltage": "220V",      "freq": "50Hz",    "poles": 2},
    "E":  {"phase": 1, "voltage": "110/120V",  "freq": "60Hz",    "poles": 4},
    "H":  {"phase": 1, "voltage": "220/230V",  "freq": "60Hz",    "poles": 4},
    "S":  {"phase": 3, "voltage": "200/220/230V", "freq": "50/60Hz", "poles": 4},
    "S3": {"phase": 3, "voltage": "380/400/415V", "freq": "50/60Hz", "poles": 4},
    "T":  {"phase": 3, "voltage": "200/220/230V", "freq": "50/60Hz", "poles": 2},
    "T3": {"phase": 3, "voltage": "380/400/415V", "freq": "50/60Hz", "poles": 2},
}

ORIENTAL_VOLTAGE = {
    "AW": {"phase": 1, "voltage": "100V",          "freq": "50/60Hz", "poles": 4},
    "BW": {"phase": 1, "voltage": "100V/110/115V", "freq": "50/60Hz", "poles": 2},
    "CW": {"phase": 1, "voltage": "200/220/230V",  "freq": "50/60Hz", "poles": 4},
    "DW": {"phase": 1, "voltage": "200/220/230V",  "freq": "50/60Hz", "poles": 2},
    "SW": {"phase": 3, "voltage": "200/220/230V",  "freq": "50/60Hz", "poles": 4},
    "TW": {"phase": 3, "voltage": "200/220/230V",  "freq": "50/60Hz", "poles": 2},
    "U":  {"phase": 3, "voltage": "400V",          "freq": "50/60Hz", "poles": 4},
}

SPG_VOLTAGE = {
    "A": {"phase": 1, "voltage": "110V",          "freq": "60Hz",    "poles": 4},
    "B": {"phase": 1, "voltage": "220V",          "freq": "60Hz",    "poles": 4},
    "C": {"phase": 1, "voltage": "100V",          "freq": "50/60Hz", "poles": 4},
    "D": {"phase": 1, "voltage": "200V",          "freq": "50/60Hz", "poles": 4},
    "E": {"phase": 1, "voltage": "115V",          "freq": "60Hz",    "poles": 4},
    "X": {"phase": 1, "voltage": "220~240V",      "freq": "50Hz",    "poles": 4},
    "U": {"phase": 3, "voltage": "200V",          "freq": "50/60Hz", "poles": 4},
    "T": {"phase": 3, "voltage": "220V",          "freq": "50/60Hz", "poles": 4},
    "S": {"phase": 3, "voltage": "380~440V",      "freq": "50/60Hz", "poles": 4},
}

PAN_VOLTAGE = {
    "L": {"phase": 1, "voltage": "100V",       "freq": "50/60Hz", "poles": None},  # poles from field ⑧
    "Y": {"phase": 1, "voltage": "200V",       "freq": "50/60Hz", "poles": None},
    "D": {"phase": 1, "voltage": "110V/115V",  "freq": "50/60Hz", "poles": None},
    "G": {"phase": 1, "voltage": "220V/230V",  "freq": "50/60Hz", "poles": None},
}


# ============================================================
# Ratio list — shared across all brands (per user confirmation)
# ============================================================
STANDARD_RATIOS = [
    3, 3.6, 5, 6, 7.5, 9, 10, 12.5, 15, 18, 20, 25,
    30, 36, 50, 60, 75, 90, 100, 120, 150, 180, 200, 250
]


# ============================================================
# Result dataclass
# ============================================================
@dataclass
class IKSpec:
    brand: str = "unknown"
    kind: str = "unknown"                       # 'motor' | 'gearhead'
    frame_mm: Optional[int] = None
    motor_type: Optional[str] = None            # 'I' | 'R' | 'T' | 'M_3ph'
    power_w: Optional[int] = None
    pinion_type: Optional[str] = None
    voltage_code: Optional[str] = None
    voltage_spec: Optional[dict] = None
    option: list = field(default_factory=list)
    ratio: Optional[float] = None
    has_middle_10x: bool = False
    mount: Optional[str] = None
    raw_code: Optional[str] = None
    warnings: list = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


# ============================================================
# SAS / ZD / Suntech decoders (identical format)
# ============================================================
SAS_MOTOR_RE = re.compile(
    r"^([02-6])"                # ① frame
    r"([IRT])"                  # ② type
    r"(K)"                      # ③ series
    r"(\d{1,3})"                # ④ power
    r"(R)?"                     # ⑤ speed adj (optional)
    r"(GN|GU|A1|A)"             # ⑥ pinion
    r"-"
    r"(S3|T3|[A-HST])"          # ⑦ voltage
    r"(FF|[TFPM]+)?"            # ⑧ option
    r"$"
)

SAS_GEAR_RE = re.compile(
    r"^([02-6])"                # ① frame
    r"(GN|GU)"                  # ② pinion
    r"(10X)?"                   # ③ middle
    r"(\d{1,3}(?:\.\d)?)"       # ④ ratio
    r"(KB|K)"                   # ⑤ mount
    r"$"
)


def decode_sas_motor(code: str, brand: str = "sas") -> Optional[IKSpec]:
    """Decode SAS/ZD/Suntech motor code."""
    code = code.strip().upper()
    m = SAS_MOTOR_RE.match(code)
    if not m:
        return None
    frame_d, mtype, _series, power, r_suffix, pinion, voltage, option = m.groups()

    spec = IKSpec(
        brand=brand,
        kind="motor",
        frame_mm=SAS_SIZE_TO_MM.get(frame_d),
        motor_type=mtype,
        power_w=int(power),
        pinion_type=pinion,
        voltage_code=voltage,
        voltage_spec=SAS_VOLTAGE.get(voltage, {}),
        option=list(option) if option else [],
        raw_code=code,
    )
    if r_suffix:
        spec.extras["speed_adjustable"] = True
    return spec


def decode_sas_gear(code: str, brand: str = "sas") -> Optional[IKSpec]:
    code = code.strip().upper()
    m = SAS_GEAR_RE.match(code)
    if not m:
        return None
    frame_d, pinion, middle, ratio_str, mount = m.groups()
    return IKSpec(
        brand=brand,
        kind="gearhead",
        frame_mm=SAS_SIZE_TO_MM.get(frame_d),
        pinion_type=pinion,
        ratio=float(ratio_str),
        has_middle_10x=bool(middle),
        mount=mount,
        raw_code=code,
    )


# ============================================================
# Oriental Motor decoder
# ============================================================
ORIENTAL_MOTOR_RE = re.compile(
    r"^([02-5])"                        # ① frame
    r"([IRT])"                          # ② type
    r"(K)"                              # ③ series
    r"(\d{1,3})"                        # ④ power
    r"(R)?"                             # ⑤ speed-adjustable (optional, same as SAS)
    r"(GN|GE|A)"                        # ⑥ shaft
    r"-"
    r"(AW|DW|BW|CW|SW|TW|U)"            # ⑦ voltage
    r"(2|3)?"                           # ⑧ RoHS
    r"(T4F|T4|T)?"                      # ⑨ option
    r"(E|U|J)?"                         # ⑩ capacitor (ignored)
    r"$"
)

ORIENTAL_GEAR_RE = re.compile(
    r"^([02-5])"
    r"(GN|GE)"
    r"(10X)?"
    r"(\d{1,3}(?:\.\d)?)"
    r"(SA|KA|RAA|RH|RA|S|K)"   # SA/KA/RAA = RoHS variants; order: longer tokens first
    r"$"
)


def decode_oriental_motor(code: str) -> Optional[IKSpec]:
    code = code.strip().upper()
    m = ORIENTAL_MOTOR_RE.match(code)
    if not m:
        return None
    frame_d, mtype, _, power, r_suffix, shaft, voltage, rohs, option, capacitor = m.groups()
    # Option decoding:
    #   T   = Terminal box → SAS 'T'
    #   T4  = TÜV certification grade → IGNORE (not SAS-relevant)
    #   T4F = TÜV + fan → SAS 'F' (T4 part ignored)
    options = []
    if option == "T":
        options.append("T")
    elif option == "T4F":
        options.append("F")
    elif option == "T4":
        pass  # cert only, no hardware option
    spec = IKSpec(
        brand="oriental",
        kind="motor",
        frame_mm=ORIENTAL_SIZE_TO_MM.get(frame_d),
        motor_type=mtype,
        power_w=int(power),
        pinion_type=shaft,
        voltage_code=voltage,
        voltage_spec=ORIENTAL_VOLTAGE.get(voltage, {}),
        option=options,
        raw_code=code,
        extras={"rohs": rohs, "cert": option, "capacitor_suffix_ignored": capacitor},
    )
    if r_suffix:
        spec.extras["speed_adjustable"] = True
    return spec


def decode_oriental_gear(code: str) -> Optional[IKSpec]:
    code = code.strip().upper()
    m = ORIENTAL_GEAR_RE.match(code)
    if not m:
        return None
    frame_d, pinion, middle, ratio_str, mount = m.groups()
    return IKSpec(
        brand="oriental",
        kind="gearhead",
        frame_mm=ORIENTAL_SIZE_TO_MM.get(frame_d),
        pinion_type=pinion,
        ratio=float(ratio_str),
        has_middle_10x=bool(middle),
        mount=mount,
        raw_code=code,
    )


# ============================================================
# SPG decoder
# ============================================================
SPG_MOTOR_RE = re.compile(
    r"^S"
    r"([6-9])"                                # ② size
    r"([IR])"                                 # ③ motor type
    r"(\d{2,3})"                              # ④ power
    r"([GSDK])"                               # ⑤ shaft
    r"([A-EXUTS])"                            # ⑥ voltage
    r"([HL])?"                                # ⑦ impact
    r"(?:-(E|T1|T2|T|B|S12|S24|S|V12|ES12|ES24|ES))?"  # ⑧ special
    r"(CE)?"                                  # certification suffix (ignored)
    r"$"
)

SPG_GEAR_RE = re.compile(
    r"^S"
    r"([6-9])"                                # ② size
    r"([SDK])"                                # ③ shaft
    r"([TABCDH])"                             # ④ output class
    r"(\d{1,3}(?:\.\d)?)"                     # ⑤ ratio
    r"(B1|B|M)"                               # ⑥ bearing
    r"([HL])?"                                # ⑦ impact
    r"(?:-S)?"                                # ⑧ flange
    r"$"
)

SPG_MIDDLE_RE = re.compile(r"^S([6-9])GX(\d+)(B1|B|M)$")


def decode_spg_motor(code: str) -> Optional[IKSpec]:
    code = code.strip().upper()
    m = SPG_MOTOR_RE.match(code)
    if not m:
        return None
    size_d, mtype, power, shaft, voltage, impact, special, cert = m.groups()
    options = []
    if special:
        options.append(special)
    return IKSpec(
        brand="spg",
        kind="motor",
        frame_mm=SPG_SIZE_TO_MM.get(size_d),
        motor_type=mtype,  # 'I' or 'R'
        power_w=int(power),
        pinion_type=shaft,  # 'G' / 'S' / 'D' / 'K'
        voltage_code=voltage,
        voltage_spec=SPG_VOLTAGE.get(voltage, {}),
        option=options,
        raw_code=code,
        extras={"impact": impact, "cert_suffix_ignored": cert},
    )


def decode_spg_gear(code: str) -> Optional[IKSpec]:
    code = code.strip().upper()
    # Middle gear first
    mm = SPG_MIDDLE_RE.match(code)
    if mm:
        size_d, ratio_str, bearing = mm.groups()
        return IKSpec(
            brand="spg", kind="gearhead",
            frame_mm=SPG_SIZE_TO_MM.get(size_d),
            ratio=float(ratio_str),
            has_middle_10x=True,
            mount=bearing,
            raw_code=code,
            extras={"is_middle_gear": True},
        )
    m = SPG_GEAR_RE.match(code)
    if not m:
        return None
    size_d, shaft, output_class, ratio_str, bearing, impact = m.groups()
    return IKSpec(
        brand="spg",
        kind="gearhead",
        frame_mm=SPG_SIZE_TO_MM.get(size_d),
        pinion_type=shaft,
        ratio=float(ratio_str),
        mount=bearing,
        raw_code=code,
        extras={"output_class": output_class, "impact": impact},
    )


# ============================================================
# Panasonic decoder
# ============================================================
PAN_MOTOR_RE = re.compile(
    r"^M"
    r"([4-9])"                      # ② size
    r"(1|R|M)"                      # ③ motor type: 1=Ind-1ph, R=Rev, M=Ind-3ph
    r"([AXZ])"                      # ④ variant
    r"(\d{1,2})"                    # ⑤ output W
    r"([GS])"                       # ⑥ shaft (G=pinion, S=round)
    r"(K)?"                         # ⑦ sealed connector
    r"(4|2)"                        # ⑧ poles
    r"([LYDG])"                     # ⑨ voltage
    r"([GS])?"                      # ⑩ classification 1
    r"(?:\(?([AB])\)?)?"            # ⑪ classification 2
    r"$"
)

PAN_GEAR_RE = re.compile(
    r"^M"
    r"X"
    r"([4-9])"                      # size
    r"G"
    r"(?:"
    r"(10X)(BA|B|MA|M|BU|F)"        # decimal form
    r"|"
    r"(\d{1,3}(?:\.\d)?)(BA|B|MA|M|BU|F)"  # standard form
    r")"
    r"$"
)

PAN_TYPE_MAP = {
    "1": "I",       # Induction single-phase
    "R": "R",       # Reversible
    "M": "M_3ph",   # Induction three-phase
}


def decode_panasonic_motor(code: str) -> Optional[IKSpec]:
    code = code.strip().upper()
    m = PAN_MOTOR_RE.match(code)
    if not m:
        return None
    size_d, type_d, variant, power, shaft, k_sealed, poles, voltage, cls1, cls2 = m.groups()

    # Coherence warnings
    warnings = []
    power_w = int(power)
    if variant == "A" and power_w != 3:
        warnings.append(f"Variant A should mean 3W, but code says {power_w}W")
    if variant == "X" and power_w > 40:
        warnings.append(f"Variant X means ≤40W, but code says {power_w}W")
    if variant == "Z" and power_w < 60:
        warnings.append(f"Variant Z means ≥60W, but code says {power_w}W")

    volt_spec = dict(PAN_VOLTAGE.get(voltage, {}))
    if poles:
        volt_spec["poles"] = int(poles)

    options = []
    if k_sealed:
        options.append("sealed_connector")

    return IKSpec(
        brand="panasonic",
        kind="motor",
        frame_mm=PAN_SIZE_TO_MM.get(size_d),
        motor_type=PAN_TYPE_MAP.get(type_d),
        power_w=power_w,
        pinion_type=shaft,  # 'G' (pinion) or 'S' (round)
        voltage_code=voltage,
        voltage_spec=volt_spec,
        option=options,
        raw_code=code,
        warnings=warnings,
        extras={"variant": variant, "classification_1": cls1, "classification_2_no_cap": cls2},
    )


def decode_panasonic_gear(code: str) -> Optional[IKSpec]:
    code = code.strip().upper()
    m = PAN_GEAR_RE.match(code)
    if not m:
        return None
    size_d, decimal, dec_bearing, ratio_str, std_bearing = m.groups()
    bearing = dec_bearing or std_bearing
    warnings = []
    if bearing == "F":
        warnings.append("Hinge-attached variant (F) has no SAS equivalent — cannot translate")
    return IKSpec(
        brand="panasonic",
        kind="gearhead",
        frame_mm=PAN_SIZE_TO_MM.get(size_d),
        ratio=10.0 if decimal else float(ratio_str),
        has_middle_10x=bool(decimal),
        mount=bearing,
        raw_code=code,
        warnings=warnings,
    )


# ============================================================
# Top-level dispatcher
# ============================================================
BRAND_DECODERS = {
    "sas":       (decode_sas_motor, decode_sas_gear),
    "zd":        (lambda c: decode_sas_motor(c, brand="zd"),      lambda c: decode_sas_gear(c, brand="zd")),
    "suntech":   (lambda c: decode_sas_motor(c, brand="suntech"), lambda c: decode_sas_gear(c, brand="suntech")),
    "oriental":  (decode_oriental_motor, decode_oriental_gear),
    "spg":       (decode_spg_motor, decode_spg_gear),
    "panasonic": (decode_panasonic_motor, decode_panasonic_gear),
}


def decode_ik(brand: str, code: str, kind: str) -> Optional[IKSpec]:
    """
    Decode an IK-series code for a given brand.
    
    Args:
        brand : one of 'sas', 'zd', 'suntech', 'oriental', 'spg', 'panasonic'
        code  : the raw code string
        kind  : 'motor' or 'gearhead'
    """
    brand = brand.lower().strip()
    if brand not in BRAND_DECODERS:
        return None
    motor_fn, gear_fn = BRAND_DECODERS[brand]
    if kind == "motor":
        return motor_fn(code)
    elif kind == "gearhead":
        return gear_fn(code)
    return None


def decode_auto(brand: str, code: str) -> Optional[IKSpec]:
    """
    Try both motor and gear-head decoders for a brand. Returns first match.
    Useful when we don't know ahead of time which kind a code is.
    """
    spec = decode_ik(brand, code, "motor")
    if spec is None:
        spec = decode_ik(brand, code, "gearhead")
    return spec


# ============================================================
# Brand metadata (for UI / routing)
# ============================================================
IK_ECOSYSTEM_BRANDS = {"sas", "zd", "suntech", "oriental", "spg", "panasonic"}
IK_CODE_IDENTICAL = {"sas", "zd", "suntech"}  # drop-in to SAS (no translation)
IK_PARTIAL_MAP = {"oriental"}                  # similar structure, different vocab
IK_MANUAL_MAP = {"spg", "panasonic"}           # completely different structure


def is_ik_brand(brand: str) -> bool:
    return brand.lower().strip() in IK_ECOSYSTEM_BRANDS
