# PWM Wordline Driver — Agent Instructions

## Setup
1. Read `program.md` for the block architecture and measurement requirements
2. Read `specs.json` for target specifications — these are the only constraint
3. Read `../../interfaces.md` for interface contracts with other CIM blocks
4. Read `design.cir`, `parameters.csv`, `results.tsv` for current state

## Freedom

You can modify ANY file except `specs.json`. You choose:
- The PWM generation approach: counter-based, delay-line based, ring-oscillator based, or any other architecture
- The buffer chain topology: simple inverter chain, tapered buffer, tristate output, etc.
- The optimization algorithm: Bayesian Optimization, Particle Swarm, CMA-ES, Optuna, scipy.optimize, manual tuning, or anything else. `pip install` anything you need.
- The evaluation methodology
- What to plot and track

`evaluate.py` provides simulation and validation utilities. You write the optimization loop yourself using whichever algorithm you prefer.

## Two Rules
1. **Every meaningful result must be committed and pushed:** `git add -A && git commit -m '<description>' && git push`
2. **README.md is the face of this design — keep it updated.** After every significant finding, optimization round, or validation result, update README.md with the latest numbers, plots, analysis, and rationale.

## Design Quality Checks

This is primarily a digital block, but analog considerations matter. After every simulation:

### Pulse Width Linearity
- Sweep all 16 input codes (0-15) and measure actual pulse width
- Plot measured pulse width vs. input code — it must be a straight line
- Compute DNL-like metric: deviation of each step from ideal T_LSB
- Watch for code-dependent errors (e.g., carry propagation delay in counter)

### Driving Strength
- The output must drive 100fF (64 cell gates on one wordline)
- Check rise/fall times are symmetric — asymmetric edges cause systematic timing errors
- Verify output reaches full rail (0V and 1.8V) — a wordline that doesn't reach VDD leaves read transistors partially on, corrupting the analog computation

### Power and Area
- Compute power at 3MHz operation (typical CIM inference clock)
- Track total transistor area (sum of W*L for all devices)
- Buffer stages should be sized with a tapering factor of ~3-4x per stage

### Timing
- T_LSB sets the precision-speed tradeoff: smaller T_LSB = faster inference but harder to control
- Total pulse width for code 15 = 15 * T_LSB, this must fit within the clock period
- Verify that the buffer chain delay is much smaller than T_LSB (otherwise it distorts the pulse)

### Anti-Gaming Checks
- A driver that outputs a constant pulse regardless of input code has "perfect" rise/fall time but is broken
- Verify that pulse width actually changes with input code by simulating at least codes 1, 7, and 15
- Check that code 0 produces NO pulse (wordline stays low)
- Confirm pulse width ratio: pw(code=14)/pw(code=7) should be ~2.0

## Tools Available
- ngspice for simulation
- SKY130 PDK models in `sky130_models/` (run `setup.sh` first)
- Python with numpy, scipy, matplotlib for analysis
- Web search for researching PWM architectures
