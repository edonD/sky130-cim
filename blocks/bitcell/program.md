# CIM SRAM Bitcell — Autonomous Design

You are designing a single SRAM bitcell for a compute-in-memory (CIM) array on SKY130 130nm.

## What This Cell Does

This is an 8-transistor (8T) SRAM cell. The first 6 transistors are a standard SRAM storage cell (two cross-coupled inverters + two access transistors). The extra 2 transistors form a **decoupled read port** used for analog computation.

During CIM operation:
- The stored bit (Q or QB) controls a read transistor
- A wordline (WL) gates a second transistor in series
- When BOTH weight=1 AND wordline=high, current flows from bitline (BL) through the read port to ground
- When weight=0, no current flows regardless of wordline
- This implements: I_out = WL × Weight × g_ds (a multiply in the analog domain)

Multiple cells share the same bitline. Their currents sum (Kirchhoff's current law), performing the accumulate step of multiply-accumulate.

## Circuit Topology: 8T SRAM CIM Cell

```
        VDD           VDD
         |             |
       [PL]          [PR]       <- PMOS loads (cross-coupled inverters)
         |             |
    Q ---+--- QB ------+        <- Storage nodes
         |             |
       [NL]          [NR]       <- NMOS drivers
         |             |
        VSS           VSS

    BLW --[AXL]-- Q    QB --[AXR]-- BLBW    <- Write access (standard 6T)
              |                  |
             WWL               WWL            <- Write wordline

    BL --[RD1]--+--[RD2]-- VSS              <- Read port (CIM compute)
                |       |
                Q      WL                    <- Gated by stored bit AND wordline
```

- `PL, PR`: PMOS load transistors (cross-coupled)
- `NL, NR`: NMOS pull-down transistors (cross-coupled)
- `AXL, AXR`: NMOS write access transistors (gated by WWL)
- `RD1`: NMOS read transistor (gate = Q, the stored bit)
- `RD2`: NMOS compute transistor (gate = WL, the wordline/input)
- Current path when weight=1 and WL=high: BL -> RD1 -> node_mid -> RD2 -> VSS

## Parameters You Optimise

| Parameter | Role | Typical Range |
|-----------|------|---------------|
| Wp | PMOS load width | 0.42-5 um |
| Lp | PMOS load length | 0.15-1 um |
| Wn | NMOS driver width | 0.42-5 um |
| Ln | NMOS driver length | 0.15-1 um |
| Wax | Access transistor width | 0.42-3 um |
| Wrd | Read port transistor width (both RD1 and RD2) | 0.42-10 um |
| Lrd | Read port transistor length | 0.15-1 um |

## What to Measure

1. **I_READ**: Set Q=1 (weight=1), WL=VDD, BL precharged to VDD. Measure steady-state current from BL to VSS through the read port. Target: > 5 uA.

2. **I_LEAK**: Set Q=0 (weight=0), WL=VDD, BL precharged to VDD. Measure leakage. Target: < 100 nA.

3. **ON/OFF Ratio**: I_READ / I_LEAK. Target: > 100.

4. **SNM**: Static noise margin of the 6T core. Apply equal and opposite DC voltage sources in the feedback loop, sweep, find the maximum noise the cell can tolerate. Target: > 100 mV.

5. **T_READ**: Apply WL step from 0 to VDD, measure time for BL current to reach 90% of I_READ. Target: < 5 ns.

## Files

| File | Editable? | Purpose |
|------|-----------|---------|
| `design.cir` | YES | Parametric SPICE netlist for the 8T cell |
| `parameters.csv` | YES | Parameter names, min, max, scale |
| `evaluate.py` | YES | Simulation runner, measurement, scoring |
| `specs.json` | **NO** | Target specifications |

## Technology

- **PDK:** SkyWater SKY130 (130nm)
- **Devices:** `sky130_fd_pr__nfet_01v8`, `sky130_fd_pr__pfet_01v8`
- **Supply:** 1.8V single supply
- **Models:** `.lib "sky130_models/sky130.lib.spice" tt`

## Interface Contract

Your final cell MUST have this port order for use by the array block:
```
.subckt cim_bitcell bl blb wl wwl q qb vdd vss
```

After optimisation, report these measured values in `measurements.json`:
- `i_read_ua`, `i_leak_na`, `on_off_ratio`, `snm_mv`, `t_read_ns`
- `c_bl_cell_ff` (bitline capacitance contributed by one cell, in fF)
- `cell_area_um2` (total transistor W*L area)

## Commit Rule

Every meaningful result must be committed and pushed:
```bash
git add -A && git commit -m '<description>' && git push
```
