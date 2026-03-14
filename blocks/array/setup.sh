#!/usr/bin/env bash
# Setup script for the CIM array block
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$BLOCK_DIR/../.." && pwd)"

# Symlink SKY130 models into this block directory
if [ ! -e "$BLOCK_DIR/sky130_models" ]; then
    ln -sf "$REPO_ROOT/sky130_models" "$BLOCK_DIR/sky130_models"
    echo "Linked sky130_models"
fi

# Check upstream dependencies
if [ ! -f "$BLOCK_DIR/../bitcell/design.cir" ]; then
    echo "WARNING: ../bitcell/design.cir not found -- bitcell block must be completed first"
fi
if [ ! -f "$BLOCK_DIR/../bitcell/measurements.json" ]; then
    echo "WARNING: ../bitcell/measurements.json not found -- bitcell block must be completed first"
fi
if [ ! -f "$BLOCK_DIR/../pwm-driver/design.cir" ]; then
    echo "WARNING: ../pwm-driver/design.cir not found -- pwm-driver block must be completed first"
fi
if [ ! -f "$BLOCK_DIR/../pwm-driver/measurements.json" ]; then
    echo "WARNING: ../pwm-driver/measurements.json not found -- pwm-driver block must be completed first"
fi

# Verify ngspice is available
if command -v ngspice &>/dev/null; then
    echo "ngspice: $(ngspice --version 2>&1 | head -1)"
else
    echo "ERROR: ngspice not found in PATH"
    exit 1
fi

# Create output directories
mkdir -p "$BLOCK_DIR/plots"
mkdir -p "$BLOCK_DIR/results"

echo "Array block setup complete."
