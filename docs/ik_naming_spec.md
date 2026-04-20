# SAS IK Series — Naming Convention (AC Motor only)

## Full Code Structure

```
{MOTOR_CODE}-{GEAR_HEAD_CODE}
```

**Rule:** Motor frame size และ pinion type **ต้องตรงกับ** gear head เสมอ
- Frame size ตำแหน่ง ① ของ motor = ตำแหน่ง ① ของ gear head
- Pinion type ตำแหน่ง ⑤/⑥ ของ motor = ตำแหน่ง ② ของ gear head
- **ห้าม mix size**

---

## MOTOR CODE (8 segments)

```
 5  I  K  120    [R]   GN   -C    F
 ①  ②  ③   ④     ⑤     ⑥   ⑦    ⑧
```

| # | Field | Values | Notes |
|---|---|---|---|
| ① | **Frame size** | `0`=42mm, `2`=60mm, `3`=70mm, `4`=80mm, `5`=90mm, `6`=104mm | mandatory |
| ② | **Motor type** | `I`=Induction, `R`=Reversible, `T`=Torque | mandatory |
| ③ | **Series** | `K` (fixed) | mandatory |
| ④ | **Power (W)** | 6, 10, 15, 25, 40, 60, 90, 120, 140, 150, 200 | mandatory |
| ⑤ | **Speed adj.** | `R` = Speed adjustable | optional, inserts between ④ and ⑥ |
| ⑥ | **Pinion type** | `GN`, `GU`, `A` (round shaft), `A1` (keyway) | mandatory, MUST match gear head |
| ⑦ | **Voltage & poles** | see table below | mandatory, prefixed with `-` |
| ⑧ | **Option (combined with ⑦)** | `T`/`F`/`FF`/`P`/`M` | optional, concatenated after voltage (no separator) |

### Voltage codes (field ⑦)
| Code | Voltage | Phase | Poles |
|---|---|---|---|
| `A` | 110V 50/60Hz | 1-phase | 4P |
| `B` | 110V 50Hz | 1-phase | 2P |
| `C` | 220V 50Hz | 1-phase | 4P |
| `D` | 220V 50Hz | 1-phase | 2P |
| `E` | 110/120V 60Hz | 1-phase | 4P |
| `H` | 220/230V 60Hz | 1-phase | 4P |
| `S` | 200/220/230V 50/60Hz | 3-phase | 4P |
| `S3` | 380/400/415V 50/60Hz | 3-phase | 4P |
| `T` | 200/220/230V 50/60Hz | 3-phase | 2P |
| `T3` | 380/400/415V 50/60Hz | 3-phase | 2P |

### Option codes (field ⑧, concatenated to ⑦)
| Code | Meaning |
|---|---|
| `T` | Terminal box type |
| `F` | With fan |
| `FF` | With forced fan |
| `P` | Thermal protector |
| `M` | Electromagnetic brake (power-off activated) |

Example combinations: `-CF` = C + F, `-CFF` = C + FF, `-CT` = C + T, `-CFM` = C + F + M

---

## GEAR HEAD CODE (4-5 segments)

```
 5    GN    [10X]    100    K
 ①    ②      ③       ④     ⑤
```

| # | Field | Values | Notes |
|---|---|---|---|
| ① | **Frame size** | same as motor (`0`/`2`/`3`/`4`/`5`/`6`) | MUST match motor |
| ② | **Pinion type** | `GN` or `GU` | MUST match motor |
| ③ | **Middle stage** | `10X` | optional, means 1:10 middle gear extension |
| ④ | **Ratio** | integer (e.g. 3, 5, 7.5, 9, 12.5, 15, 18, 25, 30, 36, 50, 60, 90, 100, 120, 150, 180, 200) | mandatory |
| ⑤ | **Mounting** | `K` = standard ball bearing, `KB` = square case (GU only) | mandatory |

### Ratio calculation with middle stage
- Normal: `5GN50K` = 1:50
- With middle: `5GN10X50K` = 1:10 × 1:50 = **1:500**
- With middle: `5GU10X100K` = 1:10 × 1:100 = **1:1000**

---

## VERIFIED EXAMPLES

| Full code | Motor | Gear head | Meaning |
|---|---|---|---|
| `5IK40GN-C-5GN50K` | 90mm Induction K 40W GN pinion, 220V 1-ph 4P | 90mm GN ratio 1:50 K | — |
| `4IK25RGN-C-4GN18K` | 80mm Induction K 25W Speed-adj GN, 220V 1-ph 4P | 80mm GN ratio 1:18 K | with R |
| `3IK15GN-CT-3GN200K` | 70mm Induction K 15W GN, 220V 1-ph 4P, terminal box | 70mm GN ratio 1:200 K | with option T |
| `5IK60GN-CF-5GN36K` | 90mm Induction K 60W GN, 220V 1-ph 4P, fan | 90mm GN ratio 1:36 K | with fan |
| `5IK60GU-CF-5GU18KB` | 90mm Induction K 60W GU, 220V 1-ph 4P, fan | 90mm GU ratio 1:18 KB | GU + KB mount |
| `5IK120GU-CF` | motor only — 90mm Induction K 120W GU, 220V 1-ph 4P, fan | (sold separately) | standalone motor |

---

## BRAND COMPATIBILITY

| Brand | Code format match | Notes |
|---|---|---|
| **SAS** | baseline | own catalog |
| **ZD** | 100% identical | drop-in |
| **Suntech** | 100% identical | drop-in |
| **Oriental Motor** | partial | to be documented |
| **SPG** | none | manual mapping |
| **Panasonic** | none | manual mapping |

---

## PARSING REGEX (Python)

### Motor
```python
MOTOR_RE = re.compile(
    r"^([02-6])"          # ① frame
    r"([IRT])"             # ② type
    r"(K)"                 # ③ series
    r"(\d{1,3})"           # ④ power
    r"(R)?"                # ⑤ speed-adj
    r"(GN|GU|A1|A)"        # ⑥ pinion
    r"-"                   # separator
    r"(S3|T3|[A-HST])"     # ⑦ voltage
    r"(FF|[TFPM]+)?"       # ⑧ option(s)
    r"$"
)
```

### Gear head
```python
GEARHEAD_RE = re.compile(
    r"^([02-6])"           # ① frame
    r"(GN|GU)"             # ② pinion
    r"(10X)?"              # ③ middle
    r"(\d{1,3})"           # ④ ratio
    r"(KB|K)"              # ⑤ mounting
    r"$"
)
```

### Full code
```python
FULL_CODE_RE = re.compile(
    r"^(?P<motor>[02-6][IRT]K\d{1,3}R?(?:GN|GU|A1|A)-(?:S3|T3|[A-HST])(?:FF|[TFPM]+)?)"
    r"-"
    r"(?P<gearhead>[02-6](?:GN|GU)(?:10X)?\d{1,3}(?:KB|K))$"
)
```
