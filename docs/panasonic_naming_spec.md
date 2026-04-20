# Panasonic — Compact AC Geared Motor — Naming Convention

## Source
Catalog: `Compact-AC-Geared-Motor.pdf` (2016/4 edition, 293 pages, Panasonic Japan)
Pages B-2 & B-3 — "Outline of induction motor" + "Coding system"

---

## MOTOR CODE (up to 11 segments)

```
 M   9  1   X    40   G   [K]   4    L   G   A
 ①   ②  ③   ④    ⑤    ⑥   ⑦    ⑧   ⑨   ⑩   ⑪
```

| # | Field | Values | Required |
|---|---|---|---|
| ① | **Maker** | `M` = Panasonic (fixed) | ✅ |
| ② | **Size** | `4`=42mm, `6`=60mm, `7`=70mm, `8`=80mm, `9`=90mm | ✅ |
| ③ | **Motor type** | `1` = Induction (single-phase), `R` = Reversible, `M` = Induction (three-phase) | ✅ |
| ④ | **Variant letter** | `A` = 3W, `X` = 40W or smaller, `Z` = 60W or larger | ✅ |
| ⑤ | **Output (W)** | 1, 3, 6, 10, 15, 25, 40, 60, 90 | ✅ |
| ⑥ | **Shape of shaft** | `G`=Pinion, `S`=Round | ✅ |
| ⑦ | **Option** | `K` = Sealed connector (left-aligned when omitted) | optional |
| ⑧ | **Poles** | `4` = 4P, `2` = 2P (42mm only) | ✅ |
| ⑨ | **Voltage** | `L`=100V, `Y`=200V, `D`=110/115V, `G`=220/230V | ✅ |
| ⑩ | **Classification 1** | `G` = overseas standards, `S` = round shaft w/ national specs | optional |
| ⑪ | **Classification 2** | `A`, `B` = no capacitor cap (not sold in Japan), `C` = compliant w/ cap | optional |

### Variant rules (coherence with power)
- `A` = 3W only
- `X` = 40W or smaller (6W, 10W, 15W, 25W, 40W)
- `Z` = 60W or larger (60W, 90W)

### Voltage detail
| Code | Voltage |
|---|---|
| `L` | Single-phase 100V |
| `Y` | Single-phase 200V |
| `D` | Single-phase 110V / 115V |
| `G` | Single-phase 220V / 230V |

### Capacitor cap rule
| ⑪ | Capacitor cap | Availability |
|---|---|---|
| (blank) | attached | Japan + overseas |
| `A` | **not** equipped | not sold in Japan |
| `B` | **not** equipped | not sold in Japan |
| `C` | attached | overseas |

---

## GEAR HEAD CODE

```
 M   X   9   G   [10X]   180   B
 ①   ②   ③   ④     ⑤     ⑥    ⑦
```

Two forms:
- **Standard**: `M X {size} G {ratio} {bearing}` e.g. `MX9G180B`
- **Decimal (1:10 middle)**: `M X {size} G 10X {bearing}` e.g. `MX9G10XB`

| # | Field | Values | Required |
|---|---|---|---|
| ① | **Maker** | `M` | ✅ |
| ② | **X** | fixed letter (indicates gear head) | ✅ |
| ③ | **Size** | `6`/`7`/`8`/`9` (matches motor) | ✅ |
| ④ | **G** | fixed (gear head marker) | ✅ |
| ⑤ | **Decimal** | `10X` (1:10 middle stage) | optional |
| ⑥ | **Ratio** | 3, 3.6, 5, 6, 7.5, 9, 10, 12.5, 15, 18, 20, 25, 30, 36, 50, 60, 75, 90, 100, 120, 150, 180, 200 | required (unless decimal) |
| ⑦ | **Bearing / variant** | `B`, `BA`, `M`, `MA`, `BU` (USA), `F` (hinge) | ✅ |

### Bearing/variant codes
| Code | Meaning |
|---|---|
| `B` | Ball bearing (standard, 1/25 or smaller ratio → uses `BA`) |
| `BA` | Ball bearing (1/30 or larger ratio) |
| `M` | Metal bearing (standard) |
| `MA` | Metal bearing (high ratio variant) |
| `BU` | Ball bearing U.S.A. version |
| `F` | Hinge-attached type (e.g. `M4G6F`) |

Note from catalog: "The model number of the gear head with a reduction ratio of 1/25 or smaller is `MX6G□BA (MA)`"
→ `BA`/`MA` is used for **higher ratios (1/30 and above)**; `B`/`M` for lower (1/3 to 1/25).

---

## VERIFIED EXAMPLES

### Motors
| Code | Parsed |
|---|---|
| `M41A3G2L` | 42mm, Induction-1ph, A=3W, pinion, 2P, 100V |
| `M61A3G4L` | 60mm, Induction-1ph, A=3W, pinion, 4P, 100V |
| `M61X6G4L` | 60mm, Induction-1ph, X-variant, 6W, pinion, 4P, 100V |
| `M61X10G4L` | 60mm, Induction-1ph, X-variant, 10W |
| `M61X6G4Y` | 60mm, 6W, pinion, 4P, 200V |
| `M71X10G4L` | 70mm, 10W, pinion, 4P, 100V |
| `M81X25G4L` | 80mm, 25W |
| `M91X40G4L` | 90mm, X-variant, 40W |
| `M91Z60G4L` | 90mm, Z-variant, 60W |
| `M9RZ90G4L` | 90mm, **Reversible**, Z, 90W |
| `M9MZ90G4L` | 90mm, **Induction-3ph**, Z, 90W |
| `M91Z90GK4LGA` | 90mm, 90W, pinion, **K**=sealed, 4P, 100V, overseas, no-cap |
| `M91Z90G4GGB` | 90mm, 90W, pinion, 4P, 220V, overseas, no-cap |

### Gear heads
| Code | Parsed |
|---|---|
| `MX6G3BA` | 60mm, ratio 1:3, ball bearing-A |
| `MX6G180B` | 60mm, ratio 1:180, ball bearing |
| `MX7G10XB` | 70mm, decimal 1:10 middle, ball bearing |
| `MX8G25M` | 80mm, ratio 1:25, metal bearing |
| `MX9G7.5B` | 90mm, ratio 1:7.5, ball bearing |
| `MX9G12.5B` | 90mm, ratio 1:12.5, ball bearing |
| `MX7G50BU` | 70mm, ratio 1:50, ball bearing USA version |

---

## PARSING REGEX

```python
import re

PAN_MOTOR_RE = re.compile(
    r"^M"
    r"([4-9])"                      # ② size
    r"(1|R|M)"                      # ③ motor type: 1=Ind-1ph, R=Reversible, M=Ind-3ph
    r"([AXZ])"                      # ④ variant: A=3W, X=≤40W, Z=60W+
    r"(\d{1,2})"                    # ⑤ output W
    r"([GS])"                       # ⑥ shaft (G=pinion, S=round)
    r"(K)?"                         # ⑦ sealed connector (optional)
    r"(4|2)"                        # ⑧ poles
    r"([LYDG])"                     # ⑨ voltage
    r"([GS])?"                      # ⑩ classification 1 (optional)
    r"(?:\(?([AB])\)?)?"            # ⑪ classification 2 with optional parens
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
```

---

## FULL CODE (pair)
Panasonic sells motor and gear head **separately** in the catalog. A pair is written with space or plus:
- Motor: `M71X10G4L` + Gear head: `MX7G50B`
- Shown in sale documents as: `M71X10G4L + MX7G50B`
- No single combined code format (unlike SAS)
