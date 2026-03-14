# SKY130 SRAM Compute-in-Memory Tile

> **Status: IN DEVELOPMENT** — Block-level design phase

## What This Is

A 64×64 SRAM-based compute-in-memory (CIM) tile for edge AI inference, designed on the open-source SkyWater SKY130 130nm CMOS process. The tile performs matrix-vector multiplication in the analog domain — the core operation behind neural network inference.

## Architecture

```
              INPUTS (4-bit digital, 64 values)
                    │
                    ▼
            ┌──────────────┐
            │  PWM Drivers │  ← Converts input values to pulse widths
            │  (64 units)  │
            └──────┬───────┘
                   │ wordlines (pulse-width encoded)
                   ▼
            ┌──────────────┐
            │  64×64 SRAM  │  ← Weights stored in memory cells
            │  CIM Array   │     Current = weight × input (analog)
            │              │     Sum on each column wire (Kirchhoff)
            └──────┬───────┘
                   │ bitlines (analog voltage = dot product)
                   ▼
            ┌──────────────┐
            │  SAR ADCs    │  ← Converts analog result to 6-bit digital
            │  (64 units)  │     Uses StrongARM comparator (validated)
            └──────┬───────┘
                   │
                   ▼
              OUTPUTS (6-bit digital, 64 values)
```

**One compute cycle = 4,096 multiply-accumulate operations in one analog shot.**

## Block Status

| Block | Status | Score | Description |
|-------|--------|-------|-------------|
| [Bitcell](blocks/bitcell/) | Pending | — | 8T SRAM CIM cell |
| [SAR ADC](blocks/adc/) | Pending | — | 6-bit ADC (reuses StrongARM comparator) |
| [PWM Driver](blocks/pwm-driver/) | Pending | — | 4-bit pulse-width modulation driver |
| [Array](blocks/array/) | Blocked | — | 64×64 compute array (needs bitcell + PWM) |
| [Integration](blocks/integration/) | Blocked | — | Full tile + MNIST demo (needs all above) |

## Build Order & Dependencies

```
Phase 1 (parallel):     Bitcell ──────┐
                                      ├──→ Array ──┐
                        PWM Driver ───┘             ├──→ Integration (MNIST)
                                                    │
                        SAR ADC ────────────────────┘

Phase 1: 3 blocks in parallel (3 cloud instances)
Phase 2: Array (1 instance, after bitcell + PWM complete)
Phase 3: Integration (1 instance, after array + ADC complete)
```

## Orchestration

```bash
# Check status of all blocks
python orchestrate.py

# After a block completes, propagate its measurements downstream
python orchestrate.py --propagate

# See which blocks are ready to launch
python orchestrate.py --launch
```

## Specifications

| Spec | Target | Description |
|------|--------|-------------|
| MVM accuracy | > 90% | Matrix-vector multiply matches numpy |
| MNIST accuracy | > 85% | Handwritten digit classification |
| Cycle time | < 500 ns | One complete 64×64 MVM |
| Power | < 10 mW | During active compute |

## Prior Art

This project builds on validated SKY130 blocks:
- **StrongARM comparator** — Score 1.00, validated across 30 PVT corners + 200 MC samples
- **Bandgap reference** — Score 1.00, 14.4 ppm/°C tempco

## Key Files

```
sky130-cim/
├── master_spec.json      Top-level tile specifications
├── interfaces.md         Signal contracts between blocks
├── orchestrate.py        Build status + dependency management
├── README.md             This file
└── blocks/
    ├── bitcell/          8T SRAM CIM cell
    ├── adc/              6-bit SAR ADC
    ├── pwm-driver/       PWM wordline driver
    ├── array/            64×64 CIM array
    └── integration/      Full tile + MNIST inference
```
