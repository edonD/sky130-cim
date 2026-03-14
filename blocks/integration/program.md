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
