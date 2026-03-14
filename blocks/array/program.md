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
