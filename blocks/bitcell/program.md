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
