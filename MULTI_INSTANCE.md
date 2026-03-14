# Multi-Instance Parallel Development

## The Idea

Run multiple AI agents simultaneously on different blocks of the CIM project. Each agent works on its own git branch/worktree, so they never conflict.

## Setup: Git Worktrees

Git worktrees let multiple branches be checked out simultaneously in different directories. Each cloud instance gets its own worktree.

### Initial Setup (run once on the main machine)

```bash
cd sky130-cim
git init
git add -A && git commit -m "Initial project structure"
git remote add origin <your-github-url>
git push -u origin main
```

### Creating Worktrees for Each Cloud Instance

```bash
# From the main repo directory:

# Instance 1: Bitcell
git worktree add ../sky130-cim-wt-bitcell -b dev/bitcell
# This creates a full checkout at ../sky130-cim-wt-bitcell on branch dev/bitcell

# Instance 2: ADC
git worktree add ../sky130-cim-wt-adc -b dev/adc

# Instance 3: PWM Driver
git worktree add ../sky130-cim-wt-pwm -b dev/pwm-driver
```

### On Each Cloud Instance

```bash
# Clone the repo
git clone <your-github-url> sky130-cim
cd sky130-cim

# Check out the block's branch
git checkout dev/bitcell  # or dev/adc, dev/pwm-driver

# Run setup
cd blocks/bitcell
bash setup.sh

# Launch the AI agent pointing at this directory
# The agent reads program.md, specs.json, and starts designing
```

### After a Block Completes

```bash
# On the cloud instance (e.g., bitcell)
cd sky130-cim
git add -A
git commit -m "Bitcell: final design, score X.XX"
git push origin dev/bitcell

# On the main machine: merge into main
git checkout main
git merge dev/bitcell
git push origin main

# Run orchestration to propagate measurements
python orchestrate.py --propagate
python orchestrate.py  # Check what's ready next
```

## Recommended Instance Allocation

### Phase 1: Three Parallel Instances

| Instance | Block | Branch | Depends On | Est. Time |
|----------|-------|--------|-----------|-----------|
| Cloud 1 | Bitcell | `dev/bitcell` | Nothing | 2-4 hours |
| Cloud 2 | SAR ADC | `dev/adc` | Nothing | 2-4 hours |
| Cloud 3 | PWM Driver | `dev/pwm-driver` | Nothing | 1-2 hours |

All three run simultaneously. No conflicts since they work in separate `blocks/` subdirectories.

### Phase 2: One Instance

| Instance | Block | Branch | Depends On | Est. Time |
|----------|-------|--------|-----------|-----------|
| Cloud 1 | Array | `dev/array` | Bitcell + PWM | 3-5 hours |

Merge bitcell and PWM results into main first. Then the array agent reads their `measurements.json` files.

### Phase 3: One Instance

| Instance | Block | Branch | Depends On | Est. Time |
|----------|-------|--------|-----------|-----------|
| Cloud 1 | Integration | `dev/integration` | Array + ADC | 2-4 hours |

Merge array and ADC results first. Then integration runs MNIST.

## Two Instances on the Same Block (Advanced)

If you want to run TWO agents on the same block simultaneously (e.g., two different optimization strategies for the bitcell):

```bash
# Strategy 1: Differential Evolution
git worktree add ../sky130-cim-bitcell-de -b dev/bitcell-de
# Launch agent with DE approach

# Strategy 2: Bayesian Optimization
git worktree add ../sky130-cim-bitcell-bo -b dev/bitcell-bo
# Launch agent with BO approach

# After both finish, compare results and merge the better one
```

This is useful for:
- Racing two optimization algorithms against each other
- Exploring different topologies (e.g., 8T vs 10T bitcell)
- One agent does design, another does verification

## File Isolation Rules

Each agent ONLY modifies files within its block directory:
- `blocks/bitcell/` — bitcell agent only
- `blocks/adc/` — ADC agent only
- `blocks/pwm-driver/` — PWM agent only

Top-level files (`interfaces.md`, `orchestrate.py`, `master_spec.json`) are read-only for block agents. Only the human or the integration agent modifies them.

## Monitoring Progress

From any machine with access to the repo:

```bash
git fetch --all

# Check each branch's latest commit
git log --oneline dev/bitcell -1
git log --oneline dev/adc -1
git log --oneline dev/pwm-driver -1

# Run orchestration status
python orchestrate.py
```
