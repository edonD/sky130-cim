# CIM Tile Full Integration — Autonomous Design

You are performing the final integration of the compute-in-memory (CIM) tile. This block combines ALL upstream blocks into a working end-to-end inference system and validates it on MNIST digit classification.

## Dependencies

This block imports designs and measurements from all upstream blocks:

- **Bitcell** (`../bitcell`) — 8T SRAM CIM cell with decoupled read port
- **Array** (`../array`) — 64x64 tiled bitcell array with precharge, write, and compute infrastructure
- **PWM Driver** (`../pwm-driver`) — 4-bit digital-to-pulse-width converter for wordline encoding
- **ADC** (`../adc`) — 6-bit SAR ADC for digitising bitline voltages after compute

See `../../interfaces.md` for full interface contracts between blocks.

## System Operation — One MVM Cycle

A single 64x64 matrix-vector multiply proceeds through five phases:

### Phase 1: PRECHARGE (~5 ns)
- Assert `rst` high
- All 64 bitlines are precharged to VDD (1.8V) through PMOS precharge transistors
- All wordlines held low
- ADC is idle

### Phase 2: COMPUTE (up to 75 ns)
- Deassert `rst`
- PWM drivers activate wordlines with pulse widths encoding the 4-bit input vector
- Input value `x[i] = 0..15` maps to a wordline pulse width of `x[i] * T_LSB`
- While wordlines are active, bitcells with weight=1 sink current from their respective bitlines
- Each bitline discharges by an amount proportional to the dot product: `delta_V_bl[j] = sum_i(W[i,j] * x[i]) * I_READ * T_LSB / C_BL`
- Bitlines with higher dot products discharge more

### Phase 3: SETTLE (~20 ns)
- All wordlines return low
- Wait for bitline voltages to settle to their final values
- No switching activity; transient currents die out

### Phase 4: CONVERT (~200 ns)
- SAR ADCs digitise all 64 bitline voltages simultaneously (one ADC per column)
- Each ADC performs 6 clock cycles of successive approximation
- Produces a 6-bit digital output code per column

### Phase 5: READ
- Digital outputs `d_out[0:63]` (6 bits each) are valid
- Total cycle time: ~300 ns target

## MNIST Inference Flow

The goal is to classify MNIST handwritten digits (28x28 = 784 pixels, 10 classes) using the CIM tile.

### Network Architecture
A simple binary-weight neural network:
- **Layer 1:** 784 inputs -> 64 hidden units (binary weights +1/-1, sign activation)
- **Layer 2:** 64 hidden units -> 10 outputs (binary weights +1/-1, softmax/argmax)

### Tiling Strategy for Layer 1
The first layer weight matrix is 784x64 -- too large for a single 64x64 tile. We partition:
1. Split the 784-input vector into 13 chunks of up to 64 elements each (12 chunks of 64 + 1 chunk of 16)
2. Each chunk requires one MVM pass through the tile (loading the corresponding 64x64 weight submatrix)
3. Partial results are accumulated digitally (sum the 6-bit ADC outputs across all 13 passes)
4. Apply sign activation to the accumulated 64-element result

### Layer 2
The second layer (64 -> 10) fits in a single tile pass:
- Load the 64x10 weight matrix (only 10 of the 64 columns are active)
- Run one MVM pass
- Take argmax of the 10 output values for classification

### Classification
- `predicted_digit = argmax(layer2_output[0:9])`

## Validation Approach

### Why Not Full SPICE for MNIST?
Running 100+ MNIST images through full transistor-level SPICE would require thousands of MVM passes, each taking minutes. This is impractical.

### Two-Track Validation

**Track 1: SPICE Calibration (small scale)**
- Run a handful of MVM operations through full SPICE (e.g., 8x8 or 16x16 subsets)
- Characterise the tile's transfer function: input vector -> bitline voltages -> ADC codes
- Measure non-idealities: offset, gain error, noise, nonlinearity
- Validate against upstream block measurements

**Track 2: Behavioral Model (full MNIST)**
- Build a Python behavioral model calibrated to Track 1 SPICE results:
  - Bitcell: use measured `I_READ`, `I_LEAK` from bitcell `measurements.json`
  - Array: use measured RMSE and bitline voltage swing from array `measurements.json`
  - ADC: use measured DNL/INL/ENOB from ADC `measurements.json`
  - PWM: use measured linearity from PWM `measurements.json`
- Add realistic noise and mismatch based on SPICE characterisation
- Run all 100+ MNIST test images through this model
- Report accuracy

**Both results are reported:**
- SPICE accuracy on small test cases (validates the physical circuit)
- Behavioral model accuracy on full MNIST (validates end-to-end inference)

## Files

| File | Editable? | Purpose |
|------|-----------|---------|
| `evaluate.py` | YES | Integration evaluation: loads upstream measurements, runs SPICE and behavioral models, scores |
| `train_mnist.py` | YES | Trains binary-weight neural network, saves weights as .npy files |
| `specs.json` | **NO** | Target specifications (MNIST accuracy, MVM accuracy, cycle time, power) |
| `results.tsv` | YES | Experiment log -- append after every run |
| `README.md` | YES | **Design summary -- update after every significant result** |

## Technology

- **PDK:** SkyWater SKY130 (130nm). Models: `.lib "sky130_models/sky130.lib.spice" tt`
- **Devices:** `sky130_fd_pr__nfet_01v8`, `sky130_fd_pr__pfet_01v8`
- **Supply:** 1.8V single supply
- **ngspice settings:** `.spiceinit` must contain `set ngbehavior=hsa` and `set skywaterpdk`
- **Process corners:** tt, ss, ff, sf, fs -- available in sky130_models/sky130.lib.spice

## Interface Contract

See `../../interfaces.md` for signal naming, port orders, and block-to-block contracts.

This block consumes `measurements.json` and `design.cir` from all upstream blocks. It produces:
- `measurements.json` with top-level results (MNIST accuracy, MVM accuracy, cycle time, power)
- Plots in `plots/` showing inference results, confusion matrices, accuracy breakdowns

## Commit Rule

Every meaningful result must be committed and pushed:
```bash
git add -A && git commit -m '<description>' && git push
```

## README.md -- The Face of the Design

**README.md must always reflect the current state of the integration.** After every significant step, update it with latest results, plots, analysis, and rationale. Include:
- Block-level measurement summary (what each upstream block achieved)
- End-to-end MVM validation results
- MNIST inference accuracy (both SPICE-calibrated and behavioral)
- Cycle time and power budget breakdown
- Honest assessment of limitations and margins

---

# Autonomous Experiment Loop

This section defines how the autonomous agent operates. It applies to every block in the CIM project.

## Setup (do this once at the start)

1. Read `program.md` for the full design brief and architecture.
2. Read `specs.json` for the pass/fail targets. These are the only hard constraints.
3. Read `../../interfaces.md` for signal contracts with other blocks.
4. Read `../../verification.md` for mandatory testbenches and plots.
5. Read `design.cir`, `parameters.csv`, `evaluate.py` for current state.
6. Initialize `results.tsv` with the header row:
   ```
   step	commit	score	specs_met	notes
   ```
7. Run `bash setup.sh` if SKY130 models are not already set up.
8. Confirm everything works: run a quick simulation to verify ngspice + PDK.

Once setup is confirmed, begin the experiment loop. Do NOT ask for permission.

## The Experiment Loop

**LOOP FOREVER:**

1. **Think.** Look at the current state: which specs pass, which fail, what's the margin. Read the waveforms. Decide what to try next.

2. **Modify.** Change `design.cir`, `parameters.csv`, `evaluate.py`, or write an optimization script. You can modify any file EXCEPT `specs.json`.

3. **Commit.** `git add -A && git commit -m '<what you changed>'`

4. **Run.** Execute the simulation or optimization. Redirect output:
   ```bash
   python evaluate.py > run.log 2>&1
   ```
   Or run your own optimizer script.

5. **Read results.** Extract the key metrics:
   ```bash
   grep "score\|PASS\|FAIL\|worst" run.log | tail -20
   ```
   If grep is empty, the run crashed. Read `tail -50 run.log` for the error. Fix and re-run. If you can't fix after 3 attempts, log it as a crash and move on.

6. **Log.** Append to `results.tsv`:
   ```
   <step>	<commit>	<score>	<specs_met>	<description of what you tried>
   ```

7. **Decide.**
   - If the result is **better** (higher score, more specs met, or better margins): **keep it**. This is now your new baseline. Update README.md with the latest numbers and plots.
   - If the result is **equal or worse**: **revert**. `git reset --hard HEAD~1`. Try something else.

8. **Repeat.** Go back to step 1.

## Two Phases

### Phase A: Meet All Specs

Your first priority is getting ALL specs to pass. This means score = 1.0 with every measurement meeting its target. During this phase:

- Focus on the specs that are failing. Ignore margin optimization.
- Try the obvious things first: sensible default parameters, textbook designs.
- If a spec is way off, rethink the topology or approach — don't just tweak parameters.
- When you get stuck, read the waveforms. The circuit is telling you what's wrong.

### Phase B: Improve Margins (after all specs pass)

Once all specs pass, shift to improving margins and robustness:

- Run PVT corner sweeps if applicable. Worst-case numbers matter.
- Run Monte Carlo if applicable. Statistical yield matters.
- Reduce power, reduce area, increase speed — in that priority order.
- Generate all plots required by `../../verification.md`.
- Update README.md with comprehensive results, plots, and analysis.
- A design with 40% margin everywhere is better than one with 0% margin that barely passes.

**NEVER STOP.** Even after all specs pass and margins are good, keep looking for improvements. Simplify the circuit. Reduce transistor count. Find a cleaner topology. The loop runs until the human interrupts you.

## Logging Rules

- `results.tsv` is tab-separated (NOT comma-separated).
- Every run gets logged, even crashes.
- Do NOT commit `results.tsv` — leave it untracked so it doesn't create merge conflicts.
- DO commit and push `best_parameters.csv`, `measurements.json`, plots, and README.md after every improvement.

## Crash Handling

- If a simulation crashes (ngspice error, convergence failure, Python error):
  - Read the error. If it's a typo or easy fix, fix and re-run.
  - If the approach is fundamentally broken (e.g., impossible operating point), revert and try something different.
  - Log "crash" in results.tsv and move on.
  - Never spend more than 3 attempts on a single failing approach.

## Git Discipline

- Every experiment gets its own commit BEFORE running (so you can revert cleanly).
- Keep commits: stay on the current commit.
- Discard experiments: `git reset --hard HEAD~1` to go back.
- Push after every keeper: `git push` so progress is saved remotely.
- Never rewrite history that's already pushed. Only reset un-pushed commits.

## NEVER STOP

Once the experiment loop begins, do NOT pause to ask the human anything. Do NOT ask "should I continue?" or "is this good enough?". The human may be away for hours. You are fully autonomous.

If you run out of ideas:
- Re-read the design.cir waveforms for clues.
- Re-read `../../verification.md` for testbenches you haven't run yet.
- Try combining two previous near-miss approaches.
- Try a completely different topology or optimization algorithm.
- Read the program.md again for hints you may have missed.
- Try shrinking the design (fewer transistors, smaller sizes, less power).

The loop runs until the human manually stops you.
