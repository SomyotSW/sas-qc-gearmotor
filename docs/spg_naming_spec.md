# SPG Co., Ltd. — Standard AC Geared Motor — Naming Convention

## Source
Catalog: `Standard_AC__English_.pdf` (325 pages, SPG Korea)
Pages 6-7 — "CODING SYSTEM"

---

## MOTOR CODE (7-8 segments)

```
 S   9    I    40    G   B   H   -E
 ①   ②    ③    ④     ⑤   ⑥   ⑦   ⑧
```

| # | Field | Values | Required |
|---|---|---|---|
| ① | **Maker** | `S` = SPG Co., Ltd. (fixed) | ✅ |
| ② | **Size** | `6`=60mm, `7`=70mm, `8`=80mm, `9`=90mm | ✅ |
| ③ | **Motor type** | `I`=Induction, `R`=Reversible | ✅ |
| ④ | **Output (W)** | `03`, `06`, `15`, `25`, `40`, `60`, `90`, `120`, `150`, `180`, `200` — **always 2-digit** | ✅ |
| ⑤ | **Shaft type** | `G`=Gear type (has pinion), `S`=Straight, `D`=D-cut, `K`=Key | ✅ |
| ⑥ | **Voltage** | see table below | ✅ |
| ⑦ | **Impact** | `H`=Heavy Impact, `L`=Light Impact | conditional (see rule) |
| ⑧ | **Special** | `E`, `T`, `T1`, `T2`, `B`, `S`, `V`, `ES` (prefixed `-`) | optional |

### Voltage codes (field ⑥)
| Code | Phase | Voltage | Frequency | Poles |
|---|---|---|---|---|
| `A` | 1-ph | 110V | 60Hz | 4P |
| `B` | 1-ph | 220V | 60Hz | 4P |
| `C` | 1-ph | 100V | 50/60Hz | 4P |
| `D` | 1-ph | 200V | 50/60Hz | 4P |
| `E` | 1-ph | 115V | 60Hz | 4P |
| `X` | 1-ph | 220~240V | 50Hz | 4P |
| `U` | 3-ph | 200V | 50/60Hz | 4P |
| `T` | 3-ph | 220V | 50/60Hz | 4P |
| `S` | 3-ph | 380~440V | 50/60Hz | 4P |

### Impact (field ⑦) — conditional rule
```
H / L applied to > 40W only
H = standard for > 60W
L = standard for > 40W  (i.e. at 60W)
≤ 40W: no H/L suffix
```

Effectively:
- ≤ 40W: field ⑦ is **omitted**
- 60W: default `L` (Light) — but `H` can be ordered
- > 60W (90W+): default `H` (Heavy)

### Special type (field ⑧)
| Code | Meaning |
|---|---|
| `E` | Electromagnetic Brake |
| `T` | Terminal Box (block) |
| `T1` | Terminal Box (PCB block) — 25~90W |
| `T2` | Conduit Box — 25~90W |
| `B` | Semi-Brake |
| `S` | Variable Speed Control (Pack): `S12`, `S24` for 12V/24V T.G. |
| `V` | Variable Speed Control (Unit): `V12` |
| `ES` | Electromagnetic Brake + Variable Speed: `ES12`, `ES24` |

---

## GEAR HEAD CODE (6-8 segments)

```
 S   9   K   C   36   B   H   -S
 ①   ②   ③   ④   ⑤    ⑥   ⑦   ⑧
```

| # | Field | Values | Required |
|---|---|---|---|
| ① | **Maker** | `S` = SPG | ✅ |
| ② | **Size** | `6`/`7`/`8`/`9` | ✅ (must match motor) |
| ③ | **Shaft type** | `S`=Straight, `D`=D-cut (default), `K`=Key | ✅ |
| ④ | **Output class** | `T`/`A`/`B`/`C`/`D`/`H` | ✅ |
| ⑤ | **Gear ratio** | integer (3-250) | ✅ |
| ⑥ | **Bearing** | `B`, `B1`, `M` | ✅ |
| ⑦ | **Impact** | `H`, `L` | conditional (same rule as motor) |
| ⑧ | **Special** | `S` = Flange Type | optional |

### Output class (field ④) — maps to motor power
| Code | Motor power range | Notes |
|---|---|---|
| `T` | 3W | |
| `A` | 6W ~ 25W | |
| `B` | 40W | |
| `C` | 60W ~ 120W | Output shaft Ø15 |
| `D` | 60W ~ 120W | Output shaft Ø18 (larger shaft variant) |
| `H` | 150W ~ 200W | |

### Bearing (field ⑥)
| Code | Meaning | Applies to |
|---|---|---|
| `B` | Ball bearing + Metal (6-40W) / All Ball (60W+) | standard |
| `B1` | All Ball bearing | 6W~40W |
| `M` | Metal bearing | 6W~40W |

### Middle Gear (decimal): `S6GX10B`
```
S 6 GX 10 B
① ②  ③  ④ ⑤
```
- `GX` = middle/decimal gear marker
- `10` = ratio 1:10 extension
- When paired with main gear: total ratio = 1:10 × main ratio

---

## VERIFIED EXAMPLES

| Code | Parsed |
|---|---|
| `S6I03GA` | SPG 60mm Induction 3W Gear-shaft Voltage-A |
| `S6I06GACE` | SPG 60mm Induction 6W Gear-shaft Voltage-A, CE certified (impedance protected) |
| `S9I60GBH` | SPG 90mm Induction 60W Gear-shaft Voltage-B Heavy-impact |
| `S9I40GCH` | SPG 90mm Induction 40W Gear-shaft Voltage-C Heavy-impact |
| `S6DT3B` | gear head 60mm D-cut 3W-class ratio 1:3 Ball+Metal |
| `S6DA10B` | gear head 60mm D-cut 6-25W-class ratio 1:10 Ball+Metal |
| `S9KC36BH` | gear head 90mm Key 60-120W-class (Ø15) ratio 1:36 Ball Heavy |
| `S9KD36BH` | gear head 90mm Key 60-120W-class (Ø18) ratio 1:36 Ball Heavy |
| `S9KC36BH-S` | same as above with Flange |
| `S6GX10B` | middle decimal gear 60mm 1:10 Ball |

---

## PARSING REGEX

```python
import re

SPG_MOTOR_RE = re.compile(
    r"^S"                                  # ① maker
    r"([6-9])"                             # ② size
    r"([IR])"                              # ③ type
    r"(\d{2,3})"                           # ④ power (2-3 digit)
    r"([GSDK])"                            # ⑤ shaft
    r"([A-EXUTS])"                         # ⑥ voltage
    r"([HL])?"                             # ⑦ impact (optional)
    r"(?:-(E|T1|T2|T|B|S12|S24|S|V12|ES12|ES24|ES))?"  # ⑧ special
    r"(CE)?"                               # certification suffix (ignore)
    r"$"
)

SPG_GEAR_RE = re.compile(
    r"^S"                                  # ① maker
    r"([6-9])"                             # ② size
    r"([SDK])"                             # ③ shaft (S/D/K, NOT G)
    r"([TABCDH])"                          # ④ output class
    r"(\d{1,3})"                           # ⑤ ratio
    r"(B1|B|M)"                            # ⑥ bearing
    r"([HL])?"                             # ⑦ impact (optional)
    r"(?:-S)?"                             # ⑧ flange (optional)
    r"$"
)

SPG_MIDDLE_RE = re.compile(
    r"^S([6-9])GX(\d+)(B1|B|M)$"
)
```

---

## FULL CODE FORMAT (motor + gear head)

SPG sells motor and gear head together with separator `-`:
```
S9I60GBH-S9KC50BH
<motor> - <gearhead>
```

Examples from catalog:
- `S9I60GBH-S9KC50BH` (motor 90/60W + gear 1:50)
- `S6I25GCH-S6DA18B` (motor 60/25W + gear 1:18)
- `S9I90GBH-S9KD100BH` (motor 90/90W + gear 1:100, Ø18 shaft)
