# CIM PWM Wordline Driver — Autonomous Design

You are designing a PWM (pulse-width modulation) wordline driver for a compute-in-memory (CIM) array on SKY130 130nm.

## What This Block Does

This block converts a 4-bit digital input value (0-15) to a pulse on the wordline whose width is proportional to the input value. This encodes the activation/input as a time-domain signal for analog multiply-accumulate computation.

- **Input value 0** = no pulse (wordline stays low)
- **Input value 15** = longest pulse
- **Pulse width** = input_value x T_LSB

The wordline pulse width multiplied by the bitcell read current implements the analog multiply: charge accumulated on the bitline is proportional to (pulse width) x (cell conductance) = (input) x (weight).

## Architecture

The PWM driver consists of two stages:

### 1. Pulse Generation (Digital Logic)

A digital counter counts up from 0 to 15 on each clock cycle. A magnitude comparator checks if `counter < input_value`. The output goes high while `counter < input_value`, producing a pulse whose width equals `input_value x T_LSB`.

In SPICE simulation, this digital logic can be modeled with:
- **Behavioral voltage sources (B sources):** Generate the ideal pulse based on input code
- **PWL voltage sources:** Piecewise-linear waveform that produces the correct pulse width

The key requirement is that the pulse width is accurately proportional to the input code (linearity).

### 2. Output Buffer Chain (Transistor-Level)

A chain of CMOS inverters/buffers drives the wordline capacitance. Each row's wordline connects to 64 bitcell gate inputs, presenting approximately 100fF of capacitive load.

The buffer chain must:
- Deliver rail-to-rail output (0 to VDD) to fully turn on/off the SRAM read transistors
- Achieve fast rise/fall times (< 0.5ns) to minimize timing uncertainty
- Use progressive sizing (each stage larger than the previous) for efficient drive

## Parameters You Optimise

| Parameter | Role | Typical Range |
|-----------|------|---------------|
| Wbuf | NMOS buffer transistor width | 0.5-20 um |
| Lbuf | Buffer transistor length | 0.15-0.5 um |
| Nstages | Number of buffer stages | 1-4 |
| Tlsb | Duration of one LSB step | 1-10 ns |
| Wlogic | Logic gate transistor width | 0.5-5 um |

## What to Measure

1. **Linearity (%):** Sweep all 16 input codes (0-15), measure actual pulse width for each, compute maximum deviation from ideal linear relationship. Target: < 5%.

2. **Rise Time (ns):** Output rise time from 10% to 90% of VDD, driving 100fF load. Target: < 0.5 ns.

3. **Fall Time (ns):** Output fall time from 90% to 10% of VDD, driving 100fF load. Target: < 0.5 ns.

4. **Power (uW):** Average power per driver at 3MHz operation. Target: < 20 uW.

5. **T_LSB (ns):** Duration of one LSB pulse width step. Target: 1-10 ns.

## Files

| File | Editable? | Purpose |
|------|-----------|---------|
| `design.cir` | YES | Parametric SPICE netlist for the PWM driver |
| `parameters.csv` | YES | Parameter names, min, max, scale |
| `evaluate.py` | YES | Simulation runner, measurement, scoring |
| `specs.json` | **NO** | Target specifications |

## Technology

- **PDK:** SkyWater SKY130 (130nm)
- **Devices:** `sky130_fd_pr__nfet_01v8`, `sky130_fd_pr__pfet_01v8`
- **Supply:** 1.8V single supply
- **Models:** `.lib "sky130_models/sky130.lib.spice" tt`

## Interface Contract

See `../../interfaces.md` for full interface contracts between blocks.

Your final driver MUST have this port order:
```
.subckt pwm_driver in3 in2 in1 in0 wl clk vdd vss
```

Where:
- `in3..in0`: 4-bit input code (in3 = MSB, in0 = LSB)
- `wl`: Wordline output (drives 100fF load)
- `clk`: Master clock input
- `vdd`: Power supply (1.8V)
- `vss`: Ground

After optimisation, report these measured values in `measurements.json`:
- `linearity_pct`, `rise_time_ns`, `fall_time_ns`, `power_uw`, `t_lsb_ns`
- `max_pulse_ns` (pulse width for code 15)
- `min_pulse_ns` (pulse width for code 1)

## Commit Rule

Every meaningful result must be committed and pushed:
```bash
git add -A && git commit -m '<description>' && git push
```
