# CIM Tile Interface Contracts

Every block in this project must respect these interfaces exactly. If you're an agent designing one block, read this file to understand what your inputs and outputs must look like.

## Signal Naming Convention

All signals use these names across every block:

| Signal | Description | Voltage Range |
|--------|-------------|---------------|
| `vdd` | Power supply | 1.8V |
| `vss` | Ground | 0V |
| `wl[0:63]` | Wordlines (row select / input encoding) | 0 to 1.8V |
| `bl[0:63]` | Bitlines (column current accumulation) | Precharged to 1.8V, discharges during compute |
| `blb[0:63]` | Bitline bar (complementary, used for write) | 0 to 1.8V |
| `wwl[0:63]` | Write wordlines (for programming weights) | 0 to 1.8V |
| `clk` | Master clock | 0 to 1.8V |
| `rst` | Reset / precharge signal | 0 to 1.8V, active high |
| `d_out[0:63]` | Digital output from ADCs (6-bit each) | Digital |

## Block-to-Block Interfaces

### 1. Bitcell → Array

The bitcell block produces a single cell. The array block tiles it 64×64.

| Parameter | Symbol | Expected Range | Measured By |
|-----------|--------|----------------|-------------|
| Read current (weight=1) | `I_READ` | 5–50 µA | Bitcell agent |
| Leakage current (weight=0) | `I_LEAK` | < 100 nA | Bitcell agent |
| ON/OFF ratio | `I_READ / I_LEAK` | > 100 | Bitcell agent |
| Bitline capacitance per cell | `C_BL_CELL` | report value | Bitcell agent |
| Read access time | `T_READ` | < 5 ns | Bitcell agent |
| Write time | `T_WRITE` | < 10 ns | Bitcell agent |
| Cell area (W×L total) | `A_CELL` | report value | Bitcell agent |

**What the array agent needs from bitcell:** The final `design.cir` subcircuit with pinout `(bl blb wl wwl vdd vss)` and the measured values above.

### 2. PWM Driver → Array (Wordlines)

The PWM driver converts a digital input value to a pulse on the wordline.

| Parameter | Symbol | Expected Range | Measured By |
|-----------|--------|----------------|-------------|
| Input bits | `N_INPUT` | 4 bits | Fixed |
| Pulse width for input=1 | `T_LSB` | 1–5 ns | PWM agent |
| Pulse width for input=15 | `T_MAX` | 15–75 ns (= 15 × T_LSB) | PWM agent |
| Rise/fall time | `T_RF` | < 0.5 ns | PWM agent |
| Output voltage swing | | 0 to 1.8V (rail-to-rail) | PWM agent |
| Wordline load capacitance | `C_WL` | ~100 fF (64 cell gates) | Array agent provides |

**What the array agent needs from PWM:** A subcircuit with pinout `(in[3:0] wl clk vdd vss)` that produces a pulse on `wl` whose width is proportional to the 4-bit input value.

### 3. Array → ADC (Bitlines)

After compute, each bitline holds a voltage that represents the dot product. The ADC digitises it.

| Parameter | Symbol | Expected Range | Measured By |
|-----------|--------|----------------|-------------|
| Bitline precharge voltage | `V_PRE` | 1.8V (= VDD) | Array agent |
| Bitline voltage after compute | `V_BL` | V_PRE − N_active × ΔV | Array agent |
| Voltage step per active cell | `ΔV` | 5–50 mV (depends on I_READ × T_pulse / C_BL) | Array agent |
| Full-scale range | `V_FS` | ΔV × 64 (worst case all weights=1) | Array agent |
| Bitline settling time | `T_SETTLE` | < 20 ns after pulse ends | Array agent |

**What the ADC agent needs:** Input voltage range `[V_PRE − V_FS, V_PRE]` and required resolution (6 bits).

### 4. ADC → Integration (Digital Output)

| Parameter | Symbol | Expected Range | Measured By |
|-----------|--------|----------------|-------------|
| Resolution | | 6 bits | Fixed |
| DNL | | < 0.5 LSB | ADC agent |
| INL | | < 1.0 LSB | ADC agent |
| Conversion time | `T_CONV` | < 200 ns | ADC agent |
| Input range | | Set by array measurements | Array agent → ADC agent |
| Output format | | 6-bit unsigned digital | ADC agent |

## Timing Diagram (One Compute Cycle)

```
         ┌──────────────────────────────────────────────────────┐
         │                  ONE COMPUTE CYCLE                   │
         ├──────┬───────────────┬──────────┬───────────┬────────┤
Phase:   │ PRE  │    COMPUTE    │  SETTLE  │  CONVERT  │  READ  │
         │      │               │          │           │        │
clk:     ──┐    │               │          │           │    ┌───
           └────┘               │          │           │    │
                                │          │           │
rst:     ──┐                    │          │           │
           └────────────────────┘          │           │
                                           │           │
wl[i]:       ┌──── T_pulse ────┐           │           │
         ────┘                  └──────────│───────────│────────
                                           │           │
bl[j]:   VDD ────────╲                     │           │
                      ╲ discharge          │           │
                       ╲── V_result ───────│───────────│────────
                                           │           │
ADC:                                       │ ┌─ SAR ──┐│
                                           │ │6 cycles││
                                           │ └────────┘│
                                           │           │
d_out:                                     │           │ VALID
         ──────────────────────────────────┘           └────────
```

**Phase durations (targets):**
- PRE (precharge): ~5 ns
- COMPUTE (wordline pulses active): up to 75 ns (depends on input values)
- SETTLE (bitline settles): ~20 ns
- CONVERT (SAR ADC): ~200 ns
- TOTAL CYCLE: ~300 ns → ~3 MHz compute rate

## Physical Constants (SKY130)

| Parameter | Value | Notes |
|-----------|-------|-------|
| VDD | 1.8V | Standard 1.8V devices |
| nfet_01v8 Vth (tt) | ~0.4V | Typical threshold |
| pfet_01v8 Vth (tt) | ~−0.4V | Typical threshold |
| Min L | 0.15 µm | |
| Min W | 0.42 µm (nfet), 0.55 µm (pfet) | |
| Avt (nfet) | ~5 mV·µm | Pelgrom mismatch |
| kT/C noise | 4.14e-21 J at 300K | |

## File Exchange Between Blocks

When a block agent finishes, it produces:
1. `best_parameters.csv` — optimised parameter values
2. `measurements.json` — all measured interface values
3. `design.cir` — final netlist (usable as subcircuit by downstream blocks)

The `orchestrate.py` script at the top level reads `measurements.json` from upstream blocks and updates `specs.json` in downstream blocks with concrete values.
