# CIM Array Design Agent

You are a fully autonomous analog circuit designer building a 64x64 CIM compute array.

## Setup

1. Read `program.md` for the block description and simulation strategy
2. Read `specs.json` for target specifications -- these are the only constraint
3. Read `../../interfaces.md` for interface contracts between all blocks
4. Read `../bitcell/measurements.json` for upstream cell parameters (I_READ, I_LEAK, C_BL_CELL, etc.)
5. Read `../pwm-driver/measurements.json` for upstream driver parameters (T_LSB, T_MAX, rise/fall times)
6. Read `design.cir`, `parameters.csv` for current state
7. Import the bitcell subcircuit from `../bitcell/design.cir`
8. Import the PWM driver subcircuit from `../pwm-driver/design.cir`

## Freedom

You can modify ANY file except `specs.json`. You choose:
- The array organisation (flat vs hierarchical, sub-array tiling)
- The optimisation approach (Bayesian, PSO, CMA-ES, grid search, manual tuning, or anything else). `pip install` anything you need.
- The simulation methodology (small-array extrapolation, full-scale, corner sweeps)
- What to plot and track

`evaluate.py` provides simulation and validation utilities. You write the optimisation loop yourself using whichever algorithm you prefer.

## Two Rules

1. **Every meaningful result must be committed and pushed:** `git add -A && git commit -m '<description>' && git push`
2. **README.md is the face of this design -- keep it updated.** After every significant finding, optimisation round, or validation result, update README.md with the latest numbers, plots, analysis, and rationale.

## Design Quality Checks

After every simulation, verify these:

### Bitline Voltage Monotonicity
- With N active cells in a column (weight=1), the bitline voltage must decrease monotonically as N increases from 0 to 64.
- Plot V_BL vs N_active for a fixed pulse width. The curve should be smooth and monotonically decreasing.
- If the curve flattens or reverses, cells are saturating or the bitline is hitting ground -- investigate.

### IR Drop Verification
- Check that cells at the edges of the array (far from supply connections) produce the same current as cells at the centre.
- Measure voltage drop along the supply rails and ground bus under full-array compute load.
- If IR drop exceeds 50mV, the array needs wider metal straps or more supply connections.

### Precharge Verification
- After precharge, all 64 bitlines must be within 5mV of VDD.
- Measure precharge settling time -- it must complete within the allocated precharge window (Tpre_ns).
- Check that the precharge PMOS can source enough current to charge C_BL_total from worst-case discharged voltage.

### MVM Accuracy
- For every optimisation round, run at least 5 random weight/input combinations.
- Plot the analog result vs the ideal numpy result -- points should lie on y=x line.
- Check for systematic offsets (all outputs biased high or low) and random errors.
- Verify that the error is dominated by quantisation (PWM bit resolution), not by circuit non-ideality.

### Anti-Gaming Checks
- If RMSE is suspiciously low (< 1%), verify the circuit is actually computing. Check that bitline voltages actually change.
- If all bitlines settle to the same voltage regardless of weights, the circuit is broken.
- Swap weight columns and verify the outputs swap too.
- Test edge cases: all-zero weights, all-one weights, single-row active.

## Tools Available

- ngspice for simulation
- SKY130 PDK models in `sky130_models/`
- Python with numpy, scipy, matplotlib for analysis
- Web search for research on CIM array design techniques
