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

---

---

# Autonomous Experiment Loop

This section defines how the autonomous agent operates. It applies to every block in the CIM project.

**Remember the big picture:** This block is part of a Compute-in-Memory chip that will perform neural network inference in the analog domain. Every design decision, every waveform, every plot should be evaluated through that lens. Ask yourself: "Will this work when 64 of these are wired together on a shared bitline?" or "Will this ADC correctly digitise a signal that came from an analog dot product?" The block in isolation means nothing — it must work in the system.

## You Have Full Freedom — Use It

You are not limited to what's in this repo. You have access to the entire internet and you should use it aggressively:

- **Search the web** for state-of-the-art designs. Look up ISSCC papers, JSSC publications, IEEE Xplore, ResearchGate, university thesis PDFs. Find what the best analog designers in the world have done for this exact type of circuit.
- **Search for SKY130 examples.** Other people have designed similar circuits on the SKY130 PDK — find their repos on GitHub, read their netlists, learn from their parameter choices.
- **Look up design techniques.** If you're stuck on offset, search "SRAM bitcell offset reduction techniques." If your ADC has missing codes, search "SAR ADC missing codes root cause." If your comparator is too slow, search "StrongARM comparator speed optimization."
- **Read textbooks and course notes online.** Razavi, Allen-Holberg, Murmann lecture slides — many are freely available and contain exact design equations you can use.
- **Find application notes and design guides.** Companies like Analog Devices, TI, and Maxim publish detailed design guides for ADCs, comparators, and memory circuits.
- **Study the competition.** Search for Mythic AI architecture, IBM analog AI ISSCC papers, academic SRAM-CIM papers from KAIST, MIT, Stanford. Understand what the best CIM designs look like and steal their best ideas.
- **pip install anything you need.** If a better optimizer exists (optuna, cma, bayesian-optimization), install it. If you need a plotting library, install it. You have full access.

Do whatever you think is necessary to produce the best possible design. There are no restrictions on your research methods. The only constraint is the specs in `specs.json` — everything else is fair game.

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
- Even during Phase A, plot waveforms after every successful run. You need to see what the circuit is doing — numbers alone are not enough.

### Phase B: Deep Verification & Margin Improvement (after all specs pass)

Once all specs pass, this is where the real engineering begins. You are no longer just hitting targets — you are proving this circuit is ready to be part of a CIM chip.

#### B.1 — Waveform Analysis (MANDATORY after every keeper)

After every run that you keep, you MUST:

1. **Plot the key waveforms** and save them to `plots/`. Every plot must have:
   - Clear axis labels (time in ns, voltage in V, current in uA)
   - A descriptive title that states what the plot shows
   - Annotation of key events (e.g., "CLK rises here", "latch regenerates", "code transition")

2. **Study every waveform critically.** For each plot, ask yourself:
   - Does this look like what the textbook says it should? If not, why?
   - Are there any sharp jumps, glitches, or ringing? If so, what causes them? Is it a simulation artifact or a real problem?
   - Is the signal settling cleanly, or is it still moving when we sample it?
   - Are the voltage levels correct? (e.g., does a "high" output actually reach VDD? Does a "low" reach VSS?)
   - Is there any unexpected current flow? Leakage where there shouldn't be?

3. **If something looks wrong, investigate before moving on.** A waveform anomaly is more important than a passing spec number. The spec might pass by accident while the circuit is doing something broken.

4. **If data is missing or a measurement returns zero/NaN**, do not ignore it. Explain why in the README. Common causes:
   - The simulation didn't run long enough
   - A node is floating
   - The circuit is in a metastable state
   - The measurement trigger condition was never met

5. **If there are sharp jumps or discontinuities in any plot**, explain them in the README:
   - Is it a clock edge? Label it.
   - Is it charge injection from a switch? Quantify it.
   - Is it a convergence artifact? Check with finer timestep.
   - Is it clipping? Check operating regions.

#### B.2 — System-Level Thinking

Remember that this block will be integrated into a CIM tile. After each improvement, ask:

- **Bitcell agent:** "If 64 of these cells share a bitline, will the currents sum linearly? Is there any interaction between cells that breaks superposition?"
- **ADC agent:** "When the input comes from a CIM bitline (not a clean voltage source), will this ADC still work? What about the bitline's output impedance and settling behavior?"
- **PWM agent:** "When this pulse drives 64 cell gates simultaneously, will the edges still be sharp? What's the actual RC delay?"
- **Array agent:** "Does the precharge fully restore the bitline? Is there residual charge from the previous cycle affecting the next computation?"
- **Integration agent:** "Does the end-to-end error budget close? Bitcell nonlinearity + ADC quantization + PWM jitter — do they add up to less than the accuracy target?"

#### B.3 — README.md as the Progress Dashboard

**README.md is how the human monitors your progress.** They will read ONLY the README to understand what you've done, what works, what doesn't, and what the circuit looks like. Write it for a designer who has never seen this block before.

README.md MUST contain (update after every keeper):

1. **Status banner** at the top: which specs pass, which fail, current score.

2. **Spec table** with measured values, targets, margin percentage, and pass/fail:
   ```
   | Spec | Target | Measured | Margin | Status |
   ```

3. **Waveform plots** — the most important ones, embedded as `![description](plots/filename.png)`. For each plot, include:
   - One sentence explaining what the plot shows
   - One sentence explaining what to look for (what "good" looks like)
   - If anything is anomalous, explain it

4. **Design parameters** — current values in a table.

5. **Design rationale** — why you chose this topology, why these sizes. Not just "optimiser found these" but the engineering reason.

6. **What was tried and rejected** — a brief log of approaches that didn't work and why. This prevents the next agent (or a human) from repeating dead ends.

7. **Known limitations** — honest assessment. What are the weak points? What would break first if this went to silicon?

8. **Experiment history** — summary table of all runs (can reference results.tsv).

**If a README section has no data yet**, don't delete the section — write "Pending: will be updated after [next step]" so the human knows it's planned.

**If a plot shows unexpected behavior**, don't hide it. Show the plot, annotate the anomaly, and explain your hypothesis for what's causing it. Honest reporting is more valuable than clean-looking results.

#### B.4 — Verification Plots Checklist

Refer to `../../verification.md` for the full list of mandatory testbenches and plots for your block. During Phase B, systematically work through every testbench. For each one:

1. Run the testbench simulation
2. Save the plot to `plots/` with the exact filename specified in verification.md
3. Add the plot to README.md with analysis
4. If the testbench reveals a problem, fix the design and re-run

Do not consider Phase B complete until every testbench in verification.md has been run and every plot has been generated.

#### B.5 — Margin Improvement

After all verification plots are done, continue improving:

- Run PVT corner sweeps if applicable. Worst-case numbers matter.
- Run Monte Carlo if applicable. Statistical yield matters.
- Reduce power, reduce area, increase speed — in that priority order.
- A design with 40% margin everywhere is better than one with 0% margin that barely passes.
- Try simplifying: fewer transistors, smaller sizes, less complexity. If you can remove something and still pass, that's a win.

**NEVER STOP.** Even after all specs pass, all plots are generated, and margins are good, keep looking for improvements. Simplify the circuit. Reduce transistor count. Find a cleaner topology. The loop runs until the human interrupts you.

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
- Re-read the waveforms. Look at every node. The circuit is telling you something.
- Re-read `../../verification.md` for testbenches you haven't run yet.
- Try combining two previous near-miss approaches.
- Try a completely different topology or optimization algorithm.
- Read the program.md again for hints you may have missed.
- Try shrinking the design (fewer transistors, smaller sizes, less power).
- Re-read `../../interfaces.md` — think about how your block connects to the others. Is there an interface issue you haven't considered?

The loop runs until the human manually stops you.
