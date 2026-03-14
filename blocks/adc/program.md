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
