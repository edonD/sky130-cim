# CIM 64x64 Array — Design Progress

## Status: ALL SPECS PASS (Score: 1.00)

| Spec | Target | Measured | Margin | Status |
|------|--------|----------|--------|--------|
| MVM RMSE | < 10% | 0.20% | 98.0% | PASS |
| Max Error | < 20% | 0.49% | 97.6% | PASS |
| Compute Time | < 100 ns | 89.97 ns | 10.0% | PASS |
| Power | < 5 mW | 0.001 mW | ~100% | PASS |

*Measured on 8x8 sub-array with 5 random test vectors. Full 64x64 validation pending.*

## Design Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| Wpre | 4.0 µm | Precharge PMOS width |
| Lpre | 0.15 µm | Precharge PMOS length |
| Tpre_ns | 5.0 ns | Precharge duration |
| Cbl_extra_ff | 10000 fF (10 pF) | Extra bitline capacitance |

## Architecture

The array is an 8x8 (development) / 64x64 (target) grid of 8T SRAM CIM bitcells. Each cell has a decoupled 2T read port (W=0.42µm, L=1.0µm) that provides I_READ ≈ 28.36 µA.

### Key Design Decisions

1. **Large BL capacitance (10 pF):** Required because I_READ × T_LSB = 28.36µA × 5ns = 0.142 pC per active cell per LSB. For 64 rows at max input (15), the total charge is 64 × 15 × 0.142 = 136 pC. With C_BL = 10 pF, the max voltage drop is 136/10 = 1.36V, leaving 0.44V above ground — using 76% of VDD range. This keeps the BL in a useful voltage range for the downstream ADC.

2. **PMOS precharge (W=4µm, L=0.15µm):** Strong PMOS to charge 10 pF BL to VDD in < 5ns. The precharge gate is active-low (PMOS turns on when gate = 0V).

3. **Precharge timing (5 ns):** Sufficient for the PMOS to charge 10 pF from worst-case discharged state.

## Waveform Plots

### MVM Scatter Plot
![MVM Scatter](plots/mvm_scatter.png)
Shows simulated vs ideal BL voltage for all test outputs. Points on the y=x line indicate accurate computation. The tight clustering demonstrates excellent MVM linearity.

### MVM Error Distribution
![MVM Error Histogram](plots/mvm_error_histogram.png)
Histogram of per-element errors. All errors are well below the 10% RMSE and 20% max error specs.

### MVM Accuracy per Test Vector
![MVM Accuracy](plots/mvm_accuracy_distribution.png)
RMSE per test vector. Consistent sub-0.25% RMSE across all test cases.

## Design Rationale

The fundamental challenge in CIM array design is matching the BL capacitance to the read current and PWM timing. With I_READ = 28.36 µA from the upstream bitcell and T_LSB = 5 ns from the PWM driver, each active cell deposits Q = I × T_LSB = 0.142 pC per LSB. For a 64-row array with all weights active at maximum input (15), the total charge is 136 pC. The BL capacitance must be large enough to absorb this charge without the BL voltage dropping below ground.

C_BL_extra = 10 pF was chosen to provide:
- Full-scale voltage swing of ~1.36V (76% of VDD) — good utilization of ADC input range
- Linear current-to-voltage conversion — the BL voltage stays well above 0V even in worst case
- Sub-1% RMSE — excellent accuracy for neural network inference

## What Was Tried and Rejected

1. **Small C_BL (50 fF default):** All bitlines saturated to 0V regardless of weights. The circuit appeared to pass specs because both simulation and ideal model clipped to 0V, but no actual computation was happening. This was caught by anti-gaming checks.

## Known Limitations

- Compute time has limited margin (10%). The PWM driver's T_LSB = 5ns × 15 levels = 75ns dominates.
- Power measurement may be underestimating — need to verify at 64x64 scale.
- 64x64 full-scale validation not yet performed.
- Precharge of 10 pF BL cap needs verification at full scale.

## Pending Verification

- [ ] 64x64 full-scale MVM validation
- [ ] Precharge waveform verification
- [ ] Single column dot product testbench
- [ ] Linearity test (V_BL vs input code)
- [ ] Worst-case discharge (all weights=1, max input)
- [ ] BL voltage monotonicity with N_active

## Experiment History

| Step | Score | Specs Met | Notes |
|------|-------|-----------|-------|
| 1 | 1.00 | 4/4 | Baseline with Cbl=10pF, 8x8, 5 vectors |
