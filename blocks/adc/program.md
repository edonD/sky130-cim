# Autonomous Circuit Design — 6-bit SAR ADC for Compute-in-Memory

You are an autonomous analog/mixed-signal circuit designer. Your goal: design a 6-bit SAR (Successive Approximation Register) ADC that meets every specification in `specs.json` using the SKY130 foundry PDK.

This ADC is part of a compute-in-memory (CIM) system. It reads the analog bitline voltage produced by a CIM multiply-accumulate operation and converts it to a 6-bit digital output code. The input voltage range is 0 to 1.8V (full supply rail), which will be narrowed later based on actual array output swing measurements.

## Architecture

The SAR ADC consists of three main blocks:

1. **StrongARM Comparator** — Already designed and validated in the `sky130-comparator` project. Import the proven design (topology: tail NMOS, input differential pair, reset PMOS, cross-coupled inverter latch). Do not redesign from scratch.

2. **Binary-Weighted Capacitive DAC** — An array of 6 capacitors with binary weighting:
   - C5 = 32 * Cu (MSB)
   - C4 = 16 * Cu
   - C3 = 8 * Cu
   - C2 = 4 * Cu
   - C1 = 2 * Cu
   - C0 = 1 * Cu (LSB)
   - Total capacitance: 63 * Cu + Cu (termination) = 64 * Cu

   The bottom plates are switched between VREF (VDD=1.8V) and ground by the SAR logic. The top plate connects to the comparator input.

3. **SAR Logic** — A digital state machine that performs the successive approximation algorithm:
   - **Sample phase:** Close the sampling switch, charge the top plate to Vin
   - **Conversion phase (6 clock cycles):** Starting from MSB (bit 5) to LSB (bit 0):
     1. Set the current bit to 1 (switch corresponding cap bottom plate to VREF)
     2. Trigger the comparator
     3. If comparator says DAC > Vin, clear the bit (switch back to GND); otherwise keep it set
     4. Move to the next bit
   - After 6 cycles, d[5:0] holds the digital output code

## Interface

Port order for the ADC subcircuit:
```
.subckt sar_adc_6b vin d5 d4 d3 d2 d1 d0 clk vdd vss
```

- `vin` — Analog input voltage (0 to 1.8V)
- `d5..d0` — 6-bit digital output (d5 = MSB, d0 = LSB)
- `clk` — SAR clock input (drives the conversion sequencing)
- `vdd` — Supply (1.8V)
- `vss` — Ground

See `../../interfaces.md` for full interface contracts and signal naming conventions.

## Parameters to Optimise

| Parameter | Description | Min | Max | Scale |
|-----------|-------------|-----|-----|-------|
| Cu | Unit capacitance (fF) | 10 | 500 | log |
| Wcomp_in | Comparator input pair width (um) | 10 | 100 | log |
| Lcomp_in | Comparator input pair length (um) | 0.5 | 2 | log |
| Wcomp_latch | Comparator latch width (um) | 0.5 | 10 | log |
| Lcomp_latch | Comparator latch length (um) | 0.15 | 1 | log |
| Wcomp_tail | Comparator tail width (um) | 5 | 50 | log |
| Tsar_ns | SAR clock period (ns) | 5 | 50 | log |

Key tradeoffs:
- **Cu:** Larger Cu improves matching (lower DNL/INL) but increases power and conversion time
- **Wcomp_in / Lcomp_in:** Larger input pair reduces comparator offset but adds load to DAC
- **Tsar_ns:** Shorter period = faster conversion but comparator may not resolve in time

## Optimization — Your Choice

You choose your own optimization approach. There is no built-in optimizer — you decide what works best and implement it yourself. Some options:

- **Bayesian Optimization** (e.g. `scikit-optimize`, `botorch`, `ax-platform`)
- **Particle Swarm Optimization** (e.g. `pyswarm`, `pyswarms`)
- **CMA-ES** (e.g. `cma`, `pycma`)
- **Differential Evolution** (e.g. `scipy.optimize.differential_evolution`)
- **Optuna** for hyperparameter-style search
- **Manual tuning** with design intuition
- **Any other method** — `pip install` anything you need

`evaluate.py` provides simulation and validation utilities (ngspice runner, measurement extraction, scoring, plotting). You write the optimization loop yourself.

## Files

| File | Editable? | Purpose |
|------|-----------|---------|
| `design.cir` | YES | Parametric SPICE netlist (comparator + DAC + SAR logic) |
| `parameters.csv` | YES | Parameter names, min, max, scale |
| `evaluate.py` | YES | Simulation utilities, measurement extraction, scoring |
| `specs.json` | **NO** | Target specifications |
| `results.tsv` | YES | Experiment log — append after every run |
| `README.md` | YES | **Design summary — update after every significant result** |

## Technology

- **PDK:** SkyWater SKY130 (130nm). Models: `.lib "sky130_models/sky130.lib.spice" tt`
- **Devices:** `sky130_fd_pr__nfet_01v8`, `sky130_fd_pr__pfet_01v8` (and LVT/HVT variants)
- **Capacitors:** `sky130_fd_pr__cap_mim_m3_1` or ideal capacitors for initial design
- **Instantiation:** `XM1 drain gate source bulk sky130_fd_pr__nfet_01v8 W=10u L=0.5u nf=1`
- **Supply:** 1.8V single supply. Nodes: `vdd` = supply, `vss` = 0V
- **Units:** Always specify W and L with `u` suffix (micrometers). Capacitors with `f` (femtofarads).
- **ngspice settings:** `.spiceinit` must contain `set ngbehavior=hsa` and `set skywaterpdk`
- **Process corners:** tt, ss, ff, sf, fs — available in sky130_models/sky130.lib.spice

## Specifications

The ADC must meet these specs:

| Spec | Target | Description |
|------|--------|-------------|
| `dnl_lsb` | < 0.5 LSB | Differential non-linearity (worst case across all codes) |
| `inl_lsb` | < 1.0 LSB | Integral non-linearity (worst case across all codes) |
| `enob` | > 5.0 bits | Effective number of bits |
| `conversion_time_ns` | < 200 ns | Time for one complete 6-bit conversion |
| `power_uw` | < 50 uW | Average power during conversion |

## Measurement Methodology

### DNL and INL
- Sweep input voltage across full range in steps smaller than 1 LSB (at least 4x oversampling: 256+ points for 64 codes)
- Record the output code at each input voltage
- Compute code transition points (where code changes)
- DNL[k] = (actual step width for code k) / (ideal step width) - 1
- INL[k] = cumulative sum of DNL from code 0 to code k
- Report worst-case |DNL| and |INL|

### ENOB
- Apply a ramp or sinusoidal input
- Compute ENOB = (SINAD - 1.76) / 6.02 from the output code sequence
- Alternatively: ENOB = N - log2(sigma_quantization / sigma_ideal) where N=6

### Conversion Time
- Total time = sample phase + 6 * Tsar_ns (one clock per bit)
- Must complete full conversion in < 200 ns

### Power
- Average supply current * VDD during one complete conversion cycle

## Comparator Integration

The StrongARM comparator from the `sky130-comparator` project should be incorporated as a subcircuit. Key points:
- Copy the proven topology (tail NMOS, NMOS input pair, PMOS reset, cross-coupled NMOS/PMOS latch)
- The comparator parameters (Wcomp_in, Lcomp_in, etc.) map to the original Win, Lin, etc.
- The comparator is clocked by the SAR logic — one comparison per SAR clock cycle
- Ensure the comparator resolves fully within Tsar_ns

## Commit Rule

Every meaningful result must be committed and pushed:
```bash
git add -A && git commit -m '<description>' && git push
```

## README.md — The Face of the Design

**README.md must always reflect the current state of the design.** After every significant step, update it with latest results, plots, analysis, and rationale.

---

---

# Autonomous Experiment Loop

This section defines how the autonomous agent operates. It applies to every block in the CIM project.

**Remember the big picture:** This block is part of a Compute-in-Memory chip that will perform neural network inference in the analog domain. Every design decision, every waveform, every plot should be evaluated through that lens. Ask yourself: "Will this work when 64 of these are wired together on a shared bitline?" or "Will this ADC correctly digitise a signal that came from an analog dot product?" The block in isolation means nothing — it must work in the system.

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
