# CIM 64x64 Array -- Autonomous Design

You are designing the compute array for a compute-in-memory (CIM) tile on SKY130 130nm. The array is a 64x64 grid of SRAM CIM bitcells that performs a matrix-vector multiply (MVM) in the analog domain.

## Dependencies

This block depends on two upstream blocks being complete:

1. **Bitcell block** (`../bitcell/design.cir`) -- provides the 8T SRAM CIM bitcell subcircuit with pinout `(bl blb wl wwl q qb vdd vss)`. Read `../bitcell/measurements.json` for measured cell parameters (I_READ, I_LEAK, C_BL_CELL, etc.).

2. **PWM driver block** (`../pwm-driver/design.cir`) -- provides wordline driver subcircuits that convert 4-bit digital inputs into PWM pulses. Read `../pwm-driver/measurements.json` for driver parameters (T_LSB, T_MAX, rise/fall times).

See `../../interfaces.md` for the full interface contracts between all blocks.

## What This Block Does

The array tiles 64x64 bitcells into a grid where:
- Each **row** shares a wordline (WL) -- the input activation
- Each **column** shares a bitline (BL) -- the output accumulation line
- The stored weight matrix W[i][j] programs whether cell (i,j) conducts or not

### Operation (One Compute Cycle)

1. **Precharge phase:** Assert `rst` high. PMOS precharge transistors pull all 64 bitlines to VDD. All wordlines are low.

2. **Compute phase:** Deassert `rst`. Apply PWM-encoded input pulses on all 64 wordlines simultaneously. Each wordline pulse width is proportional to the corresponding input value (set by upstream PWM drivers). Cells with weight=1 discharge their bitline; cells with weight=0 do not.

3. **Settle phase:** All wordlines return low. Bitline voltages settle to their final values.

4. **Result:** The voltage on bitline j after compute is:
   ```
   V_BL[j] = VDD - (1/C_BL) * sum_i( W[i][j] * I_READ * T_pulse[i] )
   ```
   where the sum is over all 64 rows. This is the dot product of the input vector (encoded as pulse widths) and weight column j, expressed as a voltage drop.

5. The 64 bitline voltages are passed to downstream ADCs for digitisation.

## Parameters You Optimise

| Parameter | Role | Range |
|-----------|------|-------|
| Wpre | Precharge PMOS width | 0.5--10 um |
| Lpre | Precharge PMOS length | 0.15--0.5 um |
| Tpre_ns | Precharge duration | 2--20 ns |
| Cbl_extra_ff | Extra bitline parasitic capacitance model | 0--100 fF |

The precharge transistors must be strong enough to pull all 64 bitlines to VDD within the precharge window, but not so large that they add excessive parasitic capacitance.

## What to Measure

1. **MVM RMSE (%):** Program a known weight matrix W, apply a known input vector x (via PWM pulse widths), read the 64 bitline voltages, convert to dot-product results, and compare against `numpy.matmul(W, x)`. Compute the normalised root-mean-square error across all 64 outputs. Repeat for 10 random test vectors and report the average. Target: < 10%.

2. **Max Error (%):** The worst single-element error across all test vectors. Target: < 20%.

3. **Compute Time (ns):** Time from first wordline activation to the point where all bitlines have settled (within 1% of final value). Target: < 100 ns.

4. **Power (mW):** Total array power during one compute cycle (precharge + compute + settle). Target: < 5 mW.

## Simulation Strategy

- **Start with an 8x8 sub-array** for fast simulation iteration and optimisation. Verify correctness at this scale first.
- **Scale to 64x64** for final validation. The bitline capacitance scales linearly with the number of rows (64 cells per column), so the 8x8 results can be extrapolated, but a full-scale verification is required for sign-off.
- Use the bitcell's measured `C_BL_CELL` to model the total bitline capacitance: `C_BL_total = N_rows * C_BL_CELL + C_BL_extra`.

## Key Challenges

- **Bitline capacitance grows with array size:** 64 cells per column adds significant capacitance. The precharge must be strong enough, and the voltage swing must still be large enough for ADC resolution.
- **Charge sharing and parasitic coupling:** Adjacent bitlines can couple capacitively, causing crosstalk errors.
- **Cell linearity:** Each cell must contribute current linearly with pulse width. If any cell's read transistors enter triode region or saturate differently, the dot-product is distorted.
- **IR drop along wordlines and bitlines:** Long metal lines have resistance. Cells at the far end of a wordline or bitline may see reduced voltage, causing systematic errors.
- **Precharge uniformity:** All 64 bitlines must reach VDD to within a few mV before compute begins. Incomplete precharge causes offset errors.

## Interface

- **Inputs:** 64 wordline pulses (from PWM drivers), weight programming interface (WWL, BLW, BLBW)
- **Outputs:** 64 bitline voltages (to ADCs)
- **Port order:** `(wl[63:0] bl[63:0] pre rst wwl[63:0] blw[63:0] blbw[63:0] vdd vss)`

## Files

| File | Editable? | Purpose |
|------|-----------|---------|
| `design.cir` | YES | Parametric SPICE netlist for the CIM array |
| `parameters.csv` | YES | Parameter names, min, max, scale |
| `evaluate.py` | YES | Simulation runner, MVM verification, scoring |
| `specs.json` | **NO** | Target specifications |

## Technology

- **PDK:** SkyWater SKY130 (130nm)
- **Devices:** `sky130_fd_pr__nfet_01v8`, `sky130_fd_pr__pfet_01v8`
- **Supply:** 1.8V single supply
- **Models:** `.lib "sky130_models/sky130.lib.spice" tt`

## Commit Rule

Every meaningful result must be committed and pushed:
```bash
git add -A && git commit -m '<description>' && git push
```
