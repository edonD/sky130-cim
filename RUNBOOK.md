# CIM Tile — Runbook

Step-by-step instructions for the entire build, from launch to MNIST inference.

---

## Phase 1: Parallel Block Design (3 instances)

### Launch

```bash
cd sky130-cim/infra
./deploy.sh phase1
```

This spins up 3x `c6a.4xlarge` (16 vCPU, 32GB RAM, ~$0.61/hr each).

### SSH into each and start the agent

**Bitcell** (34.204.193.234):
```bash
ssh -i ~/.ssh/schemato-key.pem ubuntu@<BITCELL_IP>
export ANTHROPIC_API_KEY="sk-ant-..."
tmux new -s bitcell
./launch_agent.sh bitcell
```

**ADC** (100.56.9.255):
```bash
ssh -i ~/.ssh/schemato-key.pem ubuntu@<ADC_IP>
export ANTHROPIC_API_KEY="sk-ant-..."
tmux new -s adc
./launch_agent.sh adc
```

**PWM Driver** (44.210.23.231):
```bash
ssh -i ~/.ssh/schemato-key.pem ubuntu@<PWM_IP>
export ANTHROPIC_API_KEY="sk-ant-..."
tmux new -s pwm
./launch_agent.sh pwm-driver
```

### Monitor

```bash
# Reconnect to a running agent
ssh -i ~/.ssh/schemato-key.pem ubuntu@<IP>
tmux attach -t bitcell   # or adc, pwm

# Check git log for progress
ssh -i ~/.ssh/schemato-key.pem ubuntu@<IP> \
  "cd ~/workspace/sky130-cim && git log --oneline -5"
```

### When done

Each agent will:
1. Meet all specs in `specs.json`
2. Generate all plots in `plots/`
3. Update `README.md` with results
4. Commit and push to the repo

Expected duration: **2–4 hours per block** (all 3 run simultaneously).

---

## Between Phase 1 and Phase 2

### Pull results and propagate measurements

```bash
# On your local machine
cd sky130-cim
git pull

# Check status
python orchestrate.py
# Expected: bitcell=DONE, adc=DONE, pwm-driver=DONE

# Propagate upstream measurements to downstream blocks
python orchestrate.py --propagate
# This writes upstream_config.json into array/ and integration/
# with concrete values (I_READ, T_LSB, DNL, etc.)

# Commit the propagated configs
git add -A && git commit -m "Propagate Phase 1 measurements to downstream blocks" && git push
```

### Kill Phase 1 instances

```bash
cd infra
terraform destroy -auto-approve
```

---

## Phase 2: Array Design (1 instance)

### Launch

```bash
cd sky130-cim/infra
./deploy.sh phase2
```

### SSH in and start

```bash
ssh -i ~/.ssh/schemato-key.pem ubuntu@<ARRAY_IP>
export ANTHROPIC_API_KEY="sk-ant-..."

# Setup (same script — installs ngspice-44, claude, PDK)
bash full_setup.sh array

tmux new -s array
./launch_agent.sh array
```

### What the array agent does

1. Imports the bitcell subcircuit from `../bitcell/design.cir`
2. Reads upstream measurements (I_READ, T_LSB, etc.) from `upstream_config.json`
3. Tiles cells into an 8×8 array first, then scales to 64×64
4. Programs random weight matrices
5. Applies PWM-encoded input vectors
6. Measures bitline voltages and compares against numpy MVM
7. Produces plots: MVM scatter, error histogram, bitline waveforms
8. Optimises precharge sizing and timing

Expected duration: **3–5 hours**.

### When done

```bash
cd sky130-cim
git pull
python orchestrate.py --propagate   # Push array measurements to integration
python orchestrate.py               # Should show array=DONE
git add -A && git commit -m "Propagate Phase 2 measurements" && git push

cd infra && terraform destroy -auto-approve
```

---

## Phase 3: Integration + MNIST (1 instance)

### Launch

```bash
cd sky130-cim/infra
./deploy.sh phase3
```

### SSH in and start

```bash
ssh -i ~/.ssh/schemato-key.pem ubuntu@<INTEGRATION_IP>
export ANTHROPIC_API_KEY="sk-ant-..."
bash full_setup.sh integration
tmux new -s integration
./launch_agent.sh integration
```

### What the integration agent does

1. Reads all upstream measurements from bitcell, adc, pwm-driver, array
2. Trains a binary-weight neural network for MNIST (784→64→10)
3. Builds a behavioral CIM tile model calibrated to SPICE measurements:
   - I_READ and leakage from bitcell
   - ADC DNL/INL/ENOB from ADC block
   - PWM linearity from PWM block
   - MVM RMSE from array block
4. Runs one small SPICE MVM for ground truth validation
5. Runs 1000 MNIST images through the behavioral model
6. Produces plots: confusion matrix, accuracy chart, example classifications
7. Reports final accuracy and performance numbers

Expected duration: **2–4 hours**.

### When done

```bash
cd sky130-cim
git pull
python orchestrate.py   # Should show all 5 blocks DONE
cd infra && terraform destroy -auto-approve
```

---

## Phase 4: Review and Report

### Check everything passed

```bash
python orchestrate.py
# All 5 blocks should show [DONE] with scores
```

### Review each block's README

Each block's `README.md` contains:
- Final spec results with pass/fail
- All testbench plots (waveforms, linearity, accuracy)
- Design rationale and parameter values
- Honest assessment of margins and limitations

### Generate the final HTML report

```bash
# Similar to the comparator report
python gen_cim_report.py   # (you'll write this based on the comparator's gen_report.py)
```

### Decide next steps

| Result | Action |
|--------|--------|
| All specs met with good margin | Proceed to tapeout planning |
| One block has thin margin | Re-run that block's agent with tighter specs |
| MNIST accuracy < 85% | Investigate: is the array RMSE too high? ADC too coarse? |
| Array MVM error > 10% | Check bitcell I_READ uniformity, increase precharge time |

---

## Cost Summary

| Phase | Instances | Type | Hours | Cost/hr | Total |
|-------|-----------|------|-------|---------|-------|
| Phase 1 | 3 | c6a.4xlarge | 2–4 | $0.61 | $3.66–$7.32 |
| Phase 2 | 1 | c6a.4xlarge | 3–5 | $0.61 | $1.83–$3.05 |
| Phase 3 | 1 | c6a.4xlarge | 2–4 | $0.61 | $1.22–$2.44 |
| **Total** | | | **7–13** | | **$6.71–$12.81** |

---

## Quick Reference

### Terraform commands

```bash
cd sky130-cim/infra
terraform apply -auto-approve    # Launch instances
terraform output ssh_commands    # Get SSH commands
terraform destroy -auto-approve  # Kill instances
```

### Orchestrator commands

```bash
cd sky130-cim
python orchestrate.py              # Show status of all blocks
python orchestrate.py --propagate  # Push measurements downstream
python orchestrate.py --launch     # Show what's ready to launch
```

### Agent commands (on EC2)

```bash
./launch_agent.sh <block>       # Start the autonomous agent
tmux attach -t <block>          # Reconnect to running agent
tmux ls                         # List tmux sessions
```

### Emergency: kill a stuck agent

```bash
tmux kill-session -t <block>    # Kill the tmux session
# Or just terminate the instance:
cd infra && terraform destroy -auto-approve
```

---

## Dependency Graph

```
Phase 1 (parallel, 3 instances):

    ┌──────────┐   ┌──────────┐   ┌──────────┐
    │ Bitcell  │   │   ADC    │   │   PWM    │
    │ (8T SRAM)│   │ (6-bit)  │   │ (4-bit)  │
    └────┬─────┘   └────┬─────┘   └────┬─────┘
         │              │              │
         │  propagate   │              │  propagate
         ▼              │              ▼
Phase 2 (1 instance):   │
    ┌──────────┐        │
    │  Array   │◄───────│──────────────┘
    │ (64×64)  │        │
    └────┬─────┘        │
         │              │
         │  propagate   │  propagate
         ▼              ▼
Phase 3 (1 instance):
    ┌─────────────────────┐
    │    Integration      │
    │  (MNIST inference)  │
    └─────────────────────┘
```
