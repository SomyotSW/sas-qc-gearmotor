# Oriental Motor — World K Series — Naming Convention

## Source
Catalog: `WORLD-K.pdf` (Oriental Motor AUDIN distributor edition, Série World K, May 2008)
Page 9 — "Référence produit"

---

## MOTOR CODE (7–9 segments)

```
 5  I  K  40    GN   -CW   2    T4F   E
 ①  ②  ③  ④     ⑤     ⑥    ⑦    ⑧    ⑨
```

| # | Field | Values | Required |
|---|---|---|---|
| ① | **Frame size** | `0`=42mm, `2`=60mm, `3`=70mm, `4`=80mm, `5`=90mm | ✅ |
| ② | **Motor type** | `I`=Induction, `R`=Reversible, `T`=Torque | ✅ |
| ③ | **Series** | `K` (fixed) | ✅ |
| ④ | **Power (W)** | 1, 3, 6, 15, 25, 40, 60, 90, 150 | ✅ |
| ⑤ | **Shaft type** | `GN`, `GE`, `A` (round) | ✅ |
| ⑥ | **Voltage** | `AW`, `DW`, `BW`, `CW`, `SW`, `TW`, `U` | ✅ (prefixed `-`) |
| ⑦ | **RoHS indicator** | `2` or `3` | optional |
| ⑧ | **Option / certification** | `T`, `T4`, `T4F` | optional |
| ⑨ | **Capacitor suffix** | `E`, `U`, `J` | optional — **ACCEPT but IGNORE** (not on nameplate) |

### Voltage codes (field ⑥)
| Code | Phase | Voltage | Poles |
|---|---|---|---|
| `AW` | 1-ph | 100V | 4P |
| `BW` | 1-ph | 100V / 110/115V | 2P |
| `CW` | 1-ph | 200/220/230V | 4P |
| `DW` | 1-ph | 200/220/230V | 2P |
| `SW` | 3-ph | 200/220/230V | 4P |
| `TW` | 3-ph | 200/220/230V | 2P |
| `U`  | 3-ph | 400V | 4P |

### Option codes (field ⑧)
| Code | Meaning |
|---|---|
| `T` | Terminal box |
| `T4` | TÜV-certified grade |
| `T4F` | TÜV-certified + fan (implied) |

---

## GEAR HEAD CODE (4–5 segments)

```
 5  GN   [10X]   50   S
 ①  ②     ③     ④    ⑤
```

| # | Field | Values | Required |
|---|---|---|---|
| ① | **Frame size** | same as motor (`0`/`2`/`3`/`4`/`5`) | ✅ |
| ② | **Pinion type** | `GN`, `GE` — MUST match motor's shaft type | ✅ |
| ③ | **Middle stage** | `10X` (1:10 extension) | optional |
| ④ | **Ratio** | 3, 5, 7.5, 9, 12.5, 15, 18, 25, 30, 36, 50, 60, 90, 100, 120, 150, 180, 200 | ✅ |
| ⑤ | **Mounting / type** | `S`, `K`, `RH`, `RA` | ✅ |

### Mounting codes (field ⑤)
| Code | Meaning | Equivalent |
|---|---|---|
| `S` | GN-S long-life / low-noise | new, RoHS |
| `K` | GN-K ball bearing | same as SAS `K` |
| `RH` | Right-angle hollow shaft | — |
| `RA` | Right-angle solid shaft | — |

For GE pinion: `GE-S`, `GE-K`, `RH`, `RA`

---

## VERIFIED EXAMPLES

| Code | Meaning |
|---|---|
| `5IK40GN-CW2` | 90mm Induction K 40W GN, 1-ph 220V 4P, RoHS |
| `4IK25GN-CW2` | 80mm Induction K 25W GN, 1-ph 220V 4P, RoHS |
| `5IK40A-AW2` | 90mm Induction K 40W round-shaft, 1-ph 100V 4P, RoHS |
| `4IK25GN-UT4` | 80mm Induction K 25W GN, 3-ph 400V 4P, TÜV-cert |
| `5IK60GE-UT4F` | 90mm Induction K 60W GE, 3-ph 400V 4P, TÜV + fan |
| `4GN18S` | gear head 80mm GN ratio 1:18 long-life |
| `5GN50K` | gear head 90mm GN ratio 1:50 ball bearing |
| `5GN10X100S` | gear head 90mm GN middle 1:10 × 1:100 = 1:1000 long-life |

---

## PARSING REGEX

```python
import re

ORIENTAL_MOTOR_RE = re.compile(
    r"^([02-5])"                           # ① frame
    r"([IRT])"                             # ② type
    r"(K)"                                 # ③ series
    r"(\d{1,3})"                           # ④ power
    r"(GN|GE|A)"                           # ⑤ shaft
    r"-"
    r"(AW|DW|BW|CW|SW|TW|U)"               # ⑥ voltage
    r"(2|3)?"                              # ⑦ RoHS
    r"(T4F|T4|T)?"                         # ⑧ option
    r"(E|U|J)?"                            # ⑨ capacitor (ignored)
    r"$"
)

ORIENTAL_GEAR_RE = re.compile(
    r"^([02-5])"
    r"(GN|GE)"
    r"(10X)?"
    r"(\d{1,3})"
    r"(S|K|RH|RA)"
    r"$"
)
```
