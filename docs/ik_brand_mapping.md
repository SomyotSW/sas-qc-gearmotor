# IK Series — Brand Compatibility Mapping

## Compatibility summary

| Brand | Code structure | Voltage vocab | Shaft vocab | Mount vocab | Strategy |
|---|---|---|---|---|---|
| **SAS** | baseline | single-letter (A/B/C/D/S) | GN/GU/A/A1 | K/KB | — |
| **ZD** | 100% identical to SAS | same | same | same | **drop-in (no translation)** |
| **Suntech** | 100% identical to SAS | same | same | same | **drop-in (no translation)** |
| **Oriental Motor** | same structure | **2-letter + RoHS** (CW2/UT4) | GN/GE/A | S/K/RH/RA | translation table below |
| **SPG** | different | — | — | — | manual mapping (separate spec) |
| **Panasonic** | different | — | — | — | manual mapping (separate spec) |

---

## SAS ↔ Oriental Motor translation

### Voltage code mapping
Converting Oriental voltage code → SAS voltage code:

| Oriental | Meaning | SAS equivalent |
|---|---|---|
| `AW` | 1-ph 100V 4P | — (SAS uses 110V) ≈ `A` |
| `BW` | 1-ph 100V 2P | — (closest: `B` at 110V) |
| `CW` | 1-ph 200/220/230V 4P | **`C`** |
| `DW` | 1-ph 200/220/230V 2P | **`D`** |
| `SW` | 3-ph 200/220/230V 4P | **`S`** |
| `TW` | 3-ph 200/220/230V 2P | **`T`** |
| `U` | 3-ph 400V 4P | **`S3`** |

### Shaft type mapping

| Oriental | SAS | Notes |
|---|---|---|
| `GN` | `GN` | identical |
| `GE` | — | Oriental-exclusive; **closest SAS: `GU`** (high-eff, same application) |
| `A` | `A` | identical (round shaft) |

### Gear head mounting mapping

| Oriental | SAS | Notes |
|---|---|---|
| `S` (GN-S / GE-S long-life) | `K` | SAS `K` = standard ball bearing; Oriental `S` adds long-life/low-noise but functionally equivalent |
| `K` (GN-K) | `K` | identical |
| `RH`, `RA` | — | right-angle variants; SAS equivalent not in IK series (different product line) |

### Option mapping (motor field ⑧)

| Oriental | SAS | Notes |
|---|---|---|
| `T` | `T` | terminal box — identical |
| `T4` | — | TÜV certification grade — **ignore** (certification, not hardware) |
| `T4F` | `F` | TÜV + fan → map to SAS `F` |

### Capacitor suffix (Oriental field ⑨)
Oriental adds `E`/`U`/`J` only on packaging — **NEVER on nameplate**. Drop it.

---

## Example translations

| Oriental code | → | SAS equivalent |
|---|---|---|
| `4IK25GN-CW2` | → | `4IK25GN-C` |
| `5IK40GN-CW2` | → | `5IK40GN-C` |
| `5IK60GN-CW2` | → | `5IK60GN-C` |
| `5IK90GN-UT4` | → | `5IK90GN-S3` (TÜV cert ignored) |
| `5IK60GE-UT4F` | → | `5IK60GU-S3F` (GE→GU, TÜV→drop, F→F) |
| `5IK40GN-CW2T` | → | `5IK40GN-CT` |
| `4GN18S` | → | `4GN18K` |
| `5GN50K` | → | `5GN50K` (identical) |
| `5GN10X100S` | → | `5GN10X100K` |

Full pair example:
- Oriental: `5IK60GE-UT4F` + `5GE50S` → SAS: `5IK60GU-S3F-5GU50K`

---

## ZD / Suntech

**No translation needed.** Code vocabulary matches SAS 100%.

When sale scans a ZD or Suntech nameplate:
- Decoder: reuse `sas_ik_decoder` as-is
- Matcher: target brand = SAS → drop-in replacement
- UI: show "drop-in compatible" banner (no code rewrite needed beyond brand change)

Example:
- ZD: `5IK60GU-CF` + `5GU50KB` → SAS: `5IK60GU-CF` + `5GU50KB` (identical)

---

## SAS ↔ SPG translation

SPG code structure is **completely different** from SAS — requires field-by-field translation, not string mapping.

### Size mapping (semantic, not numeric)
SPG size = last digit of mm (6=60, 7=70, 8=80, 9=90)
SAS size = ordinal (2=60, 3=70, 4=80, 5=90)

| SPG size | mm | SAS size |
|---|---|---|
| `6` | 60mm | `2` |
| `7` | 70mm | `3` |
| `8` | 80mm | `4` |
| `9` | 90mm | `5` |

### Voltage mapping
| SPG | Meaning | SAS |
|---|---|---|
| `A` | 1-ph 110V 60Hz 4P | `A` |
| `B` | 1-ph 220V 60Hz 4P | `C` (closest: 1-ph 220V 4P) |
| `C` | 1-ph 100V 50/60Hz 4P | — (SAS has no 100V) |
| `D` | 1-ph 200V 50/60Hz 4P | `C` (220V ≈ 200V range) |
| `E` | 1-ph 115V 60Hz 4P | `E` |
| `X` | 1-ph 220~240V 50Hz 4P | `C` |
| `U` | 3-ph 200V 50/60Hz 4P | `S` |
| `T` | 3-ph 220V 50/60Hz 4P | `S` |
| `S` | 3-ph 380~440V 50/60Hz 4P | `S3` |

### Shaft type mapping (⚠ SPG doesn't distinguish GN/GU — use power)

**When SPG shaft ⑤ = `G` (Gear type, has pinion):**

Map to SAS pinion by **motor power**:
| Motor power | SAS pinion | Notes |
|---|---|---|
| ≤ 40W | `GN` | standard pitch |
| 60W | `GN` (default) | ambiguous — GU also valid |
| > 60W (90W+) | `GU` | high-efficiency required |

**When SPG shaft ⑤ ≠ `G`:**
| SPG | SAS |
|---|---|
| `S` (Straight) | `A` (round shaft) |
| `D` (D-cut) | `A` (no direct equivalent; round closest) |
| `K` (Key) | `A1` (keyway shaft) |

### SAS gear head mount rule (KB vs K)
- `GU` pinion + 60W~120W → `KB` (square case)
- All other combinations → `K`

### Option mapping

| SPG | Meaning | SAS |
|---|---|---|
| `T` | Terminal Box | `T` |
| `T1`, `T2` | alternative terminal types | `T` |
| `E` | Electromagnetic Brake | `M` |
| `B` | Semi-Brake | — (SAS has no semi-brake) |
| `S12`, `S24`, `V12` | Speed control accessory | — (SAS sells controllers separately) |
| `ES` | Brake + Speed control | `M` + external controller |
| `CE` | certification suffix | ignore |
| `H` / `L` impact (field ⑦) | bearing grade | ignore (SAS uses standard only) |

### Gear head field mapping

SPG gear head → SAS gear head:
| SPG field | SAS equivalent |
|---|---|
| size (6/7/8/9) | SAS size (2/3/4/5) |
| shaft type (S/D/K) | pinion type from motor (GN/GU) |
| output class (T/A/B/C/D/H) | informational only (not in SAS code) |
| ratio | ratio (identical values) |
| bearing (B/B1/M) | informational (not in SAS code) |
| impact (H/L) | ignore |
| Flange `-S` | no direct SAS equivalent |

### Ratio list
**Snap SPG ratios to SAS standard list** (ratios outside the list are rounded to nearest):
```
SAS/SPG standard: 3, 3.6, 5, 6, 7.5, 9, 10, 12.5, 15, 18, 20, 25,
                  30, 36, 50, 60, 75, 90, 100, 120, 150, 180, 200, 250
```

### Example translations

| SPG (motor+gearhead) | → | SAS equivalent |
|---|---|---|
| `S9I40GBH-S9KB36BH` | → | `5IK40GN-C-5GN36K` (40W → GN, 220V-B → C, bearing+impact ignored) |
| `S9I60GBH-S9KC50BH` | → | `5IK60GN-C-5GN50K` (60W → default GN; GN+K mount) |
| `S9I90GBH-S9KD36BH` | → | `5IK90GU-C-5GU36KB` (90W → GU; GU+60-120W → KB) |
| `S9I120GBH-S9KC10BH` | → | `5IK120GU-C-5GU10KB` |
| `S6I25GCH-S6DA18B` | → | `2IK25GN-C-2GN18K` (60mm size → SAS "2") |
| `S9I25RGCH` (reversible 25W) | → | `5IK25RGN-C` (motor only) |

---

## SAS ↔ Panasonic translation

Panasonic code structure is **different** from SAS — field-by-field translation required.

### Size mapping (identical to SPG logic)
| Panasonic | mm | SAS |
|---|---|---|
| `4` | 42mm | `0` |
| `6` | 60mm | `2` |
| `7` | 70mm | `3` |
| `8` | 80mm | `4` |
| `9` | 90mm | `5` |

### Voltage mapping
| Panasonic | Meaning | SAS |
|---|---|---|
| `L` | 1-ph 100V | — (SAS no 100V; closest `C` at 220V) |
| `Y` | 1-ph 200V | `C` (closest at 220V) |
| `D` | 1-ph 110V/115V | `E` (115V) or `A` (110V) |
| `G` | 1-ph 220V/230V | `C` |

### Shaft mapping
Panasonic shaft ⑥ `G` = pinion shaft. Does **not** distinguish GN vs GU.

Apply SAS pinion rule (same as SPG):
| Motor power | SAS pinion |
|---|---|
| ≤ 40W | `GN` |
| 60W | `GN` (default; override for high-eff requirement) |
| > 60W (90W+) | `GU` |

Panasonic shaft `S` (round) → SAS `A` (round shaft).

### Gear head mapping
| Panasonic bearing | Meaning | SAS equivalent |
|---|---|---|
| `B` / `BA` | Ball bearing | no SAS bearing code; use default K/KB by pinion rule |
| `M` / `MA` | Metal bearing | same default K (SAS doesn't distinguish) |
| `BU` | USA version | treat as `B` |
| `F` | Hinge attached | no direct equivalent |

### SAS gear head mount rule (KB vs K) — same as SPG
- `GU` pinion + 60W~120W → `KB` (square case)
- All other combinations → `K`

### Option mapping
| Panasonic | SAS |
|---|---|
| `K` (sealed connector, field ⑦) | ignore (SAS has no sealed connector option) |
| Classification 1 `G` (overseas) | ignore (SAS is already overseas-market) |
| Classification 2 `A`/`B` (no cap) | ignore (capacitor packaging detail) |

### Ratio list
Panasonic uses identical list: 3, 3.6, 5, 6, 7.5, 9, 10, 12.5, 15, 18, 20, 25, 30, 36, 50, 60, 75, 90, 100, 120, 150, 180, 200

### Example translations

| Panasonic (motor + gear head) | → | SAS equivalent |
|---|---|---|
| `M61X6G4L` + `MX6G50B` | → | `2IK06GN-C-2GN50K` (60mm, 6W, GN) |
| `M61X10G4L` + `MX6G30B` | → | `2IK10GN-C-2GN30K` (60mm, 10W → SAS has 10W exactly) |
| `M71X15G4L` + `MX7G30B` | → | `3IK15GN-C-3GN30K` (70mm, 15W) |
| `M81X25G4L` + `MX8G18B` | → | `4IK25GN-C-4GN18K` (80mm, 25W) |
| `M91X40G4L` + `MX9G36B` | → | `5IK40GN-C-5GN36K` (90mm, 40W, GN) |
| `M91Z60G4L` + `MX9G50B` | → | `5IK60GN-C-5GN50K` (90mm, 60W, GN default) |
| `M91Z90G4L` + `MX9G36B` | → | `5IK90GU-C-5GU36KB` (90mm, 90W, GU, KB) |
| `M91Z90G4GGB` (voltage G=220V, overseas) + `MX9G50B` | → | `5IK90GU-C-5GU50KB` |

### Notes
- **Power equivalence** (from user): SAS now supports 10W — Panasonic 10W ↔ SAS 10W (same dimensions, only code digit changes from `06` to `10`).
  - SAS power list (updated): `6, 10, 15, 25, 40, 60, 90, 120, 140, 150, 200`
- **Panasonic hinge `F` variant** (e.g. `M4G6F` or any gear head ending in `F`) → **REJECT / SHOW ERROR** — no SAS equivalent.
- **Motor type ③** (CONFIRMED): reads literally on nameplate as `1`, `R`, or `M`:
  - `1` = Induction single-phase
  - `R` = Reversible
  - `M` = Induction three-phase
- **Variant ④ rule** (CONFIRMED): `A`=3W, `X`=≤40W, `Z`=60W+

---

## Summary: supported brands

| Brand | Spec file | Mapping strategy |
|---|---|---|
| SAS | `ik_naming_spec.md` | baseline |
| ZD | — | identical to SAS (drop-in) |
| Suntech | — | identical to SAS (drop-in) |
| Oriental Motor | `oriental_naming_spec.md` | partial (voltage+shaft+mount translation) |
| SPG | `spg_naming_spec.md` | full field-by-field translation |
| Panasonic | `panasonic_naming_spec.md` | full field-by-field translation |
